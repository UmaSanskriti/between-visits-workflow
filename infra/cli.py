"""Command line entry point: `python -m infra <command>` or `bv <command>`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .brain import MockBrain
from .clock import Clock, FixedClock, iso
from .config import ConfigError, load_config, load_dotenv
from .delivery import redrive_dead_letters
from .export import export
from .orchestrator import add_suppression
from .pipeline import ALL_STAGES, run_pipeline
from .store import Store
from .summary import build_summary
from .voice import MockVoiceProvider

ROOT = Path(__file__).resolve().parents[1]


COMMON_OPTIONS = (
    ("--config", {"default": str(ROOT / "config.toml")}),
    ("--seed", {"default": str(ROOT / "seeds" / "test_doctors.csv")}),
    ("--clearances", {"default": str(ROOT / "seeds" / "clearances.csv")}),
    ("--suppressions", {"default": str(ROOT / "seeds" / "suppressions.csv")}),
    ("--now", {"help": "ISO 8601 instant to run 'as if' (also BV_NOW)"}),
    ("--data-dir", {"help": "override [paths].data_dir"}),
    ("--export-dir", {"help": "override [paths].export_dir"}),
)


class _SubParser(argparse.ArgumentParser):
    """Subcommand parser that also accepts the common options (after the subcommand)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _add_common(self, suppress=True)


def _add_common(parser: argparse.ArgumentParser, suppress: bool) -> None:
    """Common options are accepted before or after the subcommand."""
    for flag, kwargs in COMMON_OPTIONS:
        kwargs = dict(kwargs)
        if suppress:
            kwargs["default"] = argparse.SUPPRESS
        parser.add_argument(flag, **kwargs)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bv", description="Between Visits infra (dry run by default; staged live mode)")
    _add_common(p, suppress=False)
    sub = p.add_subparsers(dest="command", required=True, parser_class=_SubParser)
    run = sub.add_parser("run", help="full dry run: ingest, verify, call, deliver, summary, export")
    run.add_argument("--reset", action="store_true", help="wipe data/ first")
    run.add_argument("--trigger", default=os.environ.get("GITHUB_EVENT_NAME", "local"))
    for name in ALL_STAGES:
        sub.add_parser(name, help=f"run only the {name} stage (then summary + export)")
    sub.add_parser("summary", help="print the summary JSON")
    sub.add_parser("export", help="re-export docs/data from the current state")
    sup = sub.add_parser("suppress", help="add a do-not-contact entry (OPS-001)")
    sup.add_argument("--contact", help="contact id or external id")
    sup.add_argument("--phone", help="phone number (any format)")
    sup.add_argument("--reason", default="operator request")
    sub.add_parser("pause", help="pause calling (OPS-001)")
    sub.add_parser("resume", help="resume calling (OPS-001)")
    sub.add_parser("redrive", help="move dead-lettered transcripts back to pending")
    appr = sub.add_parser("approve", help="approve a drafted interview invite (playbook section 9)")
    appr.add_argument("interview", help="interview id, or a contact id / external id / name")
    appr.add_argument("--by", default="operator", help="approver name")
    hist = sub.add_parser("history", help="full history for one contact (OPS-001)")
    hist.add_argument("contact", help="contact id, external id, or name substring")
    sub.add_parser("reset", help="wipe data/ and docs/data")
    vp = sub.add_parser("vapi", help="Vapi account checks with free API calls (needs VAPI_API_KEY in .env)")
    vp.add_argument("action", choices=["check"])
    vp.add_argument("--create-number", action="store_true", help="create a free Vapi number if the account has none")
    ld = sub.add_parser("live-demo", help="staged live call to an allowlisted number (seeds/live/, data-live/)")
    ld.add_argument("--dial", action="store_true", help="actually place the call; without it, only print the payload")
    ld.add_argument("--create-number", action="store_true")
    ld.add_argument("--reset", action="store_true", help="wipe data-live/ first")
    ld.add_argument(
        "--any-time", action="store_true", help="waive the business-hours window for allowlisted team numbers"
    )
    return p


