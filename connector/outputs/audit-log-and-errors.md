# Output: Audit Log, Error Report, Alerts

Internal outputs, kept by infra and the connector.

## 6. Audit log
Append-only (DB-002), `audit_log.jsonl` in v0. Entry: `{timestamp, entity, entity_id, action, before, after, actor, correlation ids}`. Correlation ids are `contact_id` and `email_attempt_id`.

Goal (NFR-009): any email can be reconstructed from the attempt, the template, the approval, the gate decision and the audit entries: who was emailed, when, why it was allowed, who approved it, which template ran, and what happened.

Gate decisions are stored with their reasons, including every skip.

## 7. Error report
Doubles as the manual review queue (OPS-002). Entry: `{timestamp, email_attempt_id, error_type, field, detail}`. Types:
- `missing_field`, including a missing `reply_to`, `subject` or `sender_postal_address`
- `body_too_long`: over 150 words after filling
- `delivery_failed`
- `malformed_thread`
- `unclassified_reply`: a reply the classifier marked `unclear`
- `duplicate_of`: a later duplicate contact, flagged and never emailed

## 8. Dead-letter queue and alerts
- Threads that exhaust retries land in the DLQ and raise an alert.
- Alerts also fire on a Brain outage, a thread flagged for patient data, a spike in bounces or spam complaints, any "stop" reply, and any gate escape. A run with a gate escape exits with code 3.
