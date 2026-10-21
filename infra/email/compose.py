"""Compose the cold email (playbook section 8b) from the Brain's template, enforcing the email rules."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..calendar import stub_slots
from ..clock import Clock
from ..config import Config
from ..models import Channel, Contact, Enrichment

PLACEHOLDER_RE = re.compile(r"\{[a-z_0-9]+\}")
OPT_OUT_LINE = 'Reply "stop" and we won\'t contact you again.'
MAX_WORDS = 150


@dataclass
class EmailDraft:
    subject: str
    body: str
    script_version: str
    variables_used: dict = field(default_factory=dict)
    fallbacks_applied: list[str] = field(default_factory=list)
    word_count: int = 0
    personalization: list[str] = field(default_factory=list)


class _Vars(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _fill(template: str, variables: dict) -> str:
    return (template or "").format_map(_Vars(variables))


def build_email(
    contact: Contact,
    channel: Channel,
    brain_email: dict,
    cfg: Config,
    clock: Clock,
    enrichment: Enrichment | None = None,
) -> tuple[EmailDraft | None, list[str]]:
    variables: dict = {}
    fallbacks: list[str] = []
    errors: list[str] = []
    personalization: list[str] = []

    last_name = contact.full_name.strip().split()[-1] if contact.full_name.strip() else ""
    if last_name:
        variables["last_name"] = last_name
        variables["doctor_name"] = f"Dr. {last_name}"
        variables["greeting"] = f"Hi Dr. {last_name},"
        variables["subject"] = _fill(brain_email.get("subject", ""), variables)
    else:
        variables["last_name"] = ""
        variables["doctor_name"] = "Doctor"
        variables["greeting"] = "Hi there,"
        variables["subject"] = (
            brain_email.get("subject_fallback") or "30 minutes with your practice? (HBS research, not sales)"
        )
        fallbacks.append("doctor_name")

    if contact.practice_name.strip():
        variables["practice_name"] = contact.practice_name.strip()
    else:
        variables["practice_name"] = "your practice"
        fallbacks.append("practice_name")

    # Personalisation: what we found about the practice (enrichment) beats the seed hook, which beats nothing.
    if enrichment and enrichment.hook.strip():
        variables["hook_sentence"] = (
            f"I saw on your site that {enrichment.hook.strip().rstrip('.')} and wanted to reach out. "
        )
        personalization.append("hook: " + enrichment.hook.strip())
    elif contact.hook.strip():
        variables["hook_sentence"] = f"I saw {contact.hook.strip()} and wanted to reach out. "
        personalization.append("hook: " + contact.hook.strip())
    else:
        variables["hook_sentence"] = ""
        fallbacks.append("hook")
    variables["persona_sentence"] = ""
    if enrichment:
        practice = contact.practice_name.strip() or "your practice"
        if enrichment.glp1_mentioned:
            variables["persona_sentence"] = (
                f"Since {practice} already supports GLP-1 patients, your view of what happens between visits "
                "would be especially useful."
            )
            personalization.append("offers GLP-1 care")
        elif enrichment.weight_management_mentioned:
            variables["persona_sentence"] = (
                "Your weight-management program is exactly the setting we are trying to learn from."
            )
            personalization.append("runs a weight-management program")
        if enrichment.patient_focus.strip():
            focus = enrichment.patient_focus.strip().rstrip(".")
            variables["persona_sentence"] = (
                variables["persona_sentence"] + " " if variables["persona_sentence"] else ""
            ) + (f"Given your focus on {focus}, I'd value your perspective.")
            personalization.append("focus: " + focus)
        if enrichment.doctor_bio.strip():
            personalization.append("bio: " + enrichment.doctor_bio.strip()[:80])

    slots = stub_slots(clock.now(), contact.time_zone or cfg.default_time_zone)
    if len(slots) >= 2:
        variables["slot_1"], variables["slot_2"] = slots[0]["label"], slots[1]["label"]
        variables["slot_1_iso"] = slots[0]["iso"]
    else:
        variables["slot_1"], variables["slot_2"] = "a time next week", "the week after"
        variables["slot_1_iso"] = ""
        fallbacks.append("slot_options")

    variables["sender_name"] = (cfg.email_from_name or "").strip()
    if not variables["sender_name"]:
        errors.append("missing_required:sender_name")
    if cfg.callback_number:
        variables["signature_number"] = f" · {cfg.callback_number}"
    else:
        variables["signature_number"] = ""
        fallbacks.append("callback_number")
    variables["opt_out_line"] = OPT_OUT_LINE
    variables["script_version"] = brain_email.get("script_version") or ""
    if not variables["script_version"]:
        errors.append("missing_required:script_version")
    if errors:
        return None, errors

    variables["core_paragraph"] = _fill(brain_email.get("core_paragraph", ""), variables)
    body = _fill(brain_email.get("body", ""), variables).strip() + "\n"
    if len(body.split()) > MAX_WORDS and brain_email.get("core_paragraph_short"):
        variables["core_paragraph"] = _fill(brain_email["core_paragraph_short"], variables)  # keep under the cap
        body = _fill(brain_email.get("body", ""), variables).strip() + "\n"
        fallbacks.append("core_paragraph_shortened")
    subject = variables["subject"]
    leftover = sorted(set(PLACEHOLDER_RE.findall(subject + " " + body)))
    if leftover:
        errors.append("unfilled_placeholder:" + ",".join(leftover))
    words = len(body.split())
    if words > MAX_WORDS:
        errors.append(f"email_too_long:{words}_words_over_{MAX_WORDS}")
    if body.count("?") > 1:
        errors.append("more_than_one_ask")
    if OPT_OUT_LINE not in body:
        errors.append("missing_required:opt_out_line")
    if variables["sender_name"] not in body:
        errors.append("missing_required:sender_name_in_body")
    if "<" in body and ">" in body and re.search(r"<[a-z]+[^>]*>", body):
        errors.append("not_plain_text")
    if errors:
        return None, errors

    spoken = {k: v for k, v in variables.items() if v and k not in ("script_version",)}
    return (
        EmailDraft(
            subject=subject,
            body=body,
            script_version=variables["script_version"],
            variables_used=spoken,
            fallbacks_applied=fallbacks,
            word_count=words,
            personalization=personalization,
        ),
        [],
    )
