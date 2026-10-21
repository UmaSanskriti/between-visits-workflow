"""Email outreach channel (playbook section 8b): compose, send, classify replies, hand off to Anirudh.

Modules:
- provider: EmailProvider protocol, MockEmailProvider (dry run), SmtpEmailProvider (+ optional IMAP reply poller)
- compose:  build_email() from the Brain's template, with the playbook's email rules enforced
- classify: classify_reply() -> outcome + handoff details
"""

from .classify import classify_reply
from .compose import EmailDraft, build_email
from .provider import (
    EmailMessage,
    EmailProvider,
    EmailSendError,
    ImapReplyPoller,
    ManualReplies,
    MockEmailProvider,
    OutboxEmailProvider,
    SmtpEmailProvider,
)

__all__ = [
    "EmailDraft",
    "EmailMessage",
    "EmailProvider",
    "EmailSendError",
    "ImapReplyPoller",
    "ManualReplies",
    "MockEmailProvider",
    "OutboxEmailProvider",
    "SmtpEmailProvider",
    "build_email",
    "classify_reply",
]
