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


NO_RETRY_OUTCOMES = {
    Outcome.BOOKED,
    Outcome.DECLINED,
    Outcome.DO_NOT_CONTACT,
    Outcome.WRONG_NUMBER,
    Outcome.NOT_A_FIT,
    Outcome.DECLINED_RECORDING,
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
    processed_event_ids: list[str] = field(default_factory=list)
    duplicate_events_ignored: int = 0
    callback_requested_at: str = ""
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
    "transcripts": Transcript,
    "interviews": Interview,
    "suppressions": Suppression,
    "consent_records": ConsentRecord,
    "scripts": ScriptRecord,
}
