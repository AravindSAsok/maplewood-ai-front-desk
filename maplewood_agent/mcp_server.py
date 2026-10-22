"""MCP tool server for the synthetic Maplewood Family Clinic data.

Run with: python -m maplewood_agent.mcp_server
The package `mcp` is installed from requirements.txt.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .tools import ClinicTools

DATA_DIR = Path(os.getenv("MAPLEWOOD_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
clinic = ClinicTools(DATA_DIR)
mcp = FastMCP("maplewood-clinic-tools", json_response=True)


@mcp.tool()
def find_patient(name: str = "", contact: str = "", stated_dob: str | None = None) -> dict[str, Any]:
    """Find a likely patient chart and report whether sender verification passed.

    Verification means on-file patient/caregiver contact or exact patient name + DOB.
    Do not disclose or act on chart data when verified is false.
    """
    return clinic.find_patient(name, contact, stated_dob)


@mcp.tool()
def read_medications(patient_id: str, verification_token: str) -> list[dict[str, Any]]:
    """Read medication records using the token returned by a successful find_patient call."""
    return clinic.read_medications(patient_id, verification_token)


@mcp.tool()
def check_refill_eligibility(patient_id: str, medication_query: str, verification_token: str) -> dict[str, Any]:
    """Check refill policy using the token returned by a successful find_patient call."""
    return clinic.check_refill_eligibility(patient_id, medication_query, verification_token)


@mcp.tool()
def get_clinic_info() -> dict[str, Any]:
    """Read clinic hours, holidays, providers, visit durations and refill policy."""
    return clinic.get_clinic_info()


@mcp.tool()
def find_appointments(patient_id: str, verification_token: str, from_date: str | None = None, to_date: str | None = None, status: str = "booked") -> list[dict[str, Any]]:
    """Find a verified patient's appointments by date range and status."""
    return clinic.find_appointments(patient_id, from_date, to_date, status, verification_token)


@mcp.tool()
def find_free_slots(start_date: str, end_date: str, visit_type: str, provider: str | None = None, daypart: str | None = None, not_before: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    """Find free, policy-valid appointment slots; booking must still re-check availability."""
    return clinic.find_free_slots(start_date, end_date, visit_type, provider, daypart, not_before, limit)


@mcp.tool()
def book_appointment(patient_id: str, provider: str, slot_date: str, start: str, visit_type: str, verification_token: str) -> dict[str, Any]:
    """Book a visit using a verification token; slot rules and conflicts are rechecked."""
    return clinic.book_appointment(patient_id, provider, slot_date, start, visit_type, verification_token)


@mcp.tool()
def reschedule_appointment(patient_id: str, new_date: str, new_start: str, verification_token: str, appointment_id: str | None = None) -> dict[str, Any]:
    """Reschedule an appointment using a verification token and recheck all conflicts."""
    return clinic.reschedule_appointment(patient_id, new_date, new_start, appointment_id, verification_token)


@mcp.tool()
def cancel_appointment(patient_id: str, verification_token: str, appointment_id: str | None = None, target_date: str | None = None) -> dict[str, Any]:
    """Cancel a matching upcoming appointment using a verification token."""
    return clinic.cancel_appointment(patient_id, appointment_id, target_date, verification_token)


@mcp.tool()
def route_message(message_id: str, queues: list[str], reason: str = "") -> dict[str, Any]:
    """Route an inbox message to clinician_urgent, clinician_routine, provider_refills, front_desk or billing."""
    return clinic.route_message(message_id, queues, reason)


@mcp.tool()
def draft_reply(message_id: str, reply: str) -> dict[str, str]:
    """Store/return a patient-facing reply draft; this tool does not send messages."""
    return clinic.draft_reply(message_id, reply)


if __name__ == "__main__":
    mcp.run(transport="stdio")
