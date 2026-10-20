# Output: Audit Log and Error Report

Internal outputs, kept by the connector.

## 6. Audit log
What was generated, sent, and received, with timestamps. Entry: `{timestamp, event, call_id, actor, payload_ref, result}`

## 7. Error report
Missing fields, failed deliveries, malformed transcripts. Entry: `{timestamp, call_id, error_type, field, detail}` where `error_type` is `missing_field | delivery_failed | malformed_transcript`.
