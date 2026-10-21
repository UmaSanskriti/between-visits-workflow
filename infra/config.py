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
    retry_callback_extra_attempts: int = 2
    retry_hours_local: list[int] = field(default_factory=lambda: [10, 14, 16])
    technical_retry_minutes: int = 60
    brain_retry_minutes: int = 15
    max_calls_per_run: int = 10
    max_calls_per_day: int = 50
    delivery_max_attempts: int = 3
    delivery_backoff_seconds: float = 0.0
    voicemail_message_enabled: bool = False
    brain_down: bool = False
    live_allowlist: list[str] = field(default_factory=list)
    live_ignore_window: bool = False
    live_max_calls_per_run: int = 1
    live_poll_timeout_seconds: int = 600
    live_max_call_seconds: int = 420
    live_assistant_name: str = "between-visits-reachout-v1"
    live_voice_id: str = "Paige"
    live_model: str = "gpt-4o"
    vapi_api_key: str = ""
    vapi_phone_number_id: str = ""
    vapi_area_code: str = "617"
    outreach_channel: str = "email"  # email (default) | call
    email_from_name: str = "Anirudh Bagla"
    email_from_address: str = ""
    email_reply_to: str = ""
    email_follow_up_days: int = 3
    email_max_messages: int = 2
    email_max_per_run: int = 25
    email_max_per_day: int = 50
    email_demo_recipient: str = ""  # live mode: every outbound email is delivered here instead (staged demo)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_host: str = ""
    imap_user: str = ""
    imap_password: str = ""
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
        cfg.retry_callback_extra_attempts = int(retry.get("callback_extra_attempts", cfg.retry_callback_extra_attempts))
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
        live = raw.get("live", {})
        cfg.live_allowlist = [str(x) for x in live.get("allowlist", cfg.live_allowlist)]
        cfg.live_max_calls_per_run = int(live.get("max_calls_per_run", cfg.live_max_calls_per_run))
        cfg.live_poll_timeout_seconds = int(live.get("poll_timeout_seconds", cfg.live_poll_timeout_seconds))
        cfg.live_max_call_seconds = int(live.get("max_call_seconds", cfg.live_max_call_seconds))
        cfg.live_assistant_name = live.get("assistant_name", cfg.live_assistant_name)
        cfg.live_voice_id = live.get("voice_id", cfg.live_voice_id)
        cfg.live_model = live.get("model", cfg.live_model)
        cfg.vapi_area_code = str(live.get("area_code", cfg.vapi_area_code))
        outreach = raw.get("outreach", {})
        cfg.outreach_channel = str(outreach.get("channel", cfg.outreach_channel))
        em = raw.get("email", {})
        cfg.email_from_name = str(em.get("from_name", cfg.email_from_name))
        cfg.email_from_address = str(em.get("from_address", cfg.email_from_address))
        cfg.email_reply_to = str(em.get("reply_to", cfg.email_reply_to))
        cfg.email_follow_up_days = int(em.get("follow_up_days", cfg.email_follow_up_days))
        cfg.email_max_messages = int(em.get("max_messages", cfg.email_max_messages))
        cfg.email_max_per_run = int(em.get("max_per_run", cfg.email_max_per_run))
        cfg.email_max_per_day = int(em.get("max_per_day", cfg.email_max_per_day))
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
    cfg.vapi_api_key = (env.get("VAPI_PRIVATE_KEY") or env.get("VAPI_API_KEY") or "").strip()  # private key
    cfg.vapi_phone_number_id = env.get("VAPI_PHONE_NUMBER_ID", "").strip()
    cfg.live_ignore_window = _flag(env, "BV_LIVE_IGNORE_WINDOW", cfg.live_ignore_window)
    if env.get("BV_OUTREACH_CHANNEL"):
        cfg.outreach_channel = env["BV_OUTREACH_CHANNEL"].strip().lower()
    cfg.email_from_address = (env.get("EMAIL_FROM") or cfg.email_from_address).strip()
    cfg.email_from_name = (env.get("EMAIL_FROM_NAME") or cfg.email_from_name).strip()
    cfg.email_reply_to = (env.get("EMAIL_REPLY_TO") or cfg.email_reply_to).strip()
    cfg.smtp_host = env.get("SMTP_HOST", "").strip()
    cfg.smtp_port = int(env.get("SMTP_PORT") or cfg.smtp_port)
    cfg.smtp_user = env.get("SMTP_USER", "").strip()
    cfg.smtp_password = env.get("SMTP_PASSWORD", "")
    cfg.imap_host = env.get("IMAP_HOST", "").strip()
    cfg.imap_user = (env.get("IMAP_USER") or cfg.smtp_user).strip()
    cfg.imap_password = env.get("IMAP_PASSWORD") or cfg.smtp_password
    if env.get("BV_LIVE_ALLOWLIST"):
        cfg.live_allowlist = [x.strip() for x in env["BV_LIVE_ALLOWLIST"].split(",") if x.strip()]
    if env.get("BV_DATA_DIR"):
        cfg.data_dir = Path(env["BV_DATA_DIR"])
    if env.get("BV_EXPORT_DIR"):
        cfg.export_dir = Path(env["BV_EXPORT_DIR"])

    if cfg.mode == "live":
        if not _flag(env, "BV_ALLOW_LIVE_CALLS", False):
            raise ConfigError("mode=live needs BV_ALLOW_LIVE_CALLS=1 as an explicit, per-run opt-in.")
        if not cfg.live_allowlist:
            raise ConfigError(
                "mode=live needs a non-empty [live].allowlist (or BV_LIVE_ALLOWLIST): only listed "
                "numbers or email addresses may be contacted in the staged live test."
            )
    elif cfg.mode != "dry_run":
        raise ConfigError(f"mode={cfg.mode!r} is not supported. Use dry_run or live.")
    if cfg.outreach_channel not in ("email", "call"):
        raise ConfigError(f"[outreach].channel must be 'email' or 'call', got {cfg.outreach_channel!r}")
    cfg.live_allowlist = [x.lower() if "@" in x else x for x in cfg.live_allowlist]
    cfg.email_demo_recipient = (env.get("EMAIL_DEMO_RECIPIENT") or "").strip().lower() or next(
        (x for x in cfg.live_allowlist if "@" in x), ""
    )
    return cfg


def load_dotenv(path: str | Path, env: dict | None = None) -> dict:
    """Minimal KEY=VALUE loader for a gitignored .env (no dependency). Existing variables win."""
    env = os.environ if env is None else env
    p = Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in env:
            env[key] = value
    return env
