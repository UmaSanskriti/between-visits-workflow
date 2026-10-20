"""Local control service for the live call.

Runs on the operator's machine (where the Vapi key lives). The public dashboard talks to it over localhost:
  GET  /api/state      what the agent is doing right now, the live transcript, the last result
  POST /api/call       start the staged live call (one at a time)
  POST /api/webhook    Vapi server messages (transcript, status-update, end-of-call-report), via a tunnel
  GET  /data-live/...  the masked live export, for the dashboard's ?source=live view
  GET  /               the dashboard itself, served from docs/, so it works without GitHub Pages too
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import live
from .clock import Clock, iso
from .config import ConfigError, load_config, load_dotenv
from .export import mask_phone
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ORIGIN_PREFIXES = ("http://127.0.0.1:", "http://localhost:", "https://umasanskriti.github.io")
MAX_LOG = 80


class LiveState:
    """Thread-safe snapshot of the live call for the dashboard."""

    def __init__(self):
        self._lock = threading.Lock()
        self.data = {
            "service": "ok",
            "status": "idle",
            "tunnel_url": "",
            "live_events": False,
            "updated_at": "",
            "call": {},
            "transcript": [],
            "partial": None,
            "result": None,
            "error": "",
            "log": [],
        }

    def update(self, **fields):
        with self._lock:
            self.data.update(fields)
            self.data["updated_at"] = iso(Clock().now())

    def log(self, line: str):
        with self._lock:
            self.data["log"] = (self.data["log"] + [f"{iso(Clock().now())} {line}"])[-MAX_LOG:]
            self.data["updated_at"] = iso(Clock().now())
        print(line, flush=True)

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.data))

    def reset_call(self):
        with self._lock:
            self.data.update({"call": {}, "transcript": [], "partial": None, "result": None, "error": ""})

    def webhook(self, message: dict):
        kind = message.get("type")
        with self._lock:
            call = self.data["call"]
            if kind == "transcript":
                role = "agent" if message.get("role") in ("assistant", "bot") else "practice"
                text = (message.get("transcript") or "").strip()
                if not text:
                    return
                if message.get("transcriptType") == "final":
                    self.data["transcript"].append({"role": role, "text": text, "at": iso(Clock().now())})
                    self.data["partial"] = None
                else:
                    self.data["partial"] = {"role": role, "text": text}
            elif kind == "status-update":
                call["vapi_status"] = message.get("status", "")
                if message.get("status") == "in-progress":
                    self.data["status"] = "in_call"
                if message.get("endedReason"):
                    call["ended_reason"] = message["endedReason"]
            elif kind == "end-of-call-report":
                artifact = message.get("artifact") or {}
                call["ended_reason"] = message.get("endedReason", call.get("ended_reason", ""))
                call["summary"] = (message.get("analysis") or {}).get("summary") or message.get("summary", "")
                if not self.data["transcript"]:
                    for m in artifact.get("messages") or message.get("messages") or []:
                        if m.get("role") in ("assistant", "bot", "user") and (m.get("message") or m.get("content")):
                            self.data["transcript"].append(
                                {
                                    "role": "agent" if m["role"] != "user" else "practice",
                                    "text": m.get("message") or m.get("content"),
                                    "at": "",
                                }
                            )
                self.data["partial"] = None
            self.data["updated_at"] = iso(Clock().now())


class LiveService:
    def __init__(self, state: LiveState, port: int, tunnel_url: str = "", any_time: bool = True):
        self.state = state
        self.port = port
        self.tunnel_url = tunnel_url
        self.any_time = any_time
        self.secret = secrets.token_urlsafe(24)
        self._thread: threading.Thread | None = None
        self.paths = live.LivePaths()
        self.export_dir = ROOT / "docs" / "data-live"
        state.update(tunnel_url=tunnel_url, live_events=bool(tunnel_url))

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start_call(self) -> tuple[bool, str]:
        if self.running():
            return False, "a call is already in progress"
        self.state.reset_call()
        self.state.update(status="preparing")
        self._thread = threading.Thread(target=self._run, name="live-call", daemon=True)
        self._thread.start()
        return True, "started"

    def _run(self):
        log = self.state.log
        try:
            env = live.live_env(load_dotenv(ROOT / ".env", dict(os.environ)), any_time=self.any_time)
            cfg = load_config(ROOT / "config.toml", env=env)
            clock = Clock()
            store = Store(cfg.data_dir, clock).load()
            allowed = live.preview(store, cfg, clock, self.paths, log)
            if not allowed:
                raise RuntimeError("the gate allowed no contact; see the log")
            server_url = f"{self.tunnel_url}/api/webhook" if self.tunnel_url else ""
            provider = live.connect(
                cfg, clock, log, server_url=server_url, server_secret=self.secret if server_url else ""
            )
            self.state.update(
                status="dialing",
                call={
                    "to": mask_phone(allowed[0]["to"]),
                    "name": allowed[0]["name"],
                    "first_message": allowed[0]["first_message"],
                    "from": provider.phone_number,
                    "vapi_status": "queued",
                },
            )

            def on_poll(call: dict):
                fields = {"vapi_status": call.get("status", ""), "provider_call_id": call.get("id", "")}
                if call.get("endedReason"):
                    fields["ended_reason"] = call["endedReason"]
                snap = self.state.snapshot()
                current = snap["call"]
                current.update(fields)
                status = "in_call" if call.get("status") in ("ringing", "in-progress") else snap["status"]
                self.state.update(call=current, status=status)

            provider.on_poll = on_poll
            result = live.dial(store, cfg, clock, provider, self.paths, log)
            snap = self.state.snapshot()
            if not snap["transcript"]:
                for t in result["transcripts"]:
                    for u in t["utterances"]:
                        snap["transcript"].append(
                            {"role": "agent" if u["speaker"] == "agent" else "practice", "text": u["text"], "at": ""}
                        )
            attempts = result["attempts"]
            summary = {
                "outcome": attempts[0]["outcome"] if attempts else "",
                "failure_category": attempts[0]["failure_category"] if attempts else "",
                "duration_sec": attempts[0]["duration_sec"] if attempts else 0,
                "cost_usd": attempts[0]["cost_usd"] if attempts else 0,
                "ended_reason": attempts[0]["provider_ended_reason"] if attempts else "",
                "recording_ref": attempts[0]["recording_ref"] if attempts else "",
                "handoff": result["handoffs"][0] if result["handoffs"] else None,
                "run_id": result["run"]["run_id"],
            }
            self.state.update(status="ended", transcript=snap["transcript"], partial=None, result=summary)
            log(f"live: done, outcome {summary['outcome']}")
        except (ConfigError, live.VapiError, RuntimeError, OSError) as exc:
            log(f"live: error: {exc}")
            self.state.update(status="error", error=str(exc))
        except Exception as exc:  # noqa: BLE001 - surface anything to the dashboard rather than dying silently
            log(f"live: unexpected error: {exc!r}")
            self.state.update(status="error", error=repr(exc))


def make_handler(service: LiveService):
    docs = ROOT / "docs"
    types = {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".ics": "text/calendar",
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "between-visits-live/0.1"

        def log_message(self, fmt, *args):  # quiet access log; keep errors
            if args and str(args[1]).startswith(("4", "5")):
                print(f"http: {fmt % args}", flush=True)

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin", "")
            return not origin or origin.startswith(ALLOWED_ORIGIN_PREFIXES)

        def _cors(self):
            origin = self.headers.get("Origin", "")
            if origin and origin.startswith(ALLOWED_ORIGIN_PREFIXES):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")

        def _json(self, code: int, payload):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                return json.loads(raw.decode() or "{}")
            except ValueError:
                return {}

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                return self._json(200, service.state.snapshot())
            if path.startswith("/data-live/"):
                return self._file(service.export_dir / path[len("/data-live/") :], service.export_dir)
            if path in ("/", "/index.html"):
                return self._file(docs / "index.html", docs)
            return self._file(docs / path.lstrip("/"), docs)

        def _file(self, target: Path, base: Path):
            try:
                target, base = target.resolve(), base.resolve()
                if base not in target.parents or not target.is_file():
                    return self._json(404, {"error": "not found"})
                data = target.read_bytes()
            except OSError:
                return self._json(404, {"error": "not found"})
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", types.get(target.suffix, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/call":
                if not self._origin_ok():
                    return self._json(403, {"error": "origin not allowed"})
                ok, msg = service.start_call()
                return self._json(200 if ok else 409, {"started": ok, "message": msg})
            if path == "/api/webhook":
                if service.secret and self.headers.get("x-vapi-secret", "") != service.secret:
                    return self._json(401, {"error": "bad secret"})
                message = (self._body() or {}).get("message") or {}
                service.state.webhook(message)
                return self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

    return Handler


def start_tunnel(port: int, timeout: float = 25.0):
    """Start a Cloudflare quick tunnel (no account needed) so Vapi can reach the local webhook."""
    exe = shutil.which("cloudflared")
    if not exe:
        return None, ""
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = ""
    deadline = time.monotonic() + timeout
    assert proc.stdout is not None
    while time.monotonic() < deadline and not url:
        line = proc.stdout.readline()
        if not line:
            break
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
    if url:
        threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()  # drain output
    return proc, url


def main(port: int = 8787, no_tunnel: bool = False, any_time: bool = True) -> int:
    state = LiveState()
    proc, tunnel_url = (None, "") if no_tunnel else start_tunnel(port)
    if not no_tunnel and not tunnel_url:
        state.log(
            "serve: no tunnel (install cloudflared with `brew install cloudflared` for live transcript); "
            "falling back to polling, the transcript appears when the call ends"
        )
    service = LiveService(state, port, tunnel_url, any_time=any_time)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service))
    state.log(
        f"serve: listening on http://127.0.0.1:{port}  (dashboard: http://127.0.0.1:{port}/?source=live, "
        f"or https://umasanskriti.github.io/between-visits-workflow/?source=live)"
    )
    if tunnel_url:
        state.log(f"serve: live events via {tunnel_url}/api/webhook")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        if proc:
            proc.terminate()
    return 0
