# Output: To Mailer

Status: DRAFT, rewritten for email. Items marked **[CONFIRM]** are still open. This file was `to-voice.md`.

## 1. Personalized email per contact: `EmailRequest`
| Field | Content |
|-------|---------|
| `email_attempt_id`, `contact_id`, `script_version`, `attempt_number` | Traceability |
| `segment` | From specialty × practice size |
| `to`, `from_name`, `reply_to` | Published address; Anirudh Bagla; configured reply mailbox |
| `subject` | From the Brain template, filled |
| `body_text` | Hook, pitch, ask with `slot_1` and `slot_2`, AI-assisted line, opt-out line, sender name, postal address |
| `headers` | `In-Reply-To` on a follow-up; `List-Unsubscribe` |
| `variables_used`, `fallbacks_applied` | What was filled and what fell back |
| `approval` | Batch id, who approved, when. The Mailer refuses an email without it |

Shape: [../data-schema.md](../data-schema.md).

## 2. Send queue
Ordered list of contacts with the email attached. It contains only contacts that passed the send gate: not suppressed, channel verified, inside the send window in their time zone, `send_clearance` set, batch approved, daily cap and kill switch clear.

## Rules
- The opt-out line is **always appended**, whatever the Brain sent (playbook §5.4, §8).
- No literal `{placeholder}` may reach the Mailer. Required fields missing (including `reply_to` and `sender_postal_address`) block the send and go to the error report.
- Max 2 emails per practice, plus up to 2 more only after a request to write later. The follow-up goes in the same thread after 4 business days with no human reply. No retry after DECLINED, NOT_A_FIT, DO_NOT_CONTACT or BOUNCED.
- Daily cap: **10 emails/day** (assumption A6). `config.toml` still says 50 for the dry run and is the teammates' file.
- Plain text, under 150 words, no images, no attachments, no tracking.
- A reply asking for a human gets an answer from Anirudh. The connector never answers replies; it only queues them for him.

## Resolved
- Connector owns the opt-out and AI-assisted lines.
- Delivery format is the `EmailRequest`.

## Delegated to the eng team
- Mailer provider, sending domain and reply mailbox

## To pin down **[CONFIRM]**
1. Mailer limits on headers, threading and volume
2. Domain authentication and warm-up so mail is not filed as spam
3. Whether a send is stopped mid-batch if Anirudh pulls the kill switch
