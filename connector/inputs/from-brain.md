# Input: From Brain

Status: DRAFT, aligned with `clinic-outreach-brain.md` (playbook v1, §8 emails) and the infra spec §8, adapted for email. Items marked **[CONFIRM]** are still open.

## Template resolution
The connector requests a template before every email: `POST /v1/scripts/resolve` with `campaign_id`, contact summary (`contact_id`, `specialty`, `practice_name`), `attempt_number` and `channel: "email"`.

Response: `script_id`, `script_version`, `subject`, `body_template`, `required_lines` (`opt_out`, `ai_assisted`), `booking_instructions` (`interview_length_minutes`: **30**).

- The connector treats the template as opaque content plus metadata (BRN-001).
- If the Brain is unreachable, **no email is sent**; the job retries with backoff, and v0 waits 15 minutes (BRN-002, AC-6).
- Timeouts, exponential backoff with jitter, and a circuit breaker apply (BRN-003).

## Playbook content the template is built from
- **Who we are** (§1), always with "we believe" / "up to", never a guarantee
- **Goal and phase** (§2): one 30-minute conversation with the lead doctor
- **Compliance rules, LOCKED** (§5): the ones that apply to email are listed in [../compliance.md](../compliance.md)
- **Cold email** (§8b): subject, short pitch, one ask with two slots, opt-out line. This is the base for the first email
- **Invite cover note** (§8a): sent after Anirudh approves the invite
- Follow-up text and reply objection handling (§7, adapted). The playbook has no follow-up text yet; assumption A9
- The call-only content (open, route line, voicemail, phone trees) is no longer used

## Template placeholders
Playbook text uses `{{double_braces}}`; v0 code uses `{snake_case}`. **[CONFIRM]** which syntax the Brain will deliver. Variables are in [../script-variables.md](../script-variables.md).

## Segment guidance
- Brain supplies per-segment pitch guidance; the connector picks the segment from specialty × practice size

## Learnings and feedback
- Template changelog (§14): each change, its failure category, the thread that caused it, and who approved it
- Pattern notes every 10 contacts (§11) and the monthly refresh (§12); see [../analysis-of-past-scripts.md](../analysis-of-past-scripts.md)
- Nothing goes live until Anirudh approves the new `script_version`

## Resolved
- **Who chooses the pitch angle:** Brain supplies guidance; the connector picks the segment
- **Template format:** required placeholders are `ask`, `sender_name`, `subject`, `opt_out_line`, `script_version`

## To pin down **[CONFIRM]**
1. Final `/v1/scripts/resolve` and `/v1/transcripts` schema and ownership (spec open question 8); the second becomes a reply-thread endpoint
2. Placeholder syntax
3. How a new template version is announced (the monthly refresh is the planned cadence)
4. The email version of playbook §5, §6 and §10 (see [../open-questions.md](../open-questions.md))
