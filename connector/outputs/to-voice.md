# Output: To Voice

Status: DRAFT, aligned with `V0_SCOPE.md`. Items marked **[CONFIRM]** are still open.

## 1. Personalized script per call: `CallRequest`
| Field | Content |
|-------|---------|
| `call_attempt_id`, `contact_id`, `script_version`, `attempt_number` | Traceability |
| `segment` | From specialty × practice size |
| `first_message` | The LOCKED opening (AI disclosure + recording consent) followed by the route line |
| `system_prompt` | Pitch, ask for time with `slot_1..4`, close with `callback_number`, objection handling, voicemail text |
| `variables_used`, `fallbacks_applied` | What was filled and what fell back |
| metadata | Channel, time zone, clearance |

Shape: [../data-schema.md](../data-schema.md). Maps to a Vapi assistant payload later.

## 2. Call queue
Ordered list of contacts with the script attached. It contains only contacts that passed the pre-call gate (CMP-001): not suppressed, channel verified, inside the calling window in their time zone, `call_clearance` set, kill switch off.

## Rules
- The AI and recording disclosure is **always prepended**, whatever the Brain sent (BRN-001, CMP-004, CMP-005).
- No literal `{placeholder}` may reach Voice. Required fields missing (including `callback_number`) block the call and go to the error report.
- Max 2 attempts per practice, plus up to 2 more only after a call-back request. No retry after DECLINED, NOT_A_FIT, DECLINED_RECORDING, DO_NOT_CONTACT or WRONG_NUMBER.
- Daily cap: **10 calls/day** (founder decision; matches the free Vapi number). `config.toml` still says 50 for the dry run and must be set to 10 before live.
- No audio is kept. Voice must return tone, intonation and speaker-type data with the transcript ([../inputs/from-voice.md](../inputs/from-voice.md)).
- Voicemail uses the playbook's approved text. A feature flag off means hang up and log `VOICEMAIL`.
- Any request for a human: give Anirudh's name and callback number.

## Resolved
- Infra owns the AI and recording disclosure lines.
- Script delivery format is the `CallRequest`.

## Delegated to the eng team
- Voice provider choice and the callback number

## To pin down **[CONFIRM]**
1. Vapi length limits, supported variables, pronunciation handling
2. Caller ID registration so the number is not labelled "Spam Likely" (CMP-007)
