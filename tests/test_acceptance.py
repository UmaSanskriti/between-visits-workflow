"""Section 10.2 acceptance criteria, AC-1 to AC-10, executed against the dry-run pipeline."""

from __future__ import annotations

from pathlib import Path

from conftest import SATURDAY_10AM_ET, TUESDAY_10AM_ET, Env

from infra.compliance import AI_DISCLOSURE, RECORDING_DISCLOSURE
from infra.models import ContactStatus, DeliveryStatus, Outcome


def test_ac1_suppressed_contact_is_never_enqueued(ran: Env):
    assert ran.attempts("t_05") == []
    assert ran.contact("t_05").status == ContactStatus.SUPPRESSED
    reasons = [r for d in ran.decisions("t_05") for r in d["reasons"]]
    assert any(r.startswith("suppressed:") for r in reasons)
    assert any(e["action"] == "suppressed_at_ingest" for e in ran.store.audit_log)


def test_ac2_not_cleared_channel_is_blocked(ran: Env):
    assert ran.attempts("t_16") == []
    reasons = [r for d in ran.decisions("t_16") for r in d["reasons"]]
    assert "call_clearance_not_cleared" in reasons
    assert ran.decisions("t_16")[-1]["deferred"] is False


def test_ac3_outside_calling_window_is_deferred_not_placed(ran: Env):
    assert ran.attempts("t_06") == []
    decision = ran.decisions("t_06")[-1]
    assert decision["deferred"] is True
    assert any(r.startswith("outside_calling_window") for r in decision["reasons"])
    assert ran.contact("t_06").status == ContactStatus.READY


def test_ac3_weekend_run_places_zero_calls(tmp_path):
    env = Env(tmp_path, now=SATURDAY_10AM_ET)
    run, summary = env.run()
    assert run["stages"]["calls"]["placed"] == 0
    assert summary["calls"]["attempts"] == 0
    assert run["stages"]["calls"]["deferred"] > 0


def test_ac4_do_not_call_request_suppresses_immediately(ran: Env):
    attempt = ran.attempts("t_09")[0]
    assert attempt.outcome == Outcome.DO_NOT_CONTACT
    assert "opt_out_detected" in attempt.outcome_reason
    contact = ran.contact("t_09")
    assert contact.status == ContactStatus.SUPPRESSED
    identifiers = {(s.identifier_type, s.identifier) for s in ran.store.suppressions.values()}
    phone = ran.store.channels[attempt.channel_id].value
    assert ("phone", phone) in identifiers and ("contact", contact.id) in identifiers
    ran.clock.advance(days=2)
    ran.run()
    assert len(ran.attempts("t_09")) == 1
    assert any(r.startswith("suppressed:") for r in ran.decisions("t_09")[-1]["reasons"])


def test_ac5_duplicate_webhook_is_applied_once(ran: Env):
    attempt = ran.attempts("t_01")[0]
    assert attempt.duplicate_events_ignored == 1
    assert len(attempt.processed_event_ids) == 1
    transcripts = [t for t in ran.store.transcripts.values() if t.call_attempt_id == attempt.id]
    assert len(transcripts) == 1
    completed = [e for e in ran.store.audit_log if e["entity_id"] == attempt.id and e["action"] == "completed"]
    assert len(completed) == 1


def test_ac6_brain_down_means_no_call_and_a_retry(tmp_path):
    env = Env(tmp_path, brain_down=True)
    run, _ = env.run()
    assert env.voice.calls_placed == 0
    assert run["stages"]["calls"]["brain_unavailable"] > 0
    assert any(e["error_type"] == "brain_unavailable" for e in env.store.logs["errors"])
    contact = env.contact("t_01")
    assert contact.next_attempt_at == "2026-10-20T14:15:00Z"
    assert contact.status == ContactStatus.READY
    env.brain.down = False
    env.clock.advance(minutes=20)
    env.run()
    assert len(env.attempts("t_01")) == 1


def test_ac7_delivery_failure_lands_in_dlq_with_alert(ran: Env):
    attempt = ran.attempts("t_15")[0]
    transcript = ran.store.transcripts[attempt.transcript_id]
    assert transcript.delivery_status == DeliveryStatus.FAILED
    assert transcript.delivery_attempts == ran.cfg.delivery_max_attempts
    assert any(d["transcript_id"] == transcript.id for d in ran.store.logs["dlq"])
    assert any(a["type"] == "transcript_delivery_failed" for a in ran.store.logs["alerts"])
    assert attempt.id not in ran.store.brain_inbox


def test_ac8_booked_creates_one_interview_and_one_handoff(ran: Env):
    attempt = ran.attempts("t_01")[0]
    assert attempt.outcome == Outcome.BOOKED
    interviews = [i for i in ran.store.interviews.values() if i.call_attempt_id == attempt.id]
    assert len(interviews) == 1
    interview = interviews[0]
    assert interview.calendar_event_ref.startswith("file:")
    assert Path(interview.calendar_event_ref[len("file:") :]).exists()
    assert ran.contact("t_01").status == ContactStatus.BOOKED
    handoffs = [e for e in ran.store.audit_log if e["entity_id"] == interview.id and e["action"] == "draft_invite"]
    assert len(handoffs) == 1
    ran.clock.advance(days=1)
    ran.run()
    assert len([i for i in ran.store.interviews.values() if i.call_attempt_id == attempt.id]) == 1


def test_ac9_kill_switch_places_zero_calls(tmp_path):
    env = Env(tmp_path, kill=True)
    run, summary = env.run()
    assert env.voice.calls_placed == 0
    assert summary["calls"]["attempts"] == 0
    decisions = [d for d in env.store.logs["gate_decisions"] if d["run_id"] == run["run_id"]]
    assert decisions and all("kill_switch_on" in d["reasons"] for d in decisions)
    assert any(a["type"] == "kill_switch_on" for a in env.store.logs["alerts"])


def test_ac10_first_utterance_carries_disclosures(ran: Env):
    assert ran.store.scripts
    for script in ran.store.scripts.values():
        assert script.first_message.startswith(AI_DISCLOSURE)
        assert RECORDING_DISCLOSURE in script.first_message
    for transcript in ran.store.transcripts.values():
        if transcript.utterances:
            first = transcript.utterances[0]
            attempt = ran.store.call_attempts[transcript.call_attempt_id]
            assert first["speaker"] == "agent"
            if attempt.outcome == Outcome.VOICEMAIL:  # the playbook's own voicemail script identifies the AI caller
                assert "AI assistant calling for Anirudh Bagla" in first["text"]
            else:
                assert AI_DISCLOSURE in first["text"] and RECORDING_DISCLOSURE in first["text"]


def test_zero_gate_escapes_and_every_call_traceable(ran: Env):
    run = ran.store.logs["runs"][-1]
    assert run["gate_escapes"] == 0
    for attempt in ran.store.call_attempts.values():
        assert attempt.gate_result == "allowed"
        assert attempt.script_version and attempt.run_id and attempt.provider_call_id
        assert any(e["entity_id"] == attempt.id for e in ran.store.audit_log)
        allowed = [
            d for d in ran.store.logs["gate_decisions"] if d["contact_id"] == attempt.contact_id and d["allowed"]
        ]
        assert allowed


def test_pipeline_is_idempotent_across_restarts(ran: Env):
    before = (len(ran.store.contacts), len(ran.store.channels), len(ran.store.call_attempts))
    ran.reload()
    ran.run()
    after = (len(ran.store.contacts), len(ran.store.channels), len(ran.store.call_attempts))
    assert before == after
    assert ran.clock.now() == TUESDAY_10AM_ET
