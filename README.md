# Maplewood AI Front Desk

A safety-first inbox agent prototype for the **synthetic** Maplewood Family Clinic dataset supplied with the Edvak Forge take-home. It processes one result per message, exposes clinic-system tools over MCP, performs in-memory appointment mutations, and evaluates its outputs against the provided labels.

> **Prototype only.** This repository is not a medical device or a production clinical workflow. The sample metrics are in-sample results on 40 messages and must not be read as evidence of clinical safety or generalization.

## Quick start

Python 3.11+ is recommended.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[test]"
python evaluate.py
```

### macOS / Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[test]'
python evaluate.py
```

`evaluate.py` writes four artifacts to `reports/`:

- `results.json` — one result per message, using the required output keys.
- `evaluation.json` and `evaluation.md` — metrics, per-message comparisons, missed emergencies, and `must_not` checks.
- `audit_log.json` — tool calls and policy decisions with message IDs.

To process a different JSON array of messages:

```bash
python -m maplewood_agent.cli --messages path/to/messages.json --output reports/new_results.json --audit-output reports/new_audit.json
```

The clinic's simulated date is fixed at **2026-11-09** as requested. Booking/cancellation/rescheduling changes live in memory for the length of one run only.

## MCP server

The server uses the official Python MCP SDK's `FastMCP` API. It exposes:

`find_patient`, `read_medications`, `check_refill_eligibility`, `get_clinic_info`, `find_appointments`, `find_free_slots`, `book_appointment`, `reschedule_appointment`, `cancel_appointment`, `route_message`, and `draft_reply`.

Start the stdio server from the project directory:

```bash
python -m maplewood_agent.mcp_server
```

It is an MCP stdio process and waits for an MCP host to connect; it does not print a browser UI. Its default data directory is the repository's `data/` folder. `MAPLEWOOD_DATA_DIR` can override that path.

The sample evaluator defaults to an in-process tool adapter for quick, deterministic runs. To run the same orchestration through the MCP JSON-RPC stdio server instead:

```bash
python evaluate.py --mcp-tools
```

The `--mcp-tools` path starts the MCP server as a subprocess, calls the tools through the MCP protocol, and mirrors the resulting schedule locally for evaluation. Install the MCP dependency first with `pip install -e .`. If you're connecting from an external MCP host, configure its command as `python -m maplewood_agent.mcp_server` and set its working directory to this repository.

**Validation note:** the in-process agent/evaluator and all unit tests were run successfully in the build environment. The MCP SDK could not be downloaded in that environment because outbound package access was unavailable, so the stdio MCP handshake and `--mcp-tools` execution were not live-smoke-tested there. Install the declared dependencies and run `python -m maplewood_agent.mcp_server` / `python evaluate.py --mcp-tools` locally before the walkthrough.

### Verification boundary

A successful `find_patient` call returns a process-local verification token only when the sender's on-file contact matches, an authorized caregiver contact matches, or the exact patient name + DOB matches. Chart-sensitive tools require that token; knowing a patient ID alone is not sufficient. Unverified name matches are not placed in the output's `patient_id` field.

The token is a prototype access-control boundary for a single local process, **not** production authentication. It has no expiry/revocation, is not bound to a strong user session, and must be replaced before any real data is used.

## Architecture and decisions

1. **Deterministic safety and policy layer.** The emergency gate runs before ordinary actions. It recognizes the sampled red flags and returns only the policy-approved emergency reply: call 911 or go to the nearest emergency department, and confirm the clinic has been alerted. Emergency cases are routed to `clinician_urgent`; no automated diagnosis or treatment advice is drafted.
2. **Explicit tool layer.** `ClinicTools` owns patient resolution, verification, medication policy evaluation, slot search, booking mutations, routing and reply drafting. The MCP server exposes those same functions. Tool output is separate from the action policy, so eligibility cannot accidentally approve a prescription.
3. **Intent and workflow orchestration.** The agent classifies multiple intents, keeps refill + clinical or refill + billing requests from dropping one half, and only mutates appointments after verification and a successful slot check.
4. **Evaluation before presentation.** Outputs are compared with `labels_sample.json`. The evaluator reports accepted urgency accuracy, emergency recall, exact action/intent match, action micro metrics, patient-ID label match, provider overlaps and each rule listed in `must_not`.
5. **Optional LLM semantic fallback.** The default path is offline, deterministic and costs $0. If enabled, a small LLM is used only when the rules classify a message as `other`; it returns structured intent/urgency hints, not executable policy decisions. The deterministic emergency detector can still escalate, and an LLM emergency hint can increase escalation but cannot suppress a deterministic escalation.

### Optional model-assisted fallback

The sample evaluation above is the **offline baseline**, not a model-assisted run. To enable the optional OpenAI Responses API classifier:

