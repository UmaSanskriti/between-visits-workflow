"""Call orchestration (CALL-*), webhook handling, outcome side effects (LOG-*, CMP-006)."""

from __future__ import annotations

from datetime import timedelta

from .booking import FileBookingHandoff, ensure_interview
from .brain import BrainClient, BrainUnavailable, with_retries
from .clock import Clock, add_business_days, iso, to_local
from .compliance import contains_opt_out, detect_patient_data, pre_call_gate
from .config import Config
from .models import (
    FAILURE_CATEGORY,
    CallAttempt,
    CallStatus,
    Channel,
    Contact,
    ContactStatus,
    DeliveryStatus,
    Outcome,
    Suppression,
    Transcript,
)
from .scripts import build_script
from .sourcing import short_hash
from .store import Store
from .voice import OUTBOUND_CALLER_ID, CallRequest, VoiceProvider

CLOSED = {ContactStatus.BOOKED, ContactStatus.DECLINED, ContactStatus.UNREACHABLE}


def _pick_channel(store: Store, contact: Contact) -> Channel | None:
    phones = store.channels_for(contact.id, "phone")
    verified = [ch for ch in phones if ch.verified]
    return (verified or phones or [None])[0]


def schedule_calls(
    store: Store, cfg: Config, clock: Clock, brain: BrainClient, voice: VoiceProvider, run_id: str
) -> dict:
    """CALL-001: evaluate the gate for every open contact, place calls for those allowed."""
    counts = {
        "considered": 0,
        "allowed": 0,
        "blocked": 0,
        "deferred": 0,
        "placed": 0,
        "brain_unavailable": 0,
        "script_errors": 0,
        "capped": 0,
    }
    now = clock.now()
    today = iso(now)[:10]
    placed_today = sum(1 for a in store.call_attempts.values() if a.started_at[:10] == today)
    if cfg.kill_switch:
        store.alert("kill_switch_on", "kill switch is on; no calls will be placed", run_id=run_id)

    for contact in list(store.contacts.values()):
        if contact.status in CLOSED:
            continue
        counts["considered"] += 1
        channel = _pick_channel(store, contact)
        gate = pre_call_gate(store, contact, channel, cfg, clock)
        decision = {
            "run_id": run_id,
            "timestamp": iso(now),
            "contact_id": contact.id,
            "channel_id": channel.id if channel else "",
            "allowed": gate.allowed,
            "deferred": gate.deferred,
            "reasons": gate.reasons,
            "waivers": gate.waivers,
        }
        if not gate.allowed:
            store.logs["gate_decisions"].append(decision)
            store.audit(
                "contact",
                contact.id,
                "gate_deferred" if gate.deferred else "gate_blocked",
                {},
                {"reasons": gate.reasons},
                run_id=run_id,
            )
            counts["deferred" if gate.deferred else "blocked"] += 1
            continue
        per_run = cfg.live_max_calls_per_run if cfg.mode == "live" else cfg.max_calls_per_run
        if counts["placed"] >= per_run or placed_today + counts["placed"] >= cfg.max_calls_per_day:
            decision.update({"allowed": False, "deferred": True, "reasons": ["call_cap_reached"]})
            store.logs["gate_decisions"].append(decision)
            counts["capped"] += 1
            continue
        counts["allowed"] += 1
        store.logs["gate_decisions"].append(decision)
        assert channel is not None

        attempt_number = contact.attempts + 1
        attempt_id = "ca_" + short_hash(contact.id, str(attempt_number))
        request = {
            "campaign_id": cfg.campaign_id,
            "contact": {
                "contact_id": contact.id,
                "specialty": contact.specialty,
                "practice_name": contact.practice_name,
                "segment": contact.segment,
            },
            "attempt_number": attempt_number,
        }
        try:  # CALL-002 + BRN-002/003: no script, no call.
            brain_script = with_retries(
                lambda req=request: brain.resolve_script(req),
                attempts=3,
                backoff_seconds=0,
                retry_on=(BrainUnavailable,),
            )
        except BrainUnavailable as exc:
            store.error("brain_unavailable", str(exc), call_id=attempt_id, contact_id=contact.id, field="script")
            store.update(
                "contacts",
                contact,
                "brain_unavailable_backoff",
                {"next_attempt_at": iso(now + timedelta(minutes=cfg.brain_retry_minutes))},
                run_id=run_id,
            )
            counts["brain_unavailable"] += 1
            continue

        script, errors = build_script(contact, channel, brain_script, attempt_id, clock, cfg)
        if errors or script is None:
            for err in errors:
                kind = "missing_field" if err.startswith("missing_required") else "unfilled_placeholder"
                store.error(kind, err, call_id=attempt_id, contact_id=contact.id, field=err.split(":")[-1])
            counts["script_errors"] += 1
            continue
        store.scripts[attempt_id] = script
        store.audit(
            "script",
            attempt_id,
            "generated",
            {},
            {
                "script_version": script.script_version,
                "segment": script.segment,
                "fallbacks_applied": script.fallbacks_applied,
            },
            run_id=run_id,
            contact_id=contact.id,
        )

        attempt = CallAttempt(
            id=attempt_id,
            contact_id=contact.id,
            channel_id=channel.id,
            attempt_number=attempt_number,
            run_id=run_id,
            gate_result="allowed",
            scheduled_at=iso(now),
            status=CallStatus.QUEUED,
            provider=voice.name,
            script_id=script.script_id,
            script_version=script.script_version,
            segment=script.segment,
            pitch_angle=script.pitch_angle,
            fallbacks_applied=script.fallbacks_applied,
            created_at=iso(now),
            updated_at=iso(now),
        )
        store.add("call_attempts", attempt, run_id=run_id, contact_id=contact.id)
        provider_call_id = voice.place_call(
            CallRequest(
                call_attempt_id=attempt_id,
                contact_id=contact.id,
                to_number=channel.value,
                from_number=OUTBOUND_CALLER_ID,
                first_message=script.first_message,
                system_prompt=script.system_prompt,
                script_version=script.script_version,
                time_zone=contact.time_zone,
                metadata={
                    "scenario": contact.test_scenario,
                    "dry_run": True,
                    "voicemail_message": script.voicemail_message,  # empty unless features.voicemail_message
                    "pitch": script.variables_used.get("pitch", ""),
                    "close": script.variables_used.get("close", ""),
                    "slot_1_iso": script.variables_used.get("slot_1_iso", ""),
                },
            )
        )
        store.update(
            "call_attempts",
            attempt,
            "placed",
            {"provider_call_id": provider_call_id, "status": CallStatus.IN_PROGRESS, "started_at": iso(now)},
            run_id=run_id,
        )
        store.update(
            "contacts",
            contact,
            "call_placed",
            {"status": ContactStatus.IN_OUTREACH, "attempts": attempt_number, "next_attempt_at": ""},
            run_id=run_id,
            call_attempt_id=attempt_id,
        )
        counts["placed"] += 1
    return counts


