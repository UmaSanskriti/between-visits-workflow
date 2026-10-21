"""Local control service for the live call.

Runs on the operator's machine (where the Vapi key lives). The public dashboard talks to it over localhost:
  GET  /api/state      what the agent is doing right now, the live transcript, the last result
  POST /api/run        run the agent once: ingest the dummy list, gate, compose the playbook email for the first
                       eligible practice, queue it for the Gmail connector (or send via SMTP); stage progress
  GET  /api/outbox     queued messages with their full text (the Gmail driver reads them)
  POST /api/outbox/<attempt>/sent   {message_id, thread_id}: the message went out
  POST /api/reply      {attempt?, text, from?, at?}: a reply arrived (pasted, or read from Gmail)
  GET  /data/...       the export (docs/data), so the page shows the fresh run without a push
  POST /api/call       staged live call through Vapi (kept for when a Twilio number exists)
  POST /api/webhook    Vapi server messages via a tunnel
  GET  /               the dashboard itself, served from docs/
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
from .brain import MockBrain
from .clock import Clock, iso
from .config import ConfigError, load_config, load_dotenv
from .export import mask_email, mask_phone
from .pipeline import run_pipeline
from .store import Store
from .voice import MockVoiceProvider

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ORIGIN_PREFIXES = ("http://127.0.0.1:", "http://localhost:", "https://umasanskriti.github.io")
MAX_LOG = 80
IDLE_RUN = {
    "status": "idle",
    "stage": "",
    "stages": {},
    "started_at": "",
    "finished_at": "",
    "run_id": "",
    "error": "",
    "email": None,  # {attempt_id, contact_name, practice, to, subject, body, status, transport, ..., reply}
}


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
            "run": dict(IDLE_RUN),
            "sheet": {
                "path": "",
                "rows": 0,
                "last_import": "",
                "last_export": "",
                "last_result": None,
                "watching": False,
                "backend": "",
                "url": "",
            },
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

    def sheet_update(self, **fields):
        with self._lock:
            self.data["sheet"].update(fields)
            self.data["updated_at"] = iso(Clock().now())

    def run_update(self, **fields):
        with self._lock:
            self.data["run"].update(fields)
            self.data["updated_at"] = iso(Clock().now())

    def run_stage(self, name: str, status: str, counts: dict):
        with self._lock:
            run = self.data["run"]
            if name == "run":
                run.update(
                    {
                        "status": "done",
                        "stage": "",
                        "run_id": counts.get("run_id", ""),
                        "finished_at": counts.get("finished_at", "") or iso(Clock().now()),
                    }
                )
            else:
                run["stage"] = name if status == "start" else run.get("stage", "")
                run["stages"][name] = {"status": status, "counts": counts}
            self.data["updated_at"] = iso(Clock().now())

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
        self.dry_data_dir = ROOT / "data"
        self.dry_export_dir = ROOT / "docs" / "data"
        self.seeds_dir = ROOT / "seeds"
        self.env_overrides: dict = {}  # tests inject the allowlist and transport here
        self._run_thread: threading.Thread | None = None
        self._sheet_lock = threading.Lock()
        self._watcher: threading.Thread | None = None
        state.update(tunnel_url=tunnel_url, live_events=bool(tunnel_url))

    # ---- practice sheet (two-way sync: Google Sheet or the xlsx fallback) ----
    def sheet_backend(self):
        from . import sheet as sheetmod

        env = load_dotenv(ROOT / ".env", dict(os.environ))
        env.update(self.env_overrides)
        cfg = type("Cfg", (), {"data_dir": self.dry_data_dir})()
        return sheetmod.get_backend(cfg, env, log=self.state.log)

    def sheet_file(self) -> Path:  # kept for older callers/tests: the xlsx location
        from . import sheet as sheetmod

        env = load_dotenv(ROOT / ".env", dict(os.environ))
        env.update(self.env_overrides)
        return sheetmod.sheet_path(type("Cfg", (), {"data_dir": self.dry_data_dir})(), env)

    def _seed_path(self):
        try:
            backend = self.sheet_backend()
            if backend.exists():
                return backend if backend.name != "xlsx" else backend.path
        except Exception as exc:  # noqa: BLE001 - a sheet problem must not block the run; fall back to the CSV seeds
            self.state.log(f"sheet: unavailable for this run ({exc}); using the CSV seeds")
        return self.seeds_dir / "test_doctors.csv"

    def _sheet_state(self, backend, **extra) -> None:
        self.state.sheet_update(
            path=backend.describe(), backend=backend.name, url=getattr(backend, "url", "") or "", **extra
        )

    def sheet_export(self, log=None) -> dict:
        from . import sheet as sheetmod

        log = log or self.state.log
        with self._sheet_lock:
            cfg, clock, store = self._agent_context()
            backend = self.sheet_backend()
            result = sheetmod.sync_sheet(store, backend, clock, log)
            self._sheet_state(
                backend, rows=result["rows"], last_export=iso(clock.now()), last_result=result.get("imported")
            )
            return result

    def sheet_import(self, log=None) -> dict:
        from . import sheet as sheetmod
        from .export import export
        from .summary import build_summary

        log = log or self.state.log
        with self._sheet_lock:
            cfg, clock, store = self._agent_context()
            backend = self.sheet_backend()
            counts = sheetmod.import_sheet(store, backend, clock, log)
            last = store.logs["runs"][-1] if store.logs["runs"] else {}
            export(store, cfg.export_dir, build_summary(store, last), last)
            rows = sheetmod.export_sheet(store, backend, clock)  # normalise the sheet (ids written back)
            self._sheet_state(
                backend, rows=rows, last_import=iso(clock.now()), last_export=iso(clock.now()), last_result=counts
            )
            return counts

    def bootstrap_sheet(self) -> None:
        from . import sheet as sheetmod

        try:
            backend = self.sheet_backend()
        except Exception as exc:  # noqa: BLE001
            self.state.log(f"sheet: not configured ({exc})")
            self.state.sheet_update(last_result={"error": str(exc)}, watching=False)
            return
        if backend.exists():
            if sheetmod.changed_since_export(backend):
                self.sheet_import()
            else:
                side = sheetmod.read_sidecar(backend)
                self._sheet_state(
                    backend,
                    rows=side.get("rows", 0),
                    last_export=side.get("last_export", ""),
                    last_import=side.get("last_import", ""),
                )
            return
        result = self.sheet_export()
        self.state.log(f"sheet: created {backend.describe()} with {result['rows']} practices")

    def start_sheet_watcher(self, interval: float | None = None) -> None:
        from . import sheet as sheetmod

        def loop():
            while True:
                wait = interval or 3.0
                try:
                    backend = self.sheet_backend()
                    wait = interval or sheetmod.watch_interval(backend)
                    if backend.exists() and sheetmod.changed_since_export(backend) and not self.busy():
                        self.state.log("sheet: change detected, importing")
                        self.sheet_import()
                except Exception as exc:  # noqa: BLE001 - keep watching
                    self.state.log(f"sheet: watcher error: {exc}")
                    self.state.sheet_update(last_result={"error": str(exc)})
                time.sleep(wait)

        self._watcher = threading.Thread(target=loop, name="sheet-watcher", daemon=True)
        self._watcher.start()
        self.state.sheet_update(watching=True)

    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run_in_progress(self) -> bool:
        return self._run_thread is not None and self._run_thread.is_alive()

    def busy(self) -> bool:
        return self.running() or self.run_in_progress()

    # ---- the agent run: pick the first eligible dummy doctor, compose, queue or send the email ----
    def _agent_context(self):
        """Live mode on the main data dir: the dashboard shows the real email flow, dummy practices only."""
        env = load_dotenv(ROOT / ".env", dict(os.environ))
        env.pop("BV_NOW", None)
        env["BV_DATA_DIR"] = str(self.dry_data_dir)
        env["BV_EXPORT_DIR"] = str(self.dry_export_dir)
        env["BV_OUTREACH_CHANNEL"] = "email"
        env.update(self.env_overrides)
        env = live.live_env(env, any_time=self.any_time)
        cfg = load_config(ROOT / "config.toml", env=env)
        cfg.live_max_calls_per_run = 1  # one practice per press of the button
        clock = Clock()
        store = Store(cfg.data_dir, clock).load()
        return cfg, clock, store

    def start_run(self, reset: bool = True, now: str = "") -> tuple[bool, str]:
        """Run the agent once: ingest the dummy list, gate, compose, queue (Gmail connector) or send (SMTP)."""
        if self.busy():
            return False, "a run or a call is already in progress"
        self.state.run_update(
            **{**IDLE_RUN, "stages": {}, "status": "running", "started_at": iso(Clock().now()), "email": None}
        )
        self._run_thread = threading.Thread(target=self._run_agent, args=(reset,), name="agent-run", daemon=True)
        self._run_thread.start()
        return True, "started"

    def _run_agent(self, reset: bool):
        log = self.state.log
        try:
            cfg, clock, store = self._agent_context()
            if reset:
                store.reset()
            log(
                f"run: agent starting ({'fresh list' if reset else 'continuing'}; transport "
                f"{'smtp' if cfg.smtp_password else 'outbox'}; demo inbox {mask_email(cfg.email_demo_recipient)})"
            )
            provider = live.connect_email(cfg, clock, log)

            def on_stage(name, status, counts):
                if name != "run":  # the run is reported done only once the email summary below is in place
                    self.state.run_stage(name, status, counts)

            run, summary = run_pipeline(
                store,
                cfg,
                clock,
                MockBrain(store, clock, down=cfg.brain_down),
                MockVoiceProvider(cfg.webhook_secret, clock),
                self._seed_path(),
                self.seeds_dir / "clearances.csv",
                self.seeds_dir / "suppressions.csv",
                trigger="dashboard",
                on_stage=on_stage,
                mailer=provider,
            )
            attempts = [a for a in store.email_attempts.values() if a.run_id == run["run_id"]]
            email: dict
            if attempts:
                a = attempts[0]
                contact = store.contacts.get(a.contact_id)
                outbox = {m["attempt_id"]: m for m in live.email_outbox(store)}
                ch = store.channels.get(a.channel_id)
                to = outbox.get(a.id, {}).get("to") or (ch.value if ch else "")
                email = {
                    "attempt_id": a.id,
                    "contact_id": a.contact_id,
                    "contact_name": contact.full_name if contact else "",
                    "practice": contact.practice_name if contact else "",
                    "to": mask_email(to),
                    "redirected_to": a.redirected_to,
                    "subject": a.subject,
                    "body": a.body,
                    "status": "sent" if a.status == "sent" else ("error" if a.status == "failed" else "queued"),
                    "transport": getattr(provider, "name", ""),
                    "provider_message_id": a.provider_message_id,
                    "thread_id": a.thread_id,
                    "sent_at": a.sent_at,
                    "error": a.outcome_reason if a.status == "failed" else "",
                    "reply": None,
                    "personalization": list(a.personalization),
                    "research": None,
                }
                enrichment = store.enrichments.get(a.contact_id)
                if enrichment:
                    email["research"] = {
                        "summary": enrichment.summary,
                        "hook": enrichment.hook,
                        "services": list(enrichment.services),
                        "doctor_bio": enrichment.doctor_bio,
                        "glp1": enrichment.glp1_mentioned,
                        "source": enrichment.source,
                        "source_note": enrichment.source_note,
                        "website": enrichment.website,
                    }
                log(f"run: {a.id} {email['status']} for {email['contact_name']} -> {email['to']}")
            else:
                email = {"status": "error", "error": "the gate allowed no practice this run; see the service log"}
                log("run: no email was composed; every practice was blocked or deferred")
            self.state.run_update(email=email)
            try:
                self.sheet_export(log)
            except Exception as exc:  # noqa: BLE001
                log(f"sheet: export after run failed: {exc}")
            done = {
                "run_id": run["run_id"],
                "finished_at": run.get("finished_at", ""),
                "gate_escapes": run.get("gate_escapes", 0),
            }
            self.state.run_stage("run", "done", done)
        except (ConfigError, RuntimeError, OSError, ValueError, live.EmailSendError) as exc:
            log(f"run: error: {exc}")
            self.state.run_update(status="error", error=str(exc), finished_at=iso(Clock().now()))
        except Exception as exc:  # noqa: BLE001
            log(f"run: unexpected error: {exc!r}")
            self.state.run_update(status="error", error=repr(exc), finished_at=iso(Clock().now()))

    def outbox(self) -> list[dict]:
        cfg, clock, store = self._agent_context()
        return live.email_outbox(store)

    def mark_sent(self, attempt_id: str, message_id: str, thread_id: str = "") -> tuple[int, dict]:
        """The operator (or the Gmail connector driver) reports that a queued message went out."""
        if self.busy():
            return 409, {"ok": False, "message": "a run is in progress"}
        log = self.state.log
        try:
            cfg, clock, store = self._agent_context()
            attempt = live.mark_email_sent(store, cfg, clock, attempt_id, message_id, thread_id, log)
        except KeyError:
            return 404, {"ok": False, "message": f"no queued email {attempt_id}"}
        except (ConfigError, RuntimeError, OSError, ValueError) as exc:
            log(f"run: mark-sent error: {exc}")
            return 400, {"ok": False, "message": str(exc)}
        snap = self.state.snapshot()["run"]
        email = dict(snap.get("email") or {})
        if email.get("attempt_id") in ("", attempt_id):
            email.update(
                {
                    "attempt_id": attempt_id,
                    "status": "sent",
                    "provider_message_id": message_id,
                    "thread_id": thread_id,
                    "sent_at": attempt.get("sent_at", ""),
                }
            )
            self.state.run_update(email=email)
        return 200, {"ok": True, "attempt": attempt}

    def record_reply(self, text: str, attempt: str = "", sender: str = "", at: str = "") -> tuple[int, dict]:
        """Classify a reply through the pipeline (pasted on the dashboard, or read from Gmail by the driver)."""
        if self.busy():
            return 409, {"ok": False, "message": "a run is in progress"}
        text = (text or "").strip()
        if not text:
            return 400, {"ok": False, "message": "reply text is empty"}
        log = self.state.log
        snap = self.state.snapshot()["run"]
        current = snap.get("email") or {}
        needle = attempt or current.get("attempt_id") or current.get("contact_id") or ""
        if not needle:
            return 400, {"ok": False, "message": "no email to attach this reply to; run the agent first"}
        try:
            cfg, clock, store = self._agent_context()
            result = live.record_reply(store, cfg, clock, needle, text, log, sender=sender, at=at)
        except KeyError:
            return 404, {"ok": False, "message": f"no email attempt for {needle}"}
        except (ConfigError, RuntimeError, OSError, ValueError) as exc:
            log(f"run: reply error: {exc}")
            return 400, {"ok": False, "message": str(exc)}
        reply = {
            "outcome": result.get("outcome", ""),
            "handoff": result.get("handoff") or None,
            "text": text,
            "at": result.get("at") or iso(clock.now()),
            "from": sender,
        }
        if current.get("attempt_id") in ("", result.get("attempt_id")):
            email = dict(current)
            email.update({"attempt_id": result.get("attempt_id", needle), "status": "replied", "reply": reply})
            self.state.run_update(email=email)
        log(f"run: reply recorded, outcome {reply['outcome']}")
        return 200, {"ok": True, "reply": reply, "attempt_id": result.get("attempt_id", needle)}

    # ---- staged live call through Vapi (kept for when a Twilio number exists) ----
    def start_call(self) -> tuple[bool, str]:
        if self.busy():
            return False, "a call or a run is already in progress"
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
            if path == "/api/sheet":
                snap = service.state.snapshot()
                return self._json(200, {"sheet": snap["sheet"]})
            if path == "/api/outbox":
                if not self._origin_ok():
                    return self._json(403, {"error": "origin not allowed"})
                try:
                    return self._json(200, {"outbox": service.outbox()})
                except (ConfigError, OSError, ValueError) as exc:
                    return self._json(400, {"error": str(exc)})
            if path.startswith("/data-live/"):
                return self._file(service.export_dir / path[len("/data-live/") :], service.export_dir)
            if path.startswith("/data/"):
                return self._file(service.dry_export_dir / path[len("/data/") :], service.dry_export_dir)
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
            if path == "/api/run":
                if not self._origin_ok():
                    return self._json(403, {"error": "origin not allowed"})
                body = self._body() or {}
                reset = bool(body.get("reset", False))  # state persists; the agent moves to the next practice
                ok, msg = service.start_run(reset=bool(reset), now=str(body.get("now") or ""))
                return self._json(200 if ok else 409, {"started": ok, "message": msg})
            m = re.fullmatch(r"/api/outbox/([A-Za-z0-9_.-]+)/sent", path)
            if m:
                if not self._origin_ok():
                    return self._json(403, {"error": "origin not allowed"})
                body = self._body() or {}
                message_id, thread_id = str(body.get("message_id") or ""), str(body.get("thread_id") or "")
                code, payload = service.mark_sent(m.group(1), message_id, thread_id)
                return self._json(code, payload)
            if path in ("/api/sheet/import", "/api/sheet/export"):
                if not self._origin_ok():
                    return self._json(403, {"error": "origin not allowed"})
                if service.busy():
                    return self._json(409, {"error": "a run is in progress"})
                try:
                    result = service.sheet_import() if path.endswith("import") else service.sheet_export()
                except Exception as exc:  # noqa: BLE001
                    return self._json(500, {"error": str(exc)})
                return self._json(200, {"ok": True, "result": result, "sheet": service.state.snapshot()["sheet"]})
            if path == "/api/reply":
                if not self._origin_ok():
                    return self._json(403, {"error": "origin not allowed"})
                body = self._body() or {}
                code, payload = service.record_reply(
                    str(body.get("text") or ""),
                    str(body.get("attempt") or body.get("contact") or ""),
                    str(body.get("from") or ""),
                    str(body.get("at") or ""),
                )
                return self._json(code, payload)
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
    try:
        service.bootstrap_sheet()
        service.start_sheet_watcher()
    except Exception as exc:  # noqa: BLE001 - the sheet is a convenience; the service must still start
        state.log(f"sheet: not available ({exc})")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(service))
    state.log(
        f"serve: listening on http://127.0.0.1:{port}  (dashboard: http://127.0.0.1:{port}/ "
        f"or https://umasanskriti.github.io/between-visits-workflow/)"
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
