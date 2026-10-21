# Between Visits · calling-agent infra (v0)

Infra for the automated discovery-outreach loop described in
[`AB calling agent infra_ requirements specification.md`](AB%20calling%20agent%20infra_%20requirements%20specification.md):
source doctors, verify contacts, run a hard compliance gate, place calls through a voice provider, log outcomes,
and hand transcripts to the Brain. The connector contracts live in [`connector/`](connector/).

**v0 is a dry run.** The whole pipeline executes end to end, but the two external systems are mocks:
the voice provider never dials, and the Brain is a local stand-in that serves **script v1 from
[`clinic-outreach-brain.md`](clinic-outreach-brain.md)** (the Brain playbook, owner Anirudh) verbatim, including its
LOCKED opening and close. Every doctor in `seeds/` is fictional.

| | |
|---|---|
| **Ops dashboard** | https://umasanskriti.github.io/between-visits-workflow/ |
| **Scope of v0** | [`V0_SCOPE.md`](V0_SCOPE.md): what is real, what is mocked, what is deferred |
| **Run it on GitHub** | Actions → *Dry-run pipeline* → *Run workflow* |
| **Tests** | `pytest`: acceptance criteria AC-1..AC-10 plus the connector test plan |

## The MVP flow v0 exercises

```
seeds/test_doctors.csv ─► ingest ─► verify ─► compliance gate ─► script (mock Brain) ─► call (mock Voice)
                                                                                           │
docs/data/*.json ◄── export ◄── summary ◄── deliver transcripts (mock Brain) ◄── webhooks ◄┘
      │
      └─► GitHub Pages dashboard
```

| Stage | What v0 really does | Spec |
|---|---|---|
| Ingest | CSV seed adapter, deterministic ids, source + retrieval timestamp per channel, idempotent re-runs | SRC-001/003/007 |
| Verify | E.164 normalization, line type (seeded stand-in for a lookup service), email syntax, duplicate flagging, suppression match, `verification_status` | VER-001..006 |
| Clearance | `seeds/clearances.csv` is the only thing that sets `call_clearance = cleared`; a consent record is written per clearance | CMP-003/010 |
| Gate | Kill switch, pause, suppression, verification, clearance, calling window in the recipient's time zone, retry due time, max attempts. Every decision is logged | CMP-001/002, CALL-009 |
| Script | Resolve from the Brain (no script, no call), choose segment and pitch angle, fill placeholders (doctor, practice, hook, four calendar slots, callback number) with fallbacks, refuse any unfilled placeholder or missing callback number, always prepend the LOCKED AI + recording opening | CALL-002, BRN-001..003, CMP-004/005, playbook 4-6 |
| Call | `VoiceProvider` interface with a mock that emits HMAC-signed webhooks, each one twice | CALL-003/005 |
| Outcomes | Eleven outcomes (the spec's nine plus the playbook's `NOT_A_FIT` and `DECLINED_RECORDING`), a provisional failure category per call, retry policy (≥1 business day, varied hours, max 2 per playbook, plus a bounded allowance when a practice asks for a call back), opt-out phrase detection that forces `DO_NOT_CONTACT` and writes a suppression, wrong-number channel failure, draft interview + .ics held for approval on `BOOKED` | CALL-006/007, CMP-006, LOG-001..004, playbook 9-10 |
| Transcripts | Stored before delivery, patient-data heuristic holds a transcript, redacted package with idempotency key, retries, dead-letter queue, alerts | BRN-004..007, CMP-009 |
| Visibility | Summary by outcome, script version, segment, and failure category; masked export; audit log; the tracker dashboard (one card per practice with every attempt, transcript status, errors, and alerts) | LOG-005, OPS-001, NFR-004/009, brief section 6 |

## Quick start

```bash
uv venv --python 3.12 && source .venv/bin/activate      # or: python3.12 -m venv .venv
pip install -e ".[dev]"
pytest                                                   # 46 tests, ~5 s
python -m infra run --reset                              # full dry run from a clean slate
python -m infra run --now 2026-10-21T15:00:00Z           # run "as if" it were Wednesday 11:00 ET (--now works before or after `run`)
python -m http.server -d docs 8000                       # open http://localhost:8000
```

Commands (`python -m infra <command>` or `bv <command>` after install):

