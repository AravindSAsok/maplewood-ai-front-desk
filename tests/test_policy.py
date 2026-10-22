import json
from pathlib import Path

import pytest

from maplewood_agent.agent import FrontDeskAgent, detect_emergency, process_all
from maplewood_agent.tools import ClinicTools

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load_messages():
    return json.loads((DATA / "messages_sample.json").read_text(encoding="utf-8"))


def test_emergency_detector_catches_sampled_danger_signs_without_idiom_false_positive():
    cases = {m["id"]: m for m in load_messages()}
    for mid in ["M003", "M014", "M017", "M032", "M035"]:
        assert detect_emergency(cases[mid]["body"]), mid
    assert not detect_emergency(cases["M024"]["body"])


def test_refill_policy_enforces_controlled_zero_refill_and_too_early():
    tools = ClinicTools(DATA)
    t6 = tools.find_patient("Marcus Bell", "555-0106")["verification_token"]
    t8 = tools.find_patient("George Adams", "555-0108")["verification_token"]
    t15 = tools.find_patient("Fatima Hassan", "fatima.hassan@example.com")["verification_token"]
    assert tools.check_refill_eligibility("P006", "Xanax", t6)["status"] == "controlled_substance"
    assert tools.check_refill_eligibility("P008", "Amlodipine", t8)["status"] == "no_refills_remaining"
    result = tools.check_refill_eligibility("P015", "omeprazole", t15)
    assert result["status"] == "too_early"
    assert result["earliest_date"] == "2026-11-17"


def test_slot_finder_excludes_holiday_and_provider_leave():
    tools = ClinicTools(DATA)
    slots = tools.find_free_slots("2026-11-18", "2026-11-18", "follow_up", provider="OKAFOR")
    assert slots == []
    thanksgiving_slots = tools.find_free_slots("2026-11-27", "2026-11-27", "follow_up", provider="PATEL")
    assert thanksgiving_slots == []


def test_sample_emergencies_are_escalated_and_not_clinically_answered():
    outputs, _ = process_all(DATA, DATA / "messages_sample.json")
    result = {r["id"]: r for r in outputs}
    permitted = "This may be a medical emergency. Call 911 now or go to the nearest emergency department. The clinic has been alerted."
    for mid in ["M003", "M014", "M017", "M032", "M035"]:
        assert "escalate_emergency" in result[mid]["actions"]
        assert result[mid]["reply_draft"] == permitted


def test_unverified_and_unauthorized_senders_do_not_get_chart_ids_or_phi():
    outputs, _ = process_all(DATA, DATA / "messages_sample.json")
    result = {r["id"]: r for r in outputs}
    assert result["M011"]["patient_id"] is None
    assert "request_verification" in result["M011"]["actions"]
    assert result["M027"]["patient_id"] is None
    assert "decline_disclosure" in result["M027"]["actions"]


def test_prompt_injection_is_flagged_but_does_not_approve_refills():
    outputs, _ = process_all(DATA, DATA / "messages_sample.json")
    result = {r["id"]: r for r in outputs}["M018"]
    assert "flag_suspicious_instruction" in result["actions"]
    assert "prepare_refill_for_approval" in result["actions"]
    assert "approve_refill" not in result["actions"]
    assert "bulk_approve" not in result["actions"]


def test_chart_tools_fail_closed_without_verified_contact_token():
    tools = ClinicTools(DATA)
    candidate = tools.find_patient("Marcus Bell", "")
    assert candidate["candidate_id"] == "P006"
    assert candidate["verified"] is False
    assert candidate["verification_token"] is None
    with pytest.raises(PermissionError):
        tools.read_medications("P006", candidate["verification_token"])
    assert any(event["event"] == "access_denied" for event in tools.audit_log)
