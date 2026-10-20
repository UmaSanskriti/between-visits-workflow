"""Brain interface (spec section 8). The Brain is out of scope; this is the contract plus a mock.

The mock serves script v1 from `clinic-outreach-brain.md` (owner: Anirudh Bagla). Text marked LOCKED there
is reproduced verbatim and must not be edited here either.
"""

from __future__ import annotations

import time
from typing import Protocol

from .clock import Clock, iso
from .store import Store

PLAYBOOK = "clinic-outreach-brain.md"
PLAYBOOK_VERSION = "v1"
SCRIPT_ID = "clinic-outreach-v1"
SCRIPT_VERSION = "v1"
CALLER_IDENTITY = "Anirudh Bagla, a student at Harvard Business School"
ASK_MINUTES = 30

# Connector design principle: a segment-specific lead-in before the shared pitch.
SEGMENT_GUIDANCE = {
    "solo_primary_care": "Solo primary care practices tell us GLP-1 follow-ups are eating into visit time.",
    "small_group_primary_care": "Group primary care practices tell us GLP-1 patients need more support than a "
    "visit allows.",
    "solo_specialist": "Specialists managing GLP-1 patients tell us the gaps between visits are where things slip.",
    "small_group_specialist": "Specialist groups tell us GLP-1 patients need structured nutrition support between "
    "visits.",
    "default": "Doctors tell us GLP-1 patients need more support between visits than a visit allows.",
}

# Playbook section 6. The Open (LOCKED) is prepended by the infra (compliance.py), never by the template.
ROUTE_TEMPLATE = "Could I speak with the office manager, or whoever keeps {doctor_name}'s schedule?"
PITCH_TEMPLATE = (
    "{pitch_angle} Anirudh and his co-founder help independent practices like {practice_name} give their GLP-1 "
    "patients complete care: side-effect management, nutrition and exercise support. Practices share a team of "
    "nutritionists they could not hire on their own, and the insurance billing is handled for them. We believe it "
    "can raise revenue per patient by up to six times while giving patients better care. Large health systems "
    "already run this model, and we are bringing it to independent practices. {hook_sentence}We have spoken to "
    "some clinics and would love 30 minutes with {doctor_name} to hear whether it would work here."
)
ASK_TEMPLATE = "Would {slot_1} or {slot_2} work? It can be a phone call, or we can come to the practice."
ASK_FALLBACK = (
    "When would be a good time for a 30-minute conversation? It can be a phone call, or we can come to the practice."
)
CLOSE_TEMPLATE = "Before I go: that's Anirudh Bagla, and the callback number is {callback_number}. Thank you."
VOICEMAIL_TEMPLATE = (
    "Hi, this is an AI assistant calling for Anirudh Bagla at Harvard Business School. We help independent "
    "practices give their GLP-1 patients complete care, and would love 30 minutes with {doctor_name}. "
    "Please call back at {callback_number}. Thank you."
)

OBJECTIONS = {
    "Is this a sales call?": "No, we are early and not selling anything yet. We want {doctor_name}'s honest view "
    "on whether this would help.",
    "The doctor doesn't take these.": "Same answer, then ask for the office manager.",
    "The doctor is too busy.": "Understood. Is there a better week, or a time of day that usually works for "
    "{doctor_name}?",
    "Just send us an email.": "Happy to. Could I hold {slot_1} and send the invite to that address, so "
    "{doctor_name} can accept or move it?",
    "Are you a robot / real person?": "I'm an AI assistant calling for Anirudh. He'll be the one on the actual "
    "conversation.",
    "What's your company called?": "We're early, so it's Anirudh and his co-founder at HBS. Happy to have him "
    "follow up with details.",
    "How did you get this number?": "It's the main number on your website.",
    "Asks a medical or clinical question": "I can't speak to anything clinical, but that's exactly the kind of "
    "thing Anirudh would love to hear {doctor_name}'s view on.",
    "We're hospital-owned / No GLP-1 patients": "Thank them, end politely, log NOT_A_FIT.",
    "Don't call again.": "Understood, we won't contact you again. Thank you. End. Log DO_NOT_CONTACT.",
}

SYSTEM_PROMPT_TEMPLATE = (
    "You are an AI assistant calling for {caller_identity}. Script {script_version} (playbook v1). "
    "Goal: book a 30-minute research conversation with {doctor_name} at {practice_name} on Anirudh's calendar. "
    "Second-best: a named scheduler and best time to reach the doctor; a specific call-back time; a voicemail.\n"
    "Segment: {segment} ({specialty}).{practice_size_sentence}{ehr_sentence}{pronunciation_sentence}\n"
    "Rules (LOCKED): you already opened with the AI and recording disclosure; if anyone objects to recording, end "
    "politely and report DECLINED_RECORDING. Never pretend to be human. No patient information. No medical claims, "
    "no advice about drugs, dosing, side effects, or outcomes. Always say 'we believe' and 'up to'; never a "
    "guarantee. If they say not to call or contact again, say 'Understood, we won't contact you again. Thank you.', "
    "end, and report DO_NOT_CONTACT. Hospital-owned or no GLP-1 patients: thank them, end, report NOT_A_FIT. "
    'Before hanging up, always say: "{close}"\n'
    "Route: {route}\n"
    "Pitch, under 30 seconds, once you reach the office manager or the doctor: {pitch}\n"
    "Ask: {ask} If neither works, offer {slot_3} or {slot_4}. If none work, ask for the best week or time of day. "
    "Once a time is agreed, confirm date, time, call or visit, and where to send the invite. "
    "If it can't book: 'When is {doctor_name} or the office manager easiest to reach?'\n"
    "Objections: {objections}\n"
    "Phone trees: choose front desk, office, or 'all other inquiries'; avoid pharmacy, refill, billing, nurse, "
    "emergency, or clinical options; if the tree loops twice, press 0 or say 'operator'.\n"
    "Voicemail: {voicemail}"
)

BANNED_CLAIMS = [
    "any guarantee of revenue or outcomes (say 'we believe' and 'up to')",
    "medical advice about drugs, dosing, side effects, or outcomes",
    "that we are selling something or have paying customers",
    "pretending to be human",
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
            "playbook": PLAYBOOK,
            "playbook_version": PLAYBOOK_VERSION,
            "route": ROUTE_TEMPLATE,
            "pitch": PITCH_TEMPLATE,
            "ask": ASK_TEMPLATE,
            "ask_fallback": ASK_FALLBACK,
            "close": CLOSE_TEMPLATE,
            "voicemail": VOICEMAIL_TEMPLATE,
            "system_prompt": SYSTEM_PROMPT_TEMPLATE,
            "objections": OBJECTIONS,
            "pitch_angle": SEGMENT_GUIDANCE.get(segment, SEGMENT_GUIDANCE["default"]),
            "caller_identity": CALLER_IDENTITY,
            "required_disclosures": ["ai_identity", "recording"],
            "booking_instructions": {
                "interview_length_minutes": ASK_MINUTES,
                "approval_required": True,
                "approver": "Anirudh Bagla",
            },
            "max_attempts": 2,
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
            "failure_category": package.get("failure_category", ""),
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