def process_events(store: Store, cfg: Config, clock: Clock, voice: VoiceProvider, run_id: str) -> dict:
    """CALL-005: webhook-driven state, signature-checked, idempotent."""
    counts = {"events": 0, "applied": 0, "duplicates_ignored": 0, "bad_signature": 0, "unknown_call": 0}
    handoff = FileBookingHandoff(store.dir / "handoff")
    by_provider_id = {a.provider_call_id: a for a in store.call_attempts.values() if a.provider_call_id}
    for event in voice.drain_events():
        counts["events"] += 1
        if not voice.verify_signature(event):
            counts["bad_signature"] += 1
            store.alert("webhook_bad_signature", "rejected webhook with invalid signature", run_id=run_id)
            continue
        payload = event["payload"]
        attempt = by_provider_id.get(payload.get("provider_call_id", ""))
        if attempt is None:
            counts["unknown_call"] += 1
            store.alert("webhook_unknown_call", f"no call attempt for {payload.get('provider_call_id')}", run_id=run_id)
            continue
        if payload["event_id"] in attempt.processed_event_ids:
            attempt.duplicate_events_ignored += 1
            counts["duplicates_ignored"] += 1
            store.audit(
                "call_attempt",
                attempt.id,
                "webhook_duplicate_ignored",
                {},
                {"event_id": payload["event_id"]},
                run_id=run_id,
            )
            continue
        contact = store.contacts[attempt.contact_id]
        channel = store.channels[attempt.channel_id]
        apply_outcome(store, cfg, clock, attempt, contact, channel, payload, handoff, run_id)
        attempt.processed_event_ids.append(payload["event_id"])
        counts["applied"] += 1
    return counts


