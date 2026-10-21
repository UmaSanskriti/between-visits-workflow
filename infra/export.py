"""Export a masked snapshot for the GitHub Pages dashboard (NFR-004: phone/email masked outside the DB)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .store import Store

PHONE_RE = re.compile(r"\+\d{8,15}")
EMAIL_RE = re.compile(r"[^@\s\"']+@[^@\s\"']+\.[^@\s\"']+")


def mask_phone(value: str) -> str:
    return value[:2] + "•" * (len(value) - 6) + value[-4:] if len(value) >= 8 else "•••"


def mask_email(value: str) -> str:
    name, _, domain = value.partition("@")
    return (name[:1] + "•••@" + domain) if domain else "•••"


def mask_text(value: str) -> str:
    value = PHONE_RE.sub(lambda m: mask_phone(m.group(0)), value)
    return EMAIL_RE.sub(lambda m: mask_email(m.group(0)), value)


def mask_any(obj):
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, list):
        return [mask_any(x) for x in obj]
    if isinstance(obj, dict):
        return {k: mask_any(v) for k, v in obj.items()}
    return obj


def export(store: Store, export_dir: Path | str, summary: dict, run: dict) -> list[Path]:
    out = Path(export_dir)
    out.mkdir(parents=True, exist_ok=True)
    written = []

    def dump(name: str, payload) -> None:
        path = out / f"{name}.json"
        path.write_text(json.dumps(mask_any(payload), indent=1, sort_keys=True) + "\n")
        written.append(path)

    contacts = []
    for c in store.contacts.values():
        row = c.to_dict()
        row["channels"] = [ch.to_dict() for ch in store.channels_for(c.id)]
        contacts.append(row)
    dump("run", run)
    dump("summary", summary)
    dump("contacts", contacts)
    dump(
        "call_attempts",
        sorted((a.to_dict() for a in store.call_attempts.values()), key=lambda a: a["started_at"], reverse=True),
    )
    dump(
        "email_attempts",
        sorted(
            (a.to_dict() for a in store.email_attempts.values()),
            key=lambda a: a["sent_at"] or a["created_at"],
            reverse=True,
        ),
    )
    dump("scripts", [s.to_dict() for s in store.scripts.values()])
    dump("enrichments", [e.to_dict() for e in store.enrichments.values()])
    dump("transcripts", [t.to_dict() for t in store.transcripts.values()])
    dump("interviews", [i.to_dict() for i in store.interviews.values()])
    dump("suppressions", [s.to_dict() for s in store.suppressions.values()])
    dump("consent_records", [c.to_dict() for c in store.consent_records.values()])
    dump("gate_decisions", store.logs["gate_decisions"][-400:])
    dump("errors", store.logs["errors"][-300:])
    dump("dlq", store.logs["dlq"])
    dump("alerts", store.logs["alerts"][-300:])
    dump("runs", store.logs["runs"][-60:])
    dump("audit_log", store.audit_log[-400:])
    dump("brain_inbox", store.brain_inbox)
    return written