def _find_contact(store: Store, needle: str):
    needle_l = needle.lower()
    for c in store.contacts.values():
        if c.id == needle or c.external_id == needle:
            return c
    for c in store.contacts.values():
        if needle_l and needle_l in c.full_name.lower():
            return c
    return None


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    env = load_dotenv(ROOT / ".env", dict(os.environ))
    if args.command == "live-demo":
        env.update({"BV_MODE": "live", "BV_ALLOW_LIVE_CALLS": "1"})
        if args.any_time:
            env["BV_LIVE_IGNORE_WINDOW"] = "1"
        env.setdefault("BV_DATA_DIR", "data-live")
        env.setdefault("BV_EXPORT_DIR", "docs/data-live")
        for attr, default in (
            ("seed", "seeds/live/demo.csv"),
            ("clearances", "seeds/live/clearances.csv"),
            ("suppressions", "seeds/live/suppressions.csv"),
        ):
            if getattr(args, attr) == str(
                ROOT
                / "seeds"
                / {"seed": "test_doctors.csv", "clearances": "clearances.csv", "suppressions": "suppressions.csv"}[attr]
            ):
                setattr(args, attr, str(ROOT / default))
    if args.now:
        env["BV_NOW"] = args.now
    if args.data_dir:
        env["BV_DATA_DIR"] = args.data_dir
    if args.export_dir:
        env["BV_EXPORT_DIR"] = args.export_dir
    try:
        cfg = load_config(args.config, env=env)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    clock = FixedClock(cfg.now_override) if cfg.now_override else Clock()
    store = Store(cfg.data_dir, clock)

    if args.command in ("vapi", "live-demo"):
        return _vapi_commands(args, cfg, clock, store)
    if args.command == "reset":
        store.reset()
        for path in Path(cfg.export_dir).glob("*.json"):
            path.unlink()
        print(f"reset {cfg.data_dir} and {cfg.export_dir}")
        return 0

    store.load()
    brain = MockBrain(store, clock, down=cfg.brain_down)
    voice = MockVoiceProvider(cfg.webhook_secret, clock)

    if args.command == "run" or args.command in ALL_STAGES:
        if args.command == "run" and args.reset:
            store.reset()
        stages = ALL_STAGES if args.command == "run" else (args.command,)
        trigger = getattr(args, "trigger", "local")
        run, summary = run_pipeline(
            store,
            cfg,
            clock,
            brain,
            voice,
            args.seed,
            args.clearances,
            args.suppressions,
            stages=stages,
            trigger=trigger,
        )
        _print_report(run, summary)
        return 3 if run["gate_escapes"] else 0

    if args.command == "summary":
        last = store.logs["runs"][-1] if store.logs["runs"] else {}
        print(json.dumps(build_summary(store, last), indent=1))
        return 0
    if args.command == "export":
        last = store.logs["runs"][-1] if store.logs["runs"] else {}
        paths = export(store, cfg.export_dir, build_summary(store, last), last)
        print(f"exported {len(paths)} files to {cfg.export_dir}")
        return 0
    if args.command == "suppress":
        contact = _find_contact(store, args.contact) if args.contact else None
        if args.contact and contact is None:
            print(f"no contact matches {args.contact!r}", file=sys.stderr)
            return 1
        from .verify import normalize_e164

        phone = normalize_e164(args.phone) if args.phone else None
        if contact is None and phone is None:
            print("give --contact and/or --phone", file=sys.stderr)
            return 1
        from .models import Channel, Contact, ContactStatus

        target = contact or Contact(id=f"phone:{phone}", full_name="")
        channel = Channel(id="", contact_id=target.id, type="phone", value=phone) if phone else None
        if contact is None:
            from .models import Suppression
            from .sourcing import short_hash

            sid = "sup_" + short_hash("phone", phone)
            if sid not in store.suppressions:
                store.add(
                    "suppressions",
                    Suppression(
                        id=sid,
                        identifier=phone,
                        identifier_type="phone",
                        reason=args.reason,
                        source="operator",
                        created_at=iso(clock.now()),
                    ),
                    actor="operator",
                )
        else:
            if channel is None:
                phones = store.channels_for(contact.id, "phone")
                channel = phones[0] if phones else None
            add_suppression(store, contact, channel, clock, source="operator", reason=args.reason)
            store.update(
                "contacts",
                contact,
                "suppressed_by_operator",
                {"status": ContactStatus.SUPPRESSED, "next_attempt_at": ""},
                actor="operator",
            )
        store.save()
        print("suppression recorded")
        return 0
    if args.command in ("pause", "resume"):
        store.state["paused"] = args.command == "pause"
        store.audit("state", "calling", args.command, {}, {"paused": store.state["paused"]}, actor="operator")
        store.save()
        print(f"calling {'paused' if store.state['paused'] else 'resumed'}")
        return 0
    if args.command == "redrive":
        moved = redrive_dead_letters(store, clock)
        store.save()
        print(f"redrove {moved} transcript(s)")
        return 0
    if args.command == "approve":
        from .booking import approve_interview

        interview = store.interviews.get(args.interview)
        if interview is None:
            contact = _find_contact(store, args.interview)
            if contact is not None:
                interview = next((i for i in store.interviews.values() if i.contact_id == contact.id), None)
        if interview is None:
            print(f"no interview matches {args.interview!r}", file=sys.stderr)
            return 1
        approve_interview(store, interview, args.by, clock)
        store.save()
        print(f"approved {interview.id} ({interview.status})")
        return 0
    if args.command == "history":
        contact = _find_contact(store, args.contact)
        if contact is None:
            print(f"no contact matches {args.contact!r}", file=sys.stderr)
            return 1
        payload = {
            "contact": contact.to_dict(),
            "channels": [ch.to_dict() for ch in store.channels_for(contact.id)],
            "call_attempts": [a.to_dict() for a in store.attempts_for(contact.id)],
            "interviews": [i.to_dict() for i in store.interviews.values() if i.contact_id == contact.id],
            "gate_decisions": [d for d in store.logs["gate_decisions"] if d["contact_id"] == contact.id],
            "audit": [
                e for e in store.audit_log if e.get("contact_id") == contact.id or e.get("entity_id") == contact.id
            ],
        }
        print(json.dumps(payload, indent=1))
        return 0
    return 1


