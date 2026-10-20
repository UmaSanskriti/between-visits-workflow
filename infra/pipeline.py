"""One end-to-end dry run: ingest -> verify -> gate/call -> webhooks -> deliver -> summary -> export."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .brain import BrainClient
from .clock import Clock, iso
from .config import Config
from .delivery import deliver_transcripts
from .export import export
from .orchestrator import process_events, schedule_calls
from .sourcing import CsvSeedAdapter, ingest, load_clearances, load_suppressions
from .store import Store
from .summary import build_summary
from .verify import MockLineTypeLookup, verify
from .voice import VoiceProvider

ALL_STAGES = ("ingest", "verify", "call", "deliver")


def git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA", "")
    if sha:
        return sha[:7]
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def run_pipeline(
    store: Store,
    cfg: Config,
    clock: Clock,
    brain: BrainClient,
    voice: VoiceProvider,
    seed: str | Path,
    clearances: str | Path,
    suppressions: str | Path,
    stages=ALL_STAGES,
    trigger: str = "local",
) -> tuple[dict, dict]:
    started = clock.now()
    run_id = started.strftime("%Y%m%dT%H%M%SZ") + f"-{len(store.logs['runs']) + 1:03d}"
    run = {
        "run_id": run_id,
        "started_at": iso(started),
        "now_used": iso(started),
        "simulated_time": cfg.now_override is not None,
        "mode": cfg.mode,
        "kill_switch": cfg.kill_switch,
        "paused": bool(store.state.get("paused")),
        "brain_down": cfg.brain_down,
        "trigger": trigger,
        "git_sha": git_sha(),
        "seed": Path(seed).name,
        "playbook": cfg.playbook,
        "callback_number_set": bool(cfg.callback_number),
        "limits": {
            "max_calls_per_day": cfg.max_calls_per_day,
            "max_calls_per_run": cfg.max_calls_per_run,
            "max_attempts": cfg.retry_max_attempts,
        },
        "stages": {},
    }
    store.audit("run", run_id, "start", {}, {"trigger": trigger, "now_used": run["now_used"]}, run_id=run_id)

    if "ingest" in stages:
        run["stages"]["suppressions_seeded"] = load_suppressions(store, suppressions, clock)
        run["stages"]["ingest"] = ingest(store, CsvSeedAdapter(seed), clock, cfg.default_time_zone)
    if "verify" in stages:
        hints = {ch.value: ch.line_type for ch in store.channels.values() if ch.type == "phone"}
        run["stages"]["verify"] = verify(store, MockLineTypeLookup(hints), clock, load_clearances(clearances))
    if "call" in stages:
        run["stages"]["calls"] = schedule_calls(store, cfg, clock, brain, voice, run_id)
        run["stages"]["webhooks"] = process_events(store, cfg, clock, voice, run_id)
    if "deliver" in stages:
        run["stages"]["delivery"] = deliver_transcripts(store, cfg, clock, brain, run_id)

    run["finished_at"] = iso(clock.now())
    summary = build_summary(store, run)
    run["gate_escapes"] = summary["compliance"]["gate_escapes"]
    if run["gate_escapes"]:
        store.alert(
            "gate_escape", f"{run['gate_escapes']} call attempt(s) exist without an allowed gate result", run_id=run_id
        )
    store.logs["runs"].append(run)
    store.audit("run", run_id, "finish", {}, {"gate_escapes": run["gate_escapes"]}, run_id=run_id)
    store.save()
    export(store, cfg.export_dir, summary, run)
    return run, summary
