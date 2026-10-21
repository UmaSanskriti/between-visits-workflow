"""Classify a doctor's email reply into an outcome plus the handoff details Anirudh needs."""

from __future__ import annotations

import re

from ..models import Outcome

STOP_PATTERNS = [
    r"\bstop\b",
    r"unsubscribe",
    r"do not (email|contact|write)",
    r"don'?t (email|contact|write)",
    r"remove (me|us|this address)",
    r"take (me|us) off",
    r"no further contact",
    r"stop (emailing|contacting)",
]
DECLINE_PATTERNS = [
    r"\bnot interested\b",
    r"\bno,? thank",
    r"\bno thanks\b",
    r"not (able|going|looking) to",
    r"can'?t (take|do) this",
    r"we'?re not taking",
    r"pass on this",
    r"\bdecline\b",
    r"not a (good )?fit",
    r"not at this time",
    r"not right now",
    r"unable to",
    r"not available",
    r"\bno capacity\b",
]
STRONG_AGREE_PATTERNS = [
    r"\byes\b",
    r"happy to",
    r"\bsure\b",
    r"works for me",
    r"\blet'?s\b",
    r"call me",
    r"sounds good",
    r"i'?d be (glad|happy)",
    r"would love",
    r"count me in",
    r"open to (it|that|a (chat|call|conversation))",
    r"\bworks\b",
]
DAY_TIME_PATTERNS = [
    r"\b(mon|tues?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)(day)?s?\b",
    r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b",
    r"\b(morning|afternoon|evening|lunch|noon)s?\b",
    r"\bnext week\b",
    r"\btomorrow\b",
]
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
REPLIED_OUTCOMES = {Outcome.AGREED_TO_TALK, Outcome.DECLINED, Outcome.DO_NOT_CONTACT, Outcome.REPLIED_OTHER}


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def strip_quoted(text: str) -> str:
    kept = []
    for line in (text or "").splitlines():
        if line.lstrip().startswith(">"):
            continue
        if re.match(r"^\s*On .+wrote:\s*$", line):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def classify_reply(text: str) -> tuple[str, dict]:
    """Returns (outcome, handoff). Keyword heuristics; REPLIED_OTHER means a human should read it."""
    clean = strip_quoted(text)
    lowered = clean.lower()
    if _any(STOP_PATTERNS, lowered):
        return Outcome.DO_NOT_CONTACT, {}

    emails = [e.rstrip(".,;:") for e in EMAIL_RE.findall(clean)]
    phones = [m.group(0).strip() for m in PHONE_RE.finditer(clean)]
    method = "email"
    if re.search(r"whatsapp", lowered):
        method = "whatsapp"
    elif re.search(r"imessage|text me|text is fine|by text", lowered):
        method = "imessage"
    elif re.search(r"\bcall me\b|\bby phone\b|\bphone call\b|give me a call", lowered):
        method = "phone_call"
    elif re.search(r"in person|come by|stop by|visit", lowered):
        method = "in_person"
    availability = " ".join(
        s.strip() for s in re.split(r"(?<=[.!?])\s+", clean) if _any(DAY_TIME_PATTERNS, s.lower())
    ).strip()
    handoff = {
        "contact_email": emails[0] if emails else "",
        "contact_method": method,
        "contact_number": phones[0] if phones else "",
        "availability": availability,
        "person_spoken_to": "doctor",
        "notes": clean[:240],
    }

    strong_agree = _any(STRONG_AGREE_PATTERNS, lowered)
    decline = _any(DECLINE_PATTERNS, lowered)
    if decline and not strong_agree:
        return Outcome.DECLINED, {}
    if strong_agree or availability or emails or phones:
        return Outcome.AGREED_TO_TALK, handoff
    if decline:
        return Outcome.DECLINED, {}
    return Outcome.REPLIED_OTHER, handoff