def _vapi_commands(args, cfg, clock, store: Store) -> int:
    from .compliance import pre_call_gate
    from .pipeline import run_pipeline
    from .scripts import build_script
    from .vapi import VapiClient, VapiError, VapiVoiceProvider, call_body
    from .voice import CallRequest

    def connect() -> VapiVoiceProvider | None:
        try:
            client = VapiClient(cfg.vapi_api_key)
        except VapiError as exc:
            print(f"vapi: {exc}. Put VAPI_API_KEY=... in {ROOT / '.env'} (gitignored).", file=sys.stderr)
            return None
        provider = VapiVoiceProvider(client, cfg, clock)
        try:
            assistants = client.list_assistants()
            print(f"vapi: key ok, {len(assistants)} assistant(s) in the account")
            for n in client.list_phone_numbers():
                print(
                    f"vapi: number {n.get('number') or '?'}  id={n.get('id')}  provider={n.get('provider')}  "
                    f"status={n.get('status')}"
                )
            provider.ensure_phone_number(create_if_missing=args.create_number)
            if not cfg.callback_number:
                cfg.callback_number = provider.phone_number
            provider.ensure_assistant(cfg.callback_number)
        except VapiError as exc:
            print(f"vapi: {exc}", file=sys.stderr)
            return None
        print(f"vapi: dialing from {provider.phone_number or provider.phone_number_id}; callback {cfg.callback_number}")
        return provider

    if args.command == "vapi":
        provider = connect()
        if provider is None:
            return 2
        print("vapi: ready (no call placed)")
        return 0

    # live-demo: gate + script preview needs no credentials; dialing does
    if args.reset:
        store.reset()
    print(f"live: allowlist {cfg.live_allowlist}; seeds {args.seed}; data {cfg.data_dir}")
    brain = MockBrain(store, clock)
    run_pipeline(
        store,
        cfg,
        clock,
        brain,
        MockVoiceProvider(cfg.webhook_secret, clock),
        args.seed,
        args.clearances,
        args.suppressions,
        stages=("ingest", "verify"),
        trigger="live-demo-preview",
    )
    previews = 0
    for contact in store.contacts.values():
        phones = list(store.channels_for(contact.id, "phone"))
        channel = next((ch for ch in phones if ch.verified), phones[0] if phones else None)
        gate = pre_call_gate(store, contact, channel, cfg, clock)
        verdict = ("ALLOWED" if gate.allowed else "blocked: " + ", ".join(gate.reasons)) + (
            " (waived: " + ", ".join(gate.waivers) + ")" if gate.waivers else ""
        )
        print(f"live: {contact.full_name or contact.id} ({channel.value if channel else 'no phone'}) -> {verdict}")
        if not gate.allowed or channel is None:
            continue
        script, errors = build_script(
            contact, channel, brain.resolve_script({"contact": {"segment": contact.segment}}), "preview", clock, cfg
        )
        if errors:
            print(f"live: script blocked: {errors}")
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
        print("live: call body preview:\n" + json.dumps(shown, indent=1))
        previews += 1
    if not args.dial:
        print(f"live: preview only ({previews} call(s) would be placed). Re-run with --dial to place the call.")
        return 0
    if not previews:
        print("live: nothing to dial", file=sys.stderr)
        return 1
    provider = connect()
    if provider is None:
        return 2
    run, summary = run_pipeline(
        store,
        cfg,
        clock,
        brain,
        provider,
        args.seed,
        args.clearances,
        args.suppressions,
        stages=("call", "deliver"),
        trigger="live-demo",
    )
    _print_report(run, summary)
    for i in store.interviews.values():
        print(
            f"live: handoff -> email={i.contact_email or '-'} method={i.contact_method or '-'} "
            f"number={i.contact_number or '-'} availability={i.availability or '-'} notes={i.notes or '-'}"
        )
    return 3 if run["gate_escapes"] else 0


