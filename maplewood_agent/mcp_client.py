"""Small stdio MCP client/proxy for using the MCP server as the agent's tool boundary.

The agent can run in-process for the fully deterministic evaluator, or use this proxy
with `--mcp-tools`. This client uses the MCP JSON-RPC stdio transport directly, so there
is no hidden dependency on a particular MCP client wrapper API.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .tools import ClinicTools


class MCPStdioClient:
    def __init__(self, data_dir: str | Path):
        self.project_root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env["MAPLEWOOD_DATA_DIR"] = str(Path(data_dir).resolve())
        self.process = subprocess.Popen(
            [sys.executable, "-m", "maplewood_agent.mcp_server"],
            cwd=self.project_root,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._request_id = 0
        try:
            init = self._request("initialize", {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "maplewood-front-desk-agent", "version": "0.1.0"},
            })
            if "protocolVersion" not in init:
                raise RuntimeError(f"MCP initialization response was unexpected: {init}")
            self._notify("notifications/initialized", {})
        except Exception:
            self.close()
            raise

    def _read_message(self) -> dict[str, Any]:
        if not self.process.stdout:
            raise RuntimeError("MCP server stdout is not available")
        while True:
            line = self.process.stdout.readline()
            if not line:
                code = self.process.poll()
                raise RuntimeError(f"MCP server closed its output stream (exit={code}).")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON-RPC line from MCP server: {line[:200]!r}") from exc
            if isinstance(message, dict):
                return message

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.process.stdin:
            raise RuntimeError("MCP server stdin is not available")
        self._request_id += 1
        request_id = self._request_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        while True:
            response = self._read_message()
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"MCP {method} failed: {response['error']}")
            return response.get("result", {})

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        if not self.process.stdin:
            raise RuntimeError("MCP server stdin is not available")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    
    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"MCP tool {name} returned an error: {result}")
        if "structuredContent" in result:
            structured = result["structuredContent"]
            if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
                return structured["result"]
            return structured
        content = result.get("content", [])
        for item in content:
            if item.get("type") == "text":
                try:
                    return json.loads(item.get("text", ""))
                except json.JSONDecodeError:
                    return item.get("text", "")
        return None


    def close(self) -> None:
        try:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
        except Exception:
            pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)


class MCPBackedClinicTools(ClinicTools):
    """Proxy policy-tool calls to the MCP server while mirroring in-memory state for evaluation."""

    def __init__(self, data_dir: str | Path):
        super().__init__(data_dir)
        self.mcp_client = MCPStdioClient(data_dir)

    def close(self) -> None:
        self.mcp_client.close()

    def find_patient(self, name: str = "", contact: str = "", stated_dob: str | None = None) -> dict[str, Any]:
        remote = self.mcp_client.call_tool("find_patient", {"name": name, "contact": contact, "stated_dob": stated_dob})
        # Mirror the audit event, then carry the server-issued token into the local audit copy.
        super().find_patient(name, contact, stated_dob)
        token = remote.get("verification_token") if isinstance(remote, dict) else None
        candidate = remote.get("candidate_id") if isinstance(remote, dict) else None
        if token and candidate:
            self._verification_tokens[token] = candidate
        return remote

    def read_medications(self, patient_id: str, verification_token: str | None = None) -> list[dict[str, Any]]:
        remote = self.mcp_client.call_tool("read_medications", {"patient_id": patient_id, "verification_token": verification_token})
        super().read_medications(patient_id, verification_token)
        return remote

    def check_refill_eligibility(self, patient_id: str, medication_query: str = "", verification_token: str | None = None) -> dict[str, Any]:
        remote = self.mcp_client.call_tool("check_refill_eligibility", {"patient_id": patient_id, "medication_query": medication_query, "verification_token": verification_token})
        super().check_refill_eligibility(patient_id, medication_query, verification_token)
        return remote

    def get_clinic_info(self) -> dict[str, Any]:
        remote = self.mcp_client.call_tool("get_clinic_info", {})
        super().get_clinic_info()
        return remote

    def find_appointments(self, patient_id: str, from_date: str | None = None, to_date: str | None = None,
                          status: str = "booked", verification_token: str | None = None) -> list[dict[str, Any]]:
        remote = self.mcp_client.call_tool("find_appointments", {
            "patient_id": patient_id, "verification_token": verification_token,
            "from_date": from_date, "to_date": to_date, "status": status,
        })
        super().find_appointments(patient_id, from_date, to_date, status, verification_token)
        return remote

    def find_free_slots(self, start_date: str, end_date: str, visit_type: str, provider: str | None = None,
                        daypart: str | None = None, not_before: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
        remote = self.mcp_client.call_tool("find_free_slots", {
            "start_date": start_date, "end_date": end_date, "visit_type": visit_type,
            "provider": provider, "daypart": daypart, "not_before": not_before, "limit": limit,
        })
        super().find_free_slots(start_date, end_date, visit_type, provider, daypart, not_before, limit)
        return remote

    def book_appointment(self, patient_id: str, provider: str, slot_date: str, start: str, visit_type: str,
                         verification_token: str | None = None) -> dict[str, Any]:
        remote = self.mcp_client.call_tool("book_appointment", {
            "patient_id": patient_id, "provider": provider, "slot_date": slot_date,
            "start": start, "visit_type": visit_type, "verification_token": verification_token,
        })
        super().book_appointment(patient_id, provider, slot_date, start, visit_type, verification_token)
        return remote

    def reschedule_appointment(self, patient_id: str, new_date: str, new_start: str, appointment_id: str | None = None,
                               verification_token: str | None = None) -> dict[str, Any]:
        remote = self.mcp_client.call_tool("reschedule_appointment", {
            "patient_id": patient_id, "new_date": new_date, "new_start": new_start,
            "verification_token": verification_token, "appointment_id": appointment_id,
        })
        super().reschedule_appointment(patient_id, new_date, new_start, appointment_id, verification_token)
        return remote

    def cancel_appointment(self, patient_id: str, appointment_id: str | None = None, target_date: str | None = None,
                           verification_token: str | None = None) -> dict[str, Any]:
        remote = self.mcp_client.call_tool("cancel_appointment", {
            "patient_id": patient_id, "verification_token": verification_token,
            "appointment_id": appointment_id, "target_date": target_date,
        })
        super().cancel_appointment(patient_id, appointment_id, target_date, verification_token)
        return remote

    def route_message(self, message_id: str, queues: list[str], reason: str = "") -> dict[str, Any]:
        remote = self.mcp_client.call_tool("route_message", {"message_id": message_id, "queues": queues, "reason": reason})
        super().route_message(message_id, queues, reason)
        return remote

    def draft_reply(self, message_id: str, reply: str) -> dict[str, str]:
        remote = self.mcp_client.call_tool("draft_reply", {"message_id": message_id, "reply": reply})
        super().draft_reply(message_id, reply)
        return remote
