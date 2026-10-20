"""Live-call runner shared by the CLI (`live-demo`) and the local service (`serve`)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .brain import MockBrain
from .clock import Clock
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


__all__ = ["LivePaths", "VapiError", "connect", "dial", "live_env", "preview"]
