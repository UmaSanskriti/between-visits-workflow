# Script Variables

Every placeholder the connector fills in the email, aligned with playbook §4 and §8. If a source field is missing, use the fallback; never send a literal placeholder to the Mailer. Syntax is `{snake_case}` in v0 and `{{double_braces}}` in the playbook (**[CONFIRM]** with the Brain team).

| Placeholder | Source | Fallback | Notes |
|-------------|--------|----------|-------|
| `{doctor}` | Contact record (practice website) | "the doctor" in the body; subject uses `{practice}` | |
| `{practice}` | Contact record | "your practice" | |
| `{contact_name}` | Contact record, or the name on a reply | "there" ("Hi there,") | Used in the invite cover note |
| `{specialty}` | Contact record | omit | Drives segment |
| `{practice_size}` | Contact / enrichment | omit | solo / small group; drives segment |
| `{hook}` | Contact record: one line from the practice website | omit the sentence; the email works without it | Woven in naturally, never forced |
| `{segment}` | Connector: specialty × practice size | `default` | Picks pitch angle |
| `{pitch_angle}` | Connector, using Brain guidance for the segment | Brain default angle | |
| `{subject}` | Brain template | **Required** | Plain, no all caps, no "Re:" on a first email |
| `{slot_1}` … `{slot_4}` | Anirudh's calendar (stub in v0): next 10 business days, 30 minutes, Boston time | at least `slot_1` and `slot_2` required; otherwise ask for "the best week or time of day" | The first email offers 2; a reply may add 3-4 |
| `{meeting_format}` | Config | "by phone, by video, or in person at your practice" | Replaces the old call-or-visit choice on the phone |
| `{sender_name}` | Brain | **Required** | Anirudh Bagla, HBS student |
| `{reply_to}` | Config | **Required. No fallback**; blocks the send when empty | Mailbox that receives replies; eng sets it up |
| `{sender_postal_address}` | Config | **Required. No fallback**; blocks the send when empty | CAN-SPAM; still TBD |
| `{opt_out_line}` | Brain | **Required** | "Reply 'stop' and we won't contact you again." Never edited |
| `{ai_disclosure_line}` | Brain | **Required** | See [compliance.md](compliance.md); wording not yet LOCKED (assumption A16) |
| `{ask}` | Brain | **Required** | 30-minute conversation |
| `{script_version}` | Brain | **Required** | Logged, not shown in the email |

Removed with the move to email: `{callback_number}` (phone only) and the phone `{caller_identity}` line, replaced by `{sender_name}`.

## Not a placeholder: the opt-out line
The opt-out line (playbook §5.4 and §8) has no fallback and is **always appended** by the connector, even if the Brain's template drops it. The connector never edits it.

## Rules
- Required field missing: do not queue the email; write to the error report.
- Optional field missing: use the fallback and record it in `fallbacks_applied`.
- An unfilled placeholder anywhere in the output, subject included, blocks the send.
- Body is plain text and under 150 words after filling. Over that, the email is held for review.
- Inputs are public business information only. Never use patient data, and never a doctor's personal email.
