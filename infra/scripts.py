"""Connector logic: segment choice and per-call script personalization (connector/script-variables.md)."""

from __future__ import annotations

import re

from .clock import Clock, iso
from .compliance import AI_DISCLOSURE, disclosure_prefix
from .models import Channel, Contact, ScriptRecord

PRIMARY_CARE = {"family medicine", "internal medicine", "general practice", "primary care"}
PLACEHOLDER_RE = re.compile(r"\{[a-z_]+\}")
REQUIRED = ("ai_disclosure", "ask", "caller_identity", "script_version")


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


def build_script(
    contact: Contact, channel: Channel, brain: dict, call_id: str, clock: Clock
) -> tuple[ScriptRecord | None, list[str]]:
    variables: dict = {}
    fallbacks: list[str] = []
    errors: list[str] = []

    last_name = contact.full_name.strip().split()[-1] if contact.full_name.strip() else ""
    if last_name:
        variables["doctor_name"] = f"Dr. {last_name}"
        variables["greeting"] = f"Hi {variables['doctor_name']},"
    else:
        variables["doctor_name"] = "Doctor"
        variables["greeting"] = "Hi, may I speak with the doctor?"
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

    variables["ai_disclosure"] = AI_DISCLOSURE
    variables["ask"] = brain.get("ask") or ""
    variables["caller_identity"] = brain.get("caller_identity") or ""
    variables["script_version"] = brain.get("script_version") or ""
    variables["slot_options"] = "a time that works for you"
    fallbacks.append("slot_options")  # no calendar integration in v0

    for key in REQUIRED:
        if not variables.get(key):
            errors.append(f"missing_required:{key}")
    if errors:
        return None, errors

    opening = brain.get("opening", "").format_map(_Vars(variables))
    first_message = f"{disclosure_prefix(brain.get('required_disclosures', []))} {opening}".strip()
    system_prompt = brain.get("system_prompt", "").format_map(_Vars(variables))

    leftover = sorted(set(PLACEHOLDER_RE.findall(first_message + " " + system_prompt)))
    if leftover:
        errors.append("unfilled_placeholder:" + ",".join(leftover))
    if AI_DISCLOSURE not in first_message:
        errors.append("missing_required:ai_disclosure")
    if errors:
        return None, errors

    spoken = {k: v for k, v in variables.items() if k not in ("script_version",) and v}
    record = ScriptRecord(
        call_id=call_id,
        contact_id=contact.id,
        script_id=brain.get("script_id", ""),
        script_version=variables["script_version"],
        segment=variables["segment"],
        pitch_angle=variables["pitch_angle"],
        first_message=first_message,
        system_prompt=system_prompt,
        variables_used=spoken,
        fallbacks_applied=fallbacks,
        created_at=iso(clock.now()),
    )
    return record, []
