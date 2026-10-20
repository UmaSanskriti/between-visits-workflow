# Input: From Brain

Status: DRAFT, aligned with `clinic-outreach-brain.md` (playbook v1) and the infra spec §8. Items marked **[CONFIRM]** are still open.

## Script resolution
The connector requests a script before every call: `POST /v1/scripts/resolve` with `campaign_id`, contact summary (`contact_id`, `specialty`, `practice_name`) and `attempt_number`.

Response: `script_id`, `script_version`, `opening`, `turns_or_prompt`, `required_disclosures` (`ai_identity`, `recording`), `booking_instructions` (`interview_length_minutes`: **30**).

- The connector treats the script as opaque content plus metadata (BRN-001).
- If the Brain is unreachable, **no call is placed**; the job retries with backoff, and v0 waits 15 minutes (BRN-002, AC-6).
- Timeouts, exponential backoff with jitter, and a circuit breaker apply (BRN-003).

## Playbook content the script is built from
- **Who we are** (§1), always with "we believe" / "up to", never a guarantee
- **Goal and phase** (§2): one 30-minute conversation with the lead doctor
- **Compliance rules, LOCKED** (§5), the **Open** and **Close** (§6)
- Route line: ask for the office manager or whoever keeps the doctor's schedule
- Pitch (under 30 seconds), ask for time, objection handling (§7), voicemail text
- Phone-tree handling, emails (§8, out of scope for v0)

## Template placeholders
Playbook text uses `{{double_braces}}`; v0 code uses `{snake_case}`. **[CONFIRM]** which syntax the Brain will deliver. Variables are in [../script-variables.md](../script-variables.md).

## Segment guidance
- Brain supplies per-segment pitch guidance; the connector picks the segment from specialty × practice size

## Learnings and feedback
- Script changelog (§14): each change, its failure category, the call that caused it, and who approved it
- Pattern notes every 10 calls (§11) and the monthly refresh (§12); see [../analysis-of-past-scripts.md](../analysis-of-past-scripts.md)
- Nothing goes live until Anirudh approves the new `script_version`

## Resolved
- **Who chooses the pitch angle:** Brain supplies guidance; the connector picks the segment
- **Template format:** required placeholders are `ask`, `caller_identity`, `script_version`

## To pin down **[CONFIRM]**
1. Final `/v1/scripts/resolve` and `/v1/transcripts` schema and ownership (spec open question 8)
2. Placeholder syntax
3. How a new script version is announced (the monthly refresh is the planned cadence)
