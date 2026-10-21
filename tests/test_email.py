"""Email outreach channel: compose rules, reply classification, providers, gate, and the full dry run."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from conftest import ROOT, TUESDAY_10AM_ET, Env

from infra.brain import MockBrain
from infra.clock import FixedClock
from infra.compliance import pre_send_gate
from infra.config import load_config
from infra.email.classify import classify_reply
from infra.email.compose import MAX_WORDS, OPT_OUT_LINE, build_email
from infra.email.provider import (
    EmailMessage,
    ImapReplyPoller,
    MockEmailProvider,
    OutboxEmailProvider,
    SmtpEmailProvider,
)
from infra.models import Channel, Clearance, Contact, ContactStatus, DeliveryStatus, Outcome, VerificationStatus
from infra.sourcing import load_clearances
from infra.store import Store


@pytest.fixture
def email_env(tmp_path) -> Env:
    env = Env(tmp_path, channel="email")
    env.run()
    return env


def brain_email():
    return MockBrain(Store("unused", FixedClock(TUESDAY_10AM_ET)), FixedClock(TUESDAY_10AM_ET)).resolve_email({})


def contact(**kw) -> Contact:
    base = dict(
        id="c_1",
        full_name="Priya Patel",
        practice_name="Patel Family Medicine",
        specialty="family medicine",
        hook="the new GLP-1 program on your site",
        segment="solo_primary_care",
    )
    base.update(kw)
    return Contact(**base)


def channel(value="ppatel@example.test", cleared=True) -> Channel:
    return Channel(
        id="ch_1",
        contact_id="c_1",
        type="email",
        value=value,
        verified=True,
        call_clearance=Clearance.CLEARED if cleared else Clearance.NOT_CLEARED,
    )


# ---------------------------------------------------------------- compose
def test_compose_follows_playbook_8b(tmp_path):
    cfg = load_config(ROOT / "config.toml", env={"BV_DATA_DIR": str(tmp_path)})
    draft, errors = build_email(contact(), channel(), brain_email(), cfg, FixedClock(TUESDAY_10AM_ET))
    assert errors == [] and draft is not None
    assert draft.subject == "30 minutes with Dr. Patel? (HBS research, not sales)"
    assert draft.body.startswith("Hi Dr. Patel,")
    assert "I saw the new GLP-1 program on your site and wanted to reach out." in draft.body
    assert "Patel Family Medicine" in draft.body and "We're not selling anything." in draft.body
    assert draft.variables_used["slot_1"] in draft.body and draft.variables_used["slot_2"] in draft.body
    assert draft.body.rstrip().endswith(OPT_OUT_LINE) and "Anirudh Bagla" in draft.body
    assert draft.word_count < MAX_WORDS and draft.body.count("?") == 1 and "{" not in draft.body
    assert draft.fallbacks_applied == []


def test_compose_fallbacks_and_rules(tmp_path):
    cfg = load_config(ROOT / "config.toml", env={"BV_DATA_DIR": str(tmp_path)})
    draft, errors = build_email(
        contact(full_name="", hook=""), channel(), brain_email(), cfg, FixedClock(TUESDAY_10AM_ET)
    )
    assert errors == [] and draft.body.startswith("Hi there,") and "Patel Family Medicine" in draft.body
    assert draft.subject == "30 minutes with your practice? (HBS research, not sales)"
    assert "doctor_name" in draft.fallbacks_applied and "hook" in draft.fallbacks_applied
    long_brain = dict(brain_email(), body=brain_email()["body"] + " word" * 120)
    draft, errors = build_email(contact(), channel(), long_brain, cfg, FixedClock(TUESDAY_10AM_ET))
    assert draft is None and any(e.startswith("email_too_long") for e in errors)
    no_optout = dict(brain_email(), body=brain_email()["body"].replace("{opt_out_line}", ""))
    _, errors = build_email(contact(), channel(), no_optout, cfg, FixedClock(TUESDAY_10AM_ET))
    assert "missing_required:opt_out_line" in errors
    two_asks = dict(brain_email(), body=brain_email()["body"] + "\nCan we also meet your office manager?")
    _, errors = build_email(contact(), channel(), two_asks, cfg, FixedClock(TUESDAY_10AM_ET))
    assert "more_than_one_ask" in errors
    cfg.email_from_name = ""
    _, errors = build_email(contact(), channel(), brain_email(), cfg, FixedClock(TUESDAY_10AM_ET))
    assert "missing_required:sender_name" in errors


# ---------------------------------------------------------------- classify
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Please stop emailing us.", Outcome.DO_NOT_CONTACT),
        ("Unsubscribe me from this list, thanks.", Outcome.DO_NOT_CONTACT),
        ("Thanks, but we're not able to take this on right now.", Outcome.DECLINED),
        ("Not interested.", Outcome.DECLINED),
        ("Yes, happy to talk. Tuesday or Thursday afternoons work.", Outcome.AGREED_TO_TALK),
        ("Sure, call me at 617-555-0188 on Friday morning.", Outcome.AGREED_TO_TALK),
        ("You can reach me at dr.chen@example.test.", Outcome.AGREED_TO_TALK),
        ("Can you send more details about what you're building?", Outcome.REPLIED_OTHER),
        ("I'm not available this month, but happy to talk in December.", Outcome.AGREED_TO_TALK),
        ("> quoted original text\nOk.", Outcome.REPLIED_OTHER),
    ],
)
def test_classify_reply(text, expected):
    outcome, _ = classify_reply(text)
    assert outcome == expected


def test_classify_handoff_details():
    outcome, handoff = classify_reply(
        "Yes, happy to talk. Tuesday or Thursday afternoons work best. Text me on WhatsApp at 617-555-0188; "
        "my direct email is dr.patel@example.test."
    )
    assert outcome == Outcome.AGREED_TO_TALK
    assert handoff["contact_method"] == "whatsapp" and handoff["contact_number"] == "617-555-0188"
    assert handoff["contact_email"] == "dr.patel@example.test" and "Tuesday or Thursday" in handoff["availability"]
    assert handoff["person_spoken_to"] == "doctor"


# ---------------------------------------------------------------- providers
def test_mock_provider_scenarios_and_duplicates():
    mock = MockEmailProvider(FixedClock(TUESDAY_10AM_ET))
    msg = EmailMessage(
        to="a@example.test",
        from_name="A",
        from_address="b@example.test",
        reply_to="",
        subject="s",
        body_text="b",
        attempt_id="em_1",
        contact_id="c_1",
        metadata={"scenario": "booked|email_bounce"},
    )
    mid = mock.send(msg)
    events = mock.drain_events()
    assert mid.startswith("<mock-em_1@") and len(events) == 2 and events[0]["type"] == "bounce"
    assert events[0]["event_id"] == events[1]["event_id"]
    mock.send(
        EmailMessage(
            to="a",
            from_name="A",
            from_address="b@x.test",
            reply_to="",
            subject="s",
            body_text="b",
            attempt_id="em_2",
            contact_id="c_2",
            metadata={"scenario": "email_no_reply"},
        )
    )
    assert mock.drain_events() == []


class FakeSmtp:
    sent = []

    def __init__(self, host, port):
        self.host, self.port, self.logged_in = host, port, None

    def login(self, user, password):
        self.logged_in = user

    def send_message(self, mime):
        FakeSmtp.sent.append(mime)

    def quit(self):
        pass


def test_smtp_provider_builds_a_plain_text_message():
    FakeSmtp.sent = []
    provider = SmtpEmailProvider(
        "smtp.example.test",
        587,
        "uma@example.test",
        "pw",
        "uma@example.test",
        "Uma for Anirudh",
        "anirudh@example.test",
        smtp_factory=FakeSmtp,
        log=lambda *a: None,
    )
    mid = provider.send(
        EmailMessage(
            to="dr@example.test",
            from_name="",
            from_address="",
            reply_to="",
            subject="Hello?",
            body_text="Plain body.",
            headers={"X-BV-Attempt": "em_9"},
            attempt_id="em_9",
        )
    )
    mime = FakeSmtp.sent[0]
    assert mid.startswith("<") and mid.endswith("@example.test>") and mime["Message-ID"] == mid
    assert mime["From"] == "Uma for Anirudh <uma@example.test>" and mime["To"] == "dr@example.test"
    assert mime["Reply-To"] == "anirudh@example.test" and mime["X-BV-Attempt"] == "em_9"
    assert mime.get_content_type() == "text/plain" and "Plain body." in mime.get_content()
    assert provider.sent_ids == {mid: "em_9"}


class FakeImap:
    def __init__(self, messages: list[bytes]):
        self.messages = messages

    def login(self, user, password):
        return "OK", []

    def select(self, folder):
        return "OK", []

    def search(self, charset, criteria):
        return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]

    def fetch(self, num, parts):
        return "OK", [(b"1 (BODY[] {n}", self.messages[int(num) - 1])]

    def logout(self):
        return "BYE", []


def test_imap_poller_matches_replies_and_bounces():
    ours = "<abc@gse.harvard.edu>"
    reply = (
        f"From: Dr Chen <chen@example.test>\r\nTo: uma@example.test\r\nSubject: Re: 30 minutes?\r\n"
        f"In-Reply-To: {ours}\r\nMessage-ID: <r1@example.test>\r\nContent-Type: text/plain\r\n\r\n"
        "Yes, Tuesday works.\r\n\r\n> original quoted\r\n"
    ).encode()
    bounce = (
        f"From: Mail Delivery Subsystem <mailer-daemon@example.test>\r\nSubject: Undeliverable\r\n"
        f"Message-ID: <b1@example.test>\r\nContent-Type: text/plain\r\n\r\n550 user unknown. Original: {ours}\r\n"
    ).encode()
    unrelated = b"From: x@example.test\r\nSubject: hi\r\nMessage-ID: <u1@example.test>\r\n\r\nhello\r\n"
    poller = ImapReplyPoller(
        "imap.example.test", "u", "p", imap_factory=lambda: FakeImap([reply, bounce, unrelated]), log=lambda *a: None
    )
    events = poller.drain_events({ours: "em_1"})
    assert [e["type"] for e in events] == ["reply", "bounce"]
    assert events[0]["text"] == "Yes, Tuesday works." and events[0]["email_attempt_id"] == "em_1"
    assert poller.drain_events({ours: "em_1"}) == []  # seen


def test_outbox_provider_queues_without_sending(tmp_path):
    provider = OutboxEmailProvider(tmp_path / "outbox", FixedClock(TUESDAY_10AM_ET), log=lambda *a: None)
    mid = provider.send(
        EmailMessage(
            to="me@example.test",
            from_name="Uma",
            from_address="uma@example.test",
            reply_to="",
            subject="S",
            body_text="B",
            attempt_id="em_q",
            contact_id="c",
        )
    )
    assert mid == "" and provider.name == "outbox"
    stored = json.loads((tmp_path / "outbox" / "em_q.json").read_text())
    assert stored["to"] == "me@example.test" and stored["body_text"] == "B" and stored["status"] == "queued"


# ---------------------------------------------------------------- gate + clearances
def test_clearances_accept_identifier_or_phone(tmp_path):
    path = tmp_path / "c.csv"
    path.write_text("identifier,cleared_by,reason,cleared_at\n617-555-0101,x,y,z\nDr@Example.TEST,x,y,z\n")
    cleared = load_clearances(path)
    assert "+16175550101" in cleared and "dr@example.test" in cleared
    legacy = tmp_path / "l.csv"
    legacy.write_text("phone,cleared_by,reason,cleared_at\n617-555-0102,x,y,z\n")
    assert "+16175550102" in load_clearances(legacy)


def test_pre_send_gate(tmp_path):
    cfg = load_config(ROOT / "config.toml", env={"BV_DATA_DIR": str(tmp_path), "BV_OUTREACH_CHANNEL": "email"})
    clock = FixedClock(TUESDAY_10AM_ET)
    store = Store(cfg.data_dir, clock)
    c = contact(verification_status=VerificationStatus.VERIFIED, status=ContactStatus.READY)
    assert pre_send_gate(store, c, channel(), cfg, clock).allowed
    assert "email_clearance_not_cleared" in pre_send_gate(store, c, channel(cleared=False), cfg, clock).reasons
    assert "no_email_channel" in pre_send_gate(store, c, None, cfg, clock).reasons
    c.attempts = cfg.email_max_messages
    assert "max_messages_reached" in pre_send_gate(store, c, channel(), cfg, clock).reasons
    c.attempts = 0
    c.next_attempt_at = "2026-10-23T14:00:00Z"
    deferred = pre_send_gate(store, c, channel(), cfg, clock)
    assert deferred.deferred and not deferred.allowed
    live = load_config(
        ROOT / "config.toml",
        env={
            "BV_DATA_DIR": str(tmp_path),
            "BV_OUTREACH_CHANNEL": "email",
            "BV_MODE": "live",
            "BV_ALLOW_LIVE_CALLS": "1",
            "BV_LIVE_ALLOWLIST": "Me@Example.TEST",
        },
    )
    c.next_attempt_at = ""
    # staged demo: with no EMAIL_DEMO_RECIPIENT the first allowlisted address receives every email (waiver logged)
    redirected = pre_send_gate(store, c, channel(), live, clock)
    assert redirected.allowed and redirected.waivers == ["delivery_redirected_to_demo_inbox"]
    direct = pre_send_gate(store, c, channel("me@example.test"), live, clock)
    assert direct.allowed and direct.waivers == []
    strict = load_config(
        ROOT / "config.toml",
        env={
            "BV_DATA_DIR": str(tmp_path),
            "BV_OUTREACH_CHANNEL": "email",
            "BV_MODE": "live",
            "BV_ALLOW_LIVE_CALLS": "1",
            "BV_LIVE_ALLOWLIST": "Me@Example.TEST",
            "EMAIL_DEMO_RECIPIENT": "elsewhere@example.test",
        },
    )
    assert "not_in_live_allowlist" in pre_send_gate(store, c, channel(), strict, clock).reasons


# ---------------------------------------------------------------- full dry run, channel = email
def test_email_dry_run_outcomes(email_env: Env):
    env = email_env
    run = env.store.logs["runs"][-1]
    assert run["channel"] == "email" and set(run["stages"]) >= {"email", "replies"}
    assert run["stages"]["email"]["sent"] > 0 and run["stages"]["email"]["placed"] == run["stages"]["email"]["sent"]
    assert run["gate_escapes"] == 0 and env.mailer.sent

    def attempt(ext):
        cid = env.contact(ext).id
        rows = [a for a in env.store.email_attempts.values() if a.contact_id == cid]
        assert rows, ext
        return rows[-1]

    # agreed -> handoff, contact BOOKED, interview carries email + availability
    t01 = env.contact("t_01")
    assert attempt("t_01").outcome == Outcome.AGREED_TO_TALK and t01.status == ContactStatus.BOOKED
    interview = next(i for i in env.store.interviews.values() if i.contact_id == t01.id)
    assert interview.contact_email == "ppatel@example.test" and "Tuesday or Thursday" in interview.availability
    assert interview.contact_method == "whatsapp" and interview.status == "draft_pending_approval"
    # declined, stop, bounce, other, no reply
    assert attempt("t_02").outcome == Outcome.DECLINED and env.contact("t_02").status == ContactStatus.DECLINED
    assert attempt("t_09").outcome == Outcome.DO_NOT_CONTACT and env.contact("t_09").status == ContactStatus.SUPPRESSED
    assert any(
        s.identifier_type == "email" and s.identifier == "hgoldberg@brooklinepc.example.test"
        for s in env.store.suppressions.values()
    )
    assert attempt("t_11").outcome == Outcome.BOUNCED and attempt("t_11").status == "bounced"
    assert env.contact("t_11").status == ContactStatus.UNREACHABLE
    assert env.store.channels[attempt("t_11").channel_id].verified is False
    assert attempt("t_04").outcome == Outcome.REPLIED_OTHER and env.contact("t_04").status == ContactStatus.IN_OUTREACH
    assert any(a["type"] == "reply_needs_review" for a in env.store.logs["alerts"])
    assert attempt("t_03").outcome == "" and env.contact("t_03").next_attempt_at == "2026-10-23T14:00:00Z"
    # email-only contact is reachable in email mode; phone-only t_12 duplicate still blocked; t_16 not cleared
    assert attempt("t_08").status == "sent"
    assert not [a for a in env.store.email_attempts.values() if a.contact_id == env.contact("t_16").id]
    reasons = [r for d in env.decisions("t_16") for r in d["reasons"]]
    assert "email_clearance_not_cleared" in reasons
    # suppression by phone still protects the contact in email mode
    assert not [a for a in env.store.email_attempts.values() if a.contact_id == env.contact("t_05").id]
    # PII in a reply holds the thread
    held = env.store.transcripts[attempt("t_14").transcript_id]
    assert held.pii_flagged and held.delivery_status == DeliveryStatus.HELD
    # duplicate events ignored, threads delivered to the Brain with the reply
    assert attempt("t_01").duplicate_events_ignored == 1
    delivered = env.store.transcripts[attempt("t_01").transcript_id]
    assert [u["speaker"] for u in delivered.utterances] == ["agent", "doctor"]
    assert delivered.delivery_status == DeliveryStatus.DELIVERED
    assert f"{attempt('t_01').id}:reply" in env.store.brain_inbox
    # summary + export
    from infra.summary import build_summary

    summary = build_summary(env.store, run)
    assert summary["channel"] == "email" and summary["funnel"][3]["stage"] == "Emailed"
    em = summary["emails"]
    assert em["agreed"] >= 3 and em["stop"] == 1 and em["bounced"] == 1 and em["needs_review"] == 1
    assert em["awaiting_reply"] >= 2 and em["reply_rate"] is not None
    export_text = (env.cfg.export_dir / "email_attempts.json").read_text()
    assert "ppatel@example.test" not in export_text and "em_" in export_text
    assert (env.cfg.export_dir / "summary.json").exists()


def test_follow_up_then_no_reply(email_env: Env):
    env = email_env
    cid = env.contact("t_03").id
    env.clock.advance(days=1)
    env.run()
    assert env.contact("t_03").attempts == 1  # follow-up not due yet
    env.clock.advance(days=3)
    env.run()
    rows = sorted((a for a in env.store.email_attempts.values() if a.contact_id == cid), key=lambda a: a.attempt_number)
    assert len(rows) == 2 and rows[0].outcome == Outcome.NO_REPLY and rows[1].status == "sent"
    assert env.contact("t_03").attempts == 2 and env.contact("t_03").status == ContactStatus.IN_OUTREACH
    env.clock.advance(days=4)
    env.run()
    assert (
        env.contact("t_03").status == ContactStatus.UNREACHABLE and env.contact("t_03").last_outcome == Outcome.NO_REPLY
    )
    assert rows[1].outcome == Outcome.NO_REPLY
    assert len([a for a in env.store.email_attempts.values() if a.contact_id == cid]) == 2


def test_call_channel_still_runs_the_voice_flow(tmp_path):
    env = Env(tmp_path, channel="call")
    run, summary = env.run()
    assert run["channel"] == "call" and "calls" in run["stages"] and summary["calls"]["attempts"] > 0
    assert not env.store.email_attempts


def test_record_reply_and_outbox_flow(tmp_path):
    from infra import live

    cfg = load_config(
        ROOT / "config.toml",
        env={"BV_DATA_DIR": str(tmp_path / "d"), "BV_EXPORT_DIR": str(tmp_path / "x"), "BV_OUTREACH_CHANNEL": "email"},
    )
    clock = FixedClock(TUESDAY_10AM_ET)
    store = Store(cfg.data_dir, clock).load()
    paths = live.LivePaths(
        seed=ROOT / "seeds" / "test_doctors.csv",
        clearances=ROOT / "seeds" / "clearances.csv",
        suppressions=ROOT / "seeds" / "suppressions.csv",
    )
    preview = live.preview_email(store, cfg, clock, paths, lambda *a: None)
    assert any(p["to"] == "ppatel@example.test" and "Dr. Patel" in p["subject"] for p in preview)
    provider = live.connect_email(cfg, clock, lambda *a: None, transport="outbox")
    result = live.send_demo_email(store, cfg, clock, provider, paths, lambda *a: None)
    assert result["transport"] == "outbox" and result["queued"]
    queued = live.email_outbox(store)
    assert queued and queued[0]["body"].endswith(OPT_OUT_LINE + "\n") and queued[0]["to"]
    first = queued[0]["attempt_id"]
    sent = live.mark_email_sent(store, cfg, clock, first, "gmail-msg-1", "thread-1", lambda *a: None)
    assert sent["status"] == "sent" and sent["provider"] == "gmail-connector" and sent["transcript_id"]
    out = live.record_reply(
        store,
        cfg,
        clock,
        first,
        "Yes, happy to talk Thursday afternoon.",
        lambda *a: None,
        sender="dr@example.test",
        at="2026-10-21T15:00:00Z",
    )
    assert out["outcome"] == Outcome.AGREED_TO_TALK and out["contact_status"] == ContactStatus.BOOKED
    assert store.email_attempts[first].replied_at == "2026-10-21T15:00:00Z"
    assert (cfg.export_dir / "email_attempts.json").exists()


def test_default_channel_is_email_and_env_overrides(tmp_path):
    cfg = load_config(ROOT / "config.toml", env={"BV_DATA_DIR": str(tmp_path)})
    assert cfg.outreach_channel == "email" and cfg.email_follow_up_days == 3 and cfg.email_max_messages == 2
    cfg = load_config(
        ROOT / "config.toml",
        env={
            "BV_DATA_DIR": str(tmp_path),
            "BV_OUTREACH_CHANNEL": "call",
            "SMTP_HOST": "smtp.gmail.com",
            "SMTP_USER": "u@x.test",
            "SMTP_PASSWORD": "pw",
            "IMAP_HOST": "imap.gmail.com",
        },
    )
    assert cfg.outreach_channel == "call" and cfg.imap_user == "u@x.test" and cfg.imap_password == "pw"
    assert datetime.now(UTC).year >= 2026


# ---------------------------------------------------------------- staged demo: redirect to the team inbox
def test_demo_recipient_redirect_and_gate(tmp_path):
    from infra.email.provider import OutboxEmailProvider
    from infra.orchestrator import send_emails

    env = {
        "BV_DATA_DIR": str(tmp_path / "data"),
        "BV_EXPORT_DIR": str(tmp_path / "export"),
        "BV_OUTREACH_CHANNEL": "email",
        "BV_MODE": "live",
        "BV_ALLOW_LIVE_CALLS": "1",
        "BV_LIVE_ALLOWLIST": "Team@Example.TEST",
    }
    cfg = load_config(ROOT / "config.toml", env=env)
    assert cfg.email_demo_recipient == "team@example.test"  # defaults to the first allowlisted address
    clock = FixedClock(TUESDAY_10AM_ET)
    store = Store(cfg.data_dir, clock)
    c = contact(verification_status=VerificationStatus.VERIFIED, status=ContactStatus.READY)
    gate = pre_send_gate(
        store, c, channel(), cfg, clock
    )  # the practice's address is not allowlisted, the demo inbox is
    assert gate.allowed and gate.waivers == ["delivery_redirected_to_demo_inbox"]
    strict = load_config(ROOT / "config.toml", env={**env, "EMAIL_DEMO_RECIPIENT": "other@example.test"})
    assert "not_in_live_allowlist" in pre_send_gate(store, c, channel(), strict, clock).reasons

    store.contacts[c.id] = c
    ch = channel()
    store.channels[ch.id] = ch
    mailer = OutboxEmailProvider(tmp_path / "data" / "outbox", clock, log=lambda *a: None)
    counts = send_emails(store, cfg, clock, MockBrain(store, clock), mailer, "r1")
    assert counts["queued"] == 1 and mailer.queued[0].to == "team@example.test"
    attempt = next(iter(store.email_attempts.values()))
    assert attempt.redirected_to == "t\u2022\u2022\u2022@example.test" and attempt.status == "queued"
    assert "Dr. Patel" in attempt.body  # the text still addresses the doctor
    queued = json.loads((tmp_path / "data" / "outbox" / f"{attempt.id}.json").read_text())
    assert queued["to"] == "team@example.test"
