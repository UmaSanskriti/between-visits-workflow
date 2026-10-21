"""Vapi voice provider (playbook section 15: Vapi, backup Retell). Staged live test only.

Only numbers on [live].allowlist can be dialed (compliance gate). The assistant is created or updated by name
on every run so the dashboard-approved script is what goes out. Call state is pulled by polling GET /call/{id}
(CALL-005 polling fallback); there is no public webhook endpoint in v0.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime

from .clock import Clock, iso, parse_iso
from .config import Config
from .models import Outcome
from .voice import CallRequest

API = "https://api.vapi.ai"
POLL_SECONDS = 5
USER_AGENT = "between-visits-infra/0.1 (+https://github.com/UmaSanskriti/between-visits-workflow)"

# The agent's definition of success for this venture: the doctor's email, or an agreement that Anirudh may
# contact the doctor (any channel: call, WhatsApp, iMessage, email), plus whatever is needed to set it up.
LIVE_ADDENDUM = (
    "\n\nSUCCESS FOR THIS CALL: you succeed when you leave with (a) the doctor's email address, or (b) an agreement "
    "that Anirudh may contact the doctor for a 30-minute conversation, with a channel: a phone call, WhatsApp, "
    "iMessage or email are all fine, and in person is fine too. A specific day and time is a bonus, not required. "
    "Capture everything Anirudh needs to set up and join that conversation: the best days and times, the email, "
    "the number to text if they prefer WhatsApp or iMessage (confirm whether it is this number), and who you spoke "
    "to (the doctor, an assistant, or the front desk). When someone gives an email address, read it back "
    "letter by letter and confirm it. Before hanging up, repeat back what you captured in one sentence, say the "
    "close, then end the call with the endCall tool. Keep the whole call under five minutes. If you reach "
    "voicemail, leave the voicemail message only. Never ask for or discuss any patient information."
)

STRUCTURED_SCHEMA = {
    "type": "object",
    "properties": {
        "person_spoken_to": {"type": "string", "enum": ["doctor", "assistant", "front_desk", "other", "nobody"]},
        "agreed_to_talk": {
            "type": "boolean",
            "description": "The doctor, or someone speaking for the doctor, agreed that Anirudh may contact the "
            "doctor for a 30-minute conversation.",
        },
        "doctor_email": {
            "type": "string",
            "description": "Email address given for the doctor or office, exactly as confirmed, else empty",
        },
        "contact_method": {
            "type": "string",
            "enum": ["phone_call", "whatsapp", "imessage", "email", "in_person", "unknown"],
        },
        "contact_number": {
            "type": "string",
            "description": "Number for WhatsApp, iMessage or a call back if given (E.164 if possible), else empty",
        },
        "booked_slot": {
            "type": "string",
            "description": "If a specific date and time was agreed: ISO 8601 with America/New_York offset, else empty",
        },
        "availability": {
            "type": "string",
            "description": "Days and times the doctor is available or prefers, in their words, else empty",
        },
        "callback_requested": {"type": "boolean"},
        "callback_time": {
            "type": "string",
            "description": "When to call back: ISO 8601 if specific, else free text or empty",
        },
        "declined_recording": {"type": "boolean"},
        "do_not_contact": {"type": "boolean", "description": "They asked not to be called or contacted again"},
        "not_a_fit": {
            "type": "boolean",
            "description": "Hospital-owned practice, or no GLP-1 or weight-management patients",
        },
        "wrong_number": {"type": "boolean"},
        "screened_by_front_desk": {
            "type": "boolean",
            "description": "The front desk refused to connect or to take the request",
        },
        "what_to_send": {
            "type": "string",
            "description": "Anything the practice asked to receive before the conversation, else empty",
        },
        "notes": {"type": "string", "description": "Anything else Anirudh needs to set up and join the conversation"},
    },
    "required": [
        "person_spoken_to",
        "agreed_to_talk",
        "contact_method",
        "callback_requested",
        "declined_recording",
        "do_not_contact",
        "not_a_fit",
        "wrong_number",
        "screened_by_front_desk",
    ],
}
STRUCTURED_PROMPT = (
    "Extract the outcome of this outreach call per the schema. Be literal: only set agreed_to_talk when the person "
    "clearly agreed that Anirudh may contact the doctor. Copy emails and numbers exactly as confirmed on the call. "
    "Use empty strings for anything not said."
)
SUMMARY_PROMPT = (
    "Summarize the call in two sentences for Anirudh: who you reached, whether they agreed to a conversation, "
    "and how to set it up (email, channel, times)."
)
SUCCESS_PROMPT = (
    "Pass if the call obtained the doctor's email address, or an agreement that Anirudh may contact the doctor "
    "through any channel (call, WhatsApp, iMessage, email, in person), or a specific time for the conversation. "
    "Fail otherwise."
)

NO_ANSWER_REASONS = {"customer-did-not-answer", "customer-busy"}
ERROR_PREFIXES = (
    "pipeline-error",
    "call.start.error",
    "call.in-progress.error",
    "assistant-not",
    "assistant-request",
    "llm-failed",
)


class VapiError(Exception):
    pass


class VapiClient:
    """Thin HTTP client. `transport(method, path, body) -> (status, json)` can be swapped for tests."""

    def __init__(self, api_key: str, transport=None):
        if not api_key:
            raise VapiError("VAPI_API_KEY is not set")
        self.api_key = api_key
        self.transport = transport or self._http

    def _http(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode() or "null"
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"message": raw}

    def request(self, method: str, path: str, body: dict | None = None):
        status, payload = self.transport(method, path, body)
        if status >= 400:
            message = payload.get("message") if isinstance(payload, dict) else payload
            raise VapiError(f"{method} {path} -> {status}: {message}")
        return payload

    def list_assistants(self) -> list[dict]:
        return self.request("GET", "/assistant?limit=100") or []

    def create_assistant(self, body: dict) -> dict:
        return self.request("POST", "/assistant", body)

    def update_assistant(self, assistant_id: str, body: dict) -> dict:
        return self.request("PATCH", f"/assistant/{assistant_id}", body)

    def list_phone_numbers(self) -> list[dict]:
        return self.request("GET", "/phone-number?limit=100") or []

    def create_phone_number(self, body: dict) -> dict:
        return self.request("POST", "/phone-number", body)

    def create_call(self, body: dict) -> dict:
        return self.request("POST", "/call", body)

    def get_call(self, call_id: str) -> dict:
        return self.request("GET", f"/call/{call_id}")


def assistant_body(cfg: Config, callback_number: str, server_url: str = "", server_secret: str = "") -> dict:
    """The saved assistant. Per-call text arrives through variableValues (LiquidJS templates)."""
    voicemail = (
        "Hi, this is an AI assistant calling for Anirudh Bagla at Harvard Business School. We help independent "
        "practices give their GLP-1 patients complete care, and would love 30 minutes with the doctor. "
        f"Please call back at {callback_number}. Thank you."
    )
    body = {
        "name": cfg.live_assistant_name,
        "firstMessage": "{{first_message}}",
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": "openai",
            "model": cfg.live_model,
            "temperature": 0.3,
            "messages": [{"role": "system", "content": "{{system_prompt}}"}],
            "tools": [{"type": "endCall"}],
        },
        "voice": {"provider": "vapi", "voiceId": cfg.live_voice_id},
        "voicemailMessage": voicemail,
        "voicemailDetection": {"provider": "vapi"},
        "maxDurationSeconds": cfg.live_max_call_seconds,
        "silenceTimeoutSeconds": 30,
        "artifactPlan": {"recordingEnabled": True},
        "analysisPlan": {
            "summaryPrompt": SUMMARY_PROMPT,
            "structuredDataPrompt": STRUCTURED_PROMPT,
            "structuredDataSchema": STRUCTURED_SCHEMA,
            "successEvaluationPrompt": SUCCESS_PROMPT,
            "successEvaluationRubric": "PassFail",
        },
        "metadata": {"playbook": cfg.playbook, "owner": "between-visits-infra"},
    }
    if server_url:
        body["server"] = {"url": server_url, "timeoutSeconds": 20}
        if server_secret:
            body["server"]["secret"] = server_secret
        body["serverMessages"] = ["status-update", "end-of-call-report", "transcript", "hang"]
    return body


def call_body(request: CallRequest, assistant_id: str, phone_number_id: str) -> dict:
    return {
        "name": f"bv {request.call_attempt_id}",
        "assistantId": assistant_id,
        "phoneNumberId": phone_number_id,
        "customer": {"number": request.to_number},
        "assistantOverrides": {
            "firstMessage": request.first_message,
            "variableValues": {
                "first_message": request.first_message,
                "system_prompt": request.system_prompt + LIVE_ADDENDUM,
                "script_version": request.script_version,
            },
            "metadata": {
                "call_attempt_id": request.call_attempt_id,
                "contact_id": request.contact_id,
                "script_version": request.script_version,
            },
        },
    }


def _iso_or_empty(value) -> str:
    if not value or not isinstance(value, str):
        return ""
    try:
        return iso(parse_iso(value.strip()))
    except (ValueError, TypeError):
        return ""


def classify(call: dict) -> tuple[str, dict, str]:
    """Map a finished Vapi call to our outcome, handoff details, and a reason string."""
    reason = call.get("endedReason") or ""
    analysis = call.get("analysis") or {}
    sd = analysis.get("structuredData") or {}
    if not isinstance(sd, dict):
        sd = {}
    handoff = {
        "contact_email": (sd.get("doctor_email") or "").strip(),
        "contact_method": sd.get("contact_method") or "",
        "contact_number": (sd.get("contact_number") or "").strip(),
        "availability": (sd.get("availability") or "").strip(),
        "person_spoken_to": sd.get("person_spoken_to") or "",
        "notes": " ".join(
            x
            for x in (
                (sd.get("what_to_send") or "").strip(),
                (sd.get("notes") or "").strip(),
                (analysis.get("summary") or "").strip(),
            )
            if x
        ),
    }
    if reason in NO_ANSWER_REASONS:
        return Outcome.NO_ANSWER, handoff, reason
    if reason == "voicemail":
        return Outcome.VOICEMAIL, handoff, reason
    if reason.startswith(ERROR_PREFIXES) or reason.endswith(("-failed", "-error")):
        return Outcome.FAILED_TECHNICAL, handoff, reason
    if sd.get("do_not_contact"):
        return Outcome.DO_NOT_CONTACT, handoff, reason
    if sd.get("declined_recording"):
        return Outcome.DECLINED_RECORDING, handoff, reason
    if sd.get("wrong_number"):
        return Outcome.WRONG_NUMBER, handoff, reason
    if sd.get("not_a_fit"):
        return Outcome.NOT_A_FIT, handoff, reason
    if sd.get("agreed_to_talk") and _iso_or_empty(sd.get("booked_slot")):
        return Outcome.BOOKED, handoff, reason
    if sd.get("agreed_to_talk") or handoff["contact_email"]:
        return Outcome.AGREED_TO_TALK, handoff, reason
    if sd.get("callback_requested"):
        return Outcome.CALLBACK_REQUESTED, handoff, reason
    if sd.get("screened_by_front_desk"):
        return Outcome.GATEKEEPER_BLOCKED, handoff, reason
    if reason in ("silence-timed-out", "exceeded-max-duration") and not sd:
        return Outcome.NO_ANSWER, handoff, reason
    return Outcome.DECLINED, handoff, reason


def event_from_call(call: dict, call_attempt_id: str, fallback_started: datetime) -> dict:
    outcome, handoff, reason = classify(call)
    sd = (call.get("analysis") or {}).get("structuredData") or {}
    if not isinstance(sd, dict):
        sd = {}
    artifact = call.get("artifact") or {}
    messages = call.get("messages") or artifact.get("messages") or []
    speaker_for_user = {"doctor": "doctor", "assistant": "assistant", "front_desk": "office"}.get(
        sd.get("person_spoken_to") or "", "practice"
    )
    utterances = []
    for m in messages:
        role = m.get("role")
        text = m.get("message") or m.get("content") or ""
        if role in ("bot", "assistant"):
            speaker = "agent"
        elif role == "user":
            speaker = speaker_for_user
        else:
            continue
        if text:
            utterances.append(
                {"speaker": speaker, "start_ms": int(float(m.get("secondsFromStart") or 0) * 1000), "text": text}
            )
    recording = artifact.get("recordingUrl") or call.get("recordingUrl") or ""
    rec_obj = artifact.get("recording") or {}
    if not recording and isinstance(rec_obj, dict):
        recording = (
            rec_obj.get("stereoUrl")
            or ((rec_obj.get("mono") or {}).get("combinedUrl") if isinstance(rec_obj.get("mono"), dict) else "")
            or ""
        )
    started = call.get("startedAt") or iso(fallback_started)
    ended = call.get("endedAt") or started
    try:
        duration = max(0, int((parse_iso(ended) - parse_iso(started)).total_seconds()))
    except (ValueError, TypeError):
        duration = 0
    payload = {
        "event_id": f"{call.get('id')}:ended",
        "type": "call.ended",
        "provider_call_id": call.get("id", ""),
        "call_attempt_id": call_attempt_id,
        "outcome": str(outcome),
        "started_at": _iso_or_empty(started) or iso(fallback_started),
        "ended_at": _iso_or_empty(ended) or iso(fallback_started),
        "duration_sec": duration,
        "transcript": utterances,
        "recording_ref": recording,
        "booked_slot": _iso_or_empty(sd.get("booked_slot")) if outcome == Outcome.BOOKED else "",
        "callback_at": _iso_or_empty(sd.get("callback_time")) if outcome == Outcome.CALLBACK_REQUESTED else "",
        "handoff": handoff,
        "ended_reason": reason,
        "cost_usd": float(call.get("cost") or 0),
        "success_evaluation": (call.get("analysis") or {}).get("successEvaluation", ""),
        "summary": (call.get("analysis") or {}).get("summary", ""),
    }
    return {"payload": payload, "signature": "vapi-poll", "source": "vapi-poll"}


class VapiVoiceProvider:
    name = "vapi"

    def __init__(self, client: VapiClient, cfg: Config, clock: Clock, log=print, sleep=time.sleep):
        self.client = client
        self.cfg = cfg
        self.clock = clock
        self.log = log
        self.sleep = sleep
        self.assistant_id = ""
        self.phone_number_id = ""
        self.phone_number = ""
        self._pending: list[tuple[str, str, datetime]] = []  # (provider_call_id, call_attempt_id, placed_at)
        self.calls_placed = 0
        self.on_poll = None  # callback(call_dict) on every poll, used by the local service for live status

    # ---- setup (free API calls) ----
    def ensure_assistant(self, callback_number: str, server_url: str = "", server_secret: str = "") -> str:
        body = assistant_body(self.cfg, callback_number, server_url, server_secret)
        existing = [a for a in self.client.list_assistants() if a.get("name") == body["name"]]
        if existing:
            self.assistant_id = existing[0]["id"]
            self.client.update_assistant(self.assistant_id, body)
            self.log(f"vapi: updated assistant {body['name']} ({self.assistant_id})")
        else:
            created = self.client.create_assistant(body)
            self.assistant_id = created["id"]
            self.log(f"vapi: created assistant {body['name']} ({self.assistant_id})")
        return self.assistant_id

    def ensure_phone_number(self, create_if_missing: bool = False) -> str:
        numbers = self.client.list_phone_numbers()
        chosen = None
        if self.cfg.vapi_phone_number_id:
            chosen = next((n for n in numbers if n.get("id") == self.cfg.vapi_phone_number_id), None)
            if chosen is None:
                raise VapiError(f"VAPI_PHONE_NUMBER_ID {self.cfg.vapi_phone_number_id} not found in this account")
        elif numbers:
            active = [n for n in numbers if n.get("status") in (None, "active")]
            imported = [n for n in active if n.get("provider") != "vapi"]  # free Vapi numbers cannot dial out
            chosen = (imported or active or numbers)[0]
        elif create_if_missing:
            chosen = self.client.create_phone_number(
                {
                    "provider": "vapi",
                    "numberDesiredAreaCode": self.cfg.vapi_area_code,
                    "name": "between-visits-outbound",
                }
            )
            self.log(f"vapi: created free number {chosen.get('number')} ({chosen.get('id')})")
        if chosen is None:
            raise VapiError("no phone number in the Vapi account; create one in the dashboard or pass --create-number")
        self.phone_number_id = chosen["id"]
        self.phone_number = chosen.get("number") or ""
        if chosen.get("provider") == "vapi":
            self.log(
                "vapi: note, Vapi's docs say free Vapi numbers cannot place outbound calls; "
                "if the call fails at start, import a Twilio/Telnyx/Vonage number instead"
            )
        return self.phone_number_id

    def import_twilio_number(
        self, number: str, account_sid: str, auth_token: str, name: str = "between-visits-twilio"
    ) -> dict:
        """Import a Twilio number so outbound calls go through Twilio (free Vapi numbers cannot dial out)."""
        existing = [n for n in self.client.list_phone_numbers() if n.get("number") == number]
        if existing:
            self.log(f"vapi: {number} already imported ({existing[0].get('id')})")
            return existing[0]
        created = self.client.create_phone_number(
            {
                "provider": "twilio",
                "number": number,
                "twilioAccountSid": account_sid,
                "twilioAuthToken": auth_token,
                "name": name,
            }
        )
        self.log(f"vapi: imported Twilio number {number} ({created.get('id')})")
        return created

    # ---- VoiceProvider ----
    def place_call(self, request: CallRequest) -> str:
        if request.to_number not in self.cfg.live_allowlist:
            raise VapiError(f"refusing to dial {request.to_number}: not on the live allowlist")
        if not self.assistant_id or not self.phone_number_id:
            raise VapiError("assistant and phone number must be ensured before placing a call")
        body = call_body(request, self.assistant_id, self.phone_number_id)
        created = self.client.create_call(body)
        call_id = created.get("id", "")
        self.calls_placed += 1
        self._pending.append((call_id, request.call_attempt_id, self.clock.now()))
        self.log(f"vapi: call {call_id} created, status {created.get('status')}")
        return call_id

    def get_call_status(self, provider_call_id: str) -> dict:
        call = self.client.get_call(provider_call_id)
        return {"status": call.get("status"), "endedReason": call.get("endedReason")}

    def drain_events(self) -> list[dict]:
        events = []
        deadline = time.monotonic() + self.cfg.live_poll_timeout_seconds
        while self._pending and time.monotonic() < deadline:
            call_id, attempt_id, placed_at = self._pending[0]
            call = self.client.get_call(call_id)
            status = call.get("status")
            if self.on_poll:
                self.on_poll(call)
            self.log(f"vapi: call {call_id} {status}" + (f" ({call.get('endedReason')})" if status == "ended" else ""))
            if status == "ended":
                if not (call.get("analysis") or {}).get("structuredData") and not _looks_like_no_connect(call):
                    self.sleep(POLL_SECONDS)  # analysis lands a few seconds after the call ends
                    call = self.client.get_call(call_id)
                events.append(event_from_call(call, attempt_id, placed_at))
                self._pending.pop(0)
                continue
            self.sleep(POLL_SECONDS)
        for call_id, _attempt_id, _placed_at in self._pending:
            self.log(f"vapi: call {call_id} still not ended after {self.cfg.live_poll_timeout_seconds}s; left pending")
        return events

    def verify_signature(self, event: dict) -> bool:
        return event.get("source") == "vapi-poll"  # pulled over TLS from the API itself, not pushed


def _looks_like_no_connect(call: dict) -> bool:
    reason = call.get("endedReason") or ""
    return reason in NO_ANSWER_REASONS or reason == "voicemail" or reason.startswith(ERROR_PREFIXES)
