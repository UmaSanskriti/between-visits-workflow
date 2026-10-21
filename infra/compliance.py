"""Compliance and safety (section 7). These are hard gates, evaluated before every call."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time

from .clock import Clock, parse_iso, to_local
from .config import Config
from .models import Channel, Clearance, Contact, VerificationStatus
from .store import Store

# clinic-outreach-brain.md section 6, "Open" (LOCKED): the first two sentences of every call.
AI_DISCLOSURE = "Hi, this is an AI assistant calling for Anirudh Bagla, a student at Harvard Business School."
RECORDING_DISCLOSURE = "This call is recorded for our notes. Is that okay?"

OPT_OUT_PATTERNS = [
    r"don'?t call",
    r"do not call",
    r"stop calling",
    r"remove (me|us)",
    r"take (me|us) off",
    r"never call",
    r"no more calls",
    r"not to call",
]
PATIENT_DATA_PATTERNS = [
    (r"\bpatient\b.{0,80}\b(name|dob|date of birth|birthday|mrn|ssn|social security)\b", "patient identifier"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "ssn-like number"),
    (r"\b(mrn|medical record number)\b", "medical record number"),
    (r"\b(dob|date of birth)\b", "date of birth"),
]
DEFERRAL_PREFIXES = ("outside_calling_window", "retry_not_due")


@dataclass
class GateResult:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    deferred: bool = False
    waivers: list[str] = field(default_factory=list)


def in_calling_window(now_utc: datetime, tz_name: str, cfg: Config) -> tuple[bool, str]:
    """CMP-002: weekday and hour window in the recipient's local time zone."""
    local = to_local(now_utc, tz_name)
    day = local.strftime("%a")
    stamp = f"{day} {local:%H:%M} {tz_name}"
    if day not in cfg.calling_days:
        return False, f"outside_calling_window:{stamp}"
    start = time(*(int(x) for x in cfg.calling_start.split(":")))
    end = time(*(int(x) for x in cfg.calling_end.split(":")))
    if not (start <= local.time() < end):
        return False, f"outside_calling_window:{stamp}"
    return True, ""


def pre_call_gate(store: Store, contact: Contact, channel: Channel | None, cfg: Config, clock: Clock) -> GateResult:
    """CMP-001. Every failing reason is returned so operators see the full picture."""
    now = clock.now()
    reasons: list[str] = []
    if cfg.kill_switch:
        reasons.append("kill_switch_on")
    if store.state.get("paused"):
        reasons.append("calling_paused")
    suppressed, why = store.is_suppressed(contact)
    if suppressed:
        reasons.append(f"suppressed:{why}")
    if contact.verification_status != VerificationStatus.VERIFIED:
        reasons.append(f"contact_not_verified:{contact.verification_reason or contact.verification_status}")
    if channel is None:
        reasons.append("no_phone_channel")
    else:
        if not channel.verified:
            reasons.append("channel_not_verified")
        if channel.call_clearance != Clearance.CLEARED:
            reasons.append("call_clearance_not_cleared")
        if cfg.mode == "live" and channel.value not in cfg.live_allowlist:
            reasons.append("not_in_live_allowlist")  # staged live test: team-owned numbers only (spec 10.1)
    cap = cfg.retry_max_attempts
    if contact.last_outcome == "CALLBACK_REQUESTED" and contact.next_attempt_at:
        cap += cfg.retry_callback_extra_attempts  # playbook 5.7: unless they asked for a call back
    if contact.attempts >= cap:
        reasons.append("max_attempts_reached")
    waivers: list[str] = []
    ok, why = in_calling_window(now, contact.time_zone or cfg.default_time_zone, cfg)
    if not ok:
        team_number = channel is not None and channel.value in cfg.live_allowlist
        if cfg.mode == "live" and cfg.live_ignore_window and team_number:
            waivers.append("calling_window_waived_for_allowlisted_team_number")  # staged demo only, never a practice
        else:
            reasons.append(why)
    if contact.next_attempt_at and parse_iso(contact.next_attempt_at) > now:
        reasons.append(f"retry_not_due_until:{contact.next_attempt_at}")
    deferred = bool(reasons) and all(r.startswith(DEFERRAL_PREFIXES) for r in reasons)
    return GateResult(allowed=not reasons, reasons=reasons, deferred=deferred, waivers=waivers)


def pre_send_gate(store: Store, contact: Contact, channel: Channel | None, cfg: Config, clock: Clock) -> GateResult:
    """Email counterpart of pre_call_gate (playbook section 8: published addresses only, honor stop, approved batch)."""
    now = clock.now()
    reasons: list[str] = []
    if cfg.kill_switch:
        reasons.append("kill_switch_on")
    if store.state.get("paused"):
        reasons.append("outreach_paused")
    suppressed, why = store.is_suppressed(contact)
    if suppressed:
        reasons.append(f"suppressed:{why}")
    if contact.verification_status != VerificationStatus.VERIFIED:
        reasons.append(f"contact_not_verified:{contact.verification_reason or contact.verification_status}")
    if channel is None:
        reasons.append("no_email_channel")
    else:
        if not channel.verified:
            reasons.append("channel_not_verified")
        if channel.call_clearance != Clearance.CLEARED:
            reasons.append("email_clearance_not_cleared")  # the address must come from the practice's own site
        if cfg.mode == "live" and channel.value.lower() not in cfg.live_allowlist:
            reasons.append("not_in_live_allowlist")
    if contact.attempts >= cfg.email_max_messages:
        reasons.append("max_messages_reached")
    if contact.next_attempt_at and parse_iso(contact.next_attempt_at) > now:
        reasons.append(f"retry_not_due_until:{contact.next_attempt_at}")
    deferred = bool(reasons) and all(r.startswith(DEFERRAL_PREFIXES) for r in reasons)
    return GateResult(allowed=not reasons, reasons=reasons, deferred=deferred)


def contains_opt_out(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in OPT_OUT_PATTERNS)


def detect_patient_data(text: str) -> str:
    """CMP-009 heuristic. Returns a reason string, or '' when nothing matched."""
    lowered = (text or "").lower()
    for pattern, label in PATIENT_DATA_PATTERNS:
        if re.search(pattern, lowered):
            return label
    return ""


def redact(text: str) -> str:
    text = re.sub(r"\+?\d[\d\-\.\s\(\)]{8,}\d", "[phone]", text or "")
    return re.sub(r"[^@\s]+@[^@\s]+\.[^@\s]+", "[email]", text)


def disclosure_prefix(required: list[str]) -> str:
    parts = [AI_DISCLOSURE]
    if "recording" in required:
        parts.append(RECORDING_DISCLOSURE)
    return " ".join(parts)