| Command | What it does |
|---|---|
| `run [--reset] [--now ISO]` | ingest → verify → gate/call → webhooks → deliver → summary → export |
| `ingest`, `verify`, `call`, `deliver` | one stage only, then summary and export |
| `pause` / `resume` | operator pause without touching config (OPS-001) |
| `suppress --contact t_09` or `--phone ...` | add a do-not-contact entry |
| `redrive` | move dead-lettered transcripts back to pending |
| `approve t_01 --by "Anirudh Bagla"` | approve a drafted interview invite (playbook section 9) |
| `history t_01` | full history for one contact: record, channels, gate decisions, attempts, audit |
| `summary`, `export`, `reset` | print the summary, re-export the dashboard snapshot, wipe state |

Environment overrides: `BV_KILL_SWITCH=1`, `BV_NOW=<ISO 8601>`, `BV_MOCK_BRAIN_DOWN=1`, `BV_CALLBACK_NUMBER`, `BV_DATA_DIR`,
`BV_EXPORT_DIR`.
Policy knobs (calling window, retries, caps, feature flags) are in [`config.toml`](config.toml).

## Running on GitHub

Two workflows in [`.github/workflows/`](.github/workflows/):

- **CI** (`ci.yml`): lint, tests, and a smoke dry run on every push and pull request.
- **Dry-run pipeline** (`pipeline.yml`): runs the pipeline on `workflow_dispatch` (inputs: simulated time, reset,
  simulate Brain down) and on pushes to `main` that touch `infra/`, `seeds/`, or `config.toml`. It commits the new
  state to `data/` and the masked snapshot to `docs/data/`; GitHub Pages serves `docs/` as the dashboard.
  A weekday cron is included but commented out.

**Kill switch without a deploy:** set the repository variable `BV_KILL_SWITCH` to `1`
(Settings → Secrets and variables → Actions → Variables). The next run places zero calls and says why.

## Going live, staged (Vapi)

`mode = "live"` exists now, behind two explicit switches and a hard allowlist. Only numbers on the allowlist can
ever be dialed; everything else is blocked at the gate with `not_in_live_allowlist`. The staged test is meant for
team-owned phones, one call per run, capped at seven minutes.

1. Copy `.env.example` to `.env` (gitignored) and fill in `VAPI_API_KEY` and `BV_LIVE_ALLOWLIST` (your own number).
   Optional: `VAPI_PHONE_NUMBER_ID` to dial from a specific number, `BV_CALLBACK_NUMBER` for the number the agent
   gives for call backs (defaults to the number it dials from).
2. Copy `seeds/live.example/` to `seeds/live/` (gitignored) and put the same number in `demo.csv` and `clearances.csv`.
3. `python -m infra vapi check`: verifies the key, lists phone numbers, creates or updates the assistant
   (`between-visits-reachout-v1`) with script v1, the end-call tool, voicemail detection, and the structured-outcome
   schema. No call is placed. Note: Vapi's docs say free Vapi numbers cannot place outbound calls; if the account
   has only a free number, import a Twilio, Telnyx, or Vonage number and point `VAPI_PHONE_NUMBER_ID` at it.
4. `python -m infra live-demo`: runs ingest, verify, and the gate for `seeds/live/`, and prints the exact call body
   that would be sent. Nothing is dialed.
5. `python -m infra live-demo --dial`: places the call, polls Vapi until it ends, classifies the outcome from the
   structured data, stores the transcript, and drafts the handoff. State goes to `data-live/` and the masked
   snapshot to `docs/data-live/` (both gitignored); view it with `python -m http.server -d docs` at `?source=live`.

**Dashboard-driven call with live transcript.** The dashboard is a static page and the Vapi key must never
reach the browser, so a small local service holds the key and does the dialing:

```bash
python -m infra serve            # on the machine with .env; opens http://127.0.0.1:8787 and a Cloudflare quick tunnel
```

Open the dashboard (locally at `http://127.0.0.1:8787/`, or the GitHub Pages site; Chrome may ask once for
local-network permission on the public site). When the service is reachable, **Run the agent** runs the whole
outreach loop on this machine and shows every step in the "What the agent is doing" column: load the approved list,
pick the first eligible practice, pass the gate, compose the playbook email, queue it for the linked Gmail account
(or send via SMTP when `SMTP_PASSWORD` is set), wait for the reply, classify it, hand off to Anirudh.
The staged live call through Vapi (`POST /api/call`) stays available for when a Twilio number exists.

