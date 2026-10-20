# AB Calling Agent Infrastructure: Requirements Specification

|  |  |
| --- | --- |
| **Document status** | Draft v0.1, for engineering review |
| **Owner** | Product / founding team |
| **Audience** | Engineers building the infra; reviewers (legal, Brain team) |
| **Last updated** | October 20, 2026 |

> **Not legal advice.** The compliance requirements in section 7 are engineering guardrails, not a legal opinion. Counsel must review consent language, call flows, and recording practices before any live calls are placed.

---

## 1. Purpose and background

AB is an early-stage company that helps doctors provide better care to GLP-1 patients by connecting them with nutritionists and other coaches. We are in the **discovery phase** and want to interview as many doctors and independent practitioners in the Boston area as possible.

This document specifies the **infrastructure ("infra")** for an automated outreach loop: sourcing doctor contact details, placing AI-agent calls to request a discovery interview, logging outcomes, and handing call transcripts to a separate system called **the Brain**.

The project is split into two parts:

| Part | Responsibility | In this document? |
| --- | --- | --- |
| **Infra** | Contact sourcing, contact storage, compliance gating, call orchestration, outcome logging, transcript pipeline, interview booking handoff | **Yes** |
| **Brain** | Writes call scripts and conversation strategy; analyzes transcripts | **No** (interface only, see section 8) |

## 2. Goals, non-goals, and success metrics

### 2.1 Goals

1. Build a reliable, auditable pipeline from "doctor identified" to "interview booked or contact closed."
2. Keep every contact, call, and transcript traceable to its source and timestamp.
3. Make compliance checks a **hard gate** in the calling path, not an afterthought.
4. Decouple the Brain from the infra through a small, versioned interface so either side can change independently.

### 2.2 Non-goals (this phase)

- Writing scripts or conversation logic (Brain).
- Analyzing transcripts or extracting insights (Brain).
- **Target list definition and prioritization (backlogged).** See section 2.3.
- Email outreach campaigns.
- Outreach outside the Boston area.
- A user-facing web UI beyond a minimal admin/ops view (Could-have, see FR-OPS).

### 2.3 Backlogged item: target list

Defining *who* qualifies as a target (specialties, practice types, inclusion and exclusion criteria) is deferred. Until it is defined, the sourcing step accepts a **seed input** (see SRC-001) such as a CSV of names or a simple search profile like `{city: "Boston", specialties: [...]}`. The design must allow a future target-list component to feed the same input without changes downstream.

### 2.4 Success metrics

| Metric | Definition | Initial target |
| --- | --- | --- |
| Valid contacts sourced | Contacts passing verification (VER rules) | TBD with team |
| Call connect rate | Calls answered by a human or voicemail / calls placed | Track; no target yet |
| Interviews booked | Calls with outcome `BOOKED` | Track; no target yet |
| Transcript delivery rate | Completed calls whose transcript reached the Brain | ≥ 99% |
| Compliance gate escapes | Calls placed that failed a required gate | **0** |

## 3. Definitions

| Term | Meaning |
| --- | --- |
| Contact | A doctor or independent practitioner we want to interview |
| Channel | A way to reach a contact: `phone`, `email`, or `web_form` |
| Call attempt | One outbound call placed to one phone channel |
| Outcome | The classified result of a call attempt (section 6.2) |
| Suppression | A do-not-contact entry that blocks all future outreach |
| Brain | The separate system that supplies scripts and consumes transcripts |
| Voice provider | The third-party telephony and voice-agent service that places calls |

## 4. System overview

```
[Seed input] -> Sourcing -> Clean/Verify -> Contact DB
                                               |
                                               v
                          Pre-call compliance gate (hard stop)
                                               |
                       Script <- Brain         v
                                         Call orchestrator -> Voice provider
                                               |
                                               v
                                   Outcome logging + Transcript store
                                         |                 |
                                         v                 v
                                Booking handoff     Transcript -> Brain
```

### 4.1 Recommended architecture (guidance, not mandate)

- Modular services or workers connected by a **durable job queue**, so each stage is independently retryable.
- A **relational database** (for example PostgreSQL) as the system of record.
- A **voice provider abstraction layer**: no business logic may depend directly on a specific vendor SDK. Wrap the provider behind an interface (`place_call`, `get_call_status`, `handle_webhook`) so it can be swapped or mocked.
- Object storage for recordings, with signed, expiring URLs.
- Infrastructure as code, containerized services, and separate dev / staging / prod environments.

