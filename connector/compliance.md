# Compliance

**Not legal advice.** The playbook and the infra spec both say counsel must review outreach text and practices before anything is sent. The healthcare regulatory lawyer is on the list. This file is rewritten for email on assumptions ([email-pivot-assumptions.md](email-pivot-assumptions.md)); none of it has been reviewed by counsel.

## Rules the connector enforces
From playbook §5 (LOCKED) and infra spec §7, kept where they apply to email:

| Rule | Source | Connector behavior |
|------|--------|--------------------|
| Opt-out | Playbook §5.4, CMP-006 | Every email carries the opt-out line. A "stop" reply (or any clear request not to be contacted) writes a suppression (contact + email) in the same save and stops every channel permanently. Honored at once; the legal limit is 10 business days |
| Published addresses only | Playbook §5.5, §8b | Only an address published on the practice website. Never a personal address or a guessed one |
| Business hours | Playbook §5.6, CMP-002 | Send window evaluated in the contact's time zone; default weekdays 9:00 to 17:00; outside it the email is deferred |
| Max two emails | Playbook §5.7 | Two, plus up to two more after a request to write later |
| Invite email use | Playbook §5.8 | An address learned from a reply is used only for the calendar invite and its cover note |
| No patient information | Playbook §5.9, CMP-009 | A reply that appears to contain patient data is flagged and held from the Brain |
| No medical claims | Playbook §5.10 | Brain-owned; the connector adds no claims |
| Nothing without Anirudh | Playbook §5.12 | He approves each batch, each template version and each calendar invite (`draft_pending_approval`) |

## Email-specific rules (assumptions, not LOCKED)
| Rule | Connector behavior |
|------|--------------------|
| Honest sender (CAN-SPAM) | `From` is Anirudh's real name on a domain AB controls; subject matches the content; no misleading headers |
| Postal address (CAN-SPAM) | `{sender_postal_address}` is required in every email; empty blocks the send |
| Unsubscribe | The opt-out line plus a `List-Unsubscribe` header if the Mailer supports it |
| Say it is AI-assisted | The email states it was drafted with AI assistance and approved by Anirudh (assumption A16). A reply asking "is this automated?" is answered yes, plainly |
| No tracking | No open or click tracking, no images, no attachments (playbook §8 rules, assumption A14) |
| Plain text, under 150 words | Longer emails are held for review |

## Send gate
An email is sent only if all pass, and every failure reason is logged:
1. Contact not suppressed
2. Channel verified (syntax, domain, no earlier hard bounce)
3. Inside the send window
4. `send_clearance` set (default `not_cleared`, which is never emailed; set only by an authorized process, with the basis recorded)
5. Batch approved by Anirudh
6. Daily cap not reached, kill switch off

Target: **0 gate escapes**.

## Other requirements
- **Sender setup**: SPF, DKIM and DMARC on the sending domain, with a reply mailbox, so mail is authenticated and replies are received.
- **Consent evidence** (CMP-010): `consent_record` with timestamp and source, recording the basis for emailing a published business address.
- **Privacy** (NFR-004): contact data is business-contact PII; mask emails and phones in logs; support deletion on request, including reply threads.

## No longer applicable
AI voice disclosure on the call, recording consent and Massachusetts all-party consent, callback number before hang-up, caller ID and STIR/SHAKEN, voicemail, phone trees, and the audio and voice-analysis decisions. The 2026-10-20 founder decisions on AI-voice calls and no audio kept are moot for an email-only process (assumptions A11, A12).

## Decisions still needed
1. **E1: Sender identity.** Is it acceptable that Anirudh sends a message drafted with AI help, with the disclosure line in assumption A16? Only Anirudh and the lawyer can decide, and the wording is not LOCKED yet.
2. Does the playbook's LOCKED §5 need an email version (call-specific rules replaced by the email rules above)? Only Anirudh can change it, after legal review.
3. Is emailing a published business address without prior consent acceptable under CAN-SPAM and state rules for healthcare contacts? Counsel to confirm.
4. Retention periods for contacts and reply threads (spec Q6)
5. Dashboard privacy: GitHub Pages on a public repo is public, which is fine for fictional data but must change before real data.
