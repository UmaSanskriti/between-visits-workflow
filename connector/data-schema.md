# Data Schema

Based on the logical model in infra spec §6.1, adapted for email (v0 stores these as JSON files per table). Field names are provisional until the Brain team confirms the final schema. The code in `infra/` still uses the call names (`call_attempt`, `transcript`, `call_clearance`); renaming them is future work.

## Contact lifecycle
```
NEW -> SOURCED -> VERIFIED -> READY -> IN_OUTREACH -> BOOKED | DECLINED | UNREACHABLE
Any state -> SUPPRESSED (terminal, on a do-not-contact request)
```

## Entities
| Entity | Key fields |
|--------|-----------|
| `contact` | id, full_name, credentials, specialty, practice_name, address, city, state, status, verification_status, created_at, updated_at |
| `channel` | id, contact_id, type (`email`/`web_form`), value, verified, send_clearance, source, source_url, retrieved_at. Phone stays as reference data only; it is never used to place calls. |
| `email_attempt` | id, contact_id, channel_id, thread_id, attempt_number, scheduled_at, sent_at, status, outcome, outcome_reason, failure_category, lost_at, provider, provider_message_id, script_version, subject |
| `reply_thread` | id, email_attempt_id, messages (direction, sent_at, sender_role, text), reply_class, language, created_at, delivery_status, delivered_at |
| `interview` | id, contact_id, email_attempt_id, scheduled_for, calendar_event_ref, status (`draft_pending_approval` until Anirudh approves) |
| `suppression` | id, identifier (email/contact), reason, source, created_at |
| `consent_record` | id, contact_id, channel_id, consent_type, evidence_ref, captured_at, revoked_at |
| `audit_log` | id, entity, entity_id, action, before, after, actor, timestamp |

`send_clearance` replaces `call_clearance`: default `not_cleared`, which is never emailed; set only by an authorized process (assumption: a basis for emailing the published address is recorded, see [compliance.md](compliance.md)).

## Email: `EmailRequest` (connector output)
```json
{
  "email_attempt_id": "ea_789",
  "contact_id": "c_123",
  "script_version": "v1",
  "attempt_number": 1,
  "segment": "solo_primary_care",
  "to": "info@examplefamilypractice.com",
  "from_name": "Anirudh Bagla",
  "reply_to": "<configured mailbox>",
  "subject": "30 minutes with Dr. Chen? (HBS research, not sales)",
  "body_text": "<hook + pitch + ask + AI disclosure + opt-out line + postal address>",
  "headers": {"In-Reply-To": null, "List-Unsubscribe": "<mailto:...>"},
  "variables_used": {"doctor": "Dr. Chen", "practice": "Example Family Practice", "hook": "new weight-management programme"},
  "fallbacks_applied": ["practice_size"],
  "approval": {"batch_id": "b_12", "approved_by": "Anirudh", "approved_at": null}
}
```

## Reply thread package (to Brain)
```json
{
  "idempotency_key": "email_attempt_789",
  "email_attempt_id": "ea_789",
  "contact_id": "c_123",
  "script_version": "v1",
  "outcome": "BOOKED",
  "failure_category": null,
  "lost_at": null,
  "sent_at": "2026-10-21T14:02:11Z",
  "messages": [
    {"direction": "outbound", "sent_at": "2026-10-21T14:02:11Z", "sender_role": "anirudh", "text": "..."},
    {"direction": "inbound", "sent_at": "2026-10-22T09:15:40Z", "sender_role": "office_manager", "text": "..."}
  ],
  "reply_class": "accepted"
}
```
The messages are the whole record; there is no audio and no tone or speaker analysis. Outbound-only threads (no reply) and bounces are still sent, with an empty inbound list.

## Outcomes
`BOOKED | INTERESTED_NO_TIME | CALL_BACK_LATER | REFERRED | NO_REPLY | AUTO_REPLY_ONLY | BOUNCED | DECLINED | NOT_A_FIT | DO_NOT_CONTACT | FAILED_TECHNICAL`

`CALL_BACK_LATER` keeps its playbook name; for email it means "write again later at the time they gave".

## Reply classes
`accepted | proposes_other_time | interested_no_time | referred_to_other_person | declined | not_a_fit | stop | auto_reply | bounce | unclear`

## Failure categories (playbook §10, adapted for email)
| Category | How to spot it | What may change |
|---|---|---|
| No reply | No inbound after the follow-up | Subject line, send day and time, opening line |
| Bounced / undeliverable | Hard bounce event | Contact data; never a template change |
| Auto-reply only | Out-of-office, no human reply | Send timing |
| Screened by front desk | Reply asks what this is or says the doctor does not take these | Opening; how the ask is framed; who it is addressed to |
| Pitch did not land | Doctor or office manager declined | Value proposition, proof points, pitch order |
| Interested, no time agreed | Liked it, no slot worked | Slots offered; meeting format; time of day |
| Write again later | Asked for a later date | Schedule the follow-up; no template change |
| Not a fit | Hospital-owned, or no GLP-1 patients | Remove practice; tighten list filter |

"Hung up at AI disclosure" and "Stuck in phone tree" no longer apply. `lost_at` is the stage where it was lost: `delivery`, `no_reply`, `reply_declined` or `scheduling`.

## Audit and error records
See [outputs/audit-log-and-errors.md](outputs/audit-log-and-errors.md).
