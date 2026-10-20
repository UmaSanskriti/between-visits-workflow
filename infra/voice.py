"""Voice provider abstraction (spec 4.1) and the dry-run mock (NFR-007).

No business logic depends on a vendor SDK. A real provider (playbook section 15 names Vapi, backup Retell)
implements VoiceProvider; v0 ships only the mock.
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


OUTBOUND_CALLER_ID = "+16175550199"  # fictional; the registered number with caller-id reputation replaces it (CMP-007)

# scenario -> (provider outcome label, duration seconds, pitch delivered?, lines after the pitch)
SCENARIOS: dict[str, tuple[str, int, bool, list[tuple[str, str]]]] = {
    "booked": (
        Outcome.BOOKED,
        142,
        True,
        [
            ("doctor", "Sure, GLP-1 follow-ups are a real strain. I can do 30 minutes."),
            ("agent", "Wonderful. I'll hold that slot and send the invite. {close}"),
        ],
    ),
    "declined": (
        Outcome.DECLINED,
        48,
        True,
        [
            ("doctor", "I appreciate it, but we're not taking on anything new right now."),
            ("agent", "Understood, thank you for your time. {close}"),
        ],
    ),
    "no_answer": (Outcome.NO_ANSWER, 0, False, []),
    "voicemail": (Outcome.VOICEMAIL, 20, False, []),
    "callback": (
        Outcome.CALLBACK_REQUESTED,
        61,
        True,
        [
            ("doctor", "I'm with patients. Can you call back tomorrow afternoon?"),
            ("agent", "Of course. I'll call back tomorrow afternoon. {close}"),
        ],
    ),
    "gatekeeper": (
        Outcome.GATEKEEPER_BLOCKED,
        33,
        False,
        [
            ("office", "The doctor doesn't take calls like this. You can send something in writing."),
            ("agent", "Thank you, I understand. {close}"),
        ],
    ),
    "wrong_number": (
        Outcome.WRONG_NUMBER,
        18,
        False,
        [
            ("other", "You've got the wrong number, there's no doctor here."),
            ("agent", "My apologies. I'll make sure we don't call this number again."),
        ],
    ),
    "technical": (Outcome.FAILED_TECHNICAL, 0, False, []),
    "stop_calling": (
        Outcome.DECLINED,
        27,
        True,
        [
            ("doctor", "Please don't call this office again. Take us off your list."),
            ("agent", "Understood, we won't contact you again. Thank you."),
        ],
    ),
    "malformed_transcript": (Outcome.DECLINED, 40, True, []),
    "patient_data": (
        Outcome.DECLINED,
        75,
        True,
        [
            ("doctor", "Not now. I have a patient, John Doe, date of birth 03/04/1980, waiting on me."),
            ("agent", "Understood, I'll let you go. {close}"),
        ],
    ),
    "delivery_fails": (
        Outcome.BOOKED,
        120,
        True,
        [
            ("doctor", "Yes, happy to talk. Book me for 30 minutes."),
            ("agent", "Great, I'll send the details. {close}"),
        ],
    ),
    "not_a_fit": (
        Outcome.NOT_A_FIT,
        41,
        True,
        [
            ("office", "We're owned by a hospital system, so this wouldn't apply to us."),
            ("agent", "Thank you for letting me know. We won't take more of your time. {close}"),
        ],
    ),
    "agreed": (
        Outcome.AGREED_TO_TALK,
        96,
        True,
        [
            (
                "office",
                "The doctor is with patients, but she'd be happy to talk. Send the details to "
                "drchen@example.test, or text her on WhatsApp at this number. Tuesday or Thursday afternoons are best.",
            ),
            (
                "agent",
                "Perfect, I'll pass that to Anirudh: drchen@example.test, WhatsApp on this number, Tuesday or "
                "Thursday afternoons. {close}",
            ),
        ],
    ),
    "declined_recording": (
        Outcome.DECLINED_RECORDING,
        8,
        False,
        [
            ("office", "No, I don't want to be recorded."),
            ("agent", "Understood, I'll end the call here. Thank you."),
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
        meta = request.metadata
        scenario = meta.get("scenario") or self._default_scenario(request.contact_id)
        if scenario not in SCENARIOS:
            scenario = "no_answer"
        outcome, duration, pitched, lines = SCENARIOS[scenario]
        close = meta.get("close", "")
        started = self.clock.now()
        transcript: object = []
        if scenario == "voicemail":
            if meta.get("voicemail_message"):
                transcript = [{"speaker": "agent", "start_ms": 0, "text": meta["voicemail_message"]}]
            else:
                duration = 9  # hang up without a message (CMP-008 default)
        elif duration > 0:
            transcript = [{"speaker": "agent", "start_ms": 0, "text": request.first_message}]
            offset = 4000
            if pitched:
                transcript.append({"speaker": "office", "start_ms": offset, "text": "One moment, let me get someone."})
                offset += 5000
                transcript.append({"speaker": "agent", "start_ms": offset, "text": meta.get("pitch", "")})
                offset += 20000
            for speaker, text in lines:
                transcript.append({"speaker": speaker, "start_ms": offset, "text": text.replace("{close}", close)})
                offset += 6000
        if scenario == "malformed_transcript":
            transcript = "##garbled## (provider returned a non-list transcript)"
        ended = started + timedelta(seconds=duration)
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
            "handoff": {},
            "ended_reason": "mock:" + scenario,
            "cost_usd": 0.0,
        }
        if outcome == Outcome.AGREED_TO_TALK:
            payload["handoff"] = {
                "contact_email": "drchen@example.test",
                "contact_method": "whatsapp",
                "contact_number": request.to_number,
                "availability": "Tue or Thu afternoons",
                "person_spoken_to": "assistant",
                "notes": "send details first, then text",
            }
        local = to_local(started, request.time_zone)
        if outcome == Outcome.BOOKED:
            fallback = add_business_days(local, 3).replace(hour=15, minute=0, second=0, microsecond=0).isoformat()
            payload["booked_slot"] = meta.get("slot_1_iso") or fallback
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
