"""Transcript delivery to the Brain (BRN-004..007): at-least-once, idempotent, dead-lettered on failure."""

from __future__ import annotations

import time

from .brain import BrainClient, BrainRejected, BrainUnavailable
from .clock import Clock, iso
from .compliance import redact
from .config import Config
from .models import DeliveryStatus
from .store import Store


def attempt_for(store: Store, attempt_id: str):
    """A transcript belongs to a call attempt or an email attempt."""
    return store.call_attempts.get(attempt_id) or store.email_attempts.get(attempt_id)


def build_package(store: Store, transcript) -> dict:
    attempt = attempt_for(store, transcript.call_attempt_id)
    if attempt is None:
        raise KeyError(transcript.call_attempt_id)
    contact = store.contacts[transcript.contact_id]
    is_email = transcript.call_attempt_id in store.email_attempts
    return {
        "idempotency_key": transcript.idempotency_key or attempt.id,
        "channel": "email" if is_email else "call",
        "call_attempt_id": attempt.id,
        "call_id": attempt.id,
        "contact_id": contact.id,
        "script_version": attempt.script_version,
        "outcome": attempt.outcome,
        "failure_category": attempt.failure_category,
        "started_at": attempt.sent_at if is_email else attempt.started_at,
        "ended_at": attempt.replied_at if is_email else attempt.ended_at,
        "doctor_meta": {
            "segment": attempt.segment,
            "specialty": contact.specialty,
            "practice_size": contact.practice_size,
        },
        "utterances": [{**u, "text": redact(u["text"])} for u in transcript.utterances],
        "recording_ref": "" if is_email else attempt.recording_ref,
        "pii_handling": "redacted",
        "dry_run": {"scenario": contact.test_scenario},
    }


def deliver_transcripts(store: Store, cfg: Config, clock: Clock, brain: BrainClient, run_id: str) -> dict:
    counts = {"attempted": 0, "delivered": 0, "failed": 0, "held": 0, "duplicates": 0}
    for transcript in list(store.transcripts.values()):
        if transcript.delivery_status == DeliveryStatus.HELD:
            counts["held"] += 1
            continue
        if transcript.delivery_status != DeliveryStatus.PENDING:
            continue
        counts["attempted"] += 1
        package = build_package(store, transcript)
        last_error = ""
        delivered = False
        for i in range(cfg.delivery_max_attempts):
            try:
                result = brain.deliver_transcript(package)
                delivered = True
                if result.get("status") == "duplicate":
                    counts["duplicates"] += 1
                break
            except (BrainRejected, BrainUnavailable) as exc:
                last_error = str(exc)
                transcript.delivery_attempts += 1
                if cfg.delivery_backoff_seconds > 0 and i < cfg.delivery_max_attempts - 1:
                    time.sleep(cfg.delivery_backoff_seconds * (2**i))
        if delivered:
            store.update(
                "transcripts",
                transcript,
                "delivered",
                {
                    "delivery_status": DeliveryStatus.DELIVERED,
                    "delivered_at": iso(clock.now()),
                    "last_delivery_error": "",
                },
                run_id=run_id,
            )
            counts["delivered"] += 1
        else:
            store.update(
                "transcripts",
                transcript,
                "delivery_failed",
                {"delivery_status": DeliveryStatus.FAILED, "last_delivery_error": last_error},
                run_id=run_id,
            )
            store.dead_letter(
                {
                    "transcript_id": transcript.id,
                    "call_attempt_id": transcript.call_attempt_id,
                    "attempts": transcript.delivery_attempts,
                    "last_error": last_error,
                    "run_id": run_id,
                }
            )
            store.error(
                "delivery_failed",
                last_error,
                call_id=transcript.call_attempt_id,
                contact_id=transcript.contact_id,
                field="transcript",
            )
            store.alert(
                "transcript_delivery_failed",
                f"{transcript.id} dead-lettered after {transcript.delivery_attempts} attempts",
                transcript_id=transcript.id,
                run_id=run_id,
            )
            counts["failed"] += 1
    return counts


def redrive_dead_letters(store: Store, clock: Clock) -> int:
    """Operator action: move failed transcripts back to pending and clear their DLQ entries."""
    moved = 0
    for transcript in store.transcripts.values():
        if transcript.delivery_status == DeliveryStatus.FAILED:
            store.update(
                "transcripts",
                transcript,
                "redrive",
                {"delivery_status": DeliveryStatus.PENDING, "delivery_attempts": 0},
                actor="operator",
            )
            moved += 1
    store.logs["dlq"] = []
    return moved
