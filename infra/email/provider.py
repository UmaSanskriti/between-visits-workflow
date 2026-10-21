"""Email providers: a dry-run mock driven by seed scenarios, and SMTP (stdlib) with optional IMAP reply polling."""

from __future__ import annotations

import email as _email
import email.policy as _email_policy
import imaplib
import re
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage as _MimeMessage
from email.utils import formataddr, formatdate, make_msgid, parseaddr
from typing import Protocol

from ..clock import Clock, iso


class EmailSendError(Exception):
    pass


@dataclass
class EmailMessage:
    to: str
    from_name: str
    from_address: str
    reply_to: str
    subject: str
    body_text: str
    headers: dict = field(default_factory=dict)
    attempt_id: str = ""
    contact_id: str = ""
    metadata: dict = field(default_factory=dict)  # dry run only (e.g. the seed scenario)


class EmailProvider(Protocol):
    name: str

    def send(self, message: EmailMessage) -> str: ...

    def drain_events(self) -> list[dict]: ...


# scenario -> (event type, reply text)
EMAIL_SCENARIOS: dict[str, tuple[str, str] | None] = {
    "email_agreed": (
        "reply",
        "Yes, happy to talk. Tuesday or Thursday afternoons work best for me. Best email is this one, "
        "or you can text me on WhatsApp at this number. Looking forward to it.",
    ),
    "email_declined": ("reply", "Thanks for reaching out, but we're not able to take this on right now."),
    "email_stop": ("reply", "Please stop emailing us. Remove this address from your list."),
    "email_bounce": ("bounce", "550 5.1.1 The email account that you tried to reach does not exist."),
    "email_other": ("reply", "Can you send more details about what you're building and who is behind it?"),
    "email_pii": (
        "reply",
        "Sure. One of my patients, date of birth 03/04/1980, asked about this last week, so it's timely.",
    ),
    "email_no_reply": None,
}
DEFAULT_EMAIL_SCENARIO = "email_no_reply"


def pick_scenario(raw: str, known: dict, default: str) -> str:
    """Seed rows may carry several scenarios separated by '|' (one per mock); pick the first this mock knows."""
    for token in (raw or "").split("|"):
        token = token.strip()
        if token in known:
            return token
    return default


class MockEmailProvider:
    """Dry run. Emits every reply event twice so the pipeline's idempotency is exercised."""

    name = "mock"

    def __init__(self, clock: Clock):
        self.clock = clock
        self.sent: list[EmailMessage] = []
        self._pending: list[dict] = []

    def send(self, message: EmailMessage) -> str:
        self.sent.append(message)
        message_id = f"<mock-{message.attempt_id}@between-visits.test>"
        scenario = pick_scenario(message.metadata.get("scenario", ""), EMAIL_SCENARIOS, DEFAULT_EMAIL_SCENARIO)
        plan = EMAIL_SCENARIOS[scenario]
        if plan:
            kind, text = plan
            event = {
                "event_id": f"{message_id}:{kind}",
                "type": kind,
                "email_attempt_id": message.attempt_id,
                "provider_message_id": message_id,
                "text": text,
                "at": iso(self.clock.now()),
                "from": message.to if kind == "reply" else "mailer-daemon@between-visits.test",
            }
            self._pending.append(event)
            self._pending.append(dict(event))  # the duplicate delivery
        return message_id

    def drain_events(self) -> list[dict]:
        events, self._pending = self._pending, []
        return events


class OutboxEmailProvider:
    """Queue only: the message is written to <outbox_dir>/<attempt_id>.json and sent by hand through the Gmail
    connector; `email-sent <attempt-id> --message-id ...` then marks it sent."""

    name = "outbox"

    def __init__(self, outbox_dir, clock: Clock, log=print):
        from pathlib import Path

        self.outbox_dir = Path(outbox_dir)
        self.clock = clock
        self.log = log
        self.queued: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> str:
        import json

        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "attempt_id": message.attempt_id,
            "contact_id": message.contact_id,
            "to": message.to,
            "from_name": message.from_name,
            "from_address": message.from_address,
            "reply_to": message.reply_to,
            "subject": message.subject,
            "body_text": message.body_text,
            "headers": message.headers,
            "queued_at": iso(self.clock.now()),
            "status": "queued",
        }
        (self.outbox_dir / f"{message.attempt_id}.json").write_text(json.dumps(payload, indent=1) + "\n")
        self.queued.append(message)
        self.log(f"email: queued {message.attempt_id} for the Gmail connector -> {message.to}")
        return ""  # no provider id until the operator reports the send

    def drain_events(self) -> list[dict]:
        return []


class ManualReplies:
    """Operator-recorded replies (no IMAP): yields the given events once."""

    name = "manual"

    def __init__(self, events: list[dict]):
        self._events = list(events)

    def send(self, message: EmailMessage) -> str:
        raise EmailSendError("ManualReplies cannot send")

    def drain_events(self) -> list[dict]:
        events, self._events = self._events, []
        return events


