# Test Plan

Use fictional contacts only. Never test with real phone numbers. The tests live in `tests/test_connector.py` (t_01 to t_08 and the edge cases below), `tests/test_acceptance.py` (infra AC-1 to AC-10) and `tests/test_playbook.py` (alignment with the Brain playbook). All run with a fixed clock (Tuesday 10:00 ET) and mock Voice and Brain.

## Sample contacts

| ID | Case | Expected connector behavior |
|----|------|-----------------------------|
| t_01 | Complete record, solo primary care | Full personalized script, segment `solo_primary_care` |
| t_02 | Missing doctor name | Fallback ("the doctor"); recorded in `fallbacks_applied` |
| t_03 | Missing specialty | Generic pitch angle; no literal placeholder in output |
| t_04 | Name with hard pronunciation | Phonetic hint passed if Voice supports it |
| t_05 | Suppressed / do-not-contact | Excluded from the queue; skip reason logged |
| t_06 | Time zone outside the calling window | Deferred, not placed |
| t_07 | Small group practice | Different pitch angle than solo |
| t_08 | Missing phone | Error report entry; not queued |

## Outcome edge cases

| Case | Expected |
|------|----------|
| Voicemail | Playbook's approved message left (or hang up if the flag is off); outcome `VOICEMAIL`; transcript record still sent to Brain |
| No answer | Counted in the summary; retry the next business day at a rotating hour |
| "Don't call again" mid-call | Call ends politely; suppression written; no further attempt ever scheduled |
| Declines recording | End politely; `DECLINED_RECORDING`; no retry |
| Not a fit (hospital-owned, no GLP-1) | `NOT_A_FIT`; no retry; list filter tightened |
| Callback requested | Retry at the requested time; up to 2 extra attempts |
| Malformed transcript | Error report; not forwarded |
| Transcript appears to contain patient data | Held from the Brain; alert raised |
| Booked | `interview` drafted once as `draft_pending_approval`; counted by script version |
| Missing required field (e.g. `callback_number`) | Call blocked; error report |

## Infra acceptance criteria the connector must satisfy
- AC-1 suppressed contact is not enqueued
- AC-2 `call_clearance = not_cleared` is blocked
- AC-3 outside the window is deferred
- AC-4 mid-call opt-out ends the call and suppresses
- AC-5 duplicate webhook yields one update and one transcript
- AC-6 Brain down means no call and a retry
- AC-7 failed transcript delivery lands in the DLQ with an alert
- AC-8 booked yields exactly one interview and handoff
- AC-9 kill switch yields zero new calls
- AC-10 first utterance carries the AI and recording disclosure

## Checks
- [ ] No unfilled placeholder ever reaches Voice
- [ ] The LOCKED opening is verbatim in every `first_message`
- [ ] Every call has a `script_version` and an audit log entry
- [ ] Outcome summary conversion matches the raw counts
- [ ] Declines and no-answers reach the Brain
- [ ] Gate escapes are 0 on every run
