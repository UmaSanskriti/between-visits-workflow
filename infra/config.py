"""Configuration: config.toml plus environment overrides (NFR-006)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .clock import parse_iso

TRUE_VALUES = {"1", "true", "yes", "on"}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    mode: str = "dry_run"
    kill_switch: bool = False
    campaign_id: str = "clinic-outreach-discovery-2026-q4"
    callback_number: str = ""
    playbook: str = "clinic-outreach-brain.md"
    interview_length_minutes: int = 30
    default_time_zone: str = "America/New_York"
    webhook_secret: str = "dry-run-shared-secret"
    calling_days: list[str] = field(default_factory=lambda: ["Mon", "Tue", "Wed", "Thu", "Fri"])
    calling_start: str = "09:00"
    calling_end: str = "17:00"
    retry_max_attempts: int = 3
    retry_min_business_days: int = 1
    retry_hours_local: list[int] = field(default_factory=lambda: [10, 14, 16])
    technical_retry_minutes: int = 60
    brain_retry_minutes: int = 15
    max_calls_per_run: int = 10
    max_calls_per_day: int = 50
    delivery_max_attempts: int = 3
    delivery_backoff_seconds: float = 0.0
    voicemail_message_enabled: bool = False
    brain_down: bool = False
    now_override: datetime | None = None
    data_dir: Path = Path("data")
    export_dir: Path = Path("docs/data")


def _flag(env: dict, key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in TRUE_VALUES


def load_config(path: str | Path = "config.toml", env: dict | None = None) -> Config:
    env = dict(os.environ) if env is None else env
    cfg = Config()
    p = Path(path)
    if p.exists():
        raw = tomllib.loads(p.read_text())
        cfg.mode = raw.get("mode", cfg.mode)
        cfg.kill_switch = bool(raw.get("kill_switch", cfg.kill_switch))
        cfg.campaign_id = raw.get("campaign_id", cfg.campaign_id)
        cfg.callback_number = raw.get("callback_number", cfg.callback_number)
        cfg.playbook = raw.get("playbook", cfg.playbook)
        booking = raw.get("booking", {})
        cfg.interview_length_minutes = int(booking.get("interview_length_minutes", cfg.interview_length_minutes))
        cfg.default_time_zone = raw.get("default_time_zone", cfg.default_time_zone)
        cfg.webhook_secret = raw.get("webhook_secret", cfg.webhook_secret)
        win = raw.get("calling_window", {})
        cfg.calling_days = list(win.get("days", cfg.calling_days))
        cfg.calling_start = win.get("start", cfg.calling_start)
        cfg.calling_end = win.get("end", cfg.calling_end)
        retry = raw.get("retry", {})
        cfg.retry_max_attempts = int(retry.get("max_attempts", cfg.retry_max_attempts))
        cfg.retry_min_business_days = int(retry.get("min_business_days", cfg.retry_min_business_days))
        cfg.retry_hours_local = [int(h) for h in retry.get("hours_local", cfg.retry_hours_local)]
        cfg.technical_retry_minutes = int(retry.get("technical_retry_minutes", cfg.technical_retry_minutes))
        cfg.brain_retry_minutes = int(retry.get("brain_retry_minutes", cfg.brain_retry_minutes))
        limits = raw.get("limits", {})
        cfg.max_calls_per_run = int(limits.get("max_calls_per_run", cfg.max_calls_per_run))
        cfg.max_calls_per_day = int(limits.get("max_calls_per_day", cfg.max_calls_per_day))
        delivery = raw.get("delivery", {})
        cfg.delivery_max_attempts = int(delivery.get("max_attempts", cfg.delivery_max_attempts))
        cfg.delivery_backoff_seconds = float(delivery.get("backoff_seconds", cfg.delivery_backoff_seconds))
        features = raw.get("features", {})
        cfg.voicemail_message_enabled = bool(features.get("voicemail_message", cfg.voicemail_message_enabled))
        paths = raw.get("paths", {})
        cfg.data_dir = Path(paths.get("data_dir", cfg.data_dir))
        cfg.export_dir = Path(paths.get("export_dir", cfg.export_dir))

    cfg.mode = env.get("BV_MODE", cfg.mode)
    cfg.kill_switch = _flag(env, "BV_KILL_SWITCH", cfg.kill_switch)
    cfg.brain_down = _flag(env, "BV_MOCK_BRAIN_DOWN", cfg.brain_down)
    if env.get("BV_NOW"):
        try:
            cfg.now_override = parse_iso(env["BV_NOW"].strip())
        except ValueError as exc:
            raise ConfigError(f"BV_NOW must be ISO 8601, got {env['BV_NOW']!r}") from exc
    if env.get("BV_CALLBACK_NUMBER") is not None:
        cfg.callback_number = env["BV_CALLBACK_NUMBER"].strip()
    if env.get("BV_DATA_DIR"):
        cfg.data_dir = Path(env["BV_DATA_DIR"])
    if env.get("BV_EXPORT_DIR"):
        cfg.export_dir = Path(env["BV_EXPORT_DIR"])

    if cfg.mode != "dry_run":
        raise ConfigError(
            f"mode={cfg.mode!r} is not supported in v0. Only dry_run exists; no live voice provider is wired."
        )
    return cfg
