# Compliance

**Not legal advice.** The playbook and the infra spec both say counsel must review consent language, call flows and recording practices before any live call. The healthcare regulatory lawyer is on the list.

## Rules the connector enforces
From playbook §5 (LOCKED) and infra spec §7 (hard gates):

| Rule | Source | Connector behavior |
|------|--------|--------------------|
| AI disclosure first | Playbook §5.1, CMP-004 | First utterance identifies an AI assistant calling for Anirudh Bagla, an HBS student; always prepended, never edited |
| Recording consent second | Playbook §5.2, CMP-005 | Ask whether recording is OK. Massachusetts requires all-party consent. If anyone objects: end politely, log `DECLINED_RECORDING`, stop or discard the recording |
| Callback details before hang-up | Playbook §5.3 | `callback_number` is required; blocks the call when empty |
| Opt-out | Playbook §5.4, CMP-006 | "Don't call again" ends the call, writes a suppression (contact + phone) in the same save, and stops every channel permanently |
| Published main lines only | Playbook §5.5 | Never a mobile or residential number; line type checked (VER-002) |
| Business hours, Boston time | Playbook §5.6, CMP-002 | Window evaluated in the contact's time zone; default weekdays 9:00 to 17:00; outside it the call is deferred |
| Max two attempts | Playbook §5.7, CALL-007 | Two, plus up to two more after a call-back request |
| Invite email use | Playbook §5.8 | Email collected on a call is used only for the calendar invite |
| No patient information | Playbook §5.9, CMP-009 | Possible patient data flags the transcript and holds it from the Brain |
| No medical claims | Playbook §5.10 | Brain-owned; the connector adds no claims |
| Never pretend to be human | Playbook §5.11 | "Are you a robot?" is answered yes, plainly |
| Nothing without Anirudh | Playbook §5.12 | He approves each batch, each script version and each calendar invite (`draft_pending_approval`) |

## Pre-call gate (CMP-001)
A call is placed only if all pass, and every failure reason is logged:
1. Contact not suppressed
2. Channel verified
3. Inside the calling window
4. `call_clearance` set (default `not_cleared`, which is never dialed; set only by an authorized process after counsel sign-off or recorded consent)
5. Kill switch off

Target: **0 gate escapes**.

## Other requirements
- **Caller ID** (CMP-007): a valid call-back number belonging to AB, with STIR/SHAKEN attestation. Register it so it is not labelled "Spam Likely".
- **Voicemail** (CMP-008): the playbook's approved text is left; a feature flag off means hang up and log.
- **Consent evidence** (CMP-010): `consent_record` with timestamp and source.
- **Privacy** (NFR-004): contact data is business-contact PII; mask phone and email in logs; support deletion on request, including recordings and transcripts.

## Decisions still needed
1. Legal position on AI-voice calls to practice numbers (spec Q2). **Blocks any live call, even to team numbers.**
2. Recording policy: record all calls, or transcript only? (spec Q3)
3. Retention periods for contacts, recordings, transcripts (spec Q6)
4. Dashboard privacy: GitHub Pages on a public repo is public, which is fine for fictional data but must change before real data.
