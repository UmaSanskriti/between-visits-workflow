# Test Plan

Use fictional contacts only. Never send to real addresses; use a sink mailbox. The existing tests in `tests/test_connector.py`, `tests/test_acceptance.py` and `tests/test_playbook.py` still cover the call flow. The cases below are the email targets and have no tests yet. All would run with a fixed clock (Tuesday 10:00 ET) and a mock Mailer and Brain.

## Sample contacts

| ID | Case | Expected connector behavior |
|----|------|-----------------------------|
| t_01 | Complete record, solo primary care | Full personalized email, segment `solo_primary_care` |
| t_02 | Missing doctor name | Fallback ("the doctor", subject uses the practice); recorded in `fallbacks_applied` |
| t_03 | Missing specialty | Generic pitch angle; no literal placeholder in output |
| t_04 | Name with unusual spelling or accents | Rendered exactly as published; UTF-8 headers intact |
| t_05 | Suppressed / do-not-contact | Excluded from the queue; skip reason logged |
| t_06 | Time zone outside the send window | Deferred, not sent |
| t_07 | Small group practice | Different pitch angle than solo |
| t_08 | Missing email address | Error report entry; not queued |
| t_09 | Invalid email syntax or domain | Channel unverified; not queued |
| t_10 | Only a web form, no email | Handled per the web-form decision; not emailed |

## Outcome edge cases

| Case | Expected |
|------|----------|
| No reply after the first email | Follow-up after 4 business days in the same thread; then outcome `NO_REPLY`, no further send |
| Hard bounce | `BOUNCED`; channel unverified; never retried; thread record still sent to Brain |
| Auto-reply (out of office) | `AUTO_REPLY_ONLY`; does not stop the sequence or count as a reply |
| "Stop" or "don't email again" | Suppression written in the same save; no further email ever scheduled; confirmation not sent unless the template allows it |
| Doctor accepts a slot | `BOOKED`; `interview` drafted once as `draft_pending_approval`; counted by template version |
| Reply proposes a different time | `INTERESTED_NO_TIME` or `proposes_other_time`; held for Anirudh to answer |
| Forwarded to another person | `REFERRED`; the new person's address used only for the thread and the invite |
| Not a fit (hospital-owned, no GLP-1) | `NOT_A_FIT`; no retry; list filter tightened |
| "Write again in March" | `CALL_BACK_LATER`; follow-up scheduled at that date; up to 2 extra |
| Malformed reply or unreadable thread | Error report; not forwarded |
| Reply appears to contain patient data | Held from the Brain; alert raised |
| Missing required field (`reply_to`, `sender_postal_address`, `subject`) | Email blocked; error report |
| Body over 150 words after filling | Held for review |
| Daily cap reached | Remaining emails deferred to the next business day |

## Infra acceptance criteria the connector must satisfy (email versions)
- AC-1 suppressed contact is not enqueued
- AC-2 `send_clearance = not_cleared` is blocked
- AC-3 outside the window is deferred
- AC-4 a "stop" reply suppresses and no further email is scheduled
- AC-5 duplicate webhook yields one update and one thread record
- AC-6 Brain down means no send and a retry
- AC-7 failed thread delivery lands in the DLQ with an alert
- AC-8 booked yields exactly one interview and handoff
- AC-9 kill switch yields zero new sends
- AC-10 every sent email carries the opt-out line, the AI-assisted line and the postal address (replaces the AI-and-recording disclosure check)

## Content and deliverability

| Case | Expected |
|------|----------|
| Rendered email | Plain text only; no images, attachments or tracking links |
| `From`, `Reply-To`, `List-Unsubscribe` headers | Present and correct; `From` is Anirudh's real name |
| SPF / DKIM / DMARC | Pass on the test domain |
| Follow-up | Same thread (`In-Reply-To` set), `Re:` subject, same opt-out line |
| LOCKED text | The opt-out line is verbatim in every email |

## Checks
- [ ] No unfilled placeholder ever reaches the Mailer, subject included
- [ ] The opt-out line is verbatim in every email
- [ ] Every email has a `script_version`, an approval record and an audit log entry
- [ ] Outcome summary conversion matches the raw counts
- [ ] No-replies, bounces and declines reach the Brain
- [ ] Gate escapes are 0 on every run
