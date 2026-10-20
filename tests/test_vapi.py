"""Vapi adapter against a fake transport: request shapes, outcome classification, allowlist, live pipeline."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from conftest import CLEARANCES, ROOT, SEED, SUPPRESSIONS

from infra.brain import MockBrain
from infra.clock import FixedClock
from infra.config import ConfigError, load_config
from infra.models import ContactStatus, Outcome
from infra.pipeline import run_pipeline
from infra.store import Store
from infra.vapi import LIVE_ADDENDUM, VapiClient, VapiError, VapiVoiceProvider, classify, event_from_call
from infra.voice import CallRequest

NOW = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)


def ended_call(structured: dict | None, reason: str = "assistant-ended-call", call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "status": "ended",
        "endedReason": reason,
        "cost": 0.11,
        "startedAt": "2026-10-20T14:00:05Z",
        "endedAt": "2026-10-20T14:01:40Z",
        "messages": [
            {"role": "system", "message": "prompt"},
            {
                "role": "bot",
                "message": "Hi, this is an AI assistant calling for Anirudh Bagla...",
                "secondsFromStart": 0,
            },
            {"role": "user", "message": "Sure, send it to drchen@example.test", "secondsFromStart": 12.5},
        ],
        "artifact": {"recordingUrl": "https://example.test/rec.wav"},
        "analysis": {"summary": "Agreed.", "successEvaluation": "true", "structuredData": structured or {}},
    }


class FakeTransport:
    def __init__(self, call: dict | None = None):
        self.calls: list[tuple[str, str, dict | None]] = []
        self.call = call or ended_call(
            {
                "agreed_to_talk": True,
                "doctor_email": "drchen@example.test",
                "contact_method": "whatsapp",
                "contact_number": "+16175550101",
                "availability": "Tue/Thu afternoons",
                "person_spoken_to": "assistant",
            }
        )

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path.startswith("/assistant") and method == "GET":
            return 200, []
        if path == "/assistant" and method == "POST":
            return 201, {"id": "asst_1", **body}
        if path.startswith("/assistant/") and method == "PATCH":
            return 200, {"id": "asst_1"}
        if path.startswith("/phone-number") and method == "GET":
            return 200, [{"id": "pn_1", "number": "+16175550199", "provider": "twilio", "status": "active"}]
        if path == "/call" and method == "POST":
            return 201, {"id": "call_1", "status": "queued"}
        if path == "/call/call_1":
            return 200, self.call
        return 404, {"message": "not found"}


def make_provider(transport, cfg):
    provider = VapiVoiceProvider(
        VapiClient("test-key", transport=transport), cfg, FixedClock(NOW), log=lambda *a: None, sleep=lambda s: None
    )
    provider.ensure_phone_number()
    provider.ensure_assistant("+16175550199")
    return provider


def live_cfg(tmp_path, allow="+16175550101"):
    return load_config(
        ROOT / "config.toml",
        env={
            "BV_MODE": "live",
            "BV_ALLOW_LIVE_CALLS": "1",
            "BV_LIVE_ALLOWLIST": allow,
            "BV_DATA_DIR": str(tmp_path / "data"),
            "BV_EXPORT_DIR": str(tmp_path / "export"),
        },
    )


def test_live_mode_needs_explicit_opt_in_and_allowlist(tmp_path):
    with pytest.raises(ConfigError):
        load_config(ROOT / "config.toml", env={"BV_MODE": "live", "BV_DATA_DIR": str(tmp_path)})
    with pytest.raises(ConfigError):
        load_config(
            ROOT / "config.toml", env={"BV_MODE": "live", "BV_ALLOW_LIVE_CALLS": "1", "BV_DATA_DIR": str(tmp_path)}
        )
    assert live_cfg(tmp_path).mode == "live"


def test_assistant_is_saved_with_templates_tools_and_analysis(tmp_path):
    transport = FakeTransport()
    make_provider(transport, live_cfg(tmp_path))
    body = next(b for m, p, b in transport.calls if m == "POST" and p == "/assistant")
    assert body["firstMessage"] == "{{first_message}}"
    assert body["model"]["messages"][0]["content"] == "{{system_prompt}}"
    assert {"type": "endCall"} in body["model"]["tools"]
    assert body["analysisPlan"]["successEvaluationRubric"] == "PassFail"
    assert "doctor_email" in body["analysisPlan"]["structuredDataSchema"]["properties"]
    assert body["voicemailDetection"] == {"provider": "vapi"}
    assert "+16175550199" in body["voicemailMessage"]
    assert body["maxDurationSeconds"] == 420
    json.dumps(body)  # serializable


def test_call_body_and_allowlist(tmp_path):
    transport = FakeTransport()
    provider = make_provider(transport, live_cfg(tmp_path))
    req = CallRequest(
        call_attempt_id="ca_1",
        contact_id="c_1",
        to_number="+16175550101",
        from_number="+16175550199",
        first_message="Hi, locked open.",
        system_prompt="PROMPT",
        script_version="v1",
    )
    assert provider.place_call(req) == "call_1"
    body = next(b for m, p, b in transport.calls if m == "POST" and p == "/call")
    assert body["assistantId"] == "asst_1" and body["phoneNumberId"] == "pn_1"
    assert body["customer"] == {"number": "+16175550101"}
    assert body["assistantOverrides"]["firstMessage"] == "Hi, locked open."
    assert body["assistantOverrides"]["variableValues"]["system_prompt"] == "PROMPT" + LIVE_ADDENDUM
    with pytest.raises(VapiError):
        provider.place_call(
            CallRequest(
                call_attempt_id="ca_2",
                contact_id="c_2",
                to_number="+16175550102",
                from_number="",
                first_message="x",
                system_prompt="y",
                script_version="v1",
            )
        )


@pytest.mark.parametrize(
    "structured, reason, expected",
    [
        ({}, "customer-did-not-answer", Outcome.NO_ANSWER),
        ({}, "customer-busy", Outcome.NO_ANSWER),
        ({}, "voicemail", Outcome.VOICEMAIL),
        ({}, "pipeline-error-openai-llm-failed", Outcome.FAILED_TECHNICAL),
        ({}, "call.start.error-get-phone-number", Outcome.FAILED_TECHNICAL),
        ({"do_not_contact": True, "agreed_to_talk": True}, "customer-ended-call", Outcome.DO_NOT_CONTACT),
        ({"declined_recording": True}, "assistant-ended-call", Outcome.DECLINED_RECORDING),
        ({"not_a_fit": True}, "assistant-ended-call", Outcome.NOT_A_FIT),
        ({"wrong_number": True}, "customer-ended-call", Outcome.WRONG_NUMBER),
        ({"agreed_to_talk": True, "booked_slot": "2026-10-27T14:00:00-04:00"}, "assistant-ended-call", Outcome.BOOKED),
        ({"agreed_to_talk": True, "booked_slot": "Tuesday 2pm"}, "assistant-ended-call", Outcome.AGREED_TO_TALK),
        ({"agreed_to_talk": False, "doctor_email": "dr@example.test"}, "assistant-ended-call", Outcome.AGREED_TO_TALK),
        ({"callback_requested": True, "callback_time": "tomorrow"}, "assistant-ended-call", Outcome.CALLBACK_REQUESTED),
        ({"screened_by_front_desk": True}, "customer-ended-call", Outcome.GATEKEEPER_BLOCKED),
        ({"agreed_to_talk": False}, "customer-ended-call", Outcome.DECLINED),
    ],
)
def test_classify(structured, reason, expected):
    outcome, _, _ = classify(ended_call(structured, reason))
    assert outcome == expected


def test_event_from_call_shape():
    event = event_from_call(
        ended_call(
            {
                "agreed_to_talk": True,
                "doctor_email": "drchen@example.test",
                "contact_method": "whatsapp",
                "person_spoken_to": "doctor",
                "booked_slot": "2026-10-27T14:00:00-04:00",
            }
        ),
        "ca_1",
        NOW,
    )
    p = event["payload"]
    assert event["source"] == "vapi-poll" and p["outcome"] == Outcome.BOOKED
    assert p["booked_slot"] == "2026-10-27T18:00:00Z" and p["duration_sec"] == 95
    assert [u["speaker"] for u in p["transcript"]] == ["agent", "doctor"]
    assert p["transcript"][1]["start_ms"] == 12500
    assert p["handoff"]["contact_email"] == "drchen@example.test" and p["handoff"]["contact_method"] == "whatsapp"
    assert (
        p["recording_ref"].startswith("https://")
        and p["cost_usd"] == 0.11
        and p["ended_reason"] == "assistant-ended-call"
    )


def test_live_pipeline_end_to_end_with_fake_vapi(tmp_path):
    cfg = live_cfg(tmp_path)
    clock = FixedClock(NOW)
    store = Store(cfg.data_dir, clock).load()
    transport = FakeTransport()
    provider = make_provider(transport, cfg)
    run, summary = run_pipeline(
        store, cfg, clock, MockBrain(store, clock), provider, SEED, CLEARANCES, SUPPRESSIONS, trigger="test-live"
    )
    assert run["mode"] == "live" and run["stages"]["calls"]["placed"] == 1 and provider.calls_placed == 1
    t01 = next(c for c in store.contacts.values() if c.external_id == "t_01")
    assert t01.status == ContactStatus.BOOKED and t01.last_outcome == Outcome.AGREED_TO_TALK
    interview = next(i for i in store.interviews.values() if i.contact_id == t01.id)
    assert interview.contact_email == "drchen@example.test" and interview.contact_method == "whatsapp"
    assert interview.availability == "Tue/Thu afternoons" and interview.status == "draft_pending_approval"
    attempt = store.attempts_for(t01.id)[0]
    assert (
        attempt.provider == "vapi"
        and attempt.cost_usd == 0.11
        and attempt.provider_ended_reason == "assistant-ended-call"
    )
    blocked = [d for d in store.logs["gate_decisions"] if d["run_id"] == run["run_id"] and not d["allowed"]]
    assert any("not_in_live_allowlist" in d["reasons"] for d in blocked)
    assert summary["compliance"]["gate_escapes"] == 0
    # the transcript still goes to the Brain, and the export masks the number
    assert attempt.transcript_id in store.transcripts
    assert "+16175550101" not in (cfg.export_dir / "contacts.json").read_text()


def test_calling_window_waiver_applies_only_to_allowlisted_numbers_in_live_mode(tmp_path):
    from datetime import timedelta

    from infra.compliance import pre_call_gate
    from infra.models import Channel, Clearance, Contact, VerificationStatus

    evening = NOW + timedelta(hours=8)  # 18:00 ET, outside the window
    cfg = load_config(
        ROOT / "config.toml",
        env={
            "BV_MODE": "live",
            "BV_ALLOW_LIVE_CALLS": "1",
            "BV_LIVE_ALLOWLIST": "+16175550101",
            "BV_LIVE_IGNORE_WINDOW": "1",
            "BV_DATA_DIR": str(tmp_path / "data"),
            "BV_EXPORT_DIR": str(tmp_path / "export"),
        },
    )
    store = Store(cfg.data_dir, FixedClock(evening))
    contact = Contact(id="c_1", full_name="Demo", verification_status=VerificationStatus.VERIFIED, status="READY")
    team = Channel(
        id="ch_1", contact_id="c_1", type="phone", value="+16175550101", verified=True, call_clearance=Clearance.CLEARED
    )
    other = Channel(
        id="ch_2", contact_id="c_1", type="phone", value="+16175550102", verified=True, call_clearance=Clearance.CLEARED
    )
    waived = pre_call_gate(store, contact, team, cfg, FixedClock(evening))
    assert waived.allowed and waived.waivers == ["calling_window_waived_for_allowlisted_team_number"]
    blocked = pre_call_gate(store, contact, other, cfg, FixedClock(evening))
    assert not blocked.allowed and "not_in_live_allowlist" in blocked.reasons
    assert any(r.startswith("outside_calling_window") for r in blocked.reasons)
    cfg.live_ignore_window = False
    assert not pre_call_gate(store, contact, team, cfg, FixedClock(evening)).allowed
