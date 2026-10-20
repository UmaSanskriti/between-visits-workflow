# v0 scope: what we build to test the MVP flow

**Goal.** Prove the end-to-end loop from "doctor identified" to "interview booked or contact closed" runs,
is gated, is auditable, and is visible, before any vendor is chosen and before any real number is dialed.
Everything in the spec's milestones M0 to M4 that can run without a vendor or counsel sign-off is in v0.
The two external boundaries, the voice provider and the Brain, are mocked behind the interfaces the spec asks for.

**Definition of done for v0** (all met as of 2026-10-20):

1. `pytest` passes the ten acceptance criteria in spec section 10.2, the connector test plan, and the playbook
   alignment checks (locked opening, slots, callback number, two attempts, approval hold).
2. The *Dry-run pipeline* GitHub Actions workflow runs on demand and commits its state.
3. The GitHub Pages dashboard shows the latest run: funnel, gate decisions, outcomes, transcript delivery, errors.
4. Gate escapes are zero on every run and the run fails (exit code 3, alert) if they are not.

## In scope

| Area | Requirement ids | v0 implementation | Real or mock |
|---|---|---|---|
| Seed input | SRC-001, SRC-003, SRC-007 | `CsvSeedAdapter`; every channel stores source and retrieval time; deterministic ids make re-runs no-ops | Real |
| Source adapter interface | SRC-005 | `SourceAdapter` protocol; one implementation | Real (interface) |
| Normalization and line type | VER-001, VER-002 | US E.164 normalizer; `LineTypeLookup` protocol with a seeded mock | Real / mock lookup |
| Email check | VER-003 | Syntax + domain shape; no DNS lookup | Partial |
| Dedupe | VER-004 | Name+practice and phone keys; later record flagged `duplicate_of`, never dialed, listed in the error report for review | Real (flag, not merge) |
| Verification status, suppression match | VER-005, VER-006 | `verification_status` with reason; suppression checked at verify and again in the gate | Real |
| System of record, audit log | DB-001, DB-002 | JSON files per table, append-only `audit_log.jsonl` with before/after and correlation ids | Real (files, not PostgreSQL) |
| Lifecycle | spec 6.1 | NEW → SOURCED → VERIFIED → READY → IN_OUTREACH → BOOKED / DECLINED / UNREACHABLE; SUPPRESSED from any state | Real |
| Scheduler and gate | CALL-001, CMP-001, CMP-002, CMP-003 | Every open contact is gated each run; window evaluated in the contact's time zone; `call_clearance` only set from `seeds/clearances.csv`; consent record per clearance | Real |
| Kill switch, pause | CALL-009, OPS-001 | `BV_KILL_SWITCH` env/repo variable; `pause`/`resume` state flag | Real |
| Script retrieval | CALL-002, BRN-001, BRN-002, BRN-003 | `BrainClient` protocol; retries; Brain down means no call and a 15-minute backoff; the LOCKED opening is always prepended | Real logic / mock Brain serving playbook v1 |
| Personalization | connector `script-variables.md`, playbook 4 | Segment from specialty × practice size; pitch angle from Brain guidance; hook, four slots from a calendar stub, callback number; fallbacks recorded; unfilled placeholders or missing required fields (including the callback number) block the call | Real / stub calendar |
| Voice provider | CALL-003, CALL-005, CALL-006 | `VoiceProvider` protocol; mock places "calls", classifies outcomes by scenario, signs webhooks (HMAC), sends each webhook twice | Mock |
| Retry policy | CALL-007, playbook 5.7 | Max 2 (playbook), next business day, rotating hours 10/14/16 local, callback at requested time, technical failures after 60 minutes, no retry after DECLINED / NOT_A_FIT / DECLINED_RECORDING / DO_NOT_CONTACT / WRONG_NUMBER | Real |
| Opt-out | CMP-006, CALL-008 | Opt-out phrases in the transcript force DO_NOT_CONTACT; suppression (contact + phone) written in the same save | Real (phrase heuristic) |
| Caller id, voicemail | CMP-007, CMP-008, playbook 6 | Fictional outbound number constant; the playbook's approved voicemail text is filled and left (feature flag on; off means hang up and log) | Stub / real text |
| Patient data | CMP-009 | Regex heuristic; flagged transcripts are held and alerted, never delivered | Real (heuristic) |
| Outcome logging, booking | LOG-001..004, playbook 9-10 | Outcome, failure category, duration, timestamps, provider id, script version per attempt; interview drafted as `.ics` exactly once and held as `draft_pending_approval` until `approve` | Real / file handoff |
| Transcript pipeline | BRN-004..007 | Transcript stored first; redacted package with idempotency key; 3 tries; dead-letter queue; alert | Real logic / mock Brain |
| Summaries | LOG-005, connector output 6 | Counts by outcome, script version, segment; funnel; delivery rate; gate escapes | Real |
| Ops view | OPS-001, OPS-002 (partial) | Dashboard on GitHub Pages; contact history; error report doubles as the review queue | Real |
| Observability | NFR-005 (partial), NFR-009 | Correlation ids on audit entries; alerts list; any call reconstructable from attempt + script + gate decision + audit | Partial |
| Configuration | NFR-006 | `config.toml` + env overrides | Real |
| Testability | NFR-007, section 10.1 | Fixed clock, mock provider, mock Brain; unit, integration, and end-to-end dry run in CI | Real |
| CI | NFR-008 | ruff + pytest + smoke run on every push | Real |

