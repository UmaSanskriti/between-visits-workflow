"""Voice provider abstraction (section 4.1) and the dry-run mock (NFR-007).

No business logic depends on a vendor SDK. A real provider implements VoiceProvider; v0 ships only the mock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

from .clock import Clock, add_business_days, iso, to_local
from .models import Outcome


@dataclass
class CallRequest:
    call_attempt_id: str
    contact_id: str
    to_number: str
    from_number: str
    first_message: str
    system_prompt: str
    script_version: str
    time_zone: str = "America/New_York"
    metadata: dict = field(default_factory=dict)


class VoiceProvider(Protocol):
    name: str

    def place_call(self, request: CallRequest) -> str: ...

    def get_call_status(self, provider_call_id: str) -> dict: ...

    def drain_events(self) -> list[dict]: ...

    def verify_signature(self, event: dict) -> bool: ...


OUTBOUND_CALLER_ID = "+16175550199"  # fictional; a real number with STIR/SHAKEN attestation replaces it (CMP-007)

# scenario -> (provider outcome label, duration seconds, doctor-side lines)
SCENARIOS: dict[str, tuple[str, int, list[tuple[str, str]]]] = {
    "booked": (
        Outcome.BOOKED,
        142,
        [
            ("doctor", "Sure, GLP-1 follow-ups are a real strain. I can do 20 minutes."),
            ("agent", "Wonderful. I'll book it and send a confirmation. Thank you, Doctor."),
        ],
    ),
    "declined": (
        Outcome.DECLINED,
        48,
        [
            ("doctor", "I appreciate it, but we're not taking on anything new right now."),
            ("agent", "Understood, thank you for your time. Have a good day."),
        ],
    ),
    "no_answer": (Outcome.NO_ANSWER, 0, []),
    "voicemail": (Outcome.VOICEMAIL, 9, []),
    "callback": (
        Outcome.CALLBACK_REQUESTED,
        61,
        [
            ("doctor", "I'm with patients. Can you call back tomorrow afternoon?"),
            ("agent", "Of course. I'll call back tomorrow afternoon. Thank you."),
        ],
    ),
    "gatekeeper": (
        Outcome.GATEKEEPER_BLOCKED,
        33,
        [
            ("office", "The doctor doesn't take calls like this. You can send something in writing."),
            ("agent", "Thank you, I understand. Have a good day."),
        ],
    ),
    "wrong_number": (
        Outcome.WRONG_NUMBER,
        18,
        [
            ("other", "You've got the wrong number, there's no doctor here."),
            ("agent", "My apologies. I'll make sure we don't call this number again."),
        ],
    ),
    "technical": (Outcome.FAILED_TECHNICAL, 0, []),
    "stop_calling": (
        Outcome.DECLINED,
        27,
        [
            ("doctor", "Please don't call this office again. Take us off your list."),
            ("agent", "I'm sorry for the interruption. I've noted that and we will not call again."),
        ],
    ),
    "malformed_transcript": (Outcome.DECLINED, 40, []),
    "patient_data": (
        Outcome.DECLINED,
        75,
        [
            ("doctor", "Not now. I have a patient, John Doe, date of birth 03/04/1980, waiting on me."),
            ("agent", "Understood, I'll let you go. Thank you, Doctor."),
        ],
    ),
    "delivery_fails": (
        Outcome.BOOKED,
        120,
        [
            ("doctor", "Yes, happy to talk. Book me for 20 minutes."),
            ("agent", "Great, I'll send the details. Thank you."),
        ],
    ),
}
DEFAULT_ROTATION = ["booked", "declined", "no_answer", "voicemail"]


class MockVoiceProvider:
    """Deterministic provider. Emits every completion webhook twice to exercise idempotency (AC-5)."""

    name = "mock"

    def __init__(self, secret: str, clock: Clock):
        self.secret = secret.encode()
        self.clock = clock
        self._pending: list[dict] = []
        self._status: dict[str, dict] = {}
        self.calls_placed = 0

    def place_call(self, request: CallRequest) -> str:
        self.calls_placed += 1
        provider_call_id = "mock_" + request.call_attempt_id
        scenario = request.metadata.get("scenario") or self._default_scenario(request.contact_id)
        if scenario not in SCENARIOS:
            scenario = "no_answer"
        outcome, duration, lines = SCENARIOS[scenario]
        started = self.clock.now()
        ended = started + timedelta(seconds=duration)
        transcript: object = []
        if duration > 0:
            transcript = [{"speaker": "agent", "start_ms": 0, "text": request.first_message}]
            offset = 4000
            for speaker, text in lines:
                transcript.append({"speaker": speaker, "start_ms": offset, "text": text})
                offset += 6000
        if scenario == "malformed_transcript":
            transcript = "##garbled## (provider returned a non-list transcript)"
        payload = {
            "event_id": f"{provider_call_id}:completed",
            "type": "call.completed",
            "provider_call_id": provider_call_id,
            "call_attempt_id": request.call_attempt_id,
            "outcome": str(outcome),
            "started_at": iso(started),
            "ended_at": iso(ended),
            "duration_sec": duration,
            "transcript": transcript,
            "recording_ref": f"mock://recordings/{provider_call_id}" if duration > 0 else "",
            "booked_slot": "",
            "callback_at": "",
        }
        local = to_local(started, request.time_zone)
        if outcome == Outcome.BOOKED:
            slot = add_business_days(local, 3).replace(hour=15, minute=0, second=0, microsecond=0)
            payload["booked_slot"] = slot.isoformat()
        if outcome == Outcome.CALLBACK_REQUESTED:
            callback = add_business_days(local, 1).replace(hour=14, minute=0, second=0, microsecond=0)
            payload["callback_at"] = iso(callback)
        event = {"payload": payload, "signature": self._sign(payload)}
        self._pending.append(event)
        self._pending.append(json.loads(json.dumps(event)))  # the duplicate webhook
        self._status[provider_call_id] = {"status": "completed", "outcome": str(outcome)}
        return provider_call_id

    def get_call_status(self, provider_call_id: str) -> dict:
        return self._status.get(provider_call_id, {"status": "unknown"})

    def drain_events(self) -> list[dict]:
        events, self._pending = self._pending, []
        return events

    def _sign(self, payload: dict) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(self.secret, body, hashlib.sha256).hexdigest()

    def verify_signature(self, event: dict) -> bool:
        expected = self._sign(event.get("payload", {}))
        return hmac.compare_digest(expected, event.get("signature", ""))

    @staticmethod
    def _default_scenario(contact_id: str) -> str:
        index = int(hashlib.sha1(contact_id.encode()).hexdigest(), 16) % len(DEFAULT_ROTATION)
        return DEFAULT_ROTATION[index]
