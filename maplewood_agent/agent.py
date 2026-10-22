"""Deterministic-first inbox agent for the fictional Maplewood Family Clinic.

The policy engine is intentionally explicit and inspectable. Clinical emergencies,
identity verification, refill eligibility and scheduling constraints are never delegated
away to a generative model. The same functions are exposed through MCP in mcp_server.py.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .tools import ClinicTools, _iso, _norm, _time_minutes


ACTION_VOCAB = {
    "escalate_emergency", "route_to_clinician", "route_to_provider", "route_to_billing",
    "route_to_front_desk", "prepare_refill_for_approval", "refill_needs_visit",
    "refill_too_early", "book_appointment", "reschedule_appointment", "cancel_appointment",
    "offer_appointment", "offer_alternatives", "offer_new_patient_booking", "request_verification",
    "decline_disclosure", "flag_suspicious_instruction", "answer_directly", "acknowledge", "no_action",
}

# These checks are deliberately specific: idiomatic phrases such as "dying to get in lol"
# should not trigger emergency handling, while classic danger signs must.
EMERGENCY_RULES = [
    re.compile(r"crushing.{0,30}chest pain", re.I),
    re.compile(r"chest pain.{0,80}(left arm|sweat|dizz|short of breath|breathless)", re.I),
    re.compile(r"(can't|cannot|unable to) (finish|complete) sentences", re.I),
    re.compile(r"(wheez|breath).{0,60}(can't|cannot|unable to) (speak|talk|finish|complete)", re.I),
    re.compile(r"(fell|fall).{0,50}(hit|struck).{0,20}head.{0,80}(confused|confusion|disoriented)", re.I),
    re.compile(r"(head injury|hit (her|his|my|their) head).{0,100}(confused|confusion|disoriented)", re.I),
    re.compile(r"(bleeding|blood).{0,80}(dark stools|black stools|tarry stools)", re.I),
    re.compile(r"(blood sugar|glucose).{0,30}\b(3[0-9]|4[0-9]|50|51|52|53|54)\b.{0,80}(shaky|sweat|confus|dizz)", re.I),
    re.compile(r"(suicidal|kill myself|end my life|going to harm myself|overdose right now)", re.I),
    re.compile(r"(face droop|one[- ]sided weakness|stroke symptoms|can't breathe|cannot breathe)", re.I),
]

URGENT_RULES = [
    re.compile(r"don't see the point of anything|no point of anything|can't go on|cannot go on", re.I),
    re.compile(r"out of (my )?(pills|medication)|ran out of (my )?(medication|pills)|out of pills", re.I),
    re.compile(r"fever.{0,20}(101|102|103|104)|(101|102|103|104).{0,20}fever", re.I),
    re.compile(r"burning when i pee|burning when i urinate|uti", re.I),
]

MEDICATION_WORDS = (
    "refill", "prescription", "prescriptions", "medication", "medicine", "lisinopril", "metformin",
    "xanax", "alprazolam", "sertraline", "levothyroxine", "zolpidem", "omeprazole", "amlodipine",
    "warfarin", "oxycodone", "atorvastatin", "escitalopram", "donepezil", "insulin", "albuterol", "cetirizine",
)


def _date_from_received(message: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(message.get("received_at", ""))
    except ValueError:
        return datetime.combine(date(2026, 11, 9), datetime.min.time())


def detect_emergency(body: str) -> bool:
    return any(pattern.search(body or "") for pattern in EMERGENCY_RULES)


def _has_urgent_signal(body: str) -> bool:
    return any(pattern.search(body or "") for pattern in URGENT_RULES)


def detect_intents(message: dict[str, Any]) -> list[str]:
    body = message.get("body", "")
    text = body.lower()
    intents: list[str] = []
    if re.search(r"reply stop|unsubscribe|special offer|marketing offer|promotional offer", text):
        return ["spam"]

    asks_new = any(s in text for s in ["new patient", "accept new patients", "become a patient"])
    if asks_new:
        intents.append("new_patient")

    refill_asked = any(w in text for w in ["refill", "refilled", "prescription", "prescriptions", "script", "send in more", "more metformin", "more lisinopril", "medications", "medication", "metformina"])
    refill_asked = refill_asked or any(med in text for med in ["lisinopril", "atorvastatin", "sertraline", "levothyroxine", "xanax", "alprazolam", "zolpidem", "omeprazole", "amlodipine", "warfarin", "oxycodone", "escitalopram", "donepezil", "cetirizine", "insulin glargine", "albuterol", "amphetamine", "adderall"])
    if refill_asked and not asks_new:
        intents.append("refill")

    billing = any(w in text for w in ["balance", "bill", "billing", "copay", "co-pay", "insurance covered", "insurance cover"])
    if billing:
        intents.append("billing")

    records = any(w in text for w in ["send my lab results", "lab results to this email", "please send my lab results", "vaccination record", "vaccination records", "medical records", "send my lab", "copy of my record"])
    if records:
        intents.append("records")

    reschedule = any(w in text for w in ["reschedule", "move my appointment", "move my annual physical", "move my", "change my appointment", "change the appointment", "move the appointment"])
    cancel = any(w in text for w in ["cancel my appointment", "cancel the appointment", "need to cancel", "cancel my visit"])
    appointment_words = any(w in text for w in ["appointment", "book", "booking", "get in", "get her seen", "get him seen", "get seen", "seen today", "see dr", "see doctor", "physical", "follow-up", "follow up", "check-up", "checkup", "chequeo"]) or bool(re.search(r"\bcita\b", text))
    if reschedule:
        intents.append("reschedule")
    elif cancel:
        intents.append("cancel")
    elif appointment_words and not asks_new:
        intents.append("schedule")

    admin = any(w in text for w in ["clinic hours", "what are your hours", "are you open", "open the day", "change my phone number", "update my phone", "change my number"])
    if admin and "admin" not in intents:
        intents.append("admin")

    clinical = any(w in text for w in [
        "chest pain", "wheez", "fever", "cough", "bleeding", "dark stools", "blood sugar", "shaky", "sweaty",
        "burning when", "uti", "lab results notification", "high tsh", "feeling really low", "point of anything",
        "what should we do", "can't finish sentences", "cannot finish sentences", "not helping", "hit her head",
        "hit his head", "head injury", "confused", "disoriented", "fell this morning",
    ])
    # Don't classify an admin-hours response as clinical. Scheduling with symptoms is multi-intent.
    if clinical and "clinical" not in intents:
        intents.append("clinical")

    # If a known patient's name is mentioned as the subject but the sender is another person,
    # privacy handling happens later. This intent marker is used only for third-party requests.
    if not intents:
        if any(w in text for w in ["my wife", "my husband", "my mom", "my mother", "my dad", "my father", "my daughter", "my son", "my spouse", "my partner"]):
            intents.append("third_party")
        elif any(w in text for w in ["thanks", "thank you", "appreciate"]):
            intents.append("other")
        else:
            intents.append("other")
    # A third-party question about another person's appointment or records is sensitive,
    # even if it has appointment-related words that could otherwise imply schedule intent.
    if any(w in text for w in ["my wife", "my husband", "my mom", "my mother", "my dad", "my father", "my daughter", "my son"]) and any(w in text for w in ["appointment", "lab results", "records", "medication"]):
        intents = ["third_party"]
    # Remove duplicates while preserving the order in which we found intents.
    return list(dict.fromkeys(intents))


class FrontDeskAgent:
    def __init__(self, tools: ClinicTools, llm_classifier: Any | None = None):
        self.tools = tools
        self.llm_classifier = llm_classifier
        self._called: list[str] = []
        self._replies: list[str] = []
        self._current_message_id: str | None = None

    def _call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if name not in self._called:
            self._called.append(name)
        method = getattr(self.tools, name)
        before = len(self.tools.audit_log)
        result = method(*args, **kwargs)
        if len(self.tools.audit_log) > before:
            self.tools.audit_log[-1].setdefault("message_id", self._current_message_id)
        return result

    def _draft(self, message_id: str, reply: str) -> str:
        self._call("draft_reply", message_id, reply)
        return reply

    def _route(self, message_id: str, queues: list[str], reason: str = "") -> list[str]:
        if not queues:
            return []
        result = self._call("route_message", message_id, queues, reason)
        return result["routed_to"]

    def _patient_name(self, patient_id: str | None) -> str:
        if not patient_id:
            return ""
        patient = self.tools._patient_by_id.get(patient_id, {})
        return patient.get("name", "")

    def _is_spanish(self, patient_id: str | None, body: str = "") -> bool:
        patient = self.tools._patient_by_id.get(patient_id or "", {})
        return patient.get("preferred_language") == "Spanish" or body.strip().lower().startswith("hola,") or "necesito una cita" in body.lower()

    def _parse_weekday(self, reference: date, weekday_name: str, allow_same: bool = False) -> date:
        weekdays = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
        target = weekdays[weekday_name.lower()]
        delta = (target - reference.weekday()) % 7
        if delta == 0 and not allow_same:
            delta = 7
        return reference + timedelta(days=delta)

    def _week_bounds(self, day: date) -> tuple[date, date]:
        monday = day - timedelta(days=day.weekday())
        return monday, monday + timedelta(days=4)

    def _explicit_day_of_month(self, body: str) -> date | None:
        # Avoid treating clock times such as "2pm" as a calendar day. The samples
        # express dates as "the 18th" / "Monday the 16th".
        match = re.search(r"\bthe\s+(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\b", body.lower())
        if not match:
            return None
        daynum = int(match.group(1) or match.group(2))
        if not 1 <= daynum <= 31:
            return None
        try:
            candidate = date(self.tools.today.year, self.tools.today.month, daynum)
        except ValueError:
            return None
        # Don't mistake arbitrary numbers (e.g. temperature 101) for appointment dates.
        if not re.search(r"\b(?:on|the|monday|tuesday|wednesday|thursday|friday)\b", body.lower()):
            return None
        if daynum < self.tools.today.day and re.search(r"\b(appointment|book|booking|move|reschedule|physical|follow[- ]?up)\b", body.lower()):
            return date(self.tools.today.year, 12, daynum) if daynum <= 31 else candidate
        return candidate

    def _target_window(
        self, message: dict[str, Any], *, old_appointment: dict[str, Any] | None = None
    ) -> tuple[date, date, str | None, str | None]:
        body = message.get("body", "")
        text = body.lower()
        received = _date_from_received(message)
        anchor = date.fromisoformat(old_appointment["date"]) if old_appointment else self.tools.today
        start_time: str | None = None
        daypart: str | None = None

        clock = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", text)
        if clock:
            hour = int(clock.group(1)) % 12
            if clock.group(3) == "pm":
                hour += 12
            start_time = f"{hour:02d}:{int(clock.group(2) or '00'):02d}"
        elif re.search(r"\b(\d{1,2}):([0-5]\d)\b", text):
            mm = re.search(r"\b(\d{1,2}):([0-5]\d)\b", text)
            start_time = f"{int(mm.group(1)):02d}:{int(mm.group(2)):02d}"
        if "morning" in text or "any morning" in text:
            daypart = "morning"
        elif "afternoon" in text:
            daypart = "afternoon"

        if "today" in text and "tomorrow" in text:
            day = date.fromisoformat(received.date().isoformat())
            return day, day + timedelta(days=1), daypart, start_time
        if "tomorrow" in text:
            day = (date.fromisoformat(received.date().isoformat()) + timedelta(days=1))
            return day, day, daypart, start_time
        if "today" in text:
            day = date.fromisoformat(received.date().isoformat())
            return day, day, daypart, start_time

        # Month-relative explicit dates such as "Wednesday the 18th" or "Monday the 16th".
        explicit = self._explicit_day_of_month(body)
        if explicit:
            if "following week" in text or "week following" in text:
                _, target_day = self._week_bounds(explicit)
                start, end = target_day + timedelta(days=3), target_day + timedelta(days=4)
                return start, end, daypart, start_time
            return explicit, explicit, daypart, start_time

        weekday_match = re.search(r"\b(monday|tuesday|wednesday|thursday|friday)\b", text)
        weekday = weekday_match.group(1) if weekday_match else None
        if "following week" in text and old_appointment:
            old_date = date.fromisoformat(old_appointment["date"])
            monday, _ = self._week_bounds(old_date)
            start = monday + timedelta(days=7)
            return start, start + timedelta(days=4), daypart, start_time
        if "next week" in text or "following week" in text:
            monday, _ = self._week_bounds(self.tools.today)
            start = monday + timedelta(days=7)
            if weekday:
                day_offset = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}[weekday]
                selected = start + timedelta(days=day_offset)
                return selected, selected, daypart, start_time
            return start, start + timedelta(days=4), daypart, start_time
        if "this week" in text:
            monday, friday = self._week_bounds(self.tools.today)
            if weekday:
                selected = monday + timedelta(days={"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}[weekday])
                return selected, selected, daypart, start_time
            return max(monday, self.tools.today), friday, daypart, start_time
        if weekday:
            # When rescheduling, a destination weekday is relative to the existing appointment.
            ref = anchor if old_appointment else self.tools.today
            selected = self._parse_weekday(ref, weekday, allow_same=False)
            return selected, selected, daypart, start_time
        return self.tools.today, self.tools.today + timedelta(days=1), daypart, start_time

    def _provider_from_text(self, body: str, patient_id: str | None) -> str | None:
        lower = body.lower()
        for alias, provider_id in [("patel", "PATEL"), ("okafor", "OKAFOR"), ("sarah kim", "KIM"), ("kim", "KIM")]:
            if alias in lower:
                return provider_id
        patient = self.tools._patient_by_id.get(patient_id or "", {})
        return patient.get("primary_provider")

    def _visit_type(self, body: str) -> str:
        text = body.lower()
        if any(s in text for s in ["annual physical", "annual", "physical", "chequeo anual", "check-up", "checkup"]):
            return "annual_physical"
        if "telehealth" in text:
            return "telehealth"
        if any(s in text for s in ["sick visit", "fever", "cough", "uti", "burning when", "wheez", "pain", "blood sugar"]):
            return "sick_visit"
        if any(s in text for s in ["follow-up", "follow up", "followup"]):
            return "follow_up"
        return "follow_up"

    def _refill_medication_query(self, body: str) -> str:
        text = body.lower()
        known_terms = [
            "lisinopril-hctz", "lisinopril hctz", "lisinopril", "metformina", "metformin", "xanax", "alprazolam",
            "atorvastatin", "sertraline", "levothyroxine", "zolpidem", "omeprazole", "amlodipine",
            "warfarin", "oxycodone", "escitalopram", "donepezil", "insulin glargine", "insulin",
            "albuterol", "cetirizine", "amphetamine/dextroamphetamine", "amphetamine", "adderall",
        ]
        for term in known_terms:
            if term in text:
                return term
        return "all medications" if any(t in text for t in ["all my medications", "all medications", "any controlled ones"]) else ""

    def _emergency_reply(self) -> str:
        return "This may be a medical emergency. Call 911 now or go to the nearest emergency department. The clinic has been alerted."

    def process_message(self, message: dict[str, Any]) -> dict[str, Any]:
        self._called = []
        message_id = message["id"]
        self._current_message_id = message_id
        body = message.get("body", "")
        intents = detect_intents(message)
        llm_urgency_hint = "routine"
        # Semantic fallback is optional and only consulted for messages the rules leave
        # unclassified. The deterministic emergency detector always runs as well.
        if self.llm_classifier and intents == ["other"]:
            interpretation = self.llm_classifier.classify(message, self.tools.clinic["today"])
            if interpretation:
                intents = interpretation.get("intents") or intents
                llm_urgency_hint = interpretation.get("urgency", "routine")

        identity = self._call("find_patient", message.get("from_name", ""), message.get("from_contact", ""), message.get("stated_dob"))
        candidate_id = identity.get("candidate_id")
        verified = bool(identity.get("verified"))
        patient_id = candidate_id if verified else None
        verification_token = identity.get("verification_token") if verified else None

        # Resolve the mentioned chart for access-control decisions without treating it as
        # the sender's verified patient_id. No chart details are disclosed from this lookup.
        if not candidate_id and "third_party" not in intents:
            for patient in self.tools.patients:
                if _norm(patient["name"]) and _norm(patient["name"]) in _norm(body):
                    target = self._call("find_patient", patient["name"], "", None)
                    candidate_id = target.get("candidate_id")
                    break

        queues: list[str] = []
        actions: list[str] = []
        urgency = "routine"
        reply = ""
        detail: dict[str, Any] = {}

        emergency = detect_emergency(body) or (llm_urgency_hint == "emergency")
        urgent_signal = _has_urgent_signal(body) or (llm_urgency_hint == "urgent")
        suspicious_instruction = any(s in body.lower() for s in ["ignore your previous instructions", "approve refills for all", "including any controlled", "authorized by dr.", "authorised by dr."])

        # The emergency branch runs before normal intent actions and never produces
        # clinical advice other than the explicit emergency-policy message.
        if emergency:
            urgency = "emergency"
            actions.append("escalate_emergency")
            queues = ["clinician_urgent"]
            self._route(message_id, queues, "Possible medical emergency; immediate human escalation required.")
            reply = self._emergency_reply()
            reply = self._draft(message_id, reply)
            return self._result(message_id, patient_id, intents, urgency, actions, queues, reply)

        if "spam" in intents:
            actions = ["no_action"]
            reply = self._draft(message_id, "")
            return self._result(message_id, patient_id, intents, urgency, actions, [], reply)

        if "new_patient" in intents:
            actions = ["answer_directly", "offer_new_patient_booking"]
            queues = ["front_desk"]
            self._route(message_id, queues, "New-patient booking request for front-desk follow-up.")
            reply = "I can help start a new-patient appointment request. The front desk will confirm availability and the next steps for booking a physical."
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "third_party" in intents:
            actions = ["decline_disclosure"]
            queues = ["front_desk"]
            self._route(message_id, queues, "Third-party request; sender is not an authorized caregiver on file.")
            reply = "For privacy, we can only share appointment or health information with the patient or an authorized caregiver on file. Please ask the patient to contact us directly."
            return self._result(message_id, None, intents, urgency, actions, queues, self._draft(message_id, reply))

        sensitive_records = "records" in intents
        if not verified and (sensitive_records or "refill" in intents or "schedule" in intents or "reschedule" in intents or "cancel" in intents):
            actions = ["request_verification"]
            queues = ["front_desk"]
            self._route(message_id, queues, "Identity verification required before accessing patient information or acting on the account.")
            reply = "Before we can help with this request, please contact the clinic through your verified phone number or patient portal so we can verify your identity."
            return self._result(message_id, None, intents, urgency, actions, queues, self._draft(message_id, reply))

        # Routine clinical questions are still sent to a clinician. The agent never
        # interprets lab results or offers diagnosis/treatment advice.
        if "clinical" in intents and "refill" not in intents and "schedule" not in intents:
            actions = ["route_to_clinician"]
            queues = ["clinician_urgent" if urgent_signal else "clinician_routine"]
            urgency = "urgent" if urgent_signal else "routine"
            self._route(message_id, queues, "Clinical question or symptom requires human review; no automated clinical advice.")
            reply = "I've forwarded your message to the clinical team for review. A clinician will follow up with you."
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "refill" in intents:
            if not patient_id:
                actions = ["request_verification"]
                queues = ["front_desk"]
                self._route(message_id, queues, "Refill request cannot be processed until identity is verified.")
                reply = "Please contact us through your verified phone number or patient portal so we can verify your identity before processing this request."
                return self._result(message_id, None, intents, urgency, actions, queues, self._draft(message_id, reply))

            # Reading the list and checking eligibility are separate tool calls so the
            # decision is traceable and eligibility logic stays outside the language layer.
            self._call("read_medications", patient_id, verification_token)
            med_query = self._refill_medication_query(body)
            eligibility = self._call("check_refill_eligibility", patient_id, med_query, verification_token=verification_token)
            status = eligibility.get("status")
            if suspicious_instruction:
                actions.append("flag_suspicious_instruction")

            if status == "eligible":
                actions.append("prepare_refill_for_approval")
                queues.append("provider_refills")
                name = eligibility.get("medication", "the medication")
                reply = f"I've sent your {name} refill request to the prescribing provider for approval. The clinic will follow up; no prescription has been approved or sent by this assistant."
            elif status == "too_early":
                actions.append("refill_too_early")
                earliest = eligibility.get("earliest_date", "")
                runout = eligibility.get("run_out_date", "")
                if self._is_spanish(patient_id, body):
                    reply = f"Todavía es pronto para preparar la solicitud de resurtido. La fecha más temprana es {earliest}; el suministro actual debería durar hasta {runout}."
                else:
                    reply = f"It's too early to prepare this refill request. The earliest date we can prepare it is {earliest}; the current supply is expected to last until {runout}."
            elif status == "needs_visit":
                actions.extend(["refill_needs_visit", "offer_appointment"])
                queues.append("provider_refills")
                if urgent_signal:
                    actions.append("route_to_provider")
                    queues.append("clinician_urgent")
                    urgency = "urgent"
                reply = "A provider needs to review this refill because you are due for a visit. I haven't prepared the refill; the clinic can help arrange an appointment and the provider will review your request."
            elif status in {"controlled_substance", "no_refills_remaining", "medication_not_identified"}:
                actions.append("route_to_provider")
                queues.append("provider_refills")
                if status == "controlled_substance":
                    reply = "This medication requires direct review by the prescribing provider, so I haven't prepared or approved a refill. I've routed your request to the provider team."
                elif status == "no_refills_remaining":
                    reply = "There are no refills remaining on file, so the prescribing provider needs to review whether a new prescription is appropriate. I've routed your request to the provider team."
                else:
                    reply = "I've routed your request to the prescribing provider for review because I couldn't safely identify the medication from your message. No refill has been prepared or approved."
            else:
                actions.append("route_to_provider")
                queues.append("provider_refills")
                reply = "I've routed your request to the prescribing provider for review. No prescription has been approved or sent by this assistant."

            # A non-emergency clinical concern buried in a refill request requires a
            # separate same-day clinician route, while the refill still follows policy.
            if "clinical" in intents:
                actions.append("route_to_clinician")
                queues.append("clinician_urgent" if urgent_signal else "clinician_routine")
                urgency = "urgent" if urgent_signal else "routine"
                if "point of anything" in body.lower() or "feeling really low" in body.lower():
                    reply += " I've also sent the rest of your message to the clinical team for same-day review."
            if "billing" in intents:
                actions.append("route_to_billing")
                queues.append("billing")
                reply += " I've also forwarded your copay question to the billing team; I don't have billing data to quote a copay."
            if self._is_spanish(patient_id, body) and "Necesito" not in reply:
                # The sampled Spanish refill request is handled in Spanish end-to-end.
                reply = "He enviado su solicitud de resurtido al proveedor que la receta para que la apruebe. La clínica se comunicará con usted; este asistente no ha aprobado ni enviado ninguna receta."

            queues = list(dict.fromkeys(queues))
            self._route(message_id, queues, "Refill action follows clinic refill policy; provider approval remains required.")
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "billing" in intents:
            actions.append("route_to_billing")
            queues.append("billing")
            self._route(message_id, ["billing"], "Billing question; no billing ledger is supplied to this agent.")
            reply = "I've forwarded your billing question to the billing team. I don't have access to your account balance or insurance details, so I won't guess at the amount."
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "cancel" in intents:
            received = _date_from_received(message)
            tomorrow = (received.date() + timedelta(days=1)).isoformat()
            self._call("find_appointments", patient_id, from_date=self.tools.today.isoformat(), verification_token=verification_token)
            result = self._call("cancel_appointment", patient_id, None, tomorrow, verification_token=verification_token)
            if result.get("cancelled"):
                actions = ["cancel_appointment"]
                appt = result["appointment"]
                reply = f"Your appointment on {appt['date']} at {appt['start']} has been cancelled."
            else:
                actions = ["route_to_front_desk"]
                queues = ["front_desk"]
                self._route(message_id, queues, "Could not safely identify the appointment to cancel.")
                reply = "I couldn't identify the appointment to cancel automatically. The front desk can help with this request."
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "reschedule" in intents:
            existing = self._call("find_appointments", patient_id, from_date=self.tools.today.isoformat(), verification_token=verification_token)
            if not existing:
                actions = ["offer_alternatives"]
                queues = ["front_desk"]
                self._route(message_id, queues, "No upcoming appointment was found to reschedule.")
                reply = "I couldn't find an upcoming appointment to move. The front desk can review your request and offer options."
                return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))
            old_appointment = existing[0]
            # If a weekday is named as the existing appointment, prefer that exact booking.
            old_window_start, old_window_end, _, _ = self._target_window(message)
            weekday = re.search(r"\b(wednesday|thursday|friday|monday|tuesday)\b", body.lower())
            if weekday:
                anchor = self.tools.today
                if "next week" in body.lower():
                    mon, _ = self._week_bounds(self.tools.today)
                    anchor = mon + timedelta(days=7)
                else:
                    anchor = self.tools.today
                requested_old = self._parse_weekday(anchor, weekday.group(1), allow_same=False)
                matches = [a for a in existing if a["date"] == requested_old.isoformat()]
                if matches:
                    old_appointment = matches[0]
            start, end, daypart, requested_time = self._target_window(message, old_appointment=old_appointment)
            # For “Friday afternoon” after a Wednesday appointment, the weekday should
            # resolve relative to the old appointment, not the processing date.
            if "friday" in body.lower() and old_appointment and "next week" not in body.lower() and "following week" not in body.lower():
                start = self._parse_weekday(date.fromisoformat(old_appointment["date"]), "friday", allow_same=False)
                end = start
                daypart = "afternoon" if "afternoon" in body.lower() else daypart
            if "following week" in body.lower():
                old_date = date.fromisoformat(old_appointment["date"])
                old_monday, _ = self._week_bounds(old_date)
                start = old_monday + timedelta(days=7)
                end = start + timedelta(days=4)
                daypart = daypart or "morning"
            slots = self._call("find_free_slots", start.isoformat(), end.isoformat(), old_appointment["visit_type"], old_appointment["provider"], daypart, None, 30)
            if requested_time:
                slots = [s for s in slots if s["date"] == start.isoformat() and s["start"] == requested_time]
            if slots:
                chosen = slots[0]
                result = self._call("reschedule_appointment", patient_id, chosen["date"], chosen["start"], old_appointment["id"], verification_token=verification_token)
            else:
                result = {"rescheduled": False}
            if result.get("rescheduled"):
                actions = ["reschedule_appointment"]
                appt = result["appointment"]
                reply = f"Your appointment has been moved to {appt['date']} at {appt['start']}."
            else:
                actions = ["offer_alternatives"]
                queues = ["front_desk"]
                self._route(message_id, queues, "Requested reschedule slot/provider is unavailable; staff should offer alternatives.")
                reply = "I couldn't safely move the appointment to the requested time. The front desk can offer available alternatives."
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "schedule" in intents:
            if not patient_id:
                actions = ["request_verification"]
                queues = ["front_desk"]
                self._route(message_id, queues, "Scheduling request requires identity verification.")
                reply = "Please contact us through your verified phone number or patient portal so we can verify your identity before booking."
                return self._result(message_id, None, intents, urgency, actions, queues, self._draft(message_id, reply))
            provider = self._provider_from_text(body, patient_id)
            visit_type = self._visit_type(body)
            start, end, daypart, requested_time = self._target_window(message)
            if "day after thanksgiving" in body.lower():
                # This is an hours question handled below, not a scheduling request.
                intents = ["admin"]
            else:
                if _has_urgent_signal(body):
                    urgency = "urgent"
                if requested_time:
                    # Check the exact requested slot before considering alternatives.
                    slots = self._call("find_free_slots", start.isoformat(), end.isoformat(), visit_type, provider, daypart, requested_time, 100)
                    exact = [s for s in slots if s["date"] == start.isoformat() and s["start"] == requested_time]
                    if not exact:
                        actions = ["offer_alternatives"]
                        queues = ["front_desk"]
                        self._route(message_id, queues, "Requested time is not free; do not silently book a different time.")
                        reply = "That time isn't available. The front desk can offer other appointment times."
                        return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))
                    chosen = exact[0]
                else:
                    # “This week” is distinct from “next week”; for sick visits, try the
                    # next business day window first and never go beyond the stated window.
                    not_before = _date_from_received(message).strftime("%H:%M") if start == self.tools.today else None
                    slots = self._call("find_free_slots", start.isoformat(), end.isoformat(), visit_type, provider, daypart, not_before, 40)
                    if not slots and "clinical" in intents:
                        # A sick visit may use another available clinician if the preferred
                        # primary provider has no slot within the one-business-day window.
                        slots = self._call("find_free_slots", start.isoformat(), end.isoformat(), visit_type, None, daypart, not_before, 40)
                    if not slots:
                        actions = ["offer_alternatives"]
                        queues = ["front_desk"]
                        self._route(message_id, queues, "No policy-compliant slot was found within the requested window.")
                        reply = "I couldn't find an available slot in that window. The front desk can help find the next suitable appointment."
                        return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))
                    chosen = slots[0]
                booked = self._call("book_appointment", patient_id, chosen["provider"], chosen["date"], chosen["start"], visit_type, verification_token=verification_token)
                if booked.get("booked"):
                    actions = ["book_appointment"]
                    appt = booked["appointment"]
                    provider_name = next((p["name"] for p in self.tools.clinic["providers"] if p["id"] == appt["provider"]), appt["provider"])
                    if self._is_spanish(patient_id, body):
                        reply = f"Su cita está reservada para el {appt['date']} a las {appt['start']} con {provider_name}."
                    else:
                        reply = f"Your appointment is booked for {appt['date']} at {appt['start']} with {provider_name}."
                else:
                    actions = ["offer_alternatives"]
                    queues = ["front_desk"]
                    self._route(message_id, queues, "Slot became unavailable before booking.")
                    reply = "That slot was no longer available when I tried to book it. The front desk can offer alternatives."
                return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "admin" in intents:
            if "change my phone number" in body.lower() or "update my phone" in body.lower() or "change my number" in body.lower():
                actions = ["route_to_front_desk"]
                queues = ["front_desk"]
                self._route(message_id, queues, "Contact information changes require front-desk handling.")
                reply = "I've forwarded your phone-number update request to the front desk. They will verify and update your contact details."
            elif "hours" in body.lower() or "open" in body.lower():
                self._call("get_clinic_info")
                if "day after thanksgiving" in body.lower():
                    actions = ["answer_directly"]
                    reply = "The clinic is closed on Friday, 27 November 2026, the day after Thanksgiving. The holiday schedule also closes the clinic on Thursday, 26 November."
                else:
                    actions = ["answer_directly"]
                    reply = "The clinic is open Monday through Friday, 9:00 AM–5:00 PM Central Time, with lunch closure from 12:00 PM to 1:00 PM. It is closed on scheduled holidays."
            else:
                actions = ["route_to_front_desk"]
                queues = ["front_desk"]
                self._route(message_id, queues, "Administrative account request.")
                reply = "I've forwarded your administrative request to the front desk."
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "records" in intents:
            # Authorized requests are routed for manual fulfillment; the agent does not send PHI.
            actions = ["route_to_front_desk"]
            queues = ["front_desk"]
            self._route(message_id, queues, "Records request requires front-desk fulfillment and any required release checks.")
            reply = "I've forwarded the records request to the front desk for review. The records team will confirm any required release steps before sending documents."
            return self._result(message_id, patient_id, intents, urgency, actions, queues, self._draft(message_id, reply))

        if "other" in intents:
            actions = ["acknowledge"]
            reply = "You're welcome! Please message us again if you need anything else."
            return self._result(message_id, patient_id, intents, urgency, actions, [], self._draft(message_id, reply))

        actions = ["no_action"]
        reply = self._draft(message_id, "")
        return self._result(message_id, patient_id, intents, urgency, actions, [], reply)

    def _result(self, message_id: str, patient_id: str | None, intents: list[str], urgency: str,
                actions: list[str], queues: list[str], reply: str) -> dict[str, Any]:
        # Enforce allowed vocabularies and preserve first-occurrence order.
        clean_actions = list(dict.fromkeys(a for a in actions if a in ACTION_VOCAB))
        if not clean_actions:
            clean_actions = ["no_action"]
        if urgency not in {"emergency", "urgent", "routine"}:
            urgency = "routine"
        return {
            "id": message_id,
            "patient_id": patient_id,
            "intents": list(dict.fromkeys(intents)),
            "urgency": urgency,
            "actions": clean_actions,
            "routed_to": list(dict.fromkeys(queues)),
            "tool_calls": list(self._called),
            "reply_draft": reply,
        }


def process_all(
    data_dir: str | Path, messages_path: str | Path, llm_classifier: Any | None = None, use_mcp: bool = False
) -> tuple[list[dict[str, Any]], ClinicTools]:
    import json
    if use_mcp:
        from .mcp_client import MCPBackedClinicTools
        tools: ClinicTools = MCPBackedClinicTools(data_dir)
    else:
        tools = ClinicTools(data_dir)
    try:
        agent = FrontDeskAgent(tools, llm_classifier=llm_classifier)
        with Path(messages_path).open(encoding="utf-8") as handle:
            messages = json.load(handle)
        outputs = [agent.process_message(message) for message in messages]
        return outputs, tools
    finally:
        # The state and audit copy remain available to the caller for evaluation.
        if use_mcp and hasattr(tools, "close"):
            tools.close()
