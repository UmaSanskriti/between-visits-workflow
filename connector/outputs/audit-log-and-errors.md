# Output: Audit Log, Error Report, Alerts

Internal outputs, kept by infra and the connector.

## 6. Audit log
Append-only (DB-002), `audit_log.jsonl` in v0. Entry: `{timestamp, entity, entity_id, action, before, after, actor, correlation ids}`. Correlation ids are `contact_id` and `call_attempt_id`.

Goal (NFR-009): any call can be reconstructed from the attempt, the script, the gate decision and the audit entries: who was called, when, why it was allowed, which script ran, and what happened.

Gate decisions are stored with their reasons, including every skip.

## 7. Error report
Doubles as the manual review queue (OPS-002). Entry: `{timestamp, call_attempt_id, error_type, field, detail}`. Types:
- `missing_field`, including a missing `callback_number`
- `delivery_failed`
- `malformed_transcript`
- `duplicate_of`: a later duplicate contact, flagged and never dialed

## 8. Dead-letter queue and alerts
- Transcripts that exhaust retries land in the DLQ and raise an alert.
- Alerts also fire on a Brain outage, a transcript flagged for patient data, and any gate escape. A run with a gate escape exits with code 3.