def _strip_quoted(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        if line.lstrip().startswith(">"):
            continue
        if re.match(r"^\s*On .+wrote:\s*$", line) or line.strip() in ("-- ", "--"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def _plain_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                try:
                    return part.get_content()
                except (LookupError, UnicodeDecodeError):
                    payload = part.get_payload(decode=True) or b""
                    return payload.decode("utf-8", errors="replace")
        return ""
    try:
        return msg.get_content()
    except (LookupError, UnicodeDecodeError, AttributeError):
        payload = msg.get_payload(decode=True) or b""
        return payload.decode("utf-8", errors="replace")


class ImapReplyPoller:
    """Finds replies (and bounces) to messages we sent, by In-Reply-To / References. Keeps a seen-set."""

    def __init__(self, host: str, user: str, password: str, folder: str = "INBOX", imap_factory=None, log=print):
        self.host, self.user, self.password, self.folder = host, user, password, folder
        self.imap_factory = imap_factory or (lambda: imaplib.IMAP4_SSL(host, timeout=20))
        self.recent_limit = 40  # newest messages to inspect for untagged replies
        self.max_messages = 60  # hard cap on full-message fetches per poll
        self.log = log
        self.seen: set[str] = set()

    def drain_events(self, sent: dict[str, str], since_date: str = "") -> list[dict]:
        """`sent` maps provider Message-ID -> email_attempt_id. `since_date` is IMAP's DD-Mon-YYYY."""
        if not sent:
            return []
        events: list[dict] = []
        conn = self.imap_factory()
        try:
            conn.login(self.user, self.password)
            conn.select(self.folder)
            # Only messages that reference one of OUR Message-IDs, plus a bounded scan of the newest mail for
            # clients that drop threading headers. Never the whole inbox.
            candidates: list[bytes] = []
            for mid in sent:
                for header in ("In-Reply-To", "References"):
                    status, data = conn.search(None, f'(HEADER {header} "{mid}")')
                    if status == "OK" and data and data[0]:
                        candidates.extend(data[0].split())
            criteria = f"(SINCE {since_date})" if since_date else "ALL"
            status, data = conn.search(None, criteria)
            recent = data[0].split()[-self.recent_limit :] if status == "OK" and data and data[0] else []
            seen_nums: set[bytes] = set()
            ordered = [n for n in candidates + recent if not (n in seen_nums or seen_nums.add(n))]
            for num in ordered[: self.max_messages]:
                status, fetched = conn.fetch(num, "(BODY.PEEK[])")
                if status != "OK" or not fetched:
                    continue
                raw = next((part[1] for part in fetched if isinstance(part, tuple)), b"")
                msg = _email.message_from_bytes(raw, policy=_email_policy.default)
                refs = " ".join(x for x in (msg.get("In-Reply-To", ""), msg.get("References", "")) if x)
                body = _plain_text(msg)
                parent = next((mid for mid in sent if mid and (mid in refs or mid in body)), "")
                if not parent:
                    continue
                message_id = msg.get("Message-ID", "") or f"imap:{num.decode()}"
                if message_id in self.seen:
                    continue
                self.seen.add(message_id)
                sender = parseaddr(msg.get("From", ""))[1].lower()
                kind = "bounce" if ("mailer-daemon" in sender or "postmaster" in sender) else "reply"
                events.append(
                    {
                        "event_id": f"{message_id}:{kind}",
                        "type": kind,
                        "email_attempt_id": sent[parent],
                        "provider_message_id": parent,
                        "text": _strip_quoted(body) if kind == "reply" else body[:500],
                        "at": msg.get("Date", ""),
                        "from": sender,
                    }
                )
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 - best effort
                pass
        return events


class SmtpEmailProvider:
    """Plain-text email over SMTP (STARTTLS on 587, SSL on 465). `smtp_factory(host, port)` is injectable."""

    name = "smtp"

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_address: str,
        from_name: str = "Anirudh Bagla",
        reply_to: str = "",
        poller: ImapReplyPoller | None = None,
        smtp_factory=None,
        log=print,
    ):
        if not host or not from_address:
            raise EmailSendError("SMTP_HOST and EMAIL_FROM are required")
        self.host, self.port, self.user, self.password = host, int(port or 587), user, password
        self.from_address, self.from_name, self.reply_to = from_address, from_name, reply_to
        self.poller = poller
        self.smtp_factory = smtp_factory
        self.log = log
        self.sent_ids: dict[str, str] = {}  # Message-ID -> attempt id, for reply matching

    def _connect(self):
        if self.smtp_factory:
            return self.smtp_factory(self.host, self.port)
        if self.port == 465:
            return smtplib.SMTP_SSL(self.host, self.port, timeout=30)
        conn = smtplib.SMTP(self.host, self.port, timeout=30)
        conn.ehlo()
        conn.starttls()
        conn.ehlo()
        return conn

    def send(self, message: EmailMessage) -> str:
        mime = _MimeMessage()
        mime["From"] = formataddr((message.from_name or self.from_name, message.from_address or self.from_address))
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(localtime=False)
        domain = (message.from_address or self.from_address).split("@")[-1] or "between-visits.local"
        message_id = make_msgid(domain=domain)
        mime["Message-ID"] = message_id
        if message.reply_to or self.reply_to:
            mime["Reply-To"] = message.reply_to or self.reply_to
        for key, value in message.headers.items():
            mime[key] = str(value)
        mime.set_content(message.body_text)
        try:
            conn = self._connect()
            try:
                if self.user:
                    conn.login(self.user, self.password)
                conn.send_message(mime)
            finally:
                try:
                    conn.quit()
                except Exception:  # noqa: BLE001 - best effort
                    pass
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailSendError(f"smtp: {exc}") from exc
        self.sent_ids[message_id] = message.attempt_id
        self.log(f"email: sent {message_id} to {message.to}")
        return message_id

    def drain_events(self) -> list[dict]:
        if not self.poller:
            return []
        return self.poller.drain_events(self.sent_ids)
