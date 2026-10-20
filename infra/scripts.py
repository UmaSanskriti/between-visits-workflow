"""Connector logic: segment choice and per-call script personalization (connector/script-variables.md).

Output shape follows connector/outputs/to-voice.md: first_message (what the agent says when the call connects),
system_prompt (everything else), variables_used, fallbacks_applied, plus the voicemail message.
"""

from __future__ import annotations

import re

from .calendar import stub_slots
from .clock import Clock, iso
from .compliance import AI_DISCLOSURE, disclosure_prefix
from .config import Config
from .models import Channel, Contact, ScriptRecord

PRIMARY_CARE = {"family medicine", "internal medicine", "general practice", "primary care"}
PLACEHOLDER_RE = re.compile(r"\{[a-z_0-9]+\}")
REQUIRED = ("ai_disclosure", "ask", "caller_identity", "script_version", "callback_number")


def segment_for(contact: Contact) -> str:
    specialty = (contact.specialty or "").strip().lower()
    size = (contact.practice_size or "").strip().lower().replace(" ", "_")
    if not specialty:
        return "default"
    kind = "primary_care" if specialty in PRIMARY_CARE else "specialist"
    if size == "solo":
        return f"solo_{kind}"
    if size in ("small_group", "group", "small"):
        return f"small_group_{kind}"
    return f"unknown_size_{kind}"


class _Vars(dict):
    """format_map helper that leaves unknown placeholders in place so the final check catches them."""

    def __missing__(self, key):
        return "{" + key + "}"


def _fill(template: str, variables: dict) -> str:
    return (template or "").format_map(_Vars(variables))


def build_script(
    contact: Contact, channel: Channel, brain: dict, call_id: str, clock: Clock, cfg: Config
) -> tuple[ScriptRecord | None, list[str]]:
    variables: dict = {}
    fallbacks: list[str] = []
    errors: list[str] = []

    last_name = contact.full_name.strip().split()[-1] if contact.full_name.strip() else ""
    if last_name:
        variables["doctor_name"] = f"Dr. {last_name}"
    else:
        variables["doctor_name"] = "the doctor"
        fallbacks.append("doctor_name")

    if contact.practice_name.strip():
        variables["practice_name"] = contact.practice_name.strip()
    else:
        variables["practice_name"] = "your practice"
        fallbacks.append("practice_name")

    if contact.specialty.strip():
        variables["specialty"] = contact.specialty.strip()
    else:
        variables["specialty"] = "independent practice"
        fallbacks.append("specialty")

    if contact.practice_size.strip():
        variables["practice_size"] = contact.practice_size.strip()
        variables["practice_size_sentence"] = f" Practice size: {variables['practice_size']}."
    else:
        variables["practice_size_sentence"] = ""
        fallbacks.append("practice_size")

    if contact.city.strip():
        variables["location"] = ", ".join(x for x in (contact.city.strip(), contact.state.strip()) if x)
    else:
        variables["location"] = ""
        fallbacks.append("location")

    variables["ehr_sentence"] = ""  # no enrichment source in v0
    fallbacks.append("ehr")

    if contact.hook.strip():
        variables["hook"] = contact.hook.strip()
        variables["hook_sentence"] = f"I saw {variables['hook']} and wanted to reach out. "
    else:
        variables["hook_sentence"] = ""
        fallbacks.append("hook")

    variables["pronunciation_sentence"] = (
        f" Pronounce the doctor's name as: {contact.pronunciation_hint}." if contact.pronunciation_hint else ""
    )
    variables["segment"] = contact.segment or "default"
    if not contact.segment:
        fallbacks.append("segment")

    variables["pitch_angle"] = brain.get("pitch_angle") or ""
    if not variables["pitch_angle"]:
        variables["pitch_angle"] = "Doctors tell us GLP-1 patients need more support between visits."
        fallbacks.append("pitch_angle")

    slots = stub_slots(clock.now(), contact.time_zone or cfg.default_time_zone)
    for i in range(4):
        variables[f"slot_{i + 1}"] = slots[i]["label"] if i < len(slots) else "another time"
    variables["slot_1_iso"] = slots[0]["iso"] if slots else ""
    if len(slots) >= 2:
        variables["ask"] = _fill(brain.get("ask", ""), variables)
    else:
        variables["ask"] = brain.get("ask_fallback", "")
        fallbacks.append("slot_options")

    variables["ai_disclosure"] = AI_DISCLOSURE
    variables["caller_identity"] = brain.get("caller_identity") or ""
    variables["script_version"] = brain.get("script_version") or ""
    variables["callback_number"] = cfg.callback_number or ""  # playbook 15: must be set before any live call

    for key in REQUIRED:
        if not variables.get(key):
            errors.append(f"missing_required:{key}")
    if errors:
        return None, errors

    variables["route"] = _fill(brain.get("route", ""), variables)
    variables["pitch"] = _fill(brain.get("pitch", ""), variables)
    variables["close"] = _fill(brain.get("close", ""), variables)
    variables["voicemail"] = _fill(brain.get("voicemail", ""), variables)
    variables["objections"] = " | ".join(
        f"'{q}' -> {_fill(a, variables)}" for q, a in brain.get("objections", {}).items()
    )

    first_message = f"{disclosure_prefix(brain.get('required_disclosures', []))} {variables['route']}".strip()
    system_prompt = _fill(brain.get("system_prompt", ""), variables)

    leftover = sorted(set(PLACEHOLDER_RE.findall(" ".join((first_message, system_prompt, variables["voicemail"])))))
    if leftover:
        errors.append("unfilled_placeholder:" + ",".join(leftover))
    if AI_DISCLOSURE not in first_message:
        errors.append("missing_required:ai_disclosure")
    if errors:
        return None, errors

    hidden = {"script_version", "objections", "system_prompt"}
    spoken = {k: v for k, v in variables.items() if k not in hidden and v}
    record = ScriptRecord(
        call_id=call_id,
        contact_id=contact.id,
        script_id=brain.get("script_id", ""),
        script_version=variables["script_version"],
        segment=variables["segment"],
        pitch_angle=variables["pitch_angle"],
        first_message=first_message,
        system_prompt=system_prompt,
        voicemail_message=variables["voicemail"] if cfg.voicemail_message_enabled else "",
        variables_used=spoken,
        fallbacks_applied=fallbacks,
        created_at=iso(clock.now()),
    )
    return record, []
