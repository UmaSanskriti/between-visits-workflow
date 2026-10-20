# Input: From Voice / Infra

Status: DRAFT, aligned with the infra spec and `V0_SCOPE.md`. Items marked **[CONFIRM]** are still open.

In v0 the Voice side is a mock behind a `VoiceProvider` interface (`place_call`, `get_call_status`, `handle_webhook`). The target provider is Vapi (backup Retell).

## Contacts (from infra Contact DB)
| Field | Notes |
|-------|-------|
| contact_id | Stable, deterministic from the seed so re-runs are no-ops (SRC-007) |
| full_name, credentials, specialty | Doctor name from the practice website |
| practice_name, address, city, state | |
| channel: type, value | `phone`, `email`, or `web_form`. Phone is **E.164, published main business line only**, never a mobile |
| line_type | landline / mobile / VoIP / unknown (VER-002); feeds the compliance gate |
| verification_status | Only `verified` contacts are callable |
| call_clearance | `not_cleared` by default; `not_cleared` channels are never dialed (CMP-003) |
| source, retrieved_at | Per field (SRC-003) |
| hook | One specific line from the practice website |
| practice size, time zone | For segment and calling window |

## Call attempt outcomes
- `call_attempt_id` (deterministic from contact id + attempt number), `provider_call_id`, `script_version`, `attempt_number`
- `outcome`: `BOOKED | CALLBACK_REQUESTED | NO_ANSWER | VOICEMAIL | GATEKEEPER_BLOCKED | DECLINED | NOT_A_FIT | DECLINED_RECORDING | DO_NOT_CONTACT | WRONG_NUMBER | FAILED_TECHNICAL`
- `failure_category` (playbook §10), `lost_at` timestamp in the call, `next_step`
- started_at, ended_at, duration
- booked slot and the email collected on the call (used only for the invite)

## Transcripts and recordings
- Utterances with speaker and `start_ms`, `recording_ref` (signed, expiring URL or object key)
- Raw transcripts stay in the infra DB; the Brain gets the redacted package ([../outputs/to-brain.md](../outputs/to-brain.md))

## Webhook behavior
- Call state is driven by provider webhooks with a polling fallback (CALL-005)
- Webhooks are signature-verified and idempotent: a duplicate delivery must not create a second attempt update or transcript (AC-5)

## Past call history
- Prior attempts and the scripts used, for [../analysis-of-past-scripts.md](../analysis-of-past-scripts.md)

Record shapes: [../data-schema.md](../data-schema.md)

## Resolved
- **call_id owner:** infra (`call_attempt_id`)
- **Delivery to Voice:** a `CallRequest` object, mapped later to a Vapi assistant payload

## To pin down **[CONFIRM]**
1. Vapi payload mapping and length limits on prompt / first message
2. How fast outcomes and transcripts arrive after a call
3. Whether past calls can be exported in bulk
4. Failure reporting channel and latency
5. Name pronunciation handling
