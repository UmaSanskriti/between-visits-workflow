# Email Pivot: Assumptions

Written 2026-10-21 while the teammates' email version is still unpushed. Every item is an assumption, not a decision. When their push lands, compare it with this list and change the connector docs to match them.

## Channel and flow

| # | Assumption | Basis |
|---|-----------|-------|
| A1 | Email replaces calls as the only outbound channel. There is no voice agent, call queue, voicemail or call-back number. | Request to move all connector files to email |
| A2 | The email is the cold email in playbook §8b, extended with the same ask: 30 minutes with the lead doctor. | Playbook §8b is the only existing email text |
| A3 | Recipients are addresses published on the practice website only, including a role address such as `info@`. Never a personal or guessed address. | Playbook §5.5 and §8b, carried over |
| A4 | Anirudh's name is the sender and signs the email. The connector drafts, Anirudh approves each batch and each template version before anything is sent. | Playbook §5.12, carried over |
| A5 | Maximum 2 emails per practice: the first, then one follow-up after 4 business days with no reply. A reply asking us to write later earns up to 2 more. | Playbook §5.7 (two attempts), carried over |
| A6 | Send cap of 10 emails per day. | Carries over the founder's 10 calls per day; an email cap is also needed for sender reputation while the domain warms up |
| A7 | Send window is weekdays 9:00 to 17:00 in the contact's time zone. | Playbook §5.6, carried over |
| A8 | A booking is made by email thread: the practice replies with a time, Anirudh sends the calendar invite, and the playbook §8a cover note goes with it. | Playbook §8a |
| A9 | The follow-up is a short reply in the same thread (`Re:` subject), with the same opt-out line. | Common practice; not in the playbook |

## What stops applying

| # | Assumption | Effect |
|---|-----------|--------|
| A10 | The AI voice disclosure, recording consent, callback number before hang-up, phone trees and voicemail rules (playbook §5.1 to 5.3, §6) do not apply to email. | Removed from the connector |
| A11 | No audio exists, so tone, intonation and speaker-type analysis are dropped. The reply text is the record. | Replaces the founder's "no audio kept, tone data in the transcript" decision |
| A12 | The founder decision "AI-voice calls are permitted" is moot. The email equivalent is open question E1 on sender identity. | [compliance.md](compliance.md) |
| A13 | The "10 calls per day" decision carries over as A6 only. `config.toml` is left untouched. | Teammates' code |

## What stays

Opt-out on every channel permanently, no patient information, no medical claims, nothing without Anirudh, 30-minute ask, Boston-time slots, redacted data to the Brain, no LOCKED text edited by the connector.

## Email-specific additions

| # | Assumption |
|---|-----------|
| A14 | Plain text only, under 150 words, one ask, no attachments, no images, no open or click tracking (playbook §8 rules). |
| A15 | Each email carries an opt-out line, the sender's real name, and a physical postal address (CAN-SPAM). |
| A16 | The email says it was drafted with AI assistance and approved by Anirudh, and a reply asking "is this automated?" gets a plain yes. Wording is not yet LOCKED. |
| A17 | Sending uses a dedicated domain with SPF, DKIM and DMARC, and an address that can receive replies. |
| A18 | Out-of-office and other auto-replies do not count as a reply and do not stop the sequence. A reply from a different person at the practice (a forward) counts. |
| A19 | A hard bounce marks the channel unverified and is never retried. |
| A20 | The Brain receives a redacted reply thread (emails, phones and names of non-doctor staff masked) for every sent email, including no-replies and bounces, and writes the next template version. |
