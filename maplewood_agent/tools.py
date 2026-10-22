"""Shared clinic-system functions used by the local agent and the MCP server.

All data are synthetic. The state is loaded into memory; appointment mutations last
for the life of the process only.
"""
from __future__ import annotations

import json
import re
import secrets
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _time_minutes(value: str) -> int:
    hh, mm = map(int, value.split(":"))
    return hh * 60 + mm


def _minutes_time(value: int) -> str:
    return f"{value // 60:02d}:{value % 60:02d}"


def _iso(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


MEDICATION_ALIASES = {
    "xanax": "alprazolam",
    "metformina": "metformin",
    "ativan": "lorazepam",
    "ambien": "zolpidem",
    "adderall": "amphetamine dextroamphetamine",
    "oxy": "oxycodone",
    "metformin": "metformin",
    "lisinopril": "lisinopril",
    "atorvastatin": "atorvastatin",
    "sertraline": "sertraline",
    "zolpidem": "zolpidem",
    "omeprazole": "omeprazole",
    "levothyroxine": "levothyroxine",
    "amlodipine": "amlodipine",
    "warfarin": "warfarin",
    "escitalopram": "escitalopram",
    "donepezil": "donepezil",
    "cetirizine": "cetirizine",
    "insulin glargine": "insulin glargine",
    "albuterol": "albuterol",
    "lisinopril hctz": "lisinopril hctz",
}


class ClinicTools:
    """Policy-aware tools over the supplied JSON files."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.clinic = self._load("clinic.json")
        self.patients = self._load("patients.json")
        self.medications = self._load("medications.json")
        self.appointments = self._load("appointments.json")
        self.today = date.fromisoformat(self.clinic["today"])
        self.audit_log: list[dict[str, Any]] = []
        self._patient_by_id = {p["id"]: p for p in self.patients}
        # Short-lived, process-local proof that find_patient verified the sender.
        self._verification_tokens: dict[str, str] = {}

    def _load(self, filename: str) -> Any:
        with (self.data_dir / filename).open(encoding="utf-8") as handle:
            return json.load(handle)

    def record(self, event: str, **details: Any) -> None:
        self.audit_log.append({"event": event, **details})

    def find_patient(
        self, name: str = "", contact: str = "", stated_dob: str | None = None
    ) -> dict[str, Any]:
        """Resolve the intended chart and independently report verification status.

        A name-only match is a candidate, not a verified identity. A matching on-file
        contact or an exact name + DOB match verifies the sender. On-file caregiver
        contacts are also valid under the supplied policy.
        """
        clean_contact = _norm(contact)
        clean_name = _norm(name)
        candidate: dict[str, Any] | None = None
        verification_reason = "no_matching_record"

        if clean_contact:
            for patient in self.patients:
                patient_contacts = {_norm(patient.get("phone")), _norm(patient.get("email"))}
                caregiver = patient.get("authorised_caregiver") or {}
                caregiver_contacts = {_norm(caregiver.get("phone")), _norm(caregiver.get("email"))}
                if clean_contact in patient_contacts - {""}:
                    candidate = patient
                    verification_reason = "on_file_patient_contact"
                    break
                if clean_contact in caregiver_contacts - {""}:
                    candidate = patient
                    verification_reason = "on_file_authorised_caregiver_contact"
                    break

        if candidate is None and clean_name:
            for patient in self.patients:
                if _norm(patient.get("name")) == clean_name:
                    candidate = patient
                    break
                caregiver = patient.get("authorised_caregiver") or {}
                if _norm(caregiver.get("name")) == clean_name:
                    candidate = patient
                    break
            if candidate is not None:
                if stated_dob and stated_dob == candidate.get("dob") and _norm(candidate.get("name")) == clean_name:
                    verification_reason = "name_and_dob_match"
                else:
                    verification_reason = "name_only_unverified"

        verified = verification_reason in {
            "on_file_patient_contact",
            "on_file_authorised_caregiver_contact",
            "name_and_dob_match",
        }
        verification_token = secrets.token_urlsafe(24) if verified and candidate else None
        if verification_token and candidate:
            self._verification_tokens[verification_token] = candidate["id"]
        self.record(
            "find_patient",
            candidate_id=candidate["id"] if candidate else None,
            verified=verified,
            verification_reason=verification_reason,
        )
        return {
            "candidate_id": candidate["id"] if candidate else None,
            "verified": verified,
            "verification_reason": verification_reason,
            "is_authorised_caregiver": verification_reason == "on_file_authorised_caregiver_contact",
            "verification_token": verification_token,
        }

    def _require_verified(self, patient_id: str, verification_token: str | None) -> None:
        if not verification_token or self._verification_tokens.get(verification_token) != patient_id:
            self.record("access_denied", patient_id=patient_id, reason="missing_or_invalid_verification_token")
            raise PermissionError("Patient verification is required before accessing or changing a chart.")

    def read_medications(self, patient_id: str, verification_token: str | None = None) -> list[dict[str, Any]]:
        """Return medication records only with a token issued after successful verification."""
        self._require_verified(patient_id, verification_token)
        records = [deepcopy(m) for m in self.medications if m["patient_id"] == patient_id]
        self.record("read_medications", patient_id=patient_id, count=len(records))
        return records

    def check_refill_eligibility(self, patient_id: str, medication_query: str = "", verification_token: str | None = None) -> dict[str, Any]:
        """Evaluate refill criteria only with verified patient context; never issue a prescription."""
        self._require_verified(patient_id, verification_token)
        records = [m for m in self.medications if m["patient_id"] == patient_id]
        query = _norm(medication_query)
        normalized_query = query
        for alias, target in MEDICATION_ALIASES.items():
            if _norm(alias) and _norm(alias) in query:
                normalized_query = _norm(target)
                break

        selected = None
        if normalized_query and normalized_query not in {"all medications", "all my medications", "all"}:
            terms = normalized_query.split()
            for medication in records:
                name_norm = _norm(medication["medication"])
                if all(term in name_norm for term in terms) or any(term in name_norm for term in terms):
                    selected = medication
                    break
        elif len(records) == 1:
            selected = records[0]

        if selected is None:
            result = {
                "status": "medication_not_identified",
                "eligible": False,
                "reason": "No matching active medication could be identified safely.",
                "medication": None,
            }
            self.record("check_refill_eligibility", patient_id=patient_id, status=result["status"])
            return result

        medication = deepcopy(selected)
        result: dict[str, Any] = {
            "medication": medication["medication"],
            "prescriber": medication["prescriber"],
            "controlled_schedule": medication.get("controlled_schedule"),
            "refills_remaining": medication["refills_remaining"],
            "eligible": False,
        }

        if medication.get("controlled_schedule"):
            result.update(status="controlled_substance", reason="Controlled substances are never prepared by the agent.")
        elif medication["refills_remaining"] <= 0:
            result.update(status="no_refills_remaining", reason="Provider review is required for a new prescription.")
        else:
            patient = self._patient_by_id.get(patient_id)
            last_visit = date.fromisoformat(patient["last_visit"]) if patient and patient.get("last_visit") else None
            one_year_ago = self.today.replace(year=self.today.year - 1)
            if last_visit is None or last_visit < one_year_ago:
                result.update(status="needs_visit", reason="The patient has not been seen within the last 12 months.")
            else:
                run_out_date = date.fromisoformat(medication["last_filled"]) + timedelta(days=int(medication["days_supply"]))
                earliest_date = run_out_date - timedelta(days=7)
                result.update(
                    run_out_date=run_out_date.isoformat(),
                    earliest_date=earliest_date.isoformat(),
                )
                if self.today < earliest_date:
                    result.update(status="too_early", reason="The request is more than 7 days before the current supply runs out.")
                else:
                    result.update(status="eligible", eligible=True, reason="All clinic refill preparation criteria are met.")

        self.record("check_refill_eligibility", patient_id=patient_id, medication=medication["medication"], status=result["status"])
        return result

    def get_clinic_info(self) -> dict[str, Any]:
        self.record("get_clinic_info")
        return {
            "clinic_name": self.clinic["clinic_name"],
            "today": self.clinic["today"],
            "timezone": self.clinic["timezone"],
            "hours": deepcopy(self.clinic["hours"]),
            "holidays": list(self.clinic["holidays"]),
            "visit_types": deepcopy(self.clinic["visit_types"]),
            "providers": deepcopy(self.clinic["providers"]),
            "refill_policy": deepcopy(self.clinic["refill_policy"]),
        }

    def find_appointments(
        self, patient_id: str, from_date: str | None = None, to_date: str | None = None,
        status: str = "booked", verification_token: str | None = None
    ) -> list[dict[str, Any]]:
        self._require_verified(patient_id, verification_token)
        start = _iso(from_date) if from_date else self.today
        end = _iso(to_date) if to_date else date.max
        found = [
            deepcopy(a) for a in self.appointments
            if a["patient_id"] == patient_id
            and a.get("status") == status
            and start <= date.fromisoformat(a["date"]) <= end
        ]
        found.sort(key=lambda a: (a["date"], a["start"]))
        self.record("find_appointments", patient_id=patient_id, count=len(found))
        return found

    def _provider_valid_on(self, provider_id: str, slot_date: date) -> bool:
        provider = next((p for p in self.clinic["providers"] if p["id"] == provider_id), None)
        if not provider or slot_date.isoformat() in self.clinic["holidays"]:
            return False
        if slot_date.weekday() >= 5:
            return False
        for start, end in provider.get("leave", []):
            if _iso(start) <= slot_date <= _iso(end):
                return False
        return True

    def _valid_slot_time(self, start_min: int, duration: int) -> bool:
        opening = _time_minutes(self.clinic["hours"]["open"])
        closing = _time_minutes(self.clinic["hours"]["close"])
        lunch_start, lunch_end = map(_time_minutes, self.clinic["hours"]["lunch"])
        if start_min < opening or start_min + duration > closing:
            return False
        if start_min % 20 != 0:
            return False
        return not (start_min < lunch_end and start_min + duration > lunch_start)

    def find_free_slots(
        self,
        start_date: str,
        end_date: str,
        visit_type: str,
        provider: str | None = None,
        daypart: str | None = None,
        not_before: str | None = None,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        """Find policy-valid, non-overlapping slots (not a guarantee until booking)."""
        first, last = _iso(start_date), _iso(end_date)
        duration = int(self.clinic["visit_types"].get(visit_type, 20))
        providers = [p["id"] for p in self.clinic["providers"]]
        if provider:
            token = _norm(provider)
            match = next((p for p in self.clinic["providers"] if token in {_norm(p["id"]), _norm(p["name"])} or token in _norm(p["name"])), None)
            providers = [match["id"]] if match else []
        lower_time = _time_minutes(not_before) if not_before else _time_minutes(self.clinic["hours"]["open"])
        found: list[dict[str, Any]] = []
        current = max(first, self.today)
        while current <= last:
            day_providers = [p for p in providers if self._provider_valid_on(p, current)]
            for provider_id in day_providers:
                booked = [
                    a for a in self.appointments
                    if a["provider"] == provider_id and a["date"] == current.isoformat() and a.get("status") == "booked"
                ]
                for start_min in range(_time_minutes(self.clinic["hours"]["open"]), _time_minutes(self.clinic["hours"]["close"]), 20):
                    if current == self.today and start_min < lower_time:
                        continue
                    if daypart == "morning" and start_min >= 12 * 60:
                        continue
                    if daypart == "afternoon" and start_min < 13 * 60:
                        continue
                    if not self._valid_slot_time(start_min, duration):
                        continue
                    end_min = start_min + duration
                    conflict = any(
                        _time_minutes(a["start"]) < end_min and _time_minutes(a["end"]) > start_min
                        for a in booked
                    )
                    if not conflict:
                        found.append({
                            "provider": provider_id,
                            "date": current.isoformat(),
                            "start": _minutes_time(start_min),
                            "end": _minutes_time(end_min),
                            "visit_type": visit_type,
                            "duration_minutes": duration,
                        })
                        if len(found) >= limit:
                            self.record("find_free_slots", count=len(found), provider=provider, start_date=start_date, end_date=end_date)
                            return found
            current += timedelta(days=1)
        self.record("find_free_slots", count=len(found), provider=provider, start_date=start_date, end_date=end_date)
        return found

    def book_appointment(self, patient_id: str, provider: str, slot_date: str, start: str, visit_type: str, verification_token: str | None = None) -> dict[str, Any]:
        self._require_verified(patient_id, verification_token)
        duration = int(self.clinic["visit_types"].get(visit_type, 20))
        d = _iso(slot_date)
        provider_id = self._resolve_provider(provider)
        if not provider_id or not self._provider_valid_on(provider_id, d):
            result = {"booked": False, "reason": "Provider is unavailable on that date."}
            self.record("book_appointment_failed", patient_id=patient_id, reason=result["reason"])
            return result
        start_min = _time_minutes(start)
        end_min = start_min + duration
        if not self._valid_slot_time(start_min, duration):
            result = {"booked": False, "reason": "The requested time violates clinic hours, lunch, or start-time rules."}
            self.record("book_appointment_failed", patient_id=patient_id, reason=result["reason"])
            return result
        for a in self.appointments:
            if a.get("status") == "booked" and a["provider"] == provider_id and a["date"] == slot_date:
                if _time_minutes(a["start"]) < end_min and _time_minutes(a["end"]) > start_min:
                    result = {"booked": False, "reason": "The slot is no longer free."}
                    self.record("book_appointment_failed", patient_id=patient_id, reason=result["reason"])
                    return result
        next_num = max([int(a["id"][1:]) for a in self.appointments] + [0]) + 1
        appt = {
            "id": f"A{next_num:03d}", "patient_id": patient_id, "provider": provider_id,
            "date": slot_date, "start": start, "end": _minutes_time(end_min),
            "visit_type": visit_type, "status": "booked",
        }
        self.appointments.append(appt)
        self.record("book_appointment", appointment_id=appt["id"], patient_id=patient_id, provider=provider_id, date=slot_date, start=start, end=appt["end"], visit_type=visit_type)
        return {"booked": True, "appointment": deepcopy(appt)}

    def reschedule_appointment(
        self, patient_id: str, new_date: str, new_start: str, appointment_id: str | None = None,
        verification_token: str | None = None
    ) -> dict[str, Any]:
        self._require_verified(patient_id, verification_token)
        appointment = next((a for a in self.appointments if a["id"] == appointment_id and a["patient_id"] == patient_id and a.get("status") == "booked"), None) if appointment_id else None
        if appointment is None:
            candidates = [a for a in self.appointments if a["patient_id"] == patient_id and a.get("status") == "booked" and date.fromisoformat(a["date"]) >= self.today]
            candidates.sort(key=lambda a: (a["date"], a["start"]))
            appointment = candidates[0] if candidates else None
        if appointment is None:
            result = {"rescheduled": False, "reason": "No upcoming appointment was found."}
            self.record("reschedule_appointment_failed", patient_id=patient_id, reason=result["reason"])
            return result
        old = deepcopy(appointment)
        duration = _time_minutes(old["end"]) - _time_minutes(old["start"])
        if not self._provider_valid_on(old["provider"], _iso(new_date)):
            result = {"rescheduled": False, "reason": "The provider is unavailable on the requested date."}
            self.record("reschedule_appointment_failed", patient_id=patient_id, reason=result["reason"])
            return result
        start_min = _time_minutes(new_start)
        if not self._valid_slot_time(start_min, duration):
            result = {"rescheduled": False, "reason": "The requested time violates clinic rules."}
            self.record("reschedule_appointment_failed", patient_id=patient_id, reason=result["reason"])
            return result
        conflict = any(
            a is not appointment and a.get("status") == "booked" and a["provider"] == old["provider"] and a["date"] == new_date
            and _time_minutes(a["start"]) < start_min + duration and _time_minutes(a["end"]) > start_min
            for a in self.appointments
        )
        if conflict:
            result = {"rescheduled": False, "reason": "The requested slot is occupied."}
            self.record("reschedule_appointment_failed", patient_id=patient_id, reason=result["reason"])
            return result
        appointment["date"] = new_date
        appointment["start"] = new_start
        appointment["end"] = _minutes_time(start_min + duration)
        self.record("reschedule_appointment", appointment_id=old["id"], old_date=old["date"], new_date=new_date, new_start=new_start)
        return {"rescheduled": True, "appointment": deepcopy(appointment), "old_appointment": old}

    def cancel_appointment(self, patient_id: str, appointment_id: str | None = None, target_date: str | None = None, verification_token: str | None = None) -> dict[str, Any]:
        self._require_verified(patient_id, verification_token)
        appointment = next((a for a in self.appointments if appointment_id and a["id"] == appointment_id and a["patient_id"] == patient_id and a.get("status") == "booked"), None)
        if appointment is None:
            target = _iso(target_date) if target_date else None
            candidates = [
                a for a in self.appointments if a["patient_id"] == patient_id and a.get("status") == "booked"
                and date.fromisoformat(a["date"]) >= self.today
                and (target is None or date.fromisoformat(a["date"]) == target)
            ]
            candidates.sort(key=lambda a: (a["date"], a["start"]))
            appointment = candidates[0] if candidates else None
        if appointment is None:
            result = {"cancelled": False, "reason": "No matching upcoming appointment was found."}
        else:
            appointment["status"] = "cancelled"
            result = {"cancelled": True, "appointment": deepcopy(appointment)}
        self.record("cancel_appointment", patient_id=patient_id, cancelled=result["cancelled"], appointment_id=appointment.get("id") if appointment else None)
        return result

    def route_message(self, message_id: str, queues: list[str], reason: str = "") -> dict[str, Any]:
        allowed = set(self.clinic["routing_queues"])
        valid = list(dict.fromkeys(q for q in queues if q in allowed))
        entry = {"message_id": message_id, "queues": valid, "reason": reason}
        self.record("route_message", **entry)
        return {"routed_to": valid}

    def draft_reply(self, message_id: str, reply: str) -> dict[str, str]:
        self.record("draft_reply", message_id=message_id)
        return {"reply_draft": reply}

    def _resolve_provider(self, provider: str) -> str | None:
        token = _norm(provider)
        found = next((p for p in self.clinic["providers"] if token == _norm(p["id"]) or token in _norm(p["name"])), None)
        return found["id"] if found else None
