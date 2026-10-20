"""Clean and verify stage (VER-*)."""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

from .clock import Clock, iso
from .models import Clearance, ConsentRecord, ContactStatus, VerificationStatus
from .scripts import segment_for
from .store import Store

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
TERMINAL = {ContactStatus.BOOKED, ContactStatus.DECLINED, ContactStatus.UNREACHABLE, ContactStatus.SUPPRESSED}


def normalize_e164(raw: str) -> str | None:
    """VER-001. US-centric for v0; a phone library replaces this when sourcing goes beyond Boston."""
    raw = (raw or "").strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits[0] == "1":
        return "+" + digits
    return None


class LineTypeLookup(Protocol):
    def lookup(self, e164: str) -> str: ...


class MockLineTypeLookup:
    """VER-002 stand-in. Returns the seeded hint; a real lookup service replaces this."""

    def __init__(self, hints: dict[str, str] | None = None):
        self.hints = hints or {}

    def lookup(self, e164: str) -> str:
        return self.hints.get(e164, "unknown")


def _norm(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^(dr\.?|doctor)\s+", "", value)
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def verify(store: Store, lookup: LineTypeLookup, clock: Clock, clearances: dict[str, dict]) -> dict:
    now = iso(clock.now())
    counts = {
        "channels_verified": 0,
        "channels_failed": 0,
        "cleared": 0,
        "verified": 0,
        "failed": 0,
        "duplicates": 0,
        "suppressed": 0,
    }

    # 1. Channels: normalize, classify line type, syntax-check emails.
    for ch in store.channels.values():
        if ch.type == "phone":
            e164 = normalize_e164(ch.raw_value or ch.value)
            if e164:
                line_type = lookup.lookup(e164) if lookup.lookup(e164) != "unknown" else (ch.line_type or "unknown")
                store.update(
                    "channels",
                    ch,
                    "verify",
                    {"value": e164, "verified": True, "verification_reason": "", "line_type": line_type},
                )
                counts["channels_verified"] += 1
            else:
                if ch.verified or ch.verification_reason != "invalid_phone_format":
                    store.update(
                        "channels", ch, "verify", {"verified": False, "verification_reason": "invalid_phone_format"}
                    )
                    store.error(
                        "invalid_phone",
                        f"could not normalize {ch.raw_value!r}",
                        contact_id=ch.contact_id,
                        field="phone",
                    )
                counts["channels_failed"] += 1
        elif ch.type == "email":
            ok = bool(EMAIL_RE.match(ch.value))
            store.update(
                "channels", ch, "verify", {"verified": ok, "verification_reason": "" if ok else "invalid_email_format"}
            )
            counts["channels_verified" if ok else "channels_failed"] += 1

    # 2. Clearance (CMP-003): the clearances file is the only thing that can set `cleared`.
    for ch in store.channels.values():
        if ch.type != "phone":
            continue
        entry = clearances.get(ch.value)
        if entry and ch.call_clearance != Clearance.CLEARED:
            store.update(
                "channels",
                ch,
                "clearance_granted",
                {"call_clearance": Clearance.CLEARED},
                actor=entry.get("cleared_by") or "clearances.csv",
            )
            cr_id = "cr_" + hashlib.sha1(ch.id.encode()).hexdigest()[:10]
            store.add(
                "consent_records",
                ConsentRecord(
                    id=cr_id,
                    contact_id=ch.contact_id,
                    channel_id=ch.id,
                    consent_type="call_clearance",
                    evidence_ref=f"clearances.csv:{entry.get('cleared_by', '')}:{entry.get('reason', '')}",
                    captured_at=entry.get("cleared_at") or now,
                ),
                actor=entry.get("cleared_by") or "clearances.csv",
            )
            counts["cleared"] += 1
        elif not entry and ch.call_clearance == Clearance.CLEARED:
            store.update(
                "channels", ch, "clearance_revoked", {"call_clearance": Clearance.NOT_CLEARED}, actor="clearances.csv"
            )
            for cr in store.consent_records.values():
                if cr.channel_id == ch.id and not cr.revoked_at:
                    store.update("consent_records", cr, "revoke", {"revoked_at": now})

    # 3. Contacts: suppression, duplicates, verification status, segment, readiness.
    # Duplicate owners are registered across ALL contacts (including closed ones) so a duplicate stays
    # a duplicate after its original books or declines.
    owners_by_key: dict[str, str] = {}
    owners_by_phone: dict[str, str] = {}
    for contact in store.contacts.values():
        if contact.duplicate_of:
            continue
        key = f"{_norm(contact.full_name)}|{_norm(contact.practice_name)}" if _norm(contact.full_name) else ""
        if key:
            owners_by_key.setdefault(key, contact.id)
        for ch in store.channels_for(contact.id, "phone"):
            if ch.verified:
                owners_by_phone.setdefault(ch.value, contact.id)
    for contact in store.contacts.values():
        if contact.status in TERMINAL:
            continue
        phones = store.channels_for(contact.id, "phone")
        emails = store.channels_for(contact.id, "email")
        changes: dict = {"segment": segment_for(contact)}

        suppressed, why = store.is_suppressed(contact)
        if suppressed:
            changes.update({"status": ContactStatus.SUPPRESSED, "verification_reason": f"suppressed:{why}"})
            store.update("contacts", contact, "suppressed_at_ingest", changes)
            counts["suppressed"] += 1
            continue

        dup_of = contact.duplicate_of if contact.duplicate_of in store.contacts else ""
        key = f"{_norm(contact.full_name)}|{_norm(contact.practice_name)}" if _norm(contact.full_name) else ""
        if not dup_of and key and owners_by_key.get(key, contact.id) != contact.id:
            dup_of = owners_by_key[key]
        for ch in phones:
            if not dup_of and ch.verified and owners_by_phone.get(ch.value, contact.id) != contact.id:
                dup_of = owners_by_phone[ch.value]
        if dup_of:
            changes.update(
                {
                    "duplicate_of": dup_of,
                    "verification_status": VerificationStatus.FAILED,
                    "verification_reason": f"duplicate_of:{dup_of}",
                    "status": ContactStatus.SOURCED,
                }
            )
            store.update("contacts", contact, "flag_duplicate", changes)
            if not any(
                e["contact_id"] == contact.id and e["error_type"] == "duplicate_candidate" for e in store.logs["errors"]
            ):
                store.error(
                    "duplicate_candidate",
                    f"matches {dup_of}; review and merge (VER-004)",
                    contact_id=contact.id,
                    field="name+practice/phone",
                )
            counts["duplicates"] += 1
            continue

        if any(ch.verified for ch in phones):
            changes.update({"verification_status": VerificationStatus.VERIFIED, "verification_reason": ""})
            if contact.status in (ContactStatus.NEW, ContactStatus.SOURCED, ContactStatus.VERIFIED):
                changes["status"] = ContactStatus.READY
            counts["verified"] += 1
        elif any(ch.verified for ch in emails):
            changes.update(
                {
                    "verification_status": VerificationStatus.FAILED,
                    "verification_reason": "no_callable_channel",
                    "status": ContactStatus.VERIFIED,
                }
            )
            if not any(e["contact_id"] == contact.id and e["field"] == "phone" for e in store.logs["errors"]):
                store.error(
                    "missing_field", "no phone channel; email only (not queued)", contact_id=contact.id, field="phone"
                )
            counts["failed"] += 1
        else:
            changes.update(
                {"verification_status": VerificationStatus.FAILED, "verification_reason": "no_valid_channel"}
            )
            if not any(e["contact_id"] == contact.id and e["field"] == "phone" for e in store.logs["errors"]):
                store.error("missing_field", "no valid phone or email channel", contact_id=contact.id, field="phone")
            counts["failed"] += 1
        store.update("contacts", contact, "verify", changes)
    return counts
