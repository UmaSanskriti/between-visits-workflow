"""Practice research: seed and web enrichers, pipeline stage, personalised drafts, export masking."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from conftest import ROOT, Env

from infra.brain import MockBrain
from infra.clock import FixedClock
from infra.email.compose import MAX_WORDS, build_email
from infra.email.enrich import SeedEnricher, WebEnricher, html_to_text
from infra.models import Channel, Contact, Enrichment
from infra.store import Store

NOW = datetime(2026, 10, 20, 14, tzinfo=UTC)


def test_seed_enricher_reads_the_fixture_for_every_dummy_doctor():
    enricher = SeedEnricher(ROOT / "seeds" / "enrichment.csv", FixedClock(NOW))
    assert len(enricher.rows) == 21
    patel = enricher.enrich(Contact(id="c1", external_id="t_01", full_name="Priya Patel"), [])
    assert patel.source == "seed" and patel.glp1_mentioned and "GLP-1" in " ".join(patel.services)
    assert patel.hook.startswith("you ") and patel.doctor_bio and patel.confidence == "medium"
    assert enricher.enrich(Contact(id="c2", external_id="nope"), []) is None


HOME = """<html><head><title>x</title><style>p{}</style></head><body>
<nav><a href="/about">About</a><a href="/services/weight-loss">Weight</a><a href="https://other.example/x">Ext</a>
<a href="/blog">Blog</a><a href="/team">Team</a><a href="/contact">Contact</a></nav>
<h1>Maple Street Family Medicine</h1>
<p>Maple Street Family Medicine is an independent practice in Cambridge offering same-day primary care
and a medical weight management program.</p>
<script>var a=1;</script></body></html>"""
SERVICES = (
    "<html><body><p>Our weight management program includes GLP-1 therapy such as semaglutide and a dietitian."
    "</p></body></html>"
)
TEAM = "<html><body><p>Dr. Rivera trained at BU and focuses on nutrition.</p></body></html>"


def fake_site(robots="User-agent: *\nAllow: /\n"):
    calls = []

    def fetch(url, timeout=10):
        calls.append(url)
        if url.endswith("/robots.txt"):
            return robots
        if url.endswith("/services/weight-loss"):
            return SERVICES
        if url.endswith("/team"):
            return TEAM
        if url.endswith("/about"):
            return "<html><body><p>About us.</p></body></html>"
        return HOME

    return fetch, calls


def test_html_to_text_strips_scripts_and_collects_links():
    text, links = html_to_text(HOME)
    assert "var a" not in text and "Maple Street Family Medicine is an independent practice" in text
    assert "/services/weight-loss" in links and "/team" in links


def test_web_enricher_picks_relevant_same_origin_pages_and_derives_facts():
    fetch, calls = fake_site()
    enricher = WebEnricher(FixedClock(NOW), fetcher=fetch, sleep=lambda s: None, log=lambda *a: None)
    contact = Contact(
        id="c1", full_name="Ana Rivera", website="maple.example", practice_name="Maple Street Family Medicine"
    )
    e = enricher.enrich(contact, [])
    assert e.source == "web" and e.website == "https://maple.example"
    assert e.glp1_mentioned and e.weight_management_mentioned
    assert {"glp-1", "semaglutide", "weight management", "dietitian", "primary care"} <= set(e.services)
    assert e.summary.startswith("Maple Street Family Medicine is an independent practice")
    assert "Rivera" in e.doctor_bio and e.hook.startswith('you mention "')
    fetched = [c for c in calls if not c.endswith("robots.txt")]
    assert fetched[0] == "https://maple.example" and "https://other.example/x" not in fetched
    assert "https://maple.example/blog" not in fetched and "https://maple.example/contact" not in fetched
    assert len(fetched) <= 4 and "fetched from https://maple.example" in e.source_note


def test_web_enricher_respects_robots_and_missing_website():
    fetch, calls = fake_site(robots="User-agent: *\nDisallow: /\n")
    enricher = WebEnricher(FixedClock(NOW), fetcher=fetch, sleep=lambda s: None, log=lambda *a: None)
    assert enricher.enrich(Contact(id="c1", website="https://maple.example"), []) is None
    assert all(c.endswith("robots.txt") for c in calls)
    assert enricher.enrich(Contact(id="c2"), []) is None


def test_enrich_stage_is_idempotent_and_fills_the_hook(tmp_path):
    env = Env(tmp_path, channel="email")
    run, _ = env.run(stages=("ingest", "verify", "enrich"))
    counts = run["stages"]["enrich"]
    assert counts["enriched_seed"] == 21 and counts["enriched_web"] == 0
    assert len(env.store.enrichments) == 21
    t03 = env.contact("t_03")
    assert t03.hook == "you expanded the walk-in clinic this year"  # seed hook was empty, filled from research
    assert env.contact("t_01").hook == "the new GLP-1 weight-management program on your site"  # seed hook kept
    run2, _ = env.run(stages=("ingest", "verify", "enrich"))
    assert run2["stages"]["enrich"]["unchanged"] == 21 and run2["stages"]["enrich"]["enriched_seed"] == 0
    run3, _ = env.run(stages=("ingest", "verify", "enrich"), refresh_enrichment=True)
    assert run3["stages"]["enrich"]["enriched_seed"] == 21
    assert any(e["entity"] == "enrichments" for e in env.store.audit_log)


def test_drafts_are_personalised_and_stay_under_the_cap(tmp_path):
    env = Env(tmp_path, channel="email")
    env.run()
    attempts = {env.store.contacts[a.contact_id].external_id: a for a in env.store.email_attempts.values()}
    patel = attempts["t_01"]
    assert "I saw on your site that you launched a GLP-1 follow-up clinic" in patel.body
    assert "already supports GLP-1 patients" in patel.body
    assert any(p.startswith("hook:") for p in patel.personalization) and "offers GLP-1 care" in patel.personalization
    lee = attempts["t_03"]
    assert "you expanded the walk-in clinic this year" in lee.body and "already supports GLP-1" not in lee.body
    assert "Given your focus on preventive care for working adults" in lee.body
    for a in attempts.values():
        assert len(a.body.split()) <= MAX_WORDS, a.contact_id
        assert a.body.count("?") == 1


def test_compose_without_enrichment_is_unchanged():
    clock = FixedClock(NOW)
    brain = MockBrain(Store("unused", clock), clock).resolve_email({})
    contact = Contact(id="c1", full_name="Priya Patel", practice_name="Patel Family Medicine", hook="your new program")
    channel = Channel(id="ch", contact_id="c1", type="email", value="p@example.test", verified=True)
    from infra.config import Config

    cfg = Config(email_from_name="Uma", callback_number="+16175550199")
    draft, errors = build_email(contact, channel, brain, cfg, clock)
    assert not errors and "I saw your new program and wanted to reach out." in draft.body
    assert (
        draft.personalization == ["hook: your new program"]
        and "core_paragraph_shortened" not in draft.fallbacks_applied
    )
    rich = Enrichment(
        id="c1",
        contact_id="c1",
        glp1_mentioned=True,
        patient_focus="GLP-1 follow-up",
        doctor_bio="Dr. Patel trained at BMC.",
        hook="you launched a GLP-1 follow-up clinic",
    )
    draft2, errors2 = build_email(contact, channel, brain, cfg, clock, rich)
    assert not errors2 and len(draft2.body.split()) <= MAX_WORDS
    assert "core_paragraph_shortened" in draft2.fallbacks_applied  # the personalised version needed the short core
    assert draft2.personalization[0] == "hook: you launched a GLP-1 follow-up clinic"


def test_export_includes_masked_enrichments(tmp_path):
    env = Env(tmp_path, channel="email")
    env.run()
    rows = json.loads((env.cfg.export_dir / "enrichments.json").read_text())
    assert len(rows) == 21 and all("summary" in r and "services" in r for r in rows)
    summary = json.loads((env.cfg.export_dir / "summary.json").read_text())
    assert summary["enrichment"]["total"] == 21 and summary["enrichment"]["with_glp1"] >= 5
    attempts = json.loads((env.cfg.export_dir / "email_attempts.json").read_text())
    assert any(a["personalization"] for a in attempts)
