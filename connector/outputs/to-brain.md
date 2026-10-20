# Output: To Brain

Status: DRAFT, aligned with infra spec §8.2 and `V0_SCOPE.md`. Items marked **[CONFIRM]** are still open.

## 3. Transcript package: `POST /v1/transcripts`
One per completed call.
- `idempotency_key`, `call_attempt_id`, `contact_id`, `script_version`, `outcome`, `started_at`, `ended_at`
- `utterances` (speaker, `start_ms`, text), `recording_ref`
- `failure_category` and `lost_at`, so the Brain's reviewer loop can start from them

Delivery:
- The transcript is stored in the infra DB **before** delivery (BRN-004).
- Delivery is at-least-once with an idempotency key, 3 tries, then the dead-letter queue and an alert (BRN-005, AC-7).
- Status per transcript: `pending | delivered | failed` (BRN-006). Target delivery rate is 99% or higher.
- **Redacted**: phones and emails are masked. Raw transcripts stay in infra (LOG-002).
- A transcript flagged for possible patient data is held and not delivered until cleared (CMP-009, BRN-007).
- **No-answer calls** produce an empty transcript record, so the Brain sees every completed call and not only friendly ones.

## 4. Outcome summary
Counts by outcome, script version and segment; funnel; delivery rate; gate escapes (must be 0). Feeds the Brain's monthly measure step (playbook §12): calls placed, % reaching a person, % booked, top 3 failure categories.

## 5. Analysis of past scripts
Joined data and findings from [../analysis-of-past-scripts.md](../analysis-of-past-scripts.md), sent to support the Brain's reviewer notes.

## Resolved
- Transcripts are redacted for the Brain.

## To pin down **[CONFIRM]**
1. Final `/v1/transcripts` schema, which the Brain team owns (spec open question 8)
2. Whether the Brain wants `failure_category` and `lost_at` proposed by the connector or assigned by its own reviewer
