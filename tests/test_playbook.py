"""Alignment with clinic-outreach-brain.md (playbook v1): locked opening, slots, callback number, outcomes."""

from __future__ import annotations

import re

from conftest import Env

from infra.booking import approve_interview
from infra.compliance import AI_DISCLOSURE, RECORDING_DISCLOSURE
from infra.models import ContactStatus, Outcome

LOCKED_OPEN = (
    "Hi, this is an AI assistant calling for Anirudh Bagla, a student at Harvard Business School. "
    "This call is recorded for our notes. Is that okay?"
)


def test_first_message_is_the_locked_open_plus_route(ran: Env):
    script = ran.script("t_01")
    assert script.first_message == (
        f"{LOCKED_OPEN} Could I speak with the office manager, or whoever keeps Dr. Patel's schedule?"
    )
    assert f"{AI_DISCLOSURE} {RECORDING_DISCLOSURE}" == LOCKED_OPEN


def test_pitch_ask_and_close_follow_the_playbook(ran: Env):
    script = ran.script("t_01")
    prompt = script.system_prompt
    assert "we believe" in prompt.lower() and "up to six times" in prompt
    assert "30 minutes with Dr. Patel" in prompt
    assert "the callback number is +16175550199" in prompt
    assert "Never pretend to be human" in prompt
    assert re.match(r"^(Mon|Tue|Wed|Thu|Fri) \d{1,2}/\d{1,2} \d{1,2}:\d{2}(am|pm)$", script.variables_used["slot_1"])
    assert script.variables_used["slot_1"] in prompt and script.variables_used["slot_2"] in prompt


def test_voicemail_leaves_the_approved_message(ran: Env):
    attempt = ran.attempts("t_04")[0]
    assert attempt.outcome == Outcome.VOICEMAIL
    transcript = ran.store.transcripts[attempt.transcript_id]
    assert len(transcript.utterances) == 1
    assert "Please call back at +16175550199" in transcript.utterances[0]["text"]


def test_missing_callback_number_blocks_every_call(tmp_path):
    env = Env(tmp_path)
    env.cfg.callback_number = ""
    run, summary = env.run()
    assert env.voice.calls_placed == 0
    assert run["stages"]["calls"]["script_errors"] > 0
    assert any(e["detail"] == "missing_required:callback_number" for e in env.store.logs["errors"])


def test_not_a_fit_and_declined_recording_close_the_contact(ran: Env):
    fit = ran.attempts("t_19")[0]
    assert fit.outcome == Outcome.NOT_A_FIT and fit.failure_category == "Not a fit"
    rec = ran.attempts("t_20")[0]
    assert rec.outcome == Outcome.DECLINED_RECORDING and rec.failure_category == "Hung up at AI disclosure"
    for ext in ("t_19", "t_20"):
        contact = ran.contact(ext)
        assert contact.status == ContactStatus.DECLINED and contact.next_attempt_at == ""


def test_failure_categories_reach_the_summary_and_brain(ran: Env):
    from infra.summary import build_summary

    summary = build_summary(ran.store, ran.store.logs["runs"][-1])
    cats = summary["calls"]["by_failure_category"]
    assert cats["Screened out by front desk"] == 1 and cats["Call back later"] == 1
    assert any(row["failure_category"] == "Pitch did not land" for row in ran.store.brain_inbox.values())


def test_max_two_attempts_per_playbook(ran: Env):
    assert ran.cfg.retry_max_attempts == 2
    for _ in range(8):
        ran.clock.advance(days=1)
        ran.run()
    assert len(ran.attempts("t_03")) == 2
    assert ran.contact("t_03").status == ContactStatus.UNREACHABLE


def test_interview_is_a_draft_until_approved(ran: Env):
    interview = next(i for i in ran.store.interviews.values() if i.contact_id == ran.contact("t_01").id)
    assert interview.status == "draft_pending_approval"
    assert interview.scheduled_for == ran.script("t_01").variables_used["slot_1_iso"]
    approve_interview(ran.store, interview, "Anirudh Bagla", ran.clock)
    assert interview.status == "approved" and interview.approved_by == "Anirudh Bagla"
    assert any(e["action"] == "approve" and e["actor"] == "Anirudh Bagla" for e in ran.store.audit_log)


def test_callback_requests_are_honored_beyond_the_attempt_cap(ran: Env):
    """Playbook 5.7: at most two attempts unless they asked for a call back (bounded by callback_extra_attempts)."""
    for _ in range(6):  # Wed..Mon: call backs land on the next business afternoon
        ran.clock.advance(days=1)
        ran.run()
    contact = ran.contact("t_07")
    assert len(ran.attempts("t_07")) == ran.cfg.retry_max_attempts + 1
    assert contact.status == ContactStatus.IN_OUTREACH and contact.next_attempt_at
    for _ in range(2):
        ran.clock.advance(days=1)
        ran.run()
    ceiling = ran.cfg.retry_max_attempts + ran.cfg.retry_callback_extra_attempts
    assert len(ran.attempts("t_07")) == ceiling
    assert ran.contact("t_07").status == ContactStatus.UNREACHABLE
