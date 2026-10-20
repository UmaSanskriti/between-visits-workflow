# Test Plan

Use fake doctors only. Never test with real phone numbers.

## Sample doctors

| ID | Case | Expected connector behavior |
|----|------|-----------------------------|
| t_01 | Complete record, solo primary care | Full personalized script, segment `solo_primary_care` |
| t_02 | Missing name | Fallback greeting; flagged in `fallbacks_applied` |
| t_03 | Missing specialty | Generic pitch angle; no literal `{specialty}` in output |
| t_04 | Name with hard pronunciation | Phonetic hint passed if Voice supports it |
| t_05 | `dnc = true` | Excluded from queue; logged |
| t_06 | Time zone outside calling hours | Deferred in queue |
| t_07 | Small group practice | Different pitch angle than solo |
| t_08 | Missing phone | Error report entry; not queued |

## Outcome edge cases

| Case | Expected |
|------|----------|
| Voicemail | Status recorded; transcript package still sent to Brain |
| No answer | Counted in outcome summary; retry per Voice rules |
| Hostile response / "stop calling" | Opt-out flows to DNC; transcript flagged |
| Callback requested | Captured with time if given |
| Malformed transcript | Error report; not forwarded |
| Booked | Slot and email stored; counted by script version |
| Missing required field (e.g. disclosure) | Call blocked; error report |

## Checks

- [ ] No unfilled `{placeholder}` ever reaches Voice
- [ ] Every call has a script_version and an audit log entry
- [ ] Outcome summary conversion matches raw counts
- [ ] Declines and no-answers reach the Brain
