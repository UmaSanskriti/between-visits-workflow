"""Daily summary counts (LOG-005) and the connector's outcome summary by script version and segment."""

from __future__ import annotations

from collections import Counter, defaultdict

from .models import CONNECTED_OUTCOMES, ContactStatus, DeliveryStatus, Outcome
from .store import Store


def _breakdown(attempts, key):
    groups: dict[str, list] = defaultdict(list)
    for a in attempts:
        groups[getattr(a, key) or "(none)"].append(a)
    out = {}
    for name, rows in sorted(groups.items()):
        outcomes = Counter(str(a.outcome) if a.outcome else "(pending)" for a in rows)
        booked = outcomes.get(Outcome.BOOKED, 0) + outcomes.get(Outcome.AGREED_TO_TALK, 0)
        out[name] = {
            "attempts": len(rows),
            "booked": booked,
            "declined": outcomes.get(Outcome.DECLINED, 0),
            "by_outcome": dict(outcomes),
            "conversion": round(booked / len(rows), 3) if rows else 0.0,
        }
    return out


REPLIED_OUTCOMES = {Outcome.AGREED_TO_TALK, Outcome.DECLINED, Outcome.DO_NOT_CONTACT, Outcome.REPLIED_OTHER}


def email_summary(store: Store, run: dict) -> dict:
    emails = sorted(store.email_attempts.values(), key=lambda a: a.sent_at or a.created_at)
    limits = run.get("limits") or {}
    max_messages = int(limits.get("max_messages", 2))
    now_iso = run.get("finished_at") or run.get("started_at") or ""
    sent = [a for a in emails if a.status in ("sent", "bounced")]
    replied = [a for a in emails if a.outcome in REPLIED_OUTCOMES]
    contacts = store.contacts
    follow_ups_due = sum(
        1
        for c in contacts.values()
        if c.status == ContactStatus.IN_OUTREACH
        and c.attempts < max_messages
        and c.next_attempt_at
        and now_iso
        and c.next_attempt_at <= now_iso
        and any(a.contact_id == c.id for a in emails)
    )
    return {
        "attempts": len(emails),
        "sent": len(sent),
        "queued": sum(1 for a in emails if a.status == "queued"),
        "failed": sum(1 for a in emails if a.status == "failed"),
        "by_outcome": dict(Counter(str(a.outcome) if a.outcome else "(awaiting reply)" for a in emails)),
        "replied": len(replied),
        "agreed": sum(1 for a in emails if a.outcome in (Outcome.AGREED_TO_TALK, Outcome.BOOKED)),
        "declined": sum(1 for a in emails if a.outcome == Outcome.DECLINED),
        "stop": sum(1 for a in emails if a.outcome == Outcome.DO_NOT_CONTACT),
        "bounced": sum(1 for a in emails if a.outcome == Outcome.BOUNCED),
        "needs_review": sum(1 for a in emails if a.outcome == Outcome.REPLIED_OTHER),
        "no_reply": sum(1 for a in emails if a.outcome == Outcome.NO_REPLY),
        "awaiting_reply": sum(1 for a in emails if a.status == "sent" and not a.outcome),
        "follow_ups_due": follow_ups_due,
        "reply_rate": round(len(replied) / len(sent), 3) if sent else None,
        "by_script_version": _breakdown(emails, "script_version"),
        "by_segment": _breakdown(emails, "segment"),
        "duplicate_events_ignored": sum(a.duplicate_events_ignored for a in emails),
    }