```bash
pip install -e '.[llm]'
```

PowerShell:

```powershell
$env:OPENAI_API_KEY = "your-api-key"
python -m maplewood_agent.cli --llm --model gpt-6-luna
```

macOS / Linux:

```bash
export OPENAI_API_KEY="your-api-key"
python -m maplewood_agent.cli --llm --model gpt-6-luna
```

The default optional model is `gpt-6-luna` (override via `OPENAI_MODEL` or `--model`). The official model page lists text pricing at $0.10 per million input tokens and $0.50 per million output tokens at the time this README was written: [OpenAI model catalogue](https://developers.openai.com/api/docs/models). A rough estimate for a 40-message run with short messages and only a small number of fallback calls is **well under $0.01**, assuming about 20,000 input and 3,000 output tokens (about $0.0035 at those rates). This is an estimate, not a measured bill. The reported baseline uses no API calls, so its model cost is **$0**.

No real patient data is included. The model-assisted mode still sends the synthetic message content to the configured API provider; it should not be used with real PHI as-is.

## Evaluation result

Command run:

```bash
python evaluate.py
```

Current offline baseline on all 40 supplied messages:

| Metric | Result |
|---|---:|
| Urgency accuracy (accepted labels) | 100.0% |
| Emergency recall (5 labelled emergencies) | 100.0% (5/5) |
| Action exact-match accuracy | 100.0% |
| Action micro precision / recall / F1 | 100.0% / 100.0% / 100.0% |
| Intent exact-match accuracy | 100.0% |
| Patient-ID match vs raw labels | 95.0% (38/40) |
| Detected `must_not` violations | 0 |
| Provider double-bookings after simulated mutations | 0 |

Labelled emergency messages escalated: **M003, M014, M017, M032, M035**. No labelled emergency was missed by this version.

### Data-label inconsistency

Two supplied labels conflict with the brief's explicit privacy contract:

- **M011:** unknown email + mismatched stated DOB. The label sets `patient_id` to `P002`, but the brief says `patient_id` must be `null` when the sender is not verified.
- **M027:** the sender is an unauthorized third party asking about Grace Park's appointment. The label sets `patient_id` to `P013`, but the brief says to return `null` for an unverified sender.

The agent follows the written privacy contract: `patient_id: null`, no disclosed appointment or health details, and verification/decline actions. The raw patient-ID metric is therefore 38/40. This is documented in `reports/evaluation.json`; I did not silently change the labels or weaken verification to get a perfect score.

### What this result does and does not prove

The sample is small and used during implementation; the intent and action logic was tuned against its stated traps. A 100% sample score is therefore an **in-sample regression result**, not a held-out accuracy claim. The must-not checker covers the supplied label vocabulary using simple assertions; it is not a clinician-reviewed safety proof. `reports/evaluation.json` provides per-message detail, and `reports/audit_log.json` preserves the action path.

Known limits include heuristic language coverage, ambiguous appointment phrasing, simplified timezone handling, no external billing ledger, no actual records-release integration, and no real staff queue. The optional LLM fallback helps with unseen wording when the rules return `other`, but it does not make the system production-ready.

## Reused vs. built

**Reused:** Python standard library for JSON, date handling and CLI; `FastMCP` from the official MCP Python SDK for protocol exposure; optional OpenAI Python SDK for structured semantic fallback. No database or frontend framework is needed for the synthetic data.

**Built for this task:** patient/caregiver identity resolution, verification-token boundary, refill policy evaluator, date/slot conflict checks, in-memory scheduling mutations, multi-intent orchestration, emergency-only reply path, suspicious-instruction handling, per-message audit trace, and evaluation harness.

## What I'd do with another week

- Add held-out/paraphrased cases, adversarial prompt-injection tests, negation tests, and clinician-reviewed emergency recall tests.
- Replace the heuristic intent/date parser with a schema-constrained LLM planner whose tool calls are always checked by deterministic policy functions.
- Use a real persistent scheduler with transactional booking/idempotency and concurrent conflict tests.
- Integrate real routing, billing, portal, and records systems behind audited least-privilege connectors.
- Add multilingual quality evaluation, a review UI, replayable traces and latency/cost dashboards.
- Add a fail-closed fallback and alerting for tool/API failures.

## Before real patient data

This code must not be connected to real patient data without, at minimum: an organization-approved security and privacy review; verified user/session authentication with expiring/revocable authorization; encryption in transit/at rest; access controls and immutable audit records; an approved model/vendor configuration and appropriate contractual/legal review; PHI minimization and retention controls; staff queue integrations and monitored escalation SLAs; clinical leadership sign-off on triage logic; adversarial testing and independent validation; and clear human approval gates for every prescription and sensitive records release. No prescription is approved or transmitted by this prototype.
