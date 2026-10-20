from __future__ import annotations

from conftest import Env

from infra.compliance import contains_opt_out, detect_patient_data, redact
from infra.export import mask_email, mask_phone
from infra.models import Clearance, Contact
from infra.scripts import segment_for
from infra.verify import normalize_e164


def test_normalize_e164():
    assert normalize_e164("617-555-0101") == "+16175550101"
    assert normalize_e164("(617) 555-0102") == "+16175550102"
    assert normalize_e164("+1 617 555 0103") == "+16175550103"
    assert normalize_e164("1-617-555-0104") == "+16175550104"
    assert normalize_e164("555-0105") is None
    assert normalize_e164("") is None


def test_line_type_and_clearance_recorded(ran: Env):
    phones = {ch.contact_id: ch for ch in ran.store.channels.values() if ch.type == "phone"}
    assert phones[ran.contact("t_18").id].line_type == "mobile"
    assert phones[ran.contact("t_15").id].line_type == "voip"
    assert phones[ran.contact("t_16").id].call_clearance == Clearance.NOT_CLEARED
    assert phones[ran.contact("t_01").id].call_clearance == Clearance.CLEARED
    t01_channel = phones[ran.contact("t_01").id].id
    assert any(cr.channel_id == t01_channel for cr in ran.store.consent_records.values())


def test_ingest_is_idempotent(env: Env):
    env.run(stages=("ingest", "verify"))
    first = (len(env.store.contacts), len(env.store.channels), len(env.store.suppressions))
    env.run(stages=("ingest", "verify"))
    assert (len(env.store.contacts), len(env.store.channels), len(env.store.suppressions)) == first
    assert first[0] == 20


def test_segment_logic():
    solo = Contact(id="x", specialty="family medicine", practice_size="solo")
    group = Contact(id="x", specialty="endocrinology", practice_size="small_group")
    assert segment_for(solo) == "solo_primary_care"
    assert segment_for(group) == "small_group_specialist"
    assert segment_for(Contact(id="x", specialty="", practice_size="solo")) == "default"


def test_opt_out_and_pii_heuristics():
    assert contains_opt_out("Please don't call this office again")
    assert contains_opt_out("Take us off your list")
    assert not contains_opt_out("We're not taking on anything new right now")
    assert detect_patient_data("my patient John, date of birth 03/04/1980") != ""
    assert detect_patient_data("GLP-1 follow-ups are a strain") == ""


def test_masking():
    assert mask_phone("+16175550101").endswith("0101") and "555" not in mask_phone("+16175550101")
    assert mask_email("ppatel@example.test") == "p•••@example.test"
    assert "[phone]" in redact("call me at +1 617 555 0101 tomorrow")
    assert "[email]" in redact("email ppatel@example.test")


def test_export_contains_no_raw_phone_numbers(ran: Env):
    import json

    for path in ran.cfg.export_dir.glob("*.json"):
        text = path.read_text()
        assert "+16175550101" not in text, path
        assert "ppatel@example.test" not in text, path
        json.loads(text)