## 5. Functional requirements: sourcing

Priority key: **M** = Must, **S** = Should, **C** = Could (MoSCoW).

| ID | Requirement | Pri |
| --- | --- | --- |
| SRC-001 | The system shall accept a seed input (CSV upload and/or a search profile) describing which doctors to source. | M |
| SRC-002 | The system shall collect, per contact: full name, credentials, specialty, practice name, practice address, and at least one channel (phone, email, or web form URL). | M |
| SRC-003 | Every collected field shall store its **source** (URL or dataset name) and **retrieval timestamp**. | M |
| SRC-004 | Sourcing shall use public or properly licensed data only, and shall respect each site’s terms of service and `robots.txt`. | M |
| SRC-005 | The system shall support sourcing from multiple providers through a common adapter interface so sources can be added without schema changes. | S |
| SRC-006 | For practices with no phone or email but a contact form, the system shall create a `web_form` channel storing the form URL and field schema. | S |
| SRC-007 | Sourcing jobs shall be **idempotent**: re-running the same input must not create duplicate contacts. | M |
| SRC-008 | Sourcing shall be rate limited per domain and shall back off on HTTP 429/5xx responses. | M |

### 5.1 Clean and verify

| ID | Requirement | Pri |
| --- | --- | --- |
| VER-001 | Phone numbers shall be normalized to E.164 format. | M |
| VER-002 | The system shall classify each phone number by line type (landline, mobile, VoIP, unknown) via a lookup service and store the result. Line type feeds the compliance gate (CMP-003). | M |
| VER-003 | Emails shall be syntax-validated and checked for a resolvable domain. | S |
| VER-004 | Duplicate contacts shall be detected using normalized name + practice + phone/email, and merged or flagged for review. Merges shall preserve all source records. | M |
| VER-005 | Each contact shall carry a `verification_status` (`unverified`, `verified`, `failed`) with a reason. Only `verified` contacts are eligible for calling. | M |
| VER-006 | Contacts shall be matched against the suppression list at ingest and again before each call. | M |

### 5.2 Web form submission

| ID | Requirement | Pri |
| --- | --- | --- |
| FRM-001 | For `web_form` channels, the system shall be able to submit a message asking the doctor to reply, using text approved by the team (templated, versioned). | S |
| FRM-002 | The system shall **not** attempt to bypass CAPTCHAs or other bot protections. Blocked forms shall be routed to a manual queue. | M |
| FRM-003 | Each submission shall be logged with timestamp, form URL, submitted payload, and result (success, blocked, error). | M |
| FRM-004 | Form submission shall be behind a feature flag, disabled by default until terms-of-service review is complete. | M |

## 6. Functional requirements: contact store, calling, and logging

### 6.1 Contact database

| ID | Requirement | Pri |
| --- | --- | --- |
| DB-001 | The Contact DB shall be the single source of truth for all contacts, channels, call attempts, outcomes, and suppressions. | M |
| DB-002 | All state transitions shall be written to an append-only **audit log** (who/what, before, after, timestamp). | M |
| DB-003 | Schema changes shall be applied through versioned migrations. | M |

**Contact lifecycle**

```
NEW -> SOURCED -> VERIFIED -> READY -> IN_OUTREACH -> BOOKED
                                                  \-> DECLINED
                                                  \-> UNREACHABLE
Any state -> SUPPRESSED (terminal, set on do-not-contact request)
```

**Core entities (logical model)**

| Entity | Key fields |
| --- | --- |
| `contact` | id, full_name, credentials, specialty, practice_name, address, city, state, status, verification_status, created_at, updated_at |
| `channel` | id, contact_id, type (`phone`/`email`/`web_form`), value, line_type, verified, source, source_url, retrieved_at |
| `call_attempt` | id, contact_id, channel_id, scheduled_at, started_at, ended_at, status, outcome, outcome_reason, provider, provider_call_id, script_version, recording_ref, transcript_id |
| `transcript` | id, call_attempt_id, utterances (JSON), language, created_at, delivery_status, delivered_at |
| `interview` | id, contact_id, call_attempt_id, scheduled_for, calendar_event_ref, status |
| `form_submission` | id, contact_id, channel_id, template_version, payload, result, submitted_at |
| `suppression` | id, identifier (phone/email/contact), reason, source, created_at |
| `consent_record` | id, contact_id, channel_id, consent_type, evidence_ref, captured_at, revoked_at |
| `audit_log` | id, entity, entity_id, action, before, after, actor, timestamp |

