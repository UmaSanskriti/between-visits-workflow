# Data Schema

Draft record shapes. Field names are proposals until agreed with Voice and Brain.

## Doctor
```json
{
  "doctor_id": "d_001",
  "name": "Priya Patel",
  "preferred_name": "Dr. Patel",
  "practice_name": "Patel Family Medicine",
  "phone": "+16175550100",
  "specialty": "family medicine",
  "practice_size": "solo",
  "location": "Boston, MA",
  "time_zone": "America/New_York",
  "lead_source": "directory",
  "dnc": false,
  "consent_status": "none"
}
```

## Script (connector output, one per call)
```json
{
  "call_id": "c_001",
  "doctor_id": "d_001",
  "script_version": "v1.0",
  "segment": "solo_primary_care",
  "first_message": "...",
  "system_prompt": "...",
  "variables_used": {"doctor_name": "Dr. Patel", "specialty": "family medicine"},
  "fallbacks_applied": ["ehr"],
  "fallback_text": "..."
}
```

## Call
```json
{
  "call_id": "c_001",
  "doctor_id": "d_001",
  "script_version": "v1.0",
  "status": "booked",
  "timestamp": "2026-10-20T14:00:00Z",
  "duration_sec": 142,
  "booked_slot": "2026-10-23T15:00:00-04:00",
  "contact_email": "...",
  "audio_url": "..."
}
```

## Transcript package (to Brain)
```json
{
  "call_id": "c_001",
  "script_version": "v1.0",
  "outcome": "booked",
  "doctor_meta": {"segment": "solo_primary_care", "specialty": "family medicine", "practice_size": "solo"},
  "transcript": [{"speaker": "agent", "text": "..."}, {"speaker": "doctor", "text": "..."}],
  "pii_handling": "redacted"
}
```

## Audit log entry
`{timestamp, event, call_id, actor, payload_ref, result}`

## Error report entry
`{timestamp, call_id, error_type, field, detail}` where `error_type` is one of `missing_field | delivery_failed | malformed_transcript`.
