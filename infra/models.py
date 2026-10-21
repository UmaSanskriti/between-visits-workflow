"""Logical data model from the requirements spec (section 6.1) plus connector outputs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import StrEnum


class ContactStatus(StrEnum):
    NEW = "NEW"
    SOURCED = "SOURCED"
    VERIFIED = "VERIFIED"
    READY = "READY"
    IN_OUTREACH = "IN_OUTREACH"
    BOOKED = "BOOKED"
    DECLINED = "DECLINED"
    UNREACHABLE = "UNREACHABLE"
    SUPPRESSED = "SUPPRESSED"


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    FAILED = "failed"


class Clearance(StrEnum):
    NOT_CLEARED = "not_cleared"
    CLEARED = "cleared"


class Outcome(StrEnum):
    BOOKED = "BOOKED"
    CALLBACK_REQUESTED = "CALLBACK_REQUESTED"
    NO_ANSWER = "NO_ANSWER"
    VOICEMAIL = "VOICEMAIL"
    GATEKEEPER_BLOCKED = "GATEKEEPER_BLOCKED"
    DECLINED = "DECLINED"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"
    WRONG_NUMBER = "WRONG_NUMBER"
    FAILED_TECHNICAL = "FAILED_TECHNICAL"
    NOT_A_FIT = "NOT_A_FIT"  # playbook: hospital-owned or no GLP-1 patients
    DECLINED_RECORDING = "DECLINED_RECORDING"  # playbook: objected to recording; call ended politely
    AGREED_TO_TALK = "AGREED_TO_TALK"  # agreed to talk to Anirudh; email/channel captured, no slot fixed yet
    REPLIED_OTHER = "REPLIED_OTHER"  # email: a reply that needs a human to read it
    BOUNCED = "BOUNCED"  # email: address rejected
    NO_REPLY = "NO_REPLY"  # email: no reply after the follow-up window


NO_RETRY_OUTCOMES = {
    Outcome.BOOKED,
    Outcome.DECLINED,
    Outcome.DO_NOT_CONTACT,
    Outcome.WRONG_NUMBER,
    Outcome.NOT_A_FIT,
    Outcome.DECLINED_RECORDING,
    Outcome.AGREED_TO_TALK,
    Outcome.BOUNCED,
    Outcome.NO_REPLY,
}
CONNECTED_OUTCOMES = {
    Outcome.BOOKED,
    Outcome.CALLBACK_REQUESTED,
    Outcome.VOICEMAIL,
    Outcome.GATEKEEPER_BLOCKED,
    Outcome.DECLINED,
    Outcome.DO_NOT_CONTACT,
    Outcome.NOT_A_FIT,
    Outcome.DECLINED_RECORDING,
    Outcome.AGREED_TO_TALK,
    Outcome.REPLIED_OTHER,
}

# Playbook section 10: provisional failure category derived from the outcome; the reviewer loop may override it.
FAILURE_CATEGORY = {
    Outcome.NO_ANSWER: "No answer / voicemail",
    Outcome.VOICEMAIL: "No answer / voicemail",
    Outcome.GATEKEEPER_BLOCKED: "Screened out by front desk",
    Outcome.DECLINED: "Pitch did not land",
    Outcome.CALLBACK_REQUESTED: "Call back later",
    Outcome.NOT_A_FIT: "Not a fit",
    Outcome.DECLINED_RECORDING: "Hung up at AI disclosure",
    Outcome.DO_NOT_CONTACT: "Do not contact",
    Outcome.WRONG_NUMBER: "Wrong number (list data)",
    Outcome.FAILED_TECHNICAL: "Technical failure",
    Outcome.BOUNCED: "Wrong address (list data)",
    Outcome.NO_REPLY: "No reply",
    Outcome.REPLIED_OTHER: "Reply needs review",
}


class CallStatus(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    HELD = "held_pii_review"


@dataclass
class Record:
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class Contact(Record):
    id: str
    full_name: str = ""
    credentials: str = ""
    specialty: str = ""
    practice_name: str = ""
    practice_size: str = ""
    address: str = ""
    city: str = ""
    state: str = ""
    time_zone: str = "America/New_York"
    status: str = ContactStatus.NEW
    verification_status: str = VerificationStatus.UNVERIFIED
    verification_reason: str = ""
    segment: str = ""
    external_id: str = ""
    lead_source: str = ""
    pronunciation_hint: str = ""
    duplicate_of: str = ""
    attempts: int = 0
    next_attempt_at: str = ""
    last_outcome: str = ""
    hook: str = ""  # playbook section 4: one specific line from the practice website
    website: str = ""  # practice website, when known (feeds the web enricher)
    source_tag: str = ""  # dummy | real (practice sheet column)
    test_scenario: str = ""  # dry run only: drives the mock voice provider
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Channel(Record):
    id: str
    contact_id: str
    type: str  # phone | email | web_form
    value: str
    raw_value: str = ""
    line_type: str = "unknown"
    verified: bool = False
    verification_reason: str = ""
    call_clearance: str = Clearance.NOT_CLEARED
    source: str = ""
    source_url: str = ""
    retrieved_at: str = ""


@dataclass
class CallAttempt(Record):
    id: str
    contact_id: str
    channel_id: str
    attempt_number: int = 1
    run_id: str = ""
    gate_result: str = "allowed"  # a placed call may only ever carry "allowed" (CMP-001)
    scheduled_at: str = ""
    started_at: str = ""
    ended_at: str = ""
    status: str = CallStatus.QUEUED
    outcome: str = ""
    outcome_reason: str = ""
    failure_category: str = ""
    duration_sec: int = 0
    provider: str = "mock"
    provider_call_id: str = ""
    script_id: str = ""
    script_version: str = ""
    segment: str = ""
    pitch_angle: str = ""
    fallbacks_applied: list[str] = field(default_factory=list)
    recording_ref: str = ""
    transcript_id: str = ""
    cost_usd: float = 0.0
    provider_ended_reason: str = ""
    processed_event_ids: list[str] = field(default_factory=list)
    duplicate_events_ignored: int = 0
    callback_requested_at: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class EmailAttempt(Record):
    """One email sent to a practice (playbook section 8b). Replies and bounces land on the same record."""

    id: str
    contact_id: str
    channel_id: str
    attempt_number: int = 1
    run_id: str = ""
    gate_result: str = "allowed"
    subject: str = ""
    body: str = ""
    status: str = "queued"  # queued | sent | failed | bounced
    provider: str = "mock"
    provider_message_id: str = ""
    thread_id: str = ""
    sent_at: str = ""
    redirected_to: str = ""  # staged demo: masked inbox the message was actually delivered to
    script_version: str = ""
    segment: str = ""
    outcome: str = ""
    outcome_reason: str = ""
    failure_category: str = ""
    reply_text: str = ""
    replied_at: str = ""
    handoff: dict = field(default_factory=dict)
    transcript_id: str = ""
    fallbacks_applied: list[str] = field(default_factory=list)
    personalization: list[str] = field(default_factory=list)  # facts from the enrichment used in this draft
    processed_event_ids: list[str] = field(default_factory=list)
    duplicate_events_ignored: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Enrichment(Record):
    """What we would normally find online about the practice (brief step 2). Keyed by contact id."""

    id: str  # == contact_id
    contact_id: str
    source: str = "seed"  # seed | web
    website: str = ""
    summary: str = ""
    services: list[str] = field(default_factory=list)
    glp1_mentioned: bool = False
    weight_management_mentioned: bool = False
    doctor_bio: str = ""
    patient_focus: str = ""
    recent_note: str = ""
    languages: str = ""
    hook: str = ""  # a clause that completes "I noticed {hook}"
    source_note: str = ""
    research_note: str = ""  # what the research pass found or did not, and why
    website_source: str = ""  # sheet | google_cse | guess
    emails: list[dict] = field(default_factory=list)  # [{address, page_url, kind}] published on the site
    phones_found: list[str] = field(default_factory=list)
    phone_match: str = ""  # match | mismatch | unknown (site phone vs NPI phone)
    retrieved_at: str = ""
    confidence: str = "low"  # low | medium | high
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Transcript(Record):
    id: str
    call_attempt_id: str
    contact_id: str
    utterances: list[dict] = field(default_factory=list)
    language: str = "en"
    pii_flagged: bool = False
    pii_reason: str = ""
    delivery_status: str = DeliveryStatus.PENDING
    delivery_attempts: int = 0
    delivered_at: str = ""
    last_delivery_error: str = ""
    idempotency_key: str = ""
    created_at: str = ""


@dataclass
class Interview(Record):
    id: str
    contact_id: str
    call_attempt_id: str
    scheduled_for: str = ""
    calendar_event_ref: str = ""
    contact_email: str = ""
    contact_method: str = ""  # phone_call | whatsapp | imessage | email | in_person | unknown
    contact_number: str = ""
    availability: str = ""
    person_spoken_to: str = ""
    notes: str = ""
    status: str = "draft_pending_approval"  # playbook 5.12: nothing goes out without Anirudh
    approved_by: str = ""
    approved_at: str = ""
    confirmation_email: str = "skipped_no_verified_email"
    created_at: str = ""


@dataclass
class Suppression(Record):
    id: str
    identifier: str
    identifier_type: str  # phone | email | contact
    reason: str = ""
    source: str = ""
    created_at: str = ""


@dataclass
class ConsentRecord(Record):
    id: str
    contact_id: str
    channel_id: str
    consent_type: str = "call_clearance"
    evidence_ref: str = ""
    captured_at: str = ""
    revoked_at: str = ""


@dataclass
class ScriptRecord(Record):
    """Connector output #1: the personalized script sent to Voice for one call."""

    call_id: str
    contact_id: str
    script_id: str
    script_version: str
    segment: str
    pitch_angle: str
    first_message: str
    system_prompt: str
    voicemail_message: str = ""
    variables_used: dict = field(default_factory=dict)
    fallbacks_applied: list[str] = field(default_factory=list)
    created_at: str = ""


TABLES: dict[str, type[Record]] = {
    "contacts": Contact,
    "channels": Channel,
    "call_attempts": CallAttempt,
    "email_attempts": EmailAttempt,
    "enrichments": Enrichment,
    "transcripts": Transcript,
    "interviews": Interview,
    "suppressions": Suppression,
    "consent_records": ConsentRecord,
    "scripts": ScriptRecord,
}