## Alignment with the Brain playbook (v1)

`clinic-outreach-brain.md` arrived on 2026-10-20 and is more specific than the spec draft in places. v0 follows
the playbook where the two differ, because it is the document Anirudh approves scripts against:

| Topic | Spec draft | Playbook v1 | v0 does |
|---|---|---|---|
| Ask | 20-minute interview | 30-minute conversation | 30 minutes (`[booking].interview_length_minutes`) |
| Attempts | max 3 | max 2 unless call back requested | 2 (`[retry].max_attempts`), and up to 2 more only after a call-back request (`callback_extra_attempts`) |
| Voicemail | off unless approved | approved voicemail script in section 6 | on, with that text |
| Opening | AI + recording disclosure, wording TBD | LOCKED two sentences | reproduced verbatim in `compliance.py` |
| Who we ask for | the doctor | office manager / whoever keeps the schedule | route line after the opening |
| Variables | name, practice, specialty, … | plus `hook`, `slot_1..4`, `callback_number` | all three added; callback number is required and blocks calls when empty |
| Outcomes | nine | adds `not a fit`, `declined recording`, failure categories | `NOT_A_FIT`, `DECLINED_RECORDING`, `failure_category` per attempt |
| Booking | create interview, trigger handoff | draft invite held for Anirudh's one-click approval | `draft_pending_approval` + `approve` command |
| Daily volume | configurable cap | Vapi free number: 10 calls/day | cap stays 50 for the dry run; set to 10 before live |
| Email | non-goal | invite cover note and cold email, approval-gated | not built (still out of scope) |
| Target list | backlogged | section 3 defines it (independent FM/IM in Greater Boston with GLP-1 on site) | seed-only; a sourcing filter comes with the first real adapter |

Open items the playbook itself lists before live calls: callback number, calendar system, visit days, who listens
to the first live call. The pipeline surfaces the first one on the dashboard.

## Out of scope for v0 (and why)

