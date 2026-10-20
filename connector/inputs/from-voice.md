# Input: From Voice

Status: DRAFT. Items marked **[CONFIRM]** need an answer from the Voice team.

## Doctor records
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

## Call outcomes
- call_id (unique), doctor_id
- status: `answered | voicemail | no_answer | declined | booked | callback_requested`
- timestamp, duration_sec
- booked_slot (ISO 8601 with time zone), contact_email

## Transcripts and recordings
- Full transcript, audio_url, call_id

## Past call history
- Prior calls and the scripts used, so they can be analysed ([../analysis-of-past-scripts.md](../analysis-of-past-scripts.md))

Record shapes: [../data-schema.md](../data-schema.md)

## To pin down **[CONFIRM]**
1. Delivery mechanism and format of these inputs (API, webhook, file)
2. How fast outcomes and transcripts arrive after a call
3. Whether past calls can be exported in bulk
4. Who generates call_id
5. Failure reporting: channel and latency
