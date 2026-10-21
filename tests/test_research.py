"""Real-practice research: website discovery, published-email extraction, provenance, quota, approvals."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime

import pytest

from infra import research as rs
from infra.clock import FixedClock
from infra.config import Config
from infra.email.enrich import WebEnricher, extract_emails, extract_phones, html_to_text
from infra.models import Channel, Contact
from infra.store import Store

NOW = datetime(2026, 10, 21, 14, 0, tzinfo=UTC)

HOME = """<html><head><title>Patel Family Medicine | Brookline</title></head><body>
<h1>Patel Family Medicine</h1><p>Independent family medicine in Brookline since 2009. We now offer a GLP-1
weight-management program with nutrition counseling.</p>
<a href="/about">About</a> <a href="/contact-us">Contact</a> <a href="https://cdn.example.net/x">cdn</a>
<img src="logo@2x.png"><script>var e="sentry@sentry.wixpress.com"</script>
<p>Call (617) 555-0101</p></body></html>"""
CONTACT = """<html><body><p>Email us:
<a href="mailto:office@patelfamilymed.com?subject=hi">office@patelfamilymed.com</a>
or frontdesk@gmail.com. Phone 617-555-0101. Image: hero.jpg@2x.png</p></body></html>"""
ABOUT = "<html><body><p>Dr. Priya Patel trained at BMC and leads the practice.</p><p>info@example.com</p></body></html>"


def site_fetcher(pages: dict):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return pages.get("robots", "User-agent: *\nAllow: /\n")
        if url in pages:
            return pages[url]
        raise OSError(f"404 {url}")

    fetch.calls = calls
    return fetch


PATEL_SITE = {
    "https://patelfamilymed.com": HOME,
    "https://patelfamilymed.com/about": ABOUT,
    "https://patelfamilymed.com/contact-us": CONTACT,
}


def make_store(tmp_path, contacts):
    store = Store(tmp_path / "data", FixedClock(NOW))
    for c, phone in contacts:
        store.add("contacts", c)
        if phone:
            store.add("channels", Channel(id="ch_" + c.id, contact_id=c.id, type="phone", value=phone, verified=True))
    return store


def real_contact(ext, name, practice, city="Brookline", website=""):
    return Contact(
        id="c_" + ext,
        external_id=ext,
        full_name=name,
        practice_name=practice,
        city=city,
        state="MA",
        source_tag="real",
        lead_source="npi_registry",
        website=website,
        status="READY",
        verification_status="verified",
    )


# ---------------------------------------------------------------------------------------------------------------
def test_email_and_phone_extraction_filters_junk_and_prefers_mailto():
    text, links = html_to_text(CONTACT)
    emails = extract_emails(CONTACT, text, links, "https://patelfamilymed.com/contact-us")
    assert [e["address"] for e in emails] == ["office@patelfamilymed.com", "frontdesk@gmail.com"]
    assert emails[0]["kind"] == "mailto" and emails[0]["page_url"].endswith("/contact-us")
    text, links = html_to_text(HOME)
    assert extract_emails(HOME, text, links, "https://patelfamilymed.com") == []  # sentry/wixpress + png dropped
    assert extract_phones(text) == ["+16175550101"]


def test_web_enricher_collects_published_emails_and_verifies_phone(tmp_path):
    contact = real_contact("npi:1", "Priya Patel", "Patel Family Medicine", website="https://patelfamilymed.com")
    channels = [Channel(id="ch", contact_id=contact.id, type="phone", value="+16175550101", verified=True)]
    enricher = WebEnricher(FixedClock(NOW), fetcher=site_fetcher(PATEL_SITE), sleep=lambda s: None, log=lambda *a: None)
    en = enricher.enrich(contact, channels)
    assert en.source == "web" and en.glp1_mentioned and en.weight_management_mentioned
    assert [e["address"] for e in en.emails] == ["office@patelfamilymed.com", "frontdesk@gmail.com"]  # own domain first
    assert en.phones_found == ["+16175550101"] and en.phone_match == "match"
    assert "Priya Patel trained at BMC" in en.doctor_bio
    channels[0].value = "+16175559999"
    assert enricher.enrich(contact, channels).phone_match == "mismatch"


def test_cse_finder_ranks_practice_site_above_directories_and_counts_quota(tmp_path):
    seen = []

    def transport(url):
        seen.append(url)
        return {
            "items": [
                {
                    "link": "https://www.healthgrades.com/group/patel-family-medicine",
                    "title": "Patel Family Medicine - Healthgrades",
                    "snippet": "ratings",
                },
                {
                    "link": "https://patelfamilymed.com/",
                    "title": "Patel Family Medicine | Brookline",
                    "snippet": "Dr. Patel's independent practice",
                },
                {"link": "https://www.yelp.com/biz/patel", "title": "Patel Family Medicine - Yelp", "snippet": ""},
                {"link": "https://someblog.example/post", "title": "Brookline doctors", "snippet": "a list"},
            ]
        }

    quota = rs.CseQuota(tmp_path / "cse_quota.json", FixedClock(NOW), cap=2)
    finder = rs.GoogleCseFinder("key", "cx", quota, transport=transport, log=lambda *a: None)
    result = finder.find("Patel Family Medicine", "Brookline", "MA", "+16175550101", last_name="Patel")
    assert (
        result.url == "https://patelfamilymed.com/" and result.source == "google_cse" and result.confidence == "medium"
    )
    assert result.query == '"Patel Family Medicine" Brookline MA'
    assert [c["reason"] for c in result.candidates][0] == "directory, skipped"
    assert "cx=cx" in seen[0] and "q=%22Patel+Family+Medicine%22+Brookline+MA" in seen[0]
    assert quota.remaining() == 1
    finder.find("Other Practice", "Boston", "MA", "")
    with pytest.raises(rs.QuotaExceeded):
        finder.find("Third Practice", "Boston", "MA", "")
    assert len(seen) == 2  # the third call never hit the network
    assert json.loads((tmp_path / "cse_quota.json").read_text())["count"] == 2


def test_cse_finder_returns_no_url_when_nothing_plausible():
    finder = rs.GoogleCseFinder(
        "k",
        "c",
        rs.CseQuota("/tmp/does-not-matter.json", FixedClock(NOW)),
        transport=lambda url: {"items": [{"link": "https://www.zocdoc.com/x", "title": "x", "snippet": ""}]},
        log=lambda *a: None,
    )
    result = finder.find("Nowhere Medical", "Boston", "MA", "")
    assert result is not None and result.url == "" and result.candidates[0]["reason"] == "directory, skipped"


def test_sheet_finder_wins_and_guess_needs_phone_verification():
    chain = rs.ChainFinder([rs.SheetWebsiteFinder(), rs.GuessFinder(site_fetcher(PATEL_SITE), log=lambda *a: None)])
    sheet = chain.find("Anything", "Boston", "MA", "+16175550101", website="patelfamilymed.com")
    assert sheet.source == "sheet" and sheet.url == "https://patelfamilymed.com" and sheet.confidence == "high"
    guessed = chain.find("Patel Family Med", "Brookline", "MA", "+16175550101")
    assert guessed.source == "guess" and guessed.url == "https://patelfamilymed.com" and guessed.confidence == "low"
    assert chain.find("Patel Family Med", "Brookline", "MA", "+16175559999") is None  # phone not on the page


def test_research_contacts_end_to_end_records_provenance(tmp_path):
    patel = real_contact("npi:1", "Priya Patel", "Patel Family Medicine")
    nowhere = real_contact("npi:2", "Dana Okafor", "Okafor Family Health", city="Boston")
    dummy = Contact(
        id="c_t01", external_id="t_01", full_name="Dummy", practice_name="Dummy Practice", source_tag="dummy"
    )
    store = make_store(tmp_path, [(patel, "+16175550101"), (nowhere, "+16175550106"), (dummy, "+16175550199")])
    clock = FixedClock(NOW)

    def transport(url):
        if "Patel" in url:
            return {
                "items": [
                    {"link": "https://patelfamilymed.com", "title": "Patel Family Medicine", "snippet": "Brookline"}
                ]
            }
        return {"items": []}

    quota = rs.CseQuota(tmp_path / "q.json", clock)
    finder = rs.ChainFinder(
        [rs.SheetWebsiteFinder(), rs.GoogleCseFinder("k", "c", quota, transport=transport, log=lambda *a: None)]
    )
    fetch = site_fetcher(PATEL_SITE)
    cached = rs.CachingFetcher(fetch, tmp_path / "pages", clock, sleep=lambda s: None, log=lambda *a: None)
    enricher = WebEnricher(clock, fetcher=cached, sleep=lambda s: None, log=lambda *a: None)
    counts = rs.research_contacts(store, Config(), clock, finder, enricher, log=lambda *a: None)
    assert counts["considered"] == 2 and counts["researched"] == 2  # the dummy practice is not touched
    assert counts["website_found"] == 1 and counts["email_found"] == 1 and counts["phone_mismatch"] == 0
    assert patel.website == "https://patelfamilymed.com"
    en = store.enrichments[patel.id]
    assert en.source == "web" and en.website_source == "google_cse" and en.confidence == "high"
    assert "email office@patelfamilymed.com published on https://patelfamilymed.com/contact-us" in en.research_note
    email = next(ch for ch in store.channels_for(patel.id, "email"))
    assert email.value == "office@patelfamilymed.com" and email.source == "website"
    assert email.source_url == "https://patelfamilymed.com/contact-us" and email.retrieved_at and not email.verified
    assert email.call_clearance == "not_cleared"
    assert store.enrichments[nowhere.id].research_note.startswith("no website found")
    assert any(e["action"] == "website_found" for e in store.audit_log)
    # second pass: nothing re-fetched, nothing re-searched
    fetched_before = cached.fetches
    counts2 = rs.research_contacts(store, Config(), clock, finder, enricher, log=lambda *a: None)
    assert counts2["skipped"] >= 1 and cached.fetches == fetched_before
    assert quota.remaining() == rs.CSE_DAILY_CAP - 2


def test_quota_exhaustion_falls_back_to_sheet_websites(tmp_path):
    a = real_contact("npi:1", "A B", "Alpha Medical")
    b = real_contact("npi:2", "C D", "Beta Medical", website="https://patelfamilymed.com")
    store = make_store(tmp_path, [(a, "+16175550101"), (b, "+16175550101")])
    clock = FixedClock(NOW)
    quota = rs.CseQuota(tmp_path / "q.json", clock, cap=0)
    finder = rs.ChainFinder(
        [rs.SheetWebsiteFinder(), rs.GoogleCseFinder("k", "c", quota, transport=lambda u: {}, log=lambda *a: None)]
    )
    enricher = WebEnricher(clock, fetcher=site_fetcher(PATEL_SITE), sleep=lambda s: None, log=lambda *a: None)
    counts = rs.research_contacts(store, Config(), clock, finder, enricher, log=lambda *a: None)
    assert counts["skipped_quota"] == 1 and counts["website_found"] == 1
    assert any(x["type"] == "research_quota" for x in store.logs["alerts"])


def test_approve_real_writes_clearances_for_published_emails(tmp_path):
    patel = real_contact("npi:1", "Priya Patel", "Patel Family Medicine")
    dummy = Contact(id="c_t01", external_id="t_01", full_name="Dummy", practice_name="Dummy", source_tag="dummy")
    store = make_store(tmp_path, [(patel, "+16175550101"), (dummy, "")])
    store.add(
        "channels",
        Channel(
            id="ch_e1",
            contact_id=patel.id,
            type="email",
            value="office@patelfamilymed.com",
            source="website",
            source_url="https://patelfamilymed.com/contact-us",
        ),
    )
    store.add(
        "channels", Channel(id="ch_e2", contact_id=dummy.id, type="email", value="d@example.test", source="csv:x")
    )
    path = tmp_path / "clearances.csv"
    path.write_text("identifier,cleared_by,reason,cleared_at\n+16175550101,founders,test,2026-10-20T00:00:00Z\n")
    result = rs.approve_real(store, path, FixedClock(NOW), "Anirudh", log=lambda *a: None)
    rows = list(csv.DictReader(path.open()))
    assert result["added"] == 1 and [r["identifier"] for r in rows] == ["+16175550101", "office@patelfamilymed.com"]
    assert rows[1]["cleared_by"] == "Anirudh" and "published on practice website" in rows[1]["reason"]
    assert rs.approve_real(store, path, FixedClock(NOW), "Anirudh", log=lambda *a: None)["added"] == 0  # idempotent
    from infra.sourcing import load_clearances

    assert "office@patelfamilymed.com" in load_clearances(path)


def test_caching_fetcher_throttles_and_caches(tmp_path):
    slept = []
    calls = []

    def fetch(url):
        calls.append(url)
        return "<html>x</html>"

    cached = rs.CachingFetcher(fetch, tmp_path / "pages", FixedClock(NOW), sleep=slept.append, log=lambda *a: None)
    cached("https://a.example/")
    cached("https://b.example/")
    cached("https://a.example/")
    assert calls == ["https://a.example/", "https://b.example/"] and cached.hits == 1
    assert len(slept) >= 1 and all(0 < s <= 1.0 for s in slept)