**Twilio number.** Free Vapi numbers cannot place outbound calls. With a Twilio account (trial is fine: it can only
call numbers you verified by SMS, and it plays a short "press any key" message first), put `TWILIO_PHONE_NUMBER`,
`TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in `.env` and run `python -m infra vapi import-twilio`, or import the
number in the Vapi dashboard. Imported numbers are preferred automatically.

**What counts as success for the agent:** the doctor's email, or an agreement that Anirudh may contact the doctor
(a call, WhatsApp, iMessage, email, or in person all count), captured with the best days and times and the number to
text. A fixed slot is a bonus (`BOOKED`); agreement without a slot is `AGREED_TO_TALK`. Both land on the practice card
as "Handoff to Anirudh".

## Email outreach

Email is now the primary channel: the Twilio trial cannot hand a call to Vapi, and the playbook already carries an
approval-gated cold email (section 8b). The loop is the same, with email in place of the call:

1. **Gate.** Same hard checks as for calls, on the email channel: not suppressed (a "stop" reply suppresses every
   channel), address verified, address cleared (it must come from the practice's own website and be approved),
   on the live allowlist when in `live` mode, under the message cap (initial email plus one follow-up).
2. **Compose.** The Brain supplies the playbook template; the connector fills the doctor, practice, hook, and two
   slots, keeps it plain text and under 150 words, one ask, real sender name, and an opt-out line. A missing required
   field blocks the send and lands in the error report.
3. **Send.** The service queues the message in an outbox (`data/outbox/`, gitignored) and the Claude session that
   holds the Gmail connector sends it from the linked account, then reports back with
   `POST /api/outbox/<attempt>/sent {message_id, thread_id}` (or `python -m infra email-sent`). With `SMTP_PASSWORD`
   set the service sends directly. Every send is logged as an email attempt with subject, body, message id, thread
   id, and script version. In `live` mode every outbound email is delivered to `EMAIL_DEMO_RECIPIENT` (default: the
   first address on `BV_LIVE_ALLOWLIST`) instead of the practice, and the attempt records the redirect, so the demo
   never emails a real practice.
4. **Replies.** Read from the linked inbox (IMAP polling when `IMAP_*` is set, or the Gmail connector driver posting
   `POST /api/reply {attempt, text, from, at}`), or pasted into **Record reply** on the dashboard. The reply is
   classified: agreed (email, channel, availability captured as a handoff to Anirudh), declined, stop (suppressed
   everywhere), bounce (address marked failed), or other. The thread goes to the Brain like a transcript. No reply
   after `follow_up_days` schedules one follow-up, then the practice is marked no reply.

**Dashboard.** One button, **Run the agent**. The ring animates through the stages, the step list shows which
practice was picked, the gate verdict, the subject and body, "Queued for Gmail" then "Sent via Gmail" with a link to
the thread, "Waiting for a reply", and the classified reply with the handoff. Practice cards carry an **Emails**
section with the same history, including where the demo copy was delivered.

## Repository layout

```
infra/            the pipeline package (config, models, store, sourcing, verify, compliance, brain, scripts,
                  voice, vapi, live, serve, orchestrator, delivery, booking, summary, export, pipeline, cli)
tests/            acceptance criteria AC-1..AC-10, connector test plan t_01..t_08 + edge cases, unit tests
seeds/            fictional doctors, clearances, suppressions (inputs)
data/             system of record for the dry run: one JSON file per table + audit_log.jsonl (committed)
docs/             GitHub Pages dashboard; docs/data/ is the masked snapshot the page reads
connector/        interface drafts for the Voice and Brain teams (inputs/, outputs/, test plan, variables)
clinic-outreach-brain.md   the Brain playbook (v1) the mock Brain serves; LOCKED text is reproduced verbatim
config.toml       calling window, retry policy, caps, feature flags
```

## Safety notes

- `mode` is `dry_run` and the config loader refuses anything else. There is no live voice provider in v0.
- The dashboard is public. Phones and emails are masked at export, and only fictional data exists today.
  Before real contacts enter `data/`, the Pages site must become private (or be replaced) and `data/` must stop being
  committed to a public repo.
- The compliance text in `infra/compliance.py` is engineering scaffolding, not legal language. Counsel reviews it
  before any live call (see spec section 7).
