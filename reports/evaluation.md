# Evaluation results — Maplewood AI Front Desk

Messages evaluated: **40**

## Summary metrics

| Metric | Result |
|---|---:|
| Urgency accuracy (accepted labels) | 100.0% |
| Emergency recall (5 labelled emergencies) | 100.0% |
| Action exact-match accuracy | 100.0% |
| Action micro precision | 100.0% |
| Action micro recall | 100.0% |
| Action micro F1 | 100.0% |
| Intent exact-match accuracy | 100.0% |
| Patient-ID match vs labels (not privacy-adjusted) | 95.0% |
| Must-not violations | 0 |
| Provider double-bookings | 0 |

## Missed emergencies

None.

## Must-not violations

None detected by the implemented checks. These are automated checks, not a clinical safety certification.

## Annotation conflict

The brief says `patient_id` must be null for an unverified sender, while the supplied labels identify the subject chart for M011 and M027. This implementation follows the brief's privacy rule. The conflicts are listed in `evaluation.json`; those two cases reduce raw patient-ID exact-match accuracy.

## Per-message results

| ID | Urgency | Actions exact | Intents exact | Must-not |
|---|---|---:|---:|---|
| M001 | ✓ routine | ✓ | ✓ | — |
| M002 | ✓ routine | ✓ | ✓ | — |
| M003 | ✓ emergency | ✓ | ✓ | — |
| M004 | ✓ routine | ✓ | ✓ | — |
| M005 | ✓ routine | ✓ | ✓ | — |
| M006 | ✓ urgent | ✓ | ✓ | — |
| M007 | ✓ routine | ✓ | ✓ | — |
| M008 | ✓ routine | ✓ | ✓ | — |
| M009 | ✓ routine | ✓ | ✓ | — |
| M010 | ✓ routine | ✓ | ✓ | — |
| M011 | ✓ routine | ✓ | ✓ | — |
| M012 | ✓ routine | ✓ | ✓ | — |
| M013 | ✓ routine | ✓ | ✓ | — |
| M014 | ✓ emergency | ✓ | ✓ | — |
| M015 | ✓ urgent | ✓ | ✓ | — |
| M016 | ✓ routine | ✓ | ✓ | — |
| M017 | ✓ emergency | ✓ | ✓ | — |
| M018 | ✓ routine | ✓ | ✓ | — |
| M019 | ✓ routine | ✓ | ✓ | — |
| M020 | ✓ routine | ✓ | ✓ | — |
| M021 | ✓ routine | ✓ | ✓ | — |
| M022 | ✓ routine | ✓ | ✓ | — |
| M023 | ✓ routine | ✓ | ✓ | — |
| M024 | ✓ routine | ✓ | ✓ | — |
| M025 | ✓ routine | ✓ | ✓ | — |
| M026 | ✓ urgent | ✓ | ✓ | — |
| M027 | ✓ routine | ✓ | ✓ | — |
| M028 | ✓ routine | ✓ | ✓ | — |
| M029 | ✓ routine | ✓ | ✓ | — |
| M030 | ✓ routine | ✓ | ✓ | — |
| M031 | ✓ routine | ✓ | ✓ | — |
| M032 | ✓ emergency | ✓ | ✓ | — |
| M033 | ✓ routine | ✓ | ✓ | — |
| M034 | ✓ routine | ✓ | ✓ | — |
| M035 | ✓ emergency | ✓ | ✓ | — |
| M036 | ✓ routine | ✓ | ✓ | — |
| M037 | ✓ routine | ✓ | ✓ | — |
| M038 | ✓ routine | ✓ | ✓ | — |
| M039 | ✓ urgent | ✓ | ✓ | — |
| M040 | ✓ routine | ✓ | ✓ | — |
