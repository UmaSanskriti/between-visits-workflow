# Voice Interface

Status: DRAFT. Items marked **[CONFIRM]** need an answer from the Voice team.

## Receive from Voice

### Doctor records
| Field | Notes |
|-------|-------|
| doctor_id | Stable unique ID |
| name | Full name plus preferred form (e.g. "Dr. Patel") |
| practice_name | |
| phone | E.164 format |
| specialty | |
| location / time_zone | Needed for calling hours |
| lead_source | Where the doctor was sourced |
| dnc_status | Do-not-call flag |
| consent_status | Prior consent on record, if any |

### Call outcomes
- call_id (unique), doctor_id
- status: `answered | voicemail | no_answer | declined | booked | callback_requested`
- timestamp, duration_sec
- booked_slot (ISO 8601 with time zone), contact_email
- transcript (full), audio_url

## Send to Voice

- Personalized script JSON per call (see [data-schema.md](data-schema.md)): call_id, doctor_id, script_version, first_message, system_prompt, variables_used, fallback_text
- Call queue: ordered list of doctors, script attached, priority and segment tags

## To pin down **[CONFIRM]**

1. Delivery mechanism: API, webhook, or file drop? Format (JSON with system prompt, first message, variables)?
2. Length limits on the prompt and first message
3. Which variables they support; how name pronunciation is handled (phonetic hints?)
4. Who owns the AI disclosure line (see [compliance.md](compliance.md))
5. Voicemail behavior; retry rules; calling hours; opt-out handling
6. Failure reporting: channel and latency
7. Does the call ID come from Voice, or do I generate it?
