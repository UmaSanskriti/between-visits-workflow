# Between Visits · calling-agent infra (v0)

Infra for the automated discovery-outreach loop described in
[`AB calling agent infra_ requirements specification.md`](AB%20calling%20agent%20infra_%20requirements%20specification.md):
source doctors, verify contacts, run a hard compliance gate, place calls through a voice provider, log outcomes,
and hand transcripts to the Brain. The connector contracts live in [`connector/`](connector/).

**v0 is a dry run.** The whole pipeline executes end to end, but the two external systems are mocks:
the voice provider never dials, and the Brain is a local stand-in. Every doctor in `seeds/` is fictional.

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
| Script | Resolve from the Brain (no script, no call), choose segment and pitch angle, fill placeholders with fallbacks, refuse any unfilled placeholder, always prepend AI + recording disclosures | CALL-002, BRN-001..003, CMP-004/005 |
| Call | `VoiceProvider` interface with a mock that emits HMAC-signed webhooks, each one twice | CALL-003/005 |
| Outcomes | All nine outcomes, retry policy (≥1 business day, varied hours, max 3), opt-out phrase detection that forces `DO_NOT_CONTACT` and writes a suppression, wrong-number channel failure, interview + .ics handoff on `BOOKED` | CALL-006/007, CMP-006, LOG-001..004 |
| Transcripts | Stored before delivery, patient-data heuristic holds a transcript, redacted package with idempotency key, retries, dead-letter queue, alerts | BRN-004..007, CMP-009 |
| Visibility | Summary by outcome, script version, and segment; masked export; audit log; dashboard | LOG-005, OPS-001, NFR-004/009 |

## Quick start

```bash
uv venv --python 3.12 && source .venv/bin/activate      # or: python3.12 -m venv .venv
pip install -e ".[dev]"
pytest                                                   # 38 tests, ~3 s
python -m infra run --reset                              # full dry run from a clean slate
python -m infra run --now 2026-10-21T15:00:00Z           # run "as if" it were Wednesday 11:00 ET
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
| `history t_01` | full history for one contact: record, channels, gate decisions, attempts, audit |
| `summary`, `export`, `reset` | print the summary, re-export the dashboard snapshot, wipe state |

Environment overrides: `BV_KILL_SWITCH=1`, `BV_NOW=<ISO 8601>`, `BV_MOCK_BRAIN_DOWN=1`, `BV_DATA_DIR`, `BV_EXPORT_DIR`.
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

## Repository layout

```
infra/            the pipeline package (config, models, store, sourcing, verify, compliance, brain, scripts,
                  voice, orchestrator, delivery, booking, summary, export, pipeline, cli)
tests/            acceptance criteria AC-1..AC-10, connector test plan t_01..t_08 + edge cases, unit tests
seeds/            fictional doctors, clearances, suppressions (inputs)
data/             system of record for the dry run: one JSON file per table + audit_log.jsonl (committed)
docs/             GitHub Pages dashboard; docs/data/ is the masked snapshot the page reads
connector/        interface drafts for the Voice and Brain teams
config.toml       calling window, retry policy, caps, feature flags
```

## Safety notes

- `mode` is `dry_run` and the config loader refuses anything else. There is no live voice provider in v0.
- The dashboard is public. Phones and emails are masked at export, and only fictional data exists today.
  Before real contacts enter `data/`, the Pages site must become private (or be replaced) and `data/` must stop being
  committed to a public repo.
- The compliance text in `infra/compliance.py` is engineering scaffolding, not legal language. Counsel reviews it
  before any live call (see spec section 7).
