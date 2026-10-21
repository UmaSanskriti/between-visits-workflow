from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from infra.brain import MockBrain
from infra.clock import FixedClock
from infra.config import load_config
from infra.email.provider import MockEmailProvider
from infra.pipeline import run_pipeline
from infra.store import Store
from infra.voice import MockVoiceProvider

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "seeds" / "test_doctors.csv"
CLEARANCES = ROOT / "seeds" / "clearances.csv"
SUPPRESSIONS = ROOT / "seeds" / "suppressions.csv"

TUESDAY_10AM_ET = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)
SATURDAY_10AM_ET = datetime(2026, 10, 24, 14, 0, tzinfo=UTC)


class Env:
    def __init__(
        self,
        tmp_path: Path,
        now: datetime = TUESDAY_10AM_ET,
        kill: bool = False,
        brain_down: bool = False,
        channel: str = "call",
    ):
        env = {
            "BV_OUTREACH_CHANNEL": channel,
            "BV_DATA_DIR": str(tmp_path / "data"),
            "BV_EXPORT_DIR": str(tmp_path / "export"),
            "BV_KILL_SWITCH": "1" if kill else "0",
            "BV_MOCK_BRAIN_DOWN": "1" if brain_down else "0",
        }
        self.cfg = load_config(ROOT / "config.toml", env=env)
        self.clock = FixedClock(now)
        self.store = Store(self.cfg.data_dir, self.clock).load()
        self.brain = MockBrain(self.store, self.clock, down=brain_down)
        self.voice = MockVoiceProvider(self.cfg.webhook_secret, self.clock)
        self.mailer = MockEmailProvider(self.clock)

    def run(self, **kwargs):
        kwargs.setdefault("mailer", self.mailer)
        return run_pipeline(
            self.store,
            self.cfg,
            self.clock,
            self.brain,
            self.voice,
            SEED,
            CLEARANCES,
            SUPPRESSIONS,
            trigger="test",
            **kwargs,
        )

    def reload(self) -> None:
        """Drop in-memory state and reload from disk, like a fresh process would."""
        self.store = Store(self.cfg.data_dir, self.clock).load()
        self.brain = MockBrain(self.store, self.clock, down=self.brain.down)
        self.voice = MockVoiceProvider(self.cfg.webhook_secret, self.clock)
        self.mailer = MockEmailProvider(self.clock)

    def contact(self, external_id: str):
        return next(c for c in self.store.contacts.values() if c.external_id == external_id)

    def attempts(self, external_id: str):
        return self.store.attempts_for(self.contact(external_id).id)

    def decisions(self, external_id: str, run_id: str | None = None):
        cid = self.contact(external_id).id
        rows = [d for d in self.store.logs["gate_decisions"] if d["contact_id"] == cid]
        return [d for d in rows if run_id is None or d["run_id"] == run_id]

    def script(self, external_id: str):
        attempts = self.attempts(external_id)
        return self.store.scripts[attempts[0].id] if attempts else None


@pytest.fixture
def env(tmp_path) -> Env:
    return Env(tmp_path)


@pytest.fixture
def ran(tmp_path) -> Env:
    e = Env(tmp_path)
    e.run()
    return e
