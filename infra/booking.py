"""Booking handoff (LOG-003, LOG-004). The calendar tool is undecided, so v0 writes an .ics file per interview."""

from __future__ import annotations

import hashlib
from datetime import UTC, timedelta
from pathlib import Path

from .clock import Clock, iso, parse_iso
from .models import CallAttempt, Contact, Interview
from .store import Store


def ensure_interview(
    store: Store, contact: Contact, attempt: CallAttempt, slot: str, clock: Clock
) -> tuple[Interview, bool]:
    """Idempotent: one interview per call attempt (AC-8)."""
    interview_id = "int_" + hashlib.sha1(attempt.id.encode()).hexdigest()[:10]
    existing = store.interviews.get(interview_id)
    if existing:
        return existing, False
    emails = [ch for ch in store.channels_for(contact.id, "email") if ch.verified]
    interview = Interview(
        id=interview_id,
        contact_id=contact.id,
        call_attempt_id=attempt.id,
        scheduled_for=slot,
        confirmation_email=f"dry_run_would_send:{emails[0].value}" if emails else "skipped_no_verified_email",
        created_at=iso(clock.now()),
    )
    store.add("interviews", interview, call_attempt_id=attempt.id, contact_id=contact.id)
    return interview, True


class FileBookingHandoff:
    """Writes one .ics per interview. Replace with a calendar API client once the tool is chosen."""

    def __init__(self, out_dir: Path | str):
        self.out_dir = Path(out_dir)

    def handoff(self, store: Store, interview: Interview, contact: Contact, clock: Clock, minutes: int = 30) -> str:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        start = parse_iso(interview.scheduled_for).astimezone(UTC)
        end = start + timedelta(minutes=minutes)
        fmt = "%Y%m%dT%H%M%SZ"
        who = contact.full_name or "the doctor"
        ics = "\r\n".join(
            [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//Between Visits//infra v0//EN",
                "BEGIN:VEVENT",
                f"UID:{interview.id}@between-visits",
                f"DTSTAMP:{clock.now().astimezone(UTC).strftime(fmt)}",
                f"DTSTART:{start.strftime(fmt)}",
                f"DTEND:{end.strftime(fmt)}",
                f"SUMMARY:DRAFT pending approval: {minutes}-min conversation, {who} ({contact.practice_name})",
                f"DESCRIPTION:Drafted by the calling agent (dry run); held for Anirudh's approval (playbook 5.12). "
                f"Call attempt {interview.call_attempt_id}.",
                "END:VEVENT",
                "END:VCALENDAR",
                "",
            ]
        )
        path = self.out_dir / f"{interview.id}.ics"
        path.write_text(ics)
        ref = f"file:{path.as_posix()}"
        store.update("interviews", interview, "draft_invite", {"calendar_event_ref": ref})
        return ref


def approve_interview(store: Store, interview: Interview, approver: str, clock: Clock) -> Interview:
    """Playbook section 9: the invite goes out only after one-click approval."""
    store.update(
        "interviews",
        interview,
        "approve",
        {"status": "approved", "approved_by": approver, "approved_at": iso(clock.now())},
        actor=approver,
    )
    return interview