def validate_transcript(raw) -> tuple[list[dict], bool]:
    if raw in (None, "", []):
        return [], False
    if not isinstance(raw, list):
        return [], True
    out = []
    for item in raw:
        if not isinstance(item, dict) or "speaker" not in item or "text" not in item:
            return [], True
        out.append(
            {"speaker": str(item["speaker"]), "start_ms": int(item.get("start_ms", 0)), "text": str(item["text"])}
        )
    return out, False


def apply_outcome(
    store: Store,
    cfg: Config,
    clock: Clock,
    attempt: CallAttempt,
    contact: Contact,
    channel: Channel,
    payload: dict,
    handoff: FileBookingHandoff,
    run_id: str,
) -> None:
    now = clock.now()
    outcome = payload["outcome"]
    reason = ""
    utterances, malformed = validate_transcript(payload.get("transcript"))
    doctor_text = " ".join(u["text"] for u in utterances if u["speaker"] != "agent")
    if contains_opt_out(doctor_text) and outcome != Outcome.DO_NOT_CONTACT:
        reason = f"opt_out_detected_in_transcript (provider classified {outcome})"
        outcome = Outcome.DO_NOT_CONTACT

    changes: dict = {
        "status": CallStatus.FAILED if outcome == Outcome.FAILED_TECHNICAL else CallStatus.COMPLETED,
        "outcome": outcome,
        "outcome_reason": reason,
        "failure_category": "" if outcome == Outcome.BOOKED else FAILURE_CATEGORY.get(outcome, ""),
        "duration_sec": int(payload.get("duration_sec", 0)),
        "started_at": payload.get("started_at") or attempt.started_at,
        "ended_at": payload.get("ended_at", ""),
        "recording_ref": payload.get("recording_ref", ""),
        "callback_requested_at": payload.get("callback_at", ""),
        "cost_usd": float(payload.get("cost_usd", 0) or 0),
        "provider_ended_reason": payload.get("ended_reason", ""),
    }
    # BRN-004: the transcript record exists before any delivery is attempted. LOG-002: raw text retained.
    if malformed:
        store.error(
            "malformed_transcript",
            f"provider transcript is not a list of utterances: {str(payload.get('transcript'))[:60]}",
            call_id=attempt.id,
            contact_id=contact.id,
            field="transcript",
        )
        changes["outcome_reason"] = "; ".join(x for x in (reason, "transcript_malformed") if x)
    elif outcome != Outcome.FAILED_TECHNICAL:
        transcript_id = "tr_" + short_hash(attempt.id)
        if transcript_id not in store.transcripts:
            pii = detect_patient_data(" ".join(u["text"] for u in utterances))
            store.add(
                "transcripts",
                Transcript(
                    id=transcript_id,
                    call_attempt_id=attempt.id,
                    contact_id=contact.id,
                    utterances=utterances,
                    pii_flagged=bool(pii),
                    pii_reason=pii,
                    delivery_status=DeliveryStatus.HELD if pii else DeliveryStatus.PENDING,
                    idempotency_key=attempt.id,
                    created_at=iso(now),
                ),
                call_attempt_id=attempt.id,
                contact_id=contact.id,
                run_id=run_id,
            )
            if pii:
                store.alert(
                    "transcript_held_pii_review",
                    f"{transcript_id} held: {pii} (CMP-009)",
                    transcript_id=transcript_id,
                    call_attempt_id=attempt.id,
                )
        changes["transcript_id"] = transcript_id
    store.update("call_attempts", attempt, "completed", changes, run_id=run_id, contact_id=contact.id)

    contact_changes: dict = {"last_outcome": outcome}
    if outcome in (Outcome.BOOKED, Outcome.AGREED_TO_TALK):
        slot = payload.get("booked_slot") or ""
        if outcome == Outcome.BOOKED and not slot:
            slot = iso(add_business_days(to_local(now, contact.time_zone), 3))
        interview, created = ensure_interview(store, contact, attempt, slot, clock, payload.get("handoff") or {})
        if created and slot:
            handoff.handoff(store, interview, contact, clock, cfg.interview_length_minutes)
        contact_changes.update(status=ContactStatus.BOOKED, next_attempt_at="")
    elif outcome == Outcome.DO_NOT_CONTACT:
        add_suppression(
            store, contact, channel, clock, source=f"call:{attempt.id}", reason="opt-out request during call (CMP-006)"
        )
        contact_changes.update(status=ContactStatus.SUPPRESSED, next_attempt_at="")
    elif outcome in (Outcome.DECLINED, Outcome.NOT_A_FIT, Outcome.DECLINED_RECORDING):
        contact_changes.update(status=ContactStatus.DECLINED, next_attempt_at="")
    elif outcome == Outcome.WRONG_NUMBER:
        store.update(
            "channels",
            channel,
            "wrong_number",
            {"verified": False, "verification_reason": "wrong_number"},
            call_attempt_id=attempt.id,
        )
        others = [ch for ch in store.channels_for(contact.id, "phone") if ch.verified and ch.id != channel.id]
        contact_changes.update(
            status=ContactStatus.IN_OUTREACH if others else ContactStatus.UNREACHABLE, next_attempt_at=""
        )
    elif outcome == Outcome.FAILED_TECHNICAL:
        failures = sum(1 for a in store.attempts_for(contact.id) if a.outcome == Outcome.FAILED_TECHNICAL)
        if failures >= 2:
            store.alert(
                "repeated_technical_failure", f"{contact.id}: {failures} technical failures", contact_id=contact.id
            )
        contact_changes.update(retry_or_exhaust(contact, cfg, clock, minutes=cfg.technical_retry_minutes))
    elif outcome == Outcome.CALLBACK_REQUESTED:
        contact_changes.update(retry_or_exhaust(contact, cfg, clock, at=payload.get("callback_at") or None))
    else:  # NO_ANSWER, VOICEMAIL, GATEKEEPER_BLOCKED
        contact_changes.update(retry_or_exhaust(contact, cfg, clock))
    store.update(
        "contacts",
        contact,
        f"outcome_{str(outcome).lower()}",
        contact_changes,
        run_id=run_id,
        call_attempt_id=attempt.id,
    )