def build_summary(store: Store, run: dict) -> dict:
    contacts = list(store.contacts.values())
    attempts = sorted(store.call_attempts.values(), key=lambda a: a.started_at)
    emails = list(store.email_attempts.values())
    transcripts = list(store.transcripts.values())
    run_id = run.get("run_id", "")
    channel = run.get("channel") or "call"
    decisions = [d for d in store.logs["gate_decisions"] if d["run_id"] == run_id]

    by_status = Counter(str(c.status) for c in contacts)
    verified = sum(1 for c in contacts if c.verification_status == "verified")
    completed = [a for a in attempts if a.outcome]
    connected = sum(1 for a in completed if a.outcome in CONNECTED_OUTCOMES)
    booked = sum(1 for a in completed if a.outcome in (Outcome.BOOKED, Outcome.AGREED_TO_TALK))
    deliverable = [t for t in transcripts if t.delivery_status != DeliveryStatus.HELD]
    delivered = sum(1 for t in deliverable if t.delivery_status == DeliveryStatus.DELIVERED)
    gate_escapes = sum(1 for a in attempts if a.gate_result != "allowed") + sum(
        1 for a in emails if a.gate_result != "allowed"
    )
    open_email = {a.contact_id for a in emails if a.status == "sent" and not a.outcome}

    queue = []
    for c in contacts:
        if c.status in (ContactStatus.READY, ContactStatus.IN_OUTREACH):
            queue.append(
                {
                    "contact_id": c.id,
                    "external_id": c.external_id,
                    "name": c.full_name or "(no name)",
                    "practice": c.practice_name,
                    "segment": c.segment,
                    "attempts": c.attempts,
                    "next_attempt_at": c.next_attempt_at,
                    "last_outcome": c.last_outcome,
                    "status": c.status,
                    "awaiting_reply": c.id in open_email,
                    "queued_email": any(a.contact_id == c.id and a.status == "queued" for a in emails),
                }
            )
    queue.sort(key=lambda q: (q["next_attempt_at"] or "", q["name"]))

    if channel == "email":
        cleared = sum(
            1
            for c in contacts
            if c.verification_status == "verified"
            and any(ch.call_clearance == "cleared" for ch in store.channels_for(c.id, "email"))
        )
        funnel = [
            {"stage": "Sourced", "count": len(contacts)},
            {"stage": "Verified", "count": verified},
            {"stage": "Cleared to email", "count": cleared},
            {"stage": "Emailed", "count": len({a.contact_id for a in emails if a.status in ("sent", "bounced")})},
            {"stage": "Replied", "count": len({a.contact_id for a in emails if a.outcome in REPLIED_OUTCOMES})},
            {"stage": "Agreed", "count": by_status.get(ContactStatus.BOOKED, 0)},
        ]
    else:
        funnel = [
            {"stage": "Sourced", "count": len(contacts)},
            {"stage": "Verified", "count": verified},
            {
                "stage": "Cleared to call",
                "count": sum(
                    1
                    for c in contacts
                    if c.verification_status == "verified"
                    and any(ch.call_clearance == "cleared" for ch in store.channels_for(c.id, "phone"))
                ),
            },
            {"stage": "Called", "count": len({a.contact_id for a in attempts})},
            {"stage": "Connected", "count": len({a.contact_id for a in completed if a.outcome in CONNECTED_OUTCOMES})},
            {"stage": "Booked", "count": by_status.get(ContactStatus.BOOKED, 0)},
        ]

    return {
        "generated_at": run.get("finished_at") or run.get("started_at", ""),
        "run_id": run_id,
        "channel": channel,
        "emails": email_summary(store, run),
        "contacts": {
            "total": len(contacts),
            "verified": verified,
            "by_status": dict(by_status),
            "by_verification": dict(Counter(str(c.verification_status) for c in contacts)),
        },
        "funnel": funnel,
        "calls": {
            "attempts": len(attempts),
            "completed": len(completed),
            "by_outcome": dict(Counter(str(a.outcome) if a.outcome else "(pending)" for a in attempts)),
            "connect_rate": round(connected / len(completed), 3) if completed else 0.0,
            "booked": booked,
            "by_script_version": _breakdown(attempts, "script_version"),
            "by_segment": _breakdown(attempts, "segment"),
            "duplicate_webhooks_ignored": sum(a.duplicate_events_ignored for a in attempts),
            "by_failure_category": dict(Counter(a.failure_category for a in completed if a.failure_category)),
        },
        "interviews": len(store.interviews),
        "interviews_pending_approval": sum(
            1 for i in store.interviews.values() if i.status == "draft_pending_approval"
        ),
        "transcripts": {
            "total": len(transcripts),
            "delivered": delivered,
            "pending": sum(1 for t in transcripts if t.delivery_status == DeliveryStatus.PENDING),
            "failed": sum(1 for t in transcripts if t.delivery_status == DeliveryStatus.FAILED),
            "held_pii_review": sum(1 for t in transcripts if t.delivery_status == DeliveryStatus.HELD),
            "delivery_rate": round(delivered / len(deliverable), 3) if deliverable else None,
            "brain_inbox": len(store.brain_inbox),
        },
        "compliance": {
            "gate_escapes": gate_escapes,
            "kill_switch": bool(run.get("kill_switch")),
            "paused": bool(store.state.get("paused")),
            "suppressions": len(store.suppressions),
            "consent_records": len(store.consent_records),
            "last_run": {
                "considered": len(decisions),
                "allowed": sum(1 for d in decisions if d["allowed"]),
                "blocked": sum(1 for d in decisions if not d["allowed"] and not d["deferred"]),
                "deferred": sum(1 for d in decisions if not d["allowed"] and d["deferred"]),
            },
        },
        "errors": len(store.logs["errors"]),
        "dlq": len(store.logs["dlq"]),
        "alerts": len(store.logs["alerts"]),
        "queue": queue[:50],
    }
