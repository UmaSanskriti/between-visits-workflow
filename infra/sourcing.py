"""Sourcing stage (SRC-*). v0 ships one adapter: a CSV seed (SRC-001).

Web and directory adapters plug into the same SourceAdapter shape later (SRC-005).
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path
from typing import Protocol

from .clock import Clock, iso
from .models import Channel, Contact, ContactStatus, Suppression
from .store import Store
from .verify import normalize_e164


def short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def norm_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^(dr\.?|doctor)\s+", "", value)
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> list[dict]: ...


class CsvSeedAdapter:
    """SRC-001: a CSV of doctors. Column names follow seeds/test_doctors.csv."""

    name = "csv_seed"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self) -> list[dict]:
        with self.path.open(newline="") as fh:
            rows = []
            for row in csv.DictReader(fh):
                row = {key: (value or "").strip() for key, value in row.items() if key}
                row["source"] = f"csv:{self.path.name}"
                row["source_url"] = str(self.path)
                rows.append(row)
            return rows


def contact_id_for(adapter_name: str, row: dict) -> str:
    external_id = row.get("external_id", "")
    if external_id:
        return "c_" + short_hash(adapter_name, external_id)
    return "c_" + short_hash(
        norm_text(row.get("name", "")),
        norm_text(row.get("practice_name", "")),
        row.get("phone", ""),
        row.get("email", ""),
    )


def ingest(store: Store, adapter: SourceAdapter, clock: Clock, default_tz: str) -> dict:
    """Idempotent (SRC-007): re-running the same seed creates nothing new."""
    now = iso(clock.now())
    counts = {"rows": 0, "contacts_created": 0, "contacts_updated": 0, "channels_created": 0}
    for row in adapter.fetch():
        counts["rows"] += 1
        cid = contact_id_for(adapter.name, row)
        fields = {
            "full_name": row.get("name", ""),
            "credentials": row.get("credentials", ""),
            "specialty": row.get("specialty", ""),
            "practice_name": row.get("practice_name", ""),
            "practice_size": row.get("practice_size", ""),
            "address": row.get("address", ""),
            "city": row.get("city", ""),
            "state": row.get("state", ""),
            "time_zone": row.get("time_zone") or default_tz,
            "external_id": row.get("external_id", ""),
            "lead_source": row["source"],
            "pronunciation_hint": row.get("pronunciation_hint", ""),
            "hook": row.get("hook", ""),
            "test_scenario": row.get("scenario", ""),
        }
        contact = store.contacts.get(cid)
        if contact is None:
            contact = Contact(id=cid, status=ContactStatus.SOURCED, created_at=now, updated_at=now, **fields)
            store.add("contacts", contact, actor=adapter.name)
            counts["contacts_created"] += 1
        else:
            changed = {k: v for k, v in fields.items() if getattr(contact, k) != v}
            if changed:
                store.update("contacts", contact, "re-sourced", changed, actor=adapter.name)
                counts["contacts_updated"] += 1

        for kind, raw in (("phone", row.get("phone", "")), ("email", row.get("email", ""))):
            if not raw:
                continue
            ch_id = "ch_" + short_hash(cid, kind, raw)
            if ch_id in store.channels:
                continue
            channel = Channel(
                id=ch_id,
                contact_id=cid,
                type=kind,
                value=raw,
                raw_value=raw,
                line_type=row.get("line_type") or "unknown",
                source=row["source"],
                source_url=row["source_url"],
                retrieved_at=now,  # SRC-003: retrieval timestamp per field
            )
            store.add("channels", channel, actor=adapter.name)
            counts["channels_created"] += 1
    return counts


def load_suppressions(store: Store, path: str | Path, clock: Clock) -> int:
    """Seeded do-not-contact entries (VER-006). Idempotent."""
    path = Path(path)
    if not path.exists():
        return 0
    added = 0
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            kind = (row.get("identifier_type") or "phone").strip()
            identifier = (row.get("identifier") or "").strip()
            if kind == "phone":
                identifier = normalize_e164(identifier) or identifier
            if not identifier:
                continue
            sid = "sup_" + short_hash(kind, identifier)
            if sid in store.suppressions:
                continue
            store.add(
                "suppressions",
                Suppression(
                    id=sid,
                    identifier=identifier,
                    identifier_type=kind,
                    reason=(row.get("reason") or "").strip(),
                    source=(row.get("source") or str(path)).strip(),
                    created_at=iso(clock.now()),
                ),
                actor="seed",
            )
            added += 1
    return added


def load_clearances(path: str | Path) -> dict[str, dict]:
    """CMP-003: the authorized clearance list. Keyed by E.164 phone."""
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            phone = normalize_e164((row.get("phone") or "").strip())
            if phone:
                out[phone] = {k: (v or "").strip() for k, v in row.items()}
    return out
