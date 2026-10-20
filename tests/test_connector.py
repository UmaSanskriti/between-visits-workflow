"""connector/test-plan.md: sample doctors t_01..t_08 and the outcome edge cases."""

from __future__ import annotations

import re

from conftest import Env

from infra.models import ContactStatus, DeliveryStatus, Outcome

PLACEHOLDER = re.compile(r"\{[a-z_]+\}")


def test_t01_complete_record_solo_primary_care(ran: Env):
    script = ran.script("t_01")
    assert script.segment == "solo_primary_care"
    assert script.variables_used["doctor_name"] == "Dr. Patel"
    assert "Patel Family Medicine" in script.system_prompt
    assert set(script.fallbacks_applied) <= {"ehr"}
    assert "weight-management program" in script.system_prompt  # the hook, playbook section 4


def test_t02_missing_name_uses_fallback_greeting(ran: Env):
    script = ran.script("t_02")
    assert "whoever keeps the doctor's schedule" in script.first_message
    assert "doctor_name" in script.fallbacks_applied


def test_t03_missing_specialty_gets_generic_angle(ran: Env):
    script = ran.script("t_03")
    assert script.segment == "default"
    assert "specialty" in script.fallbacks_applied
    assert "{specialty}" not in script.first_message + script.system_prompt


def test_t04_pronunciation_hint_is_passed(ran: Env):
    script = ran.script("t_04")
    assert "shiv-AWN nee BREE-an" in script.system_prompt


def test_t07_small_group_gets_a_different_angle(ran: Env):
    solo, group = ran.script("t_01"), ran.script("t_07")
    assert group.segment == "small_group_primary_care"
    assert group.pitch_angle != solo.pitch_angle


def test_t07_callback_is_scheduled_for_requested_time(ran: Env):
    attempt = ran.attempts("t_07")[0]
    assert attempt.outcome == Outcome.CALLBACK_REQUESTED
    contact = ran.contact("t_07")
    assert contact.next_attempt_at == attempt.callback_requested_at != ""
    assert contact.status == ContactStatus.IN_OUTREACH


def test_t08_missing_phone_goes_to_error_report_not_queue(ran: Env):
    contact = ran.contact("t_08")
    assert ran.attempts("t_08") == []
    assert contact.status != ContactStatus.READY
    errors = [e for e in ran.store.logs["errors"] if e["contact_id"] == contact.id]
    assert any(e["error_type"] == "missing_field" and e["field"] == "phone" for e in errors)


def test_no_unfilled_placeholder_ever_reaches_voice(ran: Env):
    for script in ran.store.scripts.values():
        assert not PLACEHOLDER.search(script.first_message)
        assert not PLACEHOLDER.search(script.system_prompt)


def test_t11_wrong_number_fails_the_channel(ran: Env):
    attempt = ran.attempts("t_11")[0]
    assert attempt.outcome == Outcome.WRONG_NUMBER
    assert ran.store.channels[attempt.channel_id].verified is False
    assert ran.contact("t_11").status == ContactStatus.UNREACHABLE


def test_t12_duplicate_is_flagged_and_not_called(ran: Env):
    dup, original = ran.contact("t_12"), ran.contact("t_01")
    assert dup.duplicate_of == original.id
    assert ran.attempts("t_12") == []
    assert any(e["error_type"] == "duplicate_candidate" and e["contact_id"] == dup.id for e in ran.store.logs["errors"])


def test_t13_malformed_transcript_is_reported_not_forwarded(ran: Env):
    attempt = ran.attempts("t_13")[0]
    assert attempt.outcome == Outcome.DECLINED
    assert attempt.transcript_id == ""
    assert any(
        e["error_type"] == "malformed_transcript" and e["call_id"] == attempt.id for e in ran.store.logs["errors"]
    )


def test_t14_patient_data_holds_the_transcript(ran: Env):
    attempt = ran.attempts("t_14")[0]
    transcript = ran.store.transcripts[attempt.transcript_id]
    assert transcript.pii_flagged and transcript.delivery_status == DeliveryStatus.HELD
    assert attempt.id not in ran.store.brain_inbox
    assert any(a["type"] == "transcript_held_pii_review" for a in ran.store.logs["alerts"])


def test_t17_technical_failure_retries_with_backoff(ran: Env):
    attempt = ran.attempts("t_17")[0]
    assert attempt.outcome == Outcome.FAILED_TECHNICAL
    contact = ran.contact("t_17")
    assert contact.status == ContactStatus.IN_OUTREACH
    assert contact.next_attempt_at == "2026-10-20T15:00:00Z"


def test_t03_no_answer_retries_next_business_day_at_a_different_hour(ran: Env):
    contact = ran.contact("t_03")
    assert contact.last_outcome == Outcome.NO_ANSWER
    assert contact.next_attempt_at == "2026-10-21T18:00:00Z"  # Wed 14:00 ET


def test_retries_stop_after_max_attempts(ran: Env):
    for _ in range(8):  # a full week of daily runs, including a weekend
        ran.clock.advance(days=1)
        ran.run()
    contact = ran.contact("t_03")
    assert len(ran.attempts("t_03")) == ran.cfg.retry_max_attempts
    assert contact.status == ContactStatus.UNREACHABLE


def test_brain_sees_declines_and_no_answers_not_only_bookings(ran: Env):
    outcomes = {row["outcome"] for row in ran.store.brain_inbox.values()}
    assert {Outcome.BOOKED, Outcome.DECLINED, Outcome.NO_ANSWER, Outcome.VOICEMAIL} <= outcomes


def test_delivered_packages_are_redacted(ran: Env):
    from infra.delivery import build_package

    for transcript in ran.store.transcripts.values():
        package = build_package(ran.store, transcript)
        text = " ".join(u["text"] for u in package["utterances"])
        assert "@" not in text and not re.search(r"\+1\d{10}", text)
        assert package["pii_handling"] == "redacted"


def test_outcome_summary_matches_raw_counts(ran: Env):
    from infra.summary import build_summary

    summary = build_summary(ran.store, ran.store.logs["runs"][-1])
    by_version = summary["calls"]["by_script_version"]
    assert sum(v["attempts"] for v in by_version.values()) == len(ran.store.call_attempts)
    secured = {Outcome.BOOKED, Outcome.AGREED_TO_TALK}
    assert summary["calls"]["booked"] == sum(1 for a in ran.store.call_attempts.values() if a.outcome in secured)
    assert summary["compliance"]["gate_escapes"] == 0
