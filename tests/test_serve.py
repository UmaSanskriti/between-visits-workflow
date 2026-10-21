"""Local control service: webhook intake, state, CORS/origin rules, static files."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from infra.serve import LiveService, LiveState, make_handler
from infra.vapi import assistant_body


class StubService(LiveService):
    """Does not dial or send; records requests and fakes the agent-run state."""

    def __init__(self, state):
        super().__init__(state, port=0, tunnel_url="https://example.trycloudflare.com")
        self.starts = 0
        self.secret = "s3cret"
        self.run_requests: list[tuple[bool, str]] = []
        self.sent: list[tuple[str, str, str]] = []
        self.replies: list[tuple[str, str, str, str]] = []
        self.busy_flag = False

    def start_call(self):
        self.starts += 1
        return True, "started"

    def start_run(self, reset=True, now=""):
        self.run_requests.append((reset, now))
        if self.busy_flag:
            return False, "a run or a call is already in progress"
        self.state.run_update(status="running", stage="ingest", started_at="2026-10-20T19:00:00Z", email=None)
        return True, "started"

    def outbox(self):
        return [
            {
                "attempt_id": "em_1",
                "contact_id": "c_demo",
                "to": "me@example.test",
                "from_name": "Uma",
                "from_address": "uma@example.test",
                "reply_to": "uma@example.test",
                "subject": "30 minutes with Dr. Demo? (HBS research, not sales)",
                "body": "Hi Dr. Demo, ...",
                "redirected_to": "m\u2022\u2022\u2022@example.test",
            }
        ]

    def mark_sent(self, attempt_id, message_id, thread_id=""):
        self.sent.append((attempt_id, message_id, thread_id))
        if self.busy_flag:
            return 409, {"ok": False, "message": "busy"}
        if attempt_id != "em_1":
            return 404, {"ok": False, "message": "no queued email"}
        email = dict(self.state.snapshot()["run"].get("email") or {})
        email.update(
            {"attempt_id": attempt_id, "status": "sent", "provider_message_id": message_id, "thread_id": thread_id}
        )
        self.state.run_update(email=email)
        return 200, {"ok": True, "attempt": {"id": attempt_id, "status": "sent"}}

    def record_reply(self, text, attempt="", sender="", at=""):
        self.replies.append((text, attempt, sender, at))
        if self.busy_flag:
            return 409, {"ok": False, "message": "busy"}
        if not text.strip():
            return 400, {"ok": False, "message": "reply text is empty"}
        reply = {
            "outcome": "AGREED_TO_TALK",
            "handoff": {
                "contact_email": "d\u2022\u2022\u2022@example.test",
                "contact_method": "email",
                "contact_number": "",
                "availability": "Tue afternoons",
                "person_spoken_to": "doctor",
                "notes": "",
            },
            "text": text,
            "at": at or "2026-10-20T22:05:00Z",
            "from": sender,
        }
        email = dict(self.state.snapshot()["run"].get("email") or {})
        email.update({"status": "replied", "reply": reply})
        self.state.run_update(email=email)
        return 200, {"ok": True, "reply": reply, "attempt_id": attempt or "em_1"}


@pytest.fixture
def server():
    state = LiveState()
    service = StubService(state)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", service, state
    httpd.shutdown()


def call(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read().decode()
            body = (
                json.loads(raw or "null")
                if resp.headers.get("Content-Type", "").startswith("application/json")
                else raw
            )
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "null"), dict(exc.headers)


def test_state_has_cors_for_the_dashboard_origin(server):
    base, _, _ = server
    code, body, headers = call(base + "/api/state", headers={"Origin": "https://umasanskriti.github.io"})
    assert code == 200 and body["status"] == "idle" and body["live_events"] is True
    assert headers.get("Access-Control-Allow-Origin") == "https://umasanskriti.github.io"
    code, _, headers = call(base + "/api/state", headers={"Origin": "https://evil.example"})
    assert code == 200 and "Access-Control-Allow-Origin" not in headers


def test_start_call_requires_an_allowed_origin(server):
    base, service, _ = server
    code, _, _ = call(base + "/api/call", "POST", {}, headers={"Origin": "https://evil.example"})
    assert code == 403 and service.starts == 0
    code, body, _ = call(base + "/api/call", "POST", {}, headers={"Origin": "http://127.0.0.1:8787"})
    assert code == 200 and body["started"] and service.starts == 1


def test_webhook_needs_the_secret_and_updates_live_transcript(server):
    base, _, state = server
    code, _, _ = call(base + "/api/webhook", "POST", {"message": {"type": "transcript"}})
    assert code == 401
    hdr = {"x-vapi-secret": "s3cret"}
    call(base + "/api/webhook", "POST", {"message": {"type": "status-update", "status": "in-progress"}}, hdr)
    call(
        base + "/api/webhook",
        "POST",
        {
            "message": {
                "type": "transcript",
                "role": "assistant",
                "transcriptType": "partial",
                "transcript": "Hi, this is",
            }
        },
        hdr,
    )
    snap = state.snapshot()
    assert snap["status"] == "in_call" and snap["partial"] == {"role": "agent", "text": "Hi, this is"}
    call(
        base + "/api/webhook",
        "POST",
        {
            "message": {
                "type": "transcript",
                "role": "assistant",
                "transcriptType": "final",
                "transcript": "Hi, this is an AI assistant.",
            }
        },
        hdr,
    )
    call(
        base + "/api/webhook",
        "POST",
        {"message": {"type": "transcript", "role": "user", "transcriptType": "final", "transcript": "Sure, go ahead."}},
        hdr,
    )
    snap = state.snapshot()
    assert [u["role"] for u in snap["transcript"]] == ["agent", "practice"] and snap["partial"] is None
    call(
        base + "/api/webhook",
        "POST",
        {
            "message": {
                "type": "end-of-call-report",
                "endedReason": "customer-ended-call",
                "analysis": {"summary": "Agreed."},
            }
        },
        hdr,
    )
    snap = state.snapshot()
    assert snap["call"]["ended_reason"] == "customer-ended-call" and snap["call"]["summary"] == "Agreed."


def test_end_of_call_report_fills_transcript_when_no_live_lines(server):
    base, _, state = server
    call(
        base + "/api/webhook",
        "POST",
        {
            "message": {
                "type": "end-of-call-report",
                "endedReason": "assistant-ended-call",
                "artifact": {
                    "messages": [
                        {"role": "assistant", "message": "Hello"},
                        {"role": "user", "message": "Hi"},
                        {"role": "system", "message": "x"},
                    ]
                },
            }
        },
        {"x-vapi-secret": "s3cret"},
    )
    assert [u["text"] for u in state.snapshot()["transcript"]] == ["Hello", "Hi"]


def test_static_files_and_no_path_traversal(server):
    base, _, _ = server
    code, _, headers = call(base + "/")
    assert code == 200 and headers["Content-Type"].startswith("text/html")
    code, body, _ = call(base + "/../config.toml")
    assert code == 404 or "mode" not in str(body)
    code, _, _ = call(base + "/%2e%2e/config.toml")
    assert code == 404
    code, _, _ = call(base + "/styles.css")
    assert code == 200
    code, _, _ = call(base + "/data-live/does-not-exist.json")
    assert code == 404


def test_assistant_body_wires_server_url_and_messages():
    from infra.config import Config

    body = assistant_body(Config(), "+16175550199", "https://x.trycloudflare.com/api/webhook", "tok")
    assert body["server"] == {"url": "https://x.trycloudflare.com/api/webhook", "timeoutSeconds": 20, "secret": "tok"}
    assert "transcript" in body["serverMessages"] and "end-of-call-report" in body["serverMessages"]
    assert "server" not in assistant_body(Config(), "+16175550199")


def test_run_endpoint_origin_rule_body_and_busy(server):
    base, service, state = server
    code, _, _ = call(base + "/api/run", "POST", {}, headers={"Origin": "https://evil.example"})
    assert code == 403 and service.run_requests == []
    code, body, _ = call(base + "/api/run", "POST", {}, headers={"Origin": "https://umasanskriti.github.io"})
    assert code == 200 and body["started"] and service.run_requests == [(False, "")]  # state persists by default
    code, body, _ = call(base + "/api/run", "POST", {"reset": True}, headers={"Origin": "http://127.0.0.1:8787"})
    assert code == 200 and service.run_requests[-1] == (True, "")
    snap = state.snapshot()
    assert snap["run"]["status"] == "running" and snap["run"]["stage"] == "ingest" and snap["run"]["email"] is None
    assert set(snap["run"]) >= {"status", "stage", "stages", "started_at", "finished_at", "run_id", "error", "email"}
    service.busy_flag = True
    code, body, _ = call(base + "/api/run", "POST", {}, headers={"Origin": "http://127.0.0.1:8787"})
    assert code == 409 and body["started"] is False


def test_run_progress_state_from_stage_callbacks():
    state = LiveState()
    state.run_update(status="running")
    state.run_stage("ingest", "start", {})
    assert state.snapshot()["run"]["stage"] == "ingest"
    state.run_stage("ingest", "done", {"rows": 21})
    state.run_stage("call", "start", {})
    snap = state.snapshot()["run"]
    assert snap["stage"] == "call" and snap["stages"]["ingest"] == {"status": "done", "counts": {"rows": 21}}
    state.run_stage("run", "done", {"run_id": "r1", "finished_at": "2026-10-20T19:00:05Z", "gate_escapes": 0})
    snap = state.snapshot()["run"]
    assert snap["status"] == "done" and snap["run_id"] == "r1" and snap["finished_at"] == "2026-10-20T19:00:05Z"


def test_dry_run_data_route_is_served_and_confined(server):
    base, _, _ = server
    code, body, headers = call(base + "/data/summary.json")
    assert code == 200 and headers["Content-Type"].startswith("application/json") and "calls" in body
    code, _, _ = call(base + "/data/../../config.toml")
    assert code == 404


def test_real_agent_run_queues_the_first_eligible_practice(tmp_path):
    """No credentials: the run picks the first cleared dummy doctor, composes, and queues for the Gmail connector."""
    import time

    state = LiveState()
    service = LiveService(state, port=0, tunnel_url="")
    service.dry_data_dir = tmp_path / "data"
    service.dry_export_dir = tmp_path / "export"
    service.env_overrides = {
        "BV_LIVE_ALLOWLIST": "ppatel@example.test",
        "EMAIL_TRANSPORT": "outbox",
        "SMTP_PASSWORD": "",
    }
    ok, _ = service.start_run(reset=True)
    assert ok
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and state.snapshot()["run"]["status"] == "running":
        time.sleep(0.2)
    run = state.snapshot()["run"]
    assert run["status"] == "done", run
    assert list(run["stages"]) == ["ingest", "verify", "call", "webhooks", "deliver"]
    e = run["email"]
    assert e["status"] == "queued" and e["transport"] == "outbox" and e["contact_name"] == "Priya Patel"
    assert e["subject"].startswith("30 minutes with Dr. Patel") and "stop" in e["body"]
    assert "@" in e["to"] and "\u2022" in e["to"]  # masked, never the raw address
    assert (tmp_path / "export" / "run.json").exists() and (
        tmp_path / "data" / "outbox" / (e["attempt_id"] + ".json")
    ).exists()
    outbox = service.outbox()
    assert [m["attempt_id"] for m in outbox] == [e["attempt_id"]] and outbox[0]["to"] == "ppatel@example.test"
    code, payload = service.mark_sent(e["attempt_id"], "<gmail-1@mail.gmail.com>", "thread-1")
    assert code == 200 and payload["ok"] and state.snapshot()["run"]["email"]["status"] == "sent"
    assert state.snapshot()["run"]["email"]["thread_id"] == "thread-1"
    code, payload = service.record_reply(
        "Yes, Tuesday afternoons work, send the invite to this address", sender="ppatel@example.test"
    )
    assert code == 200 and payload["reply"]["outcome"] == "AGREED_TO_TALK"
    email = state.snapshot()["run"]["email"]
    assert email["status"] == "replied" and email["reply"]["handoff"]["availability"]
    assert not service.busy()


def test_outbox_sent_and_reply_endpoints(server):
    base, service, state = server
    code, _, _ = call(base + "/api/outbox", headers={"Origin": "https://evil.example"})
    assert code == 403
    code, body, _ = call(base + "/api/outbox")
    assert code == 200 and body["outbox"][0]["attempt_id"] == "em_1" and body["outbox"][0]["to"] == "me@example.test"
    code, body, _ = call(base + "/api/outbox/em_1/sent", "POST", {"message_id": "<m1@x>", "thread_id": "t1"})
    assert code == 200 and body["ok"] and service.sent == [("em_1", "<m1@x>", "t1")]
    assert state.snapshot()["run"]["email"]["status"] == "sent"
    code, _, _ = call(base + "/api/outbox/em_9/sent", "POST", {"message_id": "x"})
    assert code == 404
    code, body, _ = call(base + "/api/reply", "POST", {"text": "   "}, headers={"Origin": "http://127.0.0.1:8787"})
    assert code == 400 and body["ok"] is False
    code, _, _ = call(base + "/api/reply", "POST", {"text": "yes"}, headers={"Origin": "https://evil.example"})
    assert code == 403
    code, body, _ = call(
        base + "/api/reply",
        "POST",
        {
            "text": "Sure, Tuesday afternoons work. Email me.",
            "attempt": "em_1",
            "from": "doc@example.test",
            "at": "2026-10-20T22:05:00Z",
        },
        headers={"Origin": "http://localhost:8787"},
    )
    assert code == 200 and body["ok"] and body["reply"]["outcome"] == "AGREED_TO_TALK"
    assert service.replies[-1] == (
        "Sure, Tuesday afternoons work. Email me.",
        "em_1",
        "doc@example.test",
        "2026-10-20T22:05:00Z",
    )
    em = state.snapshot()["run"]["email"]
    assert em["status"] == "replied" and em["reply"]["handoff"]["availability"] == "Tue afternoons"
    service.busy_flag = True
    code, _, _ = call(base + "/api/reply", "POST", {"text": "yes"}, headers={"Origin": "http://127.0.0.1:8787"})
    assert code == 409