def retry_or_exhaust(
    contact: Contact, cfg: Config, clock: Clock, minutes: int | None = None, at: str | None = None
) -> dict:
    """CALL-007: at least one business day apart, varied hours; stop after max attempts.

    Playbook 5.7: the cap does not apply when the practice asked for a call back at a specific time.
    """
    if at and contact.attempts < cfg.retry_max_attempts + cfg.retry_callback_extra_attempts:
        return {"status": ContactStatus.IN_OUTREACH, "next_attempt_at": at}
    if contact.attempts >= cfg.retry_max_attempts:
        return {"status": ContactStatus.UNREACHABLE, "next_attempt_at": ""}
    now = clock.now()
    if minutes:
        return {"status": ContactStatus.IN_OUTREACH, "next_attempt_at": iso(now + timedelta(minutes=minutes))}
    local = to_local(now, contact.time_zone)
    hour = cfg.retry_hours_local[contact.attempts % len(cfg.retry_hours_local)]
    nxt = add_business_days(local, cfg.retry_min_business_days).replace(hour=hour, minute=0, second=0, microsecond=0)
    return {"status": ContactStatus.IN_OUTREACH, "next_attempt_at": iso(nxt)}


def add_suppression(
    store: Store, contact: Contact, channel: Channel | None, clock: Clock, source: str, reason: str
) -> list[Suppression]:
    """Written in the same save as the call outcome (CMP-006)."""
    created = []
    pairs = [("contact", contact.id)] + ([("phone", channel.value)] if channel else [])
    for kind, identifier in pairs:
        sid = "sup_" + short_hash(kind, identifier)
        if sid in store.suppressions:
            continue
        row = Suppression(
            id=sid,
            identifier=identifier,
            identifier_type=kind,
            reason=reason,
            source=source,
            created_at=iso(clock.now()),
        )
        store.add("suppressions", row, contact_id=contact.id)
        created.append(row)
    return created