### 6.2 Call orchestration

| ID | Requirement | Pri |
| --- | --- | --- |
| CALL-001 | A scheduler shall select `READY` contacts, run the pre-call gate (section 7), then enqueue a call job. | M |
| CALL-002 | Before each call, the orchestrator shall fetch the current script from the Brain (section 8) and store the `script_version` on the call attempt. | M |
| CALL-003 | Calls shall be placed through the voice provider abstraction. | M |
| CALL-004 | The system shall limit concurrency and calls per hour via configuration. | M |
| CALL-005 | Call state shall be driven by provider webhooks, with a polling fallback for missed webhooks. Webhook handlers shall verify signatures and be idempotent. | M |
| CALL-006 | The system shall detect and classify voicemail, no answer, busy, and invalid number. | M |
| CALL-007 | **Retry policy** (configurable): default max 3 attempts per contact, spaced at least 1 business day apart, at varied times of day. No retry after `DECLINED`, `DO_NOT_CONTACT`, or `WRONG_NUMBER`. | M |
| CALL-008 | The agent shall be able to hand off to a human or end the call gracefully on request, on repeated misunderstanding, or on any do-not-contact request. | M |
| CALL-009 | A global **kill switch** shall halt all new calls immediately without a deploy. | M |

**Call outcomes (enumeration)**

| Outcome | Meaning | Next action |
| --- | --- | --- |
| `BOOKED` | Interview time agreed | Create `interview`, contact -> `BOOKED` |
| `CALLBACK_REQUESTED` | Doctor asked to be called later | Schedule per requested time |
| `NO_ANSWER` | No pickup | Retry per CALL-007 |
| `VOICEMAIL` | Reached voicemail | Retry per CALL-007 (see CMP-008 on messages) |
| `GATEKEEPER_BLOCKED` | Staff declined to connect | Retry or manual review |
| `DECLINED` | Doctor not interested | Contact -> `DECLINED` |
| `DO_NOT_CONTACT` | Explicit opt-out | Contact -> `SUPPRESSED` immediately |
| `WRONG_NUMBER` | Number not for this contact | Mark channel failed |
| `FAILED_TECHNICAL` | Provider or system error | Retry with backoff; alert if repeated |

### 6.3 Outcome logging and booking

| ID | Requirement | Pri |
| --- | --- | --- |
| LOG-001 | Every call attempt shall record outcome, duration, timestamps, provider call ID, and script version. | M |
| LOG-002 | Outcome classification may be produced by the agent, but the raw transcript shall always be retained so classifications can be audited or corrected. | M |
| LOG-003 | A `BOOKED` outcome shall create an `interview` record and trigger the booking handoff. | M |
| LOG-004 | The booking handoff shall write to a calendar or scheduling tool (**decision pending**, see section 12) and send the doctor a confirmation where a verified email exists. | S |
| LOG-005 | The system shall expose daily summary counts by outcome (CLI, SQL view, or simple dashboard). | S |

### 6.4 Operations (minimal admin view)

| ID | Requirement | Pri |
| --- | --- | --- |
| OPS-001 | Operators shall be able to pause/resume calling, add a suppression entry, and view a contact’s full history. | S |
| OPS-002 | A manual review queue shall list blocked forms, ambiguous outcomes, and duplicate-merge candidates. | C |

## 7. Compliance and safety requirements

These are **hard gates**. A call shall not be placed if any required gate fails, and the failure reason shall be logged.