| Item | Why it waits |
|---|---|
| Real voice provider (CALL-003 live, CMP-007 STIR/SHAKEN) | Open question 1: build vs. buy not decided. The `VoiceProvider` interface is the seam. |
| Live calls of any kind, even to team numbers | Open question 2: counsel must confirm the consent/clearance process first. The clearance file and gate are ready for it. |
| Web sourcing adapters, rate limiting, robots.txt (SRC-002 beyond CSV, SRC-004, SRC-008) | Open question 5: sources and budget undecided; target list backlogged (spec 2.3). |
| Real line-type lookup (VER-002) | Needs a vendor and budget. The mock reads seeded hints. |
| Web form submission (FRM-001..004) | Behind terms-of-service review; feature flag stays off. |
| PostgreSQL, migrations, durable job queue (DB-003, NFR-001, 4.1) | Not needed to prove the flow. The `Store` is the seam; stages are already idempotent. |
| Calendar integration and confirmation email (LOG-004) | Open question 4. v0 writes `.ics` files and records what it would send. |
| Recording storage with signed URLs (4.1, NFR-003) | No recordings exist in a dry run; `recording_ref` is a mock pointer. |
| Secrets manager, encryption at rest, retention/deletion (NFR-003, NFR-004) | No real data yet. Masking at export is in place; retention is open question 6. |
| Contract tests against the real Brain (BRN, section 10.1) | Open question 8: the Brain team owns the final schema. The mock implements the spec's illustrative payloads. |
| Private dashboard | GitHub Pages on a public repo is public. Fine for fictional data; must change before real data. |

## How the MVP flow is tested

1. **Automated**: `tests/test_acceptance.py` runs AC-1 to AC-10 against the full pipeline with a fixed clock
   (Tuesday 10:00 ET). `tests/test_connector.py` covers t_01 to t_08 and every outcome edge case in
   `connector/test-plan.md`. `tests/test_verify.py` covers normalization, dedupe, masking, idempotent ingest.
2. **In GitHub**: *Dry-run pipeline* workflow with inputs for simulated time, reset, and Brain-down. Repo variable
   `BV_KILL_SWITCH` proves the kill switch works without a deploy (AC-9).
3. **By eye**: the dashboard shows the funnel, the gate decisions with reasons, every attempt with its script version,
   transcript delivery status, the error report, the DLQ, and a per-contact history.

## Interface decisions v0 makes provisionally

These answer some of the `[CONFIRM]` items in `connector/` so the code can exist. They are easy to change.

| Question | v0 answer |
|---|---|
| Who generates `call_id`? | Infra. `call_attempt_id` is deterministic from contact id + attempt number. |
| Who owns the AI and recording disclosure lines? | Infra. The LOCKED opening is prepended to whatever the Brain sends (BRN-001). |
| Script delivery to Voice | A `CallRequest` object (first message = opening + route, system prompt = pitch, ask, close, objections, voicemail; metadata). Maps to a Vapi assistant payload later. |
| Brain template format | Placeholders in `{snake_case}`; required: `ask`, `caller_identity`, `script_version`. |
| Raw or redacted transcripts to the Brain? | Redacted (phones, emails). Raw stays in the infra DB (LOG-002). |
| Who chooses the pitch angle? | Brain supplies per-segment guidance; connector picks the segment. |
| No-answer calls | Produce an empty transcript record so the Brain sees every completed call, not only friendly ones. |
| Voicemail | Leave the playbook's approved message (feature flag; off means hang up and log). |

## Still blocking live calls

Open questions 1 (provider), 2 (legal position on AI-voice calls), 3 (recording policy), and 7 (volume and spend cap)
from spec section 12. None of them block more v0 work; they block flipping anything from mock to real.

## Suggested next increments

- **v0.1**: Brain team swaps `MockBrain` for an HTTP client against their `/v1/scripts/resolve` and `/v1/transcripts`;
  contract tests pin the payloads.
- **v0.2**: Pick the voice provider; implement `VoiceProvider` for it in dry-run/sandbox mode; staged live test to
  team-owned numbers only, with clearances for those numbers.
- **v0.3**: First real source adapter and line-type lookup; PostgreSQL store behind the same `Store` surface.
- **v0.4**: Private ops view, secrets, retention, alerts to a channel.
