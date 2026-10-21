# Output: To Brain

Status: DRAFT, rewritten for email, aligned with infra spec §8.2. Items marked **[CONFIRM]** are still open.

## 3. Reply thread package: `POST /v1/transcripts`
One per sent email (the endpoint name is kept until the Brain team renames it).
- `idempotency_key`, `email_attempt_id`, `contact_id`, `script_version`, `outcome`, `sent_at`
- `messages`: direction, time, `sender_role`, text. There is no audio, tone or speaker-type data
- `reply_class`
- `failure_category` and `lost_at`, so the Brain's reviewer loop can start from them

Delivery:
- The thread is stored in the infra DB **before** delivery (BRN-004).
- Delivery is at-least-once with an idempotency key, 3 tries, then the dead-letter queue and an alert (BRN-005, AC-7).
- Status per thread: `pending | delivered | failed` (BRN-006). Target delivery rate is 99% or higher.
- **Redacted**: email addresses, phones, and names of staff other than the doctor are masked. Raw threads stay in infra (LOG-002).
- A thread flagged for possible patient data is held and not delivered until cleared (CMP-009, BRN-007).
- **No-reply and bounced emails** produce a thread with no inbound messages, so the Brain sees every send and not only friendly ones.

## 4. Outcome summary
Counts by outcome, template version and segment; funnel (sent, delivered, replied, booked); delivery rate; bounce rate; gate escapes (must be 0). Feeds the Brain's monthly measure step (playbook §12): emails sent, % delivered, % replied, % booked, top 3 failure categories.

## 5. Analysis of past scripts
Joined data and findings from [../analysis-of-past-scripts.md](../analysis-of-past-scripts.md), sent to support the Brain's reviewer notes.

## Resolved
- Threads are redacted for the Brain.

## To pin down **[CONFIRM]**
1. Final `/v1/transcripts` schema, which the Brain team owns (spec open question 8)
2. Whether the Brain wants `failure_category` and `lost_at` proposed by the connector or assigned by its own reviewer
3. Whether the Brain wants reply drafts suggested by the connector, or only the thread
