"""Brain interface (section 8). The Brain is out of scope; this is the contract plus a mock."""

from __future__ import annotations

import time
from typing import Protocol

from .clock import Clock, iso
from .store import Store

SCRIPT_ID = "s_discovery_v1"
SCRIPT_VERSION = "2026-10-20.1"

SEGMENT_GUIDANCE = {
    "solo_primary_care": "Solo primary care practices tell us GLP-1 follow-ups are eating into visit time.",
    "small_group_primary_care": "Group primary care practices tell us GLP-1 patients need more support than a "
    "visit allows.",
    "solo_specialist": "Specialists managing GLP-1 patients tell us the gaps between visits are where things slip.",
    "small_group_specialist": "Specialist groups tell us GLP-1 patients need structured nutrition support between "
    "visits.",
    "default": "Doctors tell us GLP-1 patients need more support between visits than a visit allows.",
}

OPENING_TEMPLATE = (
    "{greeting} I'm calling from {caller_identity}. {pitch_angle} We're building nutritionist and coach "
    "support that sits between visits, and we're learning from doctors before we build further. {ask} "
    "We can do {slot_options}."
)

SYSTEM_PROMPT_TEMPLATE = (
    "You are an AI assistant for {caller_identity}. Goal: book a 20-minute discovery conversation with "
    "{doctor_name} ({specialty}) at {practice_name}.{practice_size_sentence}{ehr_sentence}{pronunciation_sentence} "
    "Segment: {segment}. Script version: {script_version}. Lead with this angle: {pitch_angle} "
    "If asked, we are a student venture, not a vendor; no sales pitch. Never request or discuss any patient's "
    "identifying information. If the person asks you to stop calling, apologize, confirm you will not call "
    "again, and end the call. Honor any request for a human follow-up by offering an email from a founder."
)

BANNED_CLAIMS = [
    "clinical outcomes or weight-loss results",
    "endorsement by any hospital, insurer, or Harvard",
    "that the service is live or has paying customers",
]


class BrainUnavailable(Exception):
    pass


class BrainRejected(Exception):
    pass


class BrainClient(Protocol):
    def resolve_script(self, request: dict) -> dict: ...

    def deliver_transcript(self, package: dict) -> dict: ...


class MockBrain:
    """Serves one versioned template and keeps an inbox of delivered transcripts (idempotent)."""

    def __init__(self, store: Store, clock: Clock, down: bool = False):
        self.store = store
        self.clock = clock
        self.down = down
        self.resolve_calls = 0
        self.deliver_calls = 0

    def resolve_script(self, request: dict) -> dict:
        self.resolve_calls += 1
        if self.down:
            raise BrainUnavailable("mock Brain unreachable (BV_MOCK_BRAIN_DOWN=1)")
        segment = request.get("contact", {}).get("segment") or "default"
        return {
            "script_id": SCRIPT_ID,
            "script_version": SCRIPT_VERSION,
            "opening": OPENING_TEMPLATE,
            "system_prompt": SYSTEM_PROMPT_TEMPLATE,
            "pitch_angle": SEGMENT_GUIDANCE.get(segment, SEGMENT_GUIDANCE["default"]),
            "caller_identity": "Between Visits, a Harvard student venture",
            "ask": "Would you be open to a 20-minute conversation with one of our founders about how you "
            "manage GLP-1 patients between visits?",
            "required_disclosures": ["ai_identity", "recording"],
            "booking_instructions": {"interview_length_minutes": 20},
            "banned_claims": BANNED_CLAIMS,
        }

    def deliver_transcript(self, package: dict) -> dict:
        self.deliver_calls += 1
        if self.down:
            raise BrainUnavailable("mock Brain unreachable (BV_MOCK_BRAIN_DOWN=1)")
        key = package["idempotency_key"]
        if key in self.store.brain_inbox:
            return {"status": "duplicate", "idempotency_key": key}
        if package.get("dry_run", {}).get("scenario") == "delivery_fails":
            raise BrainRejected("mock Brain returned 500 (scenario delivery_fails)")
        self.store.brain_inbox[key] = {
            "received_at": iso(self.clock.now()),
            "call_attempt_id": package["call_attempt_id"],
            "contact_id": package["contact_id"],
            "outcome": package["outcome"],
            "script_version": package["script_version"],
            "segment": package["doctor_meta"].get("segment", ""),
            "utterance_count": len(package["utterances"]),
        }
        return {"status": "accepted", "idempotency_key": key}


def with_retries(fn, attempts: int, backoff_seconds: float, retry_on=(Exception,)):
    """BRN-003 / BRN-005: bounded retries with (simulated) exponential backoff."""
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except retry_on as exc:  # noqa: PERF203
            last = exc
            if i < attempts - 1 and backoff_seconds > 0:
                time.sleep(backoff_seconds * (2**i))
    assert last is not None
    raise last
