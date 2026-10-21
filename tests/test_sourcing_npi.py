"""NPI Registry adapter against a fake transport."""

from __future__ import annotations

import csv
from datetime import UTC

from infra.sourcing import GREATER_BOSTON, NpiRegistryAdapter, write_sourced_csv


def org(
    number,
    name,
    city,
    state="MA",
    phone="617-555-0199",
    taxonomy="Family Medicine",
    first="Priya",
    last="Patel",
    cred="M.D.",
    title="President",
):
    return {
        "number": number,
        "enumeration_type": "NPI-2",
        "basic": {
            "organization_name": name,
            "authorized_official_first_name": first,
            "authorized_official_last_name": last,
            "authorized_official_credential": cred,
            "authorized_official_title_or_position": title,
        },
        "addresses": [
            {"address_purpose": "MAILING", "city": "BOSTON", "state": "MA", "telephone_number": "617-555-0100"},
            {
                "address_purpose": "LOCATION",
                "address_1": "12 BEACON ST",
                "city": city,
                "state": state,
                "telephone_number": phone,
            },
        ],
        "taxonomies": [{"desc": taxonomy, "primary": True}],
    }


SAMPLE = [
    org("1000000001", "PATEL FAMILY MEDICINE PC", "BROOKLINE"),
    org("1000000002", "MASS GENERAL BRIGHAM PRIMARY CARE", "BOSTON", phone="617-555-0101"),  # system-owned
    org("1000000003", "ROMANOWSKY FAMILY MEDICINE", "WINDHAM", state="NH", phone="603-555-0102"),  # out of state
    org("1000000004", "LEE MEDICAL LLC", "FRAMINGHAM", phone="508-555-0103"),  # outside the area list
    org("1000000005", "NEW HORIZONS MEDICAL", "CAMBRIDGE", phone="617-555-0104", taxonomy="Counselor"),  # other
    org(
        "1000000006",
        "CHEN INTERNAL MEDICINE",
        "CAMBRIDGE",
        phone="617-555-0105",
        taxonomy="Internal Medicine",
        first="Wei",
        last="Chen",
        cred="DO",
    ),
    org(
        "1000000007",
        "SOMERVILLE PRIMARY CARE INC",
        "SOMERVILLE",
        phone="617-555-0106",
        first="Dana",
        last="Okafor",
        cred="",
        title="Office Manager",
    ),  # official is not a clinician: practice kept, doctor name left blank
    org("1000000001", "PATEL FAMILY MEDICINE PC", "BROOKLINE"),  # duplicate record
    org("1000000008", "CIRCLE MEDICAL CARE GROUP PA", "BOSTON", phone="888-555-0107"),  # toll-free corporate line
    org("1000000009", "DTG II PLLC", "BOSTON", phone="617-555-0108", cred="", title="SVP of Managed Care"),
    org("1000000010", "HARVARD MEDICAL FACULTY PHYSICIANS AT BETH IS", "BOSTON", phone="617-555-0109"),
]


def fake_transport(url):
    return {"result_count": len(SAMPLE), "results": SAMPLE}


def test_filters_to_independent_primary_care_in_area():
    adapter = NpiRegistryAdapter(
        cities=["Boston", "Brookline", "Cambridge", "Somerville"],
        transport=fake_transport,
        sleep=lambda s: None,
        log=lambda *a: None,
    )
    rows = adapter.fetch()
    assert [r["external_id"] for r in rows] == ["npi:1000000001", "npi:1000000006", "npi:1000000007"]
    rows = {r["external_id"]: r for r in rows}
    assert rows["npi:1000000001"]["name"] == "Priya Patel" and rows["npi:1000000001"]["credentials"] == "MD"
    assert rows["npi:1000000001"]["practice_name"] == "Patel Family Medicine PC"
    assert adapter.skipped["not_independent_or_not_a_practice"] >= 2  # counted once per query
    assert rows["npi:1000000001"]["phone"] == "+16175550199" and rows["npi:1000000001"]["city"] == "Brookline"
    assert rows["npi:1000000006"]["specialty"] == "internal medicine"
    assert rows["npi:1000000007"]["name"] == "" and rows["npi:1000000007"]["contact_title"] == "Office Manager"
    assert adapter.skipped["location_outside_state"] >= 1 and adapter.skipped["location_outside_area"] >= 1
    assert adapter.skipped["primary_taxonomy_other"] >= 1 and adapter.skipped["duplicate"] >= 1
    assert adapter.skipped["toll_free_number"] >= 1 and adapter.skipped["corporate_official"] >= 1


def test_csv_round_trips_through_ingest(tmp_path):
    from datetime import datetime

    from infra.clock import FixedClock
    from infra.sourcing import CsvSeedAdapter, ingest
    from infra.store import Store

    adapter = NpiRegistryAdapter(
        cities=["Brookline"], transport=fake_transport, sleep=lambda s: None, log=lambda *a: None
    )
    path = write_sourced_csv(adapter.fetch(), tmp_path / "batch.csv")
    with path.open() as fh:
        header = next(csv.reader(fh))
    assert "source_url" in header and "npi" in header
    store = Store(tmp_path / "data", FixedClock(datetime(2026, 10, 20, 14, tzinfo=UTC)))
    counts = ingest(store, CsvSeedAdapter(path), store.clock, "America/New_York")
    assert counts["contacts_created"] == 1
    contact = next(iter(store.contacts.values()))
    assert contact.lead_source == "npi_registry" and contact.external_id == "npi:1000000001"
    channel = store.channels_for(contact.id, "phone")[0]
    assert channel.source == "npi_registry" and channel.source_url.startswith("https://npiregistry.cms.hhs.gov/api/")


def test_default_area_and_paging():
    calls = []

    def paged(url):
        calls.append(url)
        page = [
            org(str(2000000000 + i), f"PRACTICE {i} FAMILY MEDICINE", "BOSTON", phone=f"617-555-{i:04d}")
            for i in range(200)
        ]
        return {"result_count": 200, "results": page if "skip=0" in url else []}

    adapter = NpiRegistryAdapter(
        cities=["Boston"],
        taxonomies=["Family Medicine"],
        transport=paged,
        sleep=lambda s: None,
        log=lambda *a: None,
        max_rows=5,
    )
    assert len(adapter.fetch()) == 5
    assert "Boston" in GREATER_BOSTON and len(GREATER_BOSTON) > 20