| ID | Requirement | Pri |
| --- | --- | --- |
| CMP-001 | **Pre-call gate:** before every call, verify (a) contact not suppressed, (b) channel verified, (c) within allowed calling window, (d) consent/legal basis flag set per CMP-003, (e) kill switch off. | M |
| CMP-002 | **Calling window:** calls shall be placed only within a configurable window evaluated in the recipient’s local time zone. Default: weekdays, 9:00 to 17:00 local. Counsel to confirm. | M |
| CMP-003 | **Consent / legal-basis flag:** each phone channel shall carry a `call_clearance` status set only by an authorized process (for example after counsel sign-off or recorded consent). The default is `not_cleared`, and `not_cleared` channels shall never be dialed. This keeps the legal decision out of code while enforcing it in code. | M |
| CMP-004 | **AI disclosure:** the agent’s first utterance shall identify the caller as an automated AI assistant calling on behalf of AB. | M |
| CMP-005 | **Recording disclosure:** if a call is recorded, the agent shall disclose recording at the start of the call and shall stop or discard the recording if the recipient objects. Massachusetts is generally understood to require all-party consent for recording; counsel to confirm. | M |
| CMP-006 | **Opt-out:** any request to stop calling (at any point in the call) shall be honored immediately: end the call politely, write a suppression entry in the same transaction, and confirm in the audit log. | M |
| CMP-007 | **Caller ID:** outbound calls shall present a valid, call-back-capable number belonging to AB, and use caller-ID authentication supported by the voice provider (STIR/SHAKEN attestation). | M |
| CMP-008 | **Voicemail:** the system shall not leave a voicemail unless explicitly enabled and approved; default is hang up and log `VOICEMAIL`. | M |
| CMP-009 | **No patient data:** scripts and the agent shall not solicit patient-identifiable information. If a transcript appears to contain it, the transcript shall be flagged for review and excluded from delivery until cleared. | M |
| CMP-010 | **Consent and clearance evidence** shall be stored with timestamp and source (`consent_record`). | M |

## 8. Brain interface (integration contract)

The Brain is out of scope, but the interface is in scope. The interface shall be **versioned** (`/v1/...`) and contract-tested. Payloads below are illustrative; the Brain team and infra team shall agree on the final schema.

### 8.1 Script retrieval (infra -> Brain)

`POST /v1/scripts/resolve`

```json
{
  "campaign_id": "ab-discovery-2026-q4",
  "contact": {
    "contact_id": "c_123",
    "specialty": "endocrinology",
    "practice_name": "Example Medical Group"
  },
  "attempt_number": 1
}
```

Response:

```json
{
  "script_id": "s_456",
  "script_version": "2026-10-20.3",
  "opening": "...",
  "turns_or_prompt": "...",
  "required_disclosures": ["ai_identity", "recording"],
  "booking_instructions": { "interview_length_minutes": 20 }
}
```

| ID | Requirement | Pri |
| --- | --- | --- |
| BRN-001 | The infra shall treat the script as opaque content plus metadata, and shall **always prepend** the compliance disclosures required by CMP-004 and CMP-005 regardless of script content. | M |
| BRN-002 | If the Brain is unreachable, the call shall **not** proceed with a stale or default script; the job is retried with backoff. | M |
| BRN-003 | Script requests shall use timeouts, retries with exponential backoff and jitter, and a circuit breaker. | M |

### 8.2 Transcript delivery (infra -> Brain)

`POST /v1/transcripts`

```json
{
  "idempotency_key": "call_attempt_789",
  "call_attempt_id": "ca_789",
  "contact_id": "c_123",
  "script_version": "2026-10-20.3",
  "outcome": "BOOKED",
  "started_at": "2026-10-21T14:02:11Z",
  "ended_at": "2026-10-21T14:05:40Z",
  "utterances": [
    { "speaker": "agent", "start_ms": 0, "text": "..." },
    { "speaker": "doctor", "start_ms": 4200, "text": "..." }
  ],
  "recording_ref": "signed-url-or-object-key"
}
```

| ID | Requirement | Pri |
| --- | --- | --- |
| BRN-004 | Every completed call shall produce a transcript record stored in the infra DB **before** delivery is attempted. | M |
| BRN-005 | Delivery shall be at-least-once with an **idempotency key**, with retries and a dead-letter queue for failures. | M |
| BRN-006 | Delivery status (`pending`, `delivered`, `failed`) shall be tracked per transcript and alert when items remain `failed` beyond a threshold. | M |
| BRN-007 | Transcripts flagged under CMP-009 shall not be delivered until cleared. | M |

## 9. Non-functional requirements

| ID | Category | Requirement |
| --- | --- | --- |
| NFR-001 | Reliability | Every stage uses queues with retries, exponential backoff, and dead-letter handling. No lost jobs on restart. |
| NFR-002 | Idempotency | All job handlers and webhook handlers are safe to run more than once. |
| NFR-003 | Security | Secrets in a secrets manager, never in code or logs. Least-privilege access. TLS in transit. Encryption at rest for the DB, recordings, and transcripts. |
| NFR-004 | Privacy | Contact data is business-contact PII. Define retention periods (**TBD**) and support deletion on request, including recordings and transcripts. Mask phone/email in logs. |
| NFR-005 | Observability | Structured logs with correlation IDs (contact_id, call_attempt_id). Metrics for each stage and queue depth. Alerts for failure spikes, stuck queues, and provider errors. |
| NFR-006 | Configurability | Calling window, retry policy, concurrency, and feature flags are configuration, not code. |
| NFR-007 | Testability | Voice provider and Brain are mockable. A **dry-run mode** runs the full pipeline without placing real calls. |
| NFR-008 | Maintainability | Code review required, linting and formatting in CI, README and runbook for operators. |
| NFR-009 | Auditability | Any call can be reconstructed: who was called, when, why it was allowed, which script ran, and what happened. |
| NFR-010 | Cost control | Per-day spend and call caps with alerts at thresholds. |

