# Input: From Infra / Mailer

Status: DRAFT, rewritten for email. Items marked **[CONFIRM]** are still open. This file was `from-voice.md`.

In v0 the sending side would be a mock behind a `MailProvider` interface (`send_email`, `get_message_status`, `handle_webhook`), mirroring the `VoiceProvider` interface in the current code. The real provider is undecided.

## Contacts (from infra Contact DB)
| Field | Notes |
|-------|-------|
| contact_id | Stable, deterministic from the seed so re-runs are no-ops (SRC-007) |
| full_name, credentials, specialty | Doctor name from the practice website |
| practice_name, address, city, state | |
| channel: type, value | `email` or `web_form`. Email is **published on the practice website only**, never a personal or guessed address. Phone is kept as reference only |
| verified | Syntax and domain check (VER-003), no earlier hard bounce |
| send_clearance | `not_cleared` by default; `not_cleared` channels are never emailed |
| source, retrieved_at | Per field (SRC-003) |
| hook | One specific line from the practice website |
| practice size, time zone | For segment and the send window |

## Email attempt outcomes
- `email_attempt_id` (deterministic from contact id + attempt number), `provider_message_id`, `script_version`, `attempt_number`
- `outcome`: `BOOKED | INTERESTED_NO_TIME | CALL_BACK_LATER | REFERRED | NO_REPLY | AUTO_REPLY_ONLY | BOUNCED | DECLINED | NOT_A_FIT | DO_NOT_CONTACT | FAILED_TECHNICAL`
- `failure_category`, `lost_at`, `next_step`
- sent_at, delivered_at, bounce type, reply time
- booked slot and any address learned from a reply (used only for the invite)

## Reply threads
The thread is the whole record. For each message:
- direction (outbound or inbound), sent time, text, and `sender_role` where it can be told: doctor, office manager, front desk, unknown
- `reply_class`: `accepted | proposes_other_time | interested_no_time | referred_to_other_person | declined | not_a_fit | stop | auto_reply | bounce | unclear`
- auto-replies and bounces marked as such (assumption A18)

Raw threads stay in the infra DB; the Brain gets the redacted package ([../outputs/to-brain.md](../outputs/to-brain.md)). No audio, tone or speaker-type data exists.

## Webhook behavior
- Delivery and reply state is driven by provider webhooks with a polling fallback (CALL-005, restated for mail)
- Webhooks are signature-verified and idempotent: a duplicate delivery must not create a second attempt update or thread record (AC-5)

## Past outreach history
- Prior attempts and the templates used, for [../analysis-of-past-scripts.md](../analysis-of-past-scripts.md)

Record shapes: [../data-schema.md](../data-schema.md)

## Resolved
- **Attempt id owner:** infra (`email_attempt_id`)
- **Delivery to the Mailer:** an `EmailRequest` object

## Delegated to the eng team
- Mailer provider, sending domain and the reply mailbox

## To pin down **[CONFIRM]**
1. Who classifies replies (the provider, an LLM step, or the Brain's reviewer), and how reliably
2. Mailer API limits on headers, threading and daily volume
3. How fast bounces and replies arrive
4. Whether past outreach can be exported in bulk
5. Failure reporting channel and latency
6. Handling of non-ASCII names in headers and subjects
