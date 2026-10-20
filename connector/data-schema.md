# Data Schema

Aligned with the logical model in infra spec §6.1 (v0 stores these as JSON files per table). Field names are provisional until the Brain team confirms the final schema.

## Contact lifecycle
```
NEW -> SOURCED -> VERIFIED -> READY -> IN_OUTREACH -> BOOKED | DECLINED | UNREACHABLE
Any state -> SUPPRESSED (terminal, on a do-not-contact request)
```

## Entities
| Entity | Key fields |
|--------|-----------|
| `contact` | id, full_name, credentials, specialty, practice_name, address, city, state, status, verification_status, created_at, updated_at |
| `channel` | id, contact_id, type (`phone`/`email`/`web_form`), value, line_type, verified, call_clearance, source, source_url, retrieved_at |
| `call_attempt` | id, contact_id, channel_id, scheduled_at, started_at, ended_at, status, outcome, outcome_reason, failure_category, lost_at, provider, provider_call_id, script_version, transcript_id (no audio reference) |
| `transcript` | id, call_attempt_id, utterances (with speaker type, role, tone, intonation), language, created_at, delivery_status, delivered_at |
| `interview` | id, contact_id, call_attempt_id, scheduled_for, calendar_event_ref, status (`draft_pending_approval` until Anirudh approves) |
| `suppression` | id, identifier (phone/email/contact), reason, source, created_at |
| `consent_record` | id, contact_id, channel_id, consent_type, evidence_ref, captured_at, revoked_at |
| `audit_log` | id, entity, entity_id, action, before, after, actor, timestamp |

## Script: `CallRequest` (connector output)
```json
{
  "call_attempt_id": "ca_789",
  "contact_id": "c_123",
  "script_version": "v1",
  "attempt_number": 1,
  "segment": "solo_primary_care",
  "first_message": "<LOCKED opening> + <route line>",
  "system_prompt": "pitch, ask for time, close, objections, voicemail",
  "variables_used": {"doctor": "Dr. Chen", "practice": "Example Family Practice", "hook": "new weight-management programme"},
  "fallbacks_applied": ["practice_size"]
}
```

## Transcript package (to Brain)
```json
{
  "idempotency_key": "call_attempt_789",
  "call_attempt_id": "ca_789",
  "contact_id": "c_123",
  "script_version": "v1",
  "outcome": "BOOKED",
  "failure_category": null,
  "lost_at": null,
  "started_at": "2026-10-21T14:02:11Z",
  "ended_at": "2026-10-21T14:05:40Z",
  "utterances": [
    {
      "speaker": "agent",
      "start_ms": 0,
      "text": "...",
      "speaker_type": "ai_agent",
      "speaker_role": null,
      "tone": {"label": "neutral", "confidence": 0.8},
      "intonation": {"pace_wpm": 150, "pause_before_ms": 200, "pitch_trend": "falling"}
    },
    {
      "speaker": "doctor_side",
      "start_ms": 4200,
      "text": "...",
      "speaker_type": "human",
      "speaker_role": "front_desk",
      "tone": {"label": "rushed", "confidence": 0.6},
      "intonation": {"pace_wpm": 185, "pause_before_ms": 600, "pitch_trend": "rising"}
    }
  ]
}
```
No audio is stored, so there is no `recording_ref`. Tone and intonation are captured during the call. Perceived gender is not in the schema until the open question is settled.

## Outcomes
`BOOKED | CALLBACK_REQUESTED | NO_ANSWER | VOICEMAIL | GATEKEEPER_BLOCKED | DECLINED | NOT_A_FIT | DECLINED_RECORDING | DO_NOT_CONTACT | WRONG_NUMBER | FAILED_TECHNICAL`

## Failure categories (playbook §10)
No answer / voicemail; Stuck in phone tree; Hung up at AI disclosure; Screened out by front desk; Pitch did not land; Interested, no time agreed; Call back later; Not a fit.

## Audit and error records
See [outputs/audit-log-and-errors.md](outputs/audit-log-and-errors.md).