## 10. Testing and acceptance

### 10.1 Test strategy

- **Unit tests** for normalization, dedupe logic, gate rules, retry scheduling, and state transitions.
- **Integration tests** with mocked voice provider and mocked Brain.
- **Contract tests** for the Brain interface (section 8).
- **End-to-end dry-run** from seed input to transcript delivery without live calls.
- A **staged live test** to team-owned phone numbers only, before any external contact is dialed.

### 10.2 Key acceptance criteria

| ID | Scenario |
| --- | --- |
| AC-1 | **Given** a contact on the suppression list, **when** the scheduler runs, **then** no call is enqueued and the skip reason is logged. |
| AC-2 | **Given** a channel with `call_clearance = not_cleared`, **when** the pre-call gate runs, **then** the call is blocked. |
| AC-3 | **Given** the current time is outside the calling window in the recipient’s time zone, **when** a call is due, **then** it is deferred, not placed. |
| AC-4 | **Given** the doctor says “please don’t call again” mid-call, **when** the agent hears it, **then** the call ends politely, a suppression entry exists, and no further attempt is ever scheduled. |
| AC-5 | **Given** a completed call, **when** the provider webhook arrives twice, **then** exactly one `call_attempt` update and one transcript are recorded. |
| AC-6 | **Given** the Brain is down, **when** a call is due, **then** no call is placed and the job retries with backoff. |
| AC-7 | **Given** transcript delivery fails, **when** retries exhaust, **then** the item lands in the dead-letter queue and an alert fires. |
| AC-8 | **Given** a call outcome of `BOOKED`, **when** processed, **then** an `interview` record exists and the booking handoff runs once. |
| AC-9 | **Given** the kill switch is turned on, **when** the scheduler next runs, **then** zero new calls are placed. |
| AC-10 | **Given** any call, **when** it starts, **then** the first utterance includes AI and (if recording) recording disclosures. |

## 11. Milestones (suggested)

| Milestone | Deliverables |
| --- | --- |
| M0: Foundations | Repo, CI, environments, DB schema + migrations, config, audit log, mock provider and mock Brain |
| M1: Sourcing | Seed ingest, first source adapter, normalization, line-type lookup, dedupe, verification status |
| M2: Compliance gate | Suppression list, calling-window logic, clearance flag, kill switch, gate tests |
| M3: Calling | Provider adapter, scheduler, webhooks, outcome classification, retries, dry-run |
| M4: Brain handoff | Script retrieval, transcript pipeline with idempotency and DLQ, contract tests |
| M5: Booking and hardening | Calendar handoff, daily summaries, alerts, staged live test, runbook |
| Later | Form submission (after ToS review), admin UI, target list component |

## 12. Open questions and decisions needed

| # | Question | Owner | Blocks |
| --- | --- | --- | --- |
| 1 | Voice provider choice (build vs. buy a voice-agent platform) | Eng + founders | M3 |
| 2 | Legal position on AI-voice calls to practice numbers (consent / clearance process) | Counsel | M2, any live calls |
| 3 | Recording policy: record all calls, or transcript only? | Founders + counsel | CMP-005 |
| 4 | Calendar / scheduling tool for booked interviews | Founders | LOG-004 |
| 5 | Contact data sources and budget for lookup services | Eng | M1 |
| 6 | Data retention periods for contacts, recordings, transcripts | Founders + counsel | NFR-004 |
| 7 | Target call volume per week and daily spend cap | Founders | CALL-004, NFR-010 |
| 8 | Final Brain API schema and ownership | Brain team | M4 |
| 9 | Seed input format until the target list is defined | Founders | SRC-001 |

## 13. Assumptions

- Contacts are business contacts (doctors and practices), not patients.
- The Brain team will deliver a stable interface per section 8.
- Initial scope is the Boston area only.
- The first live calls go to internal test numbers.
- Engineering stack choices are left to the implementing team unless constrained by the open questions above.