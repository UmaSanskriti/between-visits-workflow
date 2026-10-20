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
    """Does not dial; records that a start was requested."""

    def __init__(self, state):
        super().__init__(state, port=0, tunnel_url="https://example.trycloudflare.com")
        self.starts = 0
        self.secret = "s3cret"

    def start_call(self):
        self.starts += 1
        return True, "started"


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