def _print_report(run: dict, summary: dict) -> None:
    stages = run["stages"]
    print(
        f"run {run['run_id']}  mode={run['mode']}  now={run['now_used']}"
        f"{' (simulated)' if run['simulated_time'] else ''}  kill_switch={run['kill_switch']}"
    )
    for name, counts in stages.items():
        if isinstance(counts, dict):
            print(f"  {name:<12} " + "  ".join(f"{k}={v}" for k, v in counts.items()))
        else:
            print(f"  {name:<12} {counts}")
    c = summary["compliance"]
    t = summary["transcripts"]
    print(f"  contacts     total={summary['contacts']['total']}  by_status={summary['contacts']['by_status']}")
    print(f"  calls        attempts={summary['calls']['attempts']}  by_outcome={summary['calls']['by_outcome']}")
    print(
        f"  transcripts  delivered={t['delivered']}/{t['total']}  held={t['held_pii_review']}  "
        f"failed={t['failed']}  dlq={summary['dlq']}"
    )
    print(
        f"  compliance   gate_escapes={c['gate_escapes']}  blocked={c['last_run']['blocked']}  "
        f"deferred={c['last_run']['deferred']}  suppressions={c['suppressions']}"
    )
    print(f"  interviews   {summary['interviews']}   errors={summary['errors']}   alerts={summary['alerts']}")


if __name__ == "__main__":
    raise SystemExit(main())
