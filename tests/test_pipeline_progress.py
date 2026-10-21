"""run_pipeline reports stage progress in order through on_stage."""

from __future__ import annotations

from conftest import CLEARANCES, SEED, SUPPRESSIONS, Env


def test_on_stage_events_are_ordered(tmp_path):
    env = Env(tmp_path)
    events = []
    env.run(on_stage=lambda name, status, counts: events.append((name, status, dict(counts))))
    assert [(n, s) for n, s, _ in events] == [
        ("ingest", "start"),
        ("ingest", "done"),
        ("verify", "start"),
        ("verify", "done"),
        ("enrich", "start"),
        ("enrich", "done"),
        ("call", "start"),
        ("call", "done"),
        ("webhooks", "start"),
        ("webhooks", "done"),
        ("deliver", "start"),
        ("deliver", "done"),
        ("run", "done"),
    ]
    by_name = {(n, s): c for n, s, c in events}
    assert by_name[("ingest", "done")]["rows"] == 21
    assert by_name[("enrich", "done")]["enriched_seed"] == 21
    assert by_name[("call", "done")]["placed"] > 0
    assert by_name[("run", "done")]["gate_escapes"] == 0 and by_name[("run", "done")]["run_id"]
    assert SEED.exists() and CLEARANCES.exists() and SUPPRESSIONS.exists()


def test_on_stage_is_optional(tmp_path):
    env = Env(tmp_path)
    run, _ = env.run()
    assert run["run_id"]
