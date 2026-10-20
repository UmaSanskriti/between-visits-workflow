"""Time helpers. Everything stored is UTC ISO 8601 with a trailing Z."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock(Clock):
    """Deterministic clock for tests and simulated runs (BV_NOW)."""

    def __init__(self, at: datetime):
        self._at = at if at.tzinfo else at.replace(tzinfo=UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, **kwargs) -> None:
        self._at = self._at + timedelta(**kwargs)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def to_local(dt: datetime, tz_name: str) -> datetime:
    return dt.astimezone(ZoneInfo(tz_name))


def add_business_days(local_dt: datetime, days: int) -> datetime:
    out = local_dt
    while days > 0:
        out = out + timedelta(days=1)
        if out.weekday() < 5:
            days -= 1
    return out
