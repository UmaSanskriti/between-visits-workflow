"""Live-call runner shared by the CLI (`live-demo`) and the local service (`serve`)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .brain import MockBrain
from .clock import Clock, iso
from .compliance import pre_call_gate
from .config import Config
from .pipeline import run_pipeline
from .scripts import build_script
from .store import Store
from .vapi import VapiClient, VapiError, VapiVoiceProvider, call_body
from .voice import CallRequest, MockVoiceProvider

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class LivePaths:
    seed: Path = ROOT / "seeds" / "live" / "demo.csv"
    clearances: Path = ROOT / "seeds" / "live" / "clearances.csv"
    suppressions: Path = ROOT / "seeds" / "live" / "suppressions.csv"


def live_env(env: dict, any_time: bool = False) -> dict:
    """Environment for a staged live run: explicit opt-in, separate data and export dirs."""
    env = dict(env)
    env.update({"BV_MODE": "live", "BV_ALLOW_LIVE_CALLS": "1"})
    env.setdefault("BV_DATA_DIR", "data-live")
    env.setdefault("BV_EXPORT_DIR", "docs/data-live")
    if any_time:
        env["BV_LIVE_IGNORE_WINDOW"] = "1"
    return env


def connect(
    cfg: Config, clock: Clock, log, server_url: str = "", server_secret: str = "", create_number: bool = False
) -> VapiVoiceProvider:
    """Free API calls: verify the key, pick the number, create or update the assistant."""
    client = VapiClient(cfg.vapi_api_key)
    provider = VapiVoiceProvider(client, cfg, clock, log=log)
    assistants = client.list_assistants()
    log(f"vapi: key ok, {len(assistants)} assistant(s) in the account")
    for n in client.list_phone_numbers():
        log(
            f"vapi: number {n.get('number') or '?'}  id={n.get('id')}  provider={n.get('provider')}  "
            f"status={n.get('status')}"
        )
    provider.ensure_phone_number(create_if_missing=create_number)
    if not cfg.callback_number:
        cfg.callback_number = provider.phone_number
    provider.ensure_assistant(cfg.callback_number, server_url, server_secret)
    log(
        f"vapi: dialing from {provider.phone_number or provider.phone_number_id}; callback {cfg.callback_number}"
        + ("; live events to " + server_url if server_url else "; no server url (polling only)")
    )
    return provider


def preview(store: Store, cfg: Config, clock: Clock, paths: LivePaths, log) -> list[dict]:
    """Ingest + verify the live seeds and show, per contact, the gate verdict and the call body. No credentials."""
    brain = MockBrain(store, clock)
    run_pipeline(
        store,
        cfg,
        clock,
        brain,
        MockVoiceProvider(cfg.webhook_secret, clock),
        paths.seed,
        paths.clearances,
        paths.suppressions,
        stages=("ingest", "verify"),
        trigger="live-preview",
    )
    allowed = []
    for contact in store.contacts.values():
        phones = list(store.channels_for(contact.id, "phone"))
        channel = next((ch for ch in phones if ch.verified), phones[0] if phones else None)
        gate = pre_call_gate(store, contact, channel, cfg, clock)
        verdict = ("ALLOWED" if gate.allowed else "blocked: " + ", ".join(gate.reasons)) + (
            " (waived: " + ", ".join(gate.waivers) + ")" if gate.waivers else ""
        )
        log(f"live: {contact.full_name or contact.id} ({channel.value if channel else 'no phone'}) -> {verdict}")
        if not gate.allowed or channel is None:
            continue
        script, errors = build_script(
            contact, channel, brain.resolve_script({"contact": {"segment": contact.segment}}), "preview", clock, cfg
        )
        if errors:
            log(f"live: script blocked: {errors}")
            continue
        body = call_body(
            CallRequest(
                call_attempt_id="preview",
                contact_id=contact.id,
                to_number=channel.value,
                from_number=cfg.callback_number,
                first_message=script.first_message,
                system_prompt=script.system_prompt,
                script_version=script.script_version,
            ),
            "<assistant id>",
            "<phone number id>",
        )
        shown = json.loads(json.dumps(body))
        prompt = shown["assistantOverrides"]["variableValues"]["system_prompt"]
        shown["assistantOverrides"]["variableValues"]["system_prompt"] = prompt[:300] + f" ... ({len(prompt)} chars)"
        allowed.append(
            {
                "contact_id": contact.id,
                "name": contact.full_name,
                "to": channel.value,
                "body": shown,
                "first_message": script.first_message,
            }
        )
    return allowed


def dial(store: Store, cfg: Config, clock: Clock, provider: VapiVoiceProvider, paths: LivePaths, log) -> dict:
    """Place the call(s) the gate allows, wait for them to end, classify, store, export."""
    run, summary = run_pipeline(
        store,
        cfg,
        clock,
        MockBrain(store, clock),
        provider,
        paths.seed,
        paths.clearances,
        paths.suppressions,
        stages=("call", "deliver"),
        trigger="live",
    )
    attempts = [a for a in store.call_attempts.values() if a.run_id == run["run_id"]]
    handoffs = [i.to_dict() for i in store.interviews.values() if any(i.call_attempt_id == a.id for a in attempts)]
    transcripts = [
        store.transcripts[a.transcript_id].to_dict() for a in attempts if a.transcript_id in store.transcripts
    ]
    return {
        "run": run,
        "summary": summary,
        "attempts": [a.to_dict() for a in attempts],
        "handoffs": handoffs,
        "transcripts": transcripts,
    }


# ---------------------------------------------------------------------------------------------------------------
# Email channel: preview (no credentials), connect (SMTP or the Gmail-connector outbox), send, record replies.
# ---------------------------------------------------------------------------------------------------------------
from .compliance import pre_send_gate  # noqa: E402
from .delivery import deliver_transcripts  # noqa: E402
from .email.compose import build_email  # noqa: E402
from .email.provider import (  # noqa: E402
    EmailSendError,
    ImapReplyPoller,
    ManualReplies,
    MockEmailProvider,
    OutboxEmailProvider,
    SmtpEmailProvider,
)
from .export import export  # noqa: E402
from .orchestrator import mark_sent, process_email_events  # noqa: E402
from .summary import build_summary  # noqa: E402


def connect_email(cfg: Config, clock: Clock, log, transport: str = ""):
    """OutboxEmailProvider by default (sent by hand through the Gmail connector); SMTP when a password exists."""
    transport = (transport or ("smtp" if cfg.smtp_password else "outbox")).lower()
    if transport == "outbox":
        provider = OutboxEmailProvider(Path(cfg.data_dir) / "outbox", clock, log=log)
        log(f"email: outbox transport; queued messages land in {provider.outbox_dir}")
        return provider
    missing = [
        name
        for name, val in (
            ("SMTP_HOST", cfg.smtp_host),
            ("EMAIL_FROM", cfg.email_from_address),
            ("SMTP_USER", cfg.smtp_user),
            ("SMTP_PASSWORD", cfg.smtp_password),
        )
        if not val
    ]
    if missing:
        raise EmailSendError("email: missing " + ", ".join(missing) + " in .env")
    poller = None
    if cfg.imap_host:
        poller = ImapReplyPoller(
            cfg.imap_host, cfg.imap_user or cfg.smtp_user, cfg.imap_password or cfg.smtp_password, log=log
        )
    provider = SmtpEmailProvider(
        cfg.smtp_host,
        cfg.smtp_port,
        cfg.smtp_user,
        cfg.smtp_password,
        cfg.email_from_address,
        cfg.email_from_name,
        cfg.email_reply_to,
        poller=poller,
        log=log,
    )
    log(
        f"email: smtp {cfg.smtp_host}:{cfg.smtp_port} as {cfg.smtp_user}; from {cfg.email_from_address}"
        + ("; replies via IMAP " + cfg.imap_host if poller else "; no IMAP (record replies with `reply`)")
    )
    return provider


def preview_email(store: Store, cfg: Config, clock: Clock, paths: LivePaths, log) -> list[dict]:
    """Ingest + verify the seeds and show, per contact, the gate verdict and the exact email. No credentials."""
    brain = MockBrain(store, clock)
    run_pipeline(
        store,
        cfg,
        clock,
        brain,
        MockVoiceProvider(cfg.webhook_secret, clock),
        paths.seed,
        paths.clearances,
        paths.suppressions,
        stages=("ingest", "verify"),
        trigger="email-preview",
        mailer=MockEmailProvider(clock),
    )
    allowed = []
    for contact in store.contacts.values():
        emails = list(store.channels_for(contact.id, "email"))
        channel = next((ch for ch in emails if ch.verified), emails[0] if emails else None)
        gate = pre_send_gate(store, contact, channel, cfg, clock)
        verdict = "ALLOWED" if gate.allowed else "blocked: " + ", ".join(gate.reasons)
        log(f"email: {contact.full_name or contact.id} ({channel.value if channel else 'no email'}) -> {verdict}")
        if not gate.allowed or channel is None:
            continue
        draft, errors = build_email(
            contact, channel, brain.resolve_email({"contact": {"segment": contact.segment}}), cfg, clock
        )
        if errors:
            log(f"email: blocked by email rules: {errors}")
            continue
        allowed.append(
            {
                "contact_id": contact.id,
                "name": contact.full_name,
                "to": channel.value,
                "subject": draft.subject,
                "body": draft.body,
                "word_count": draft.word_count,
            }
        )
    return allowed


def send_demo_email(store: Store, cfg: Config, clock: Clock, provider, paths: LivePaths, log) -> dict:
    """Send (or queue) the email(s) the gate allows; returns the attempts so the dashboard can show them."""
    run, summary = run_pipeline(
        store,
        cfg,
        clock,
        MockBrain(store, clock),
        provider,
        paths.seed,
        paths.clearances,
        paths.suppressions,
        stages=("call", "deliver"),
        trigger="live-email",
        mailer=provider,
    )
    attempts = [a.to_dict() for a in store.email_attempts.values() if a.run_id == run["run_id"]]
    return {
        "run": run,
        "summary": summary,
        "attempts": attempts,
        "to": [a["channel_id"] for a in attempts],
        "queued": [a for a in attempts if a["status"] == "queued"],
        "transport": getattr(provider, "name", ""),
    }


def email_outbox(store: Store) -> list[dict]:
    """Queued messages waiting for a hand send through the Gmail connector, with their full text."""
    out = []
    outbox_dir = Path(store.dir) / "outbox"
    for a in store.email_attempts.values():
        if a.status != "queued":
            continue
        path = outbox_dir / f"{a.id}.json"
        message = json.loads(path.read_text()) if path.exists() else {}
        channel = store.channels.get(a.channel_id)
        out.append(
            {
                "attempt_id": a.id,
                "contact_id": a.contact_id,
                "to": message.get("to") or (channel.value if channel else ""),  # the demo inbox when redirected
                "subject": a.subject,
                "body": a.body,
                "from_name": message.get("from_name", ""),
                "from_address": message.get("from_address", ""),
                "reply_to": message.get("reply_to", ""),
                "redirected_to": a.redirected_to,
                "queued_at": message.get("queued_at", a.created_at),
            }
        )
    return out


def _last_run(store: Store) -> dict:
    return store.logs["runs"][-1] if store.logs["runs"] else {}


def mark_email_sent(
    store: Store, cfg: Config, clock: Clock, attempt_id: str, message_id: str, thread_id: str, log
) -> dict:
    """Operator reports a hand send (Gmail connector): flips queued -> sent and opens the thread transcript."""
    attempt = store.email_attempts.get(attempt_id)
    if attempt is None:
        raise KeyError(attempt_id)
    contact = store.contacts[attempt.contact_id]
    mark_sent(
        store,
        cfg,
        clock,
        attempt,
        contact,
        message_id,
        "gmail-connector",
        run_id=_last_run(store).get("run_id", "manual"),
    )
    if thread_id:
        store.update("email_attempts", attempt, "thread", {"thread_id": thread_id})
    path = Path(store.dir) / "outbox" / f"{attempt.id}.json"
    if path.exists():
        payload = json.loads(path.read_text())
        payload.update({"status": "sent", "message_id": message_id, "thread_id": thread_id})
        path.write_text(json.dumps(payload, indent=1) + "\n")
    store.save()
    run = _last_run(store)
    export(store, cfg.export_dir, build_summary(store, run), run)
    log(f"email: {attempt.id} marked sent ({message_id})")
    return attempt.to_dict()


def record_reply(
    store: Store, cfg: Config, clock: Clock, needle: str, text: str, log, sender: str = "", at: str = ""
) -> dict:
    """Operator path when replies are not polled: classify the pasted reply and apply it like an inbound event."""
    attempt = store.email_attempts.get(needle)
    if attempt is None:
        contact = next((c for c in store.contacts.values() if c.id == needle or c.external_id == needle), None)
        if contact is None:
            contact = next((c for c in store.contacts.values() if needle.lower() in (c.full_name or "").lower()), None)
        if contact is None:
            raise KeyError(needle)
        candidates = [
            a for a in store.email_attempts.values() if a.contact_id == contact.id and a.status in ("sent", "queued")
        ]
        candidates.sort(key=lambda a: a.sent_at or a.created_at, reverse=True)
        if not candidates:
            raise KeyError(f"no email attempt for {needle}")
        attempt = candidates[0]
    if attempt.status == "queued":  # the reply proves it went out
        mark_sent(
            store,
            cfg,
            clock,
            attempt,
            store.contacts[attempt.contact_id],
            "",
            "gmail-connector",
            run_id=_last_run(store).get("run_id", "manual"),
        )
    now = iso(clock.now())
    channel = store.channels.get(attempt.channel_id)
    event = {
        "event_id": f"manual:{attempt.id}:{now}",
        "type": "reply",
        "email_attempt_id": attempt.id,
        "provider_message_id": attempt.provider_message_id,
        "text": text,
        "at": at or now,
        "from": sender or (channel.value if channel else ""),
    }
    run_id = _last_run(store).get("run_id", "manual")
    process_email_events(store, cfg, clock, ManualReplies([event]), run_id)
    deliver_transcripts(store, cfg, clock, MockBrain(store, clock), run_id)
    store.save()
    run = _last_run(store)
    export(store, cfg.export_dir, build_summary(store, run), run)
    log(f"email: reply recorded for {attempt.id}: {attempt.outcome}")
    return {
        "attempt_id": attempt.id,
        "outcome": attempt.outcome,
        "handoff": attempt.handoff,
        "contact_status": store.contacts[attempt.contact_id].status,
        "text": text,
        "at": event["at"],
    }


__all__ = [
    "LivePaths",
    "VapiError",
    "connect",
    "connect_email",
    "dial",
    "email_outbox",
    "live_env",
    "mark_email_sent",
    "preview",
    "preview_email",
    "record_reply",
    "send_demo_email",
]
