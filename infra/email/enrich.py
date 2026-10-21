"""Practice research (brief step 2): what we would normally find online, stored and used to personalise.

Two sources: a seed fixture (`seeds/enrichment.csv`) standing in for a web lookup during the dry run, and a
polite web enricher for contacts with a known website (robots.txt respected, one request per second, same
origin only, a handful of pages). Both produce the same `Enrichment` record.
"""

from __future__ import annotations

import csv
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path
from typing import Protocol

from ..clock import Clock, iso
from ..models import Contact, Enrichment
from ..store import Store

USER_AGENT = "between-visits-infra/0.1 (+https://github.com/UmaSanskriti/between-visits-workflow)"
PAGE_HINTS = (
    "about",
    "services",
    "team",
    "providers",
    "weight",
    "glp",
    "nutrition",
    "contact",
    "staff",
    "appointments",
    "new-patients",
    "newpatients",
    "patient",
)
SERVICE_KEYWORDS = [
    "glp-1",
    "semaglutide",
    "tirzepatide",
    "weight management",
    "weight loss",
    "nutrition",
    "dietitian",
    "telehealth",
    "primary care",
    "chronic care",
    "diabetes",
    "obesity medicine",
]
GLP1_TERMS = ("glp-1", "glp1", "semaglutide", "tirzepatide", "wegovy", "ozempic", "zepbound", "mounjaro")
WEIGHT_TERMS = (
    "weight management",
    "weight-management",
    "weight loss",
    "weight-loss",
    "obesity medicine",
    "medical weight",
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
JUNK_EMAIL = (
    "example.com",
    "sentry",
    "wixpress",
    "godaddy",
    "noreply",
    "no-reply",
    "donotreply",
    "@2x",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    "yourdomain",
    "email.com",
    "domain.com",
)


def extract_emails(html: str, text: str, links: list[str], page_url: str) -> list[dict]:
    """mailto: links first, then addresses in the text; junk and image names dropped."""
    found: list[dict] = []
    seen: set[str] = set()

    def keep(address: str, kind: str) -> None:
        addr = address.strip().strip(".,;:()<>[]\"'").lower()
        if not addr or "@" not in addr or any(j in addr for j in JUNK_EMAIL) or addr in seen:
            return
        if not EMAIL_RE.fullmatch(addr):
            return
        seen.add(addr)
        found.append({"address": addr, "page_url": page_url, "kind": kind})

    for href in links:
        if href.lower().startswith("mailto:"):
            keep(href[7:].split("?")[0], "mailto")
    for match in EMAIL_RE.findall(text):
        keep(match, "text")
    for match in EMAIL_RE.findall(html or ""):  # addresses hidden in attributes or scripts
        keep(match, "text")
    return found


def extract_phones(text: str) -> list[str]:
    out: list[str] = []
    for raw in PHONE_RE.findall(text or ""):
        digits = re.sub(r"\D", "", raw)
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) == 10 and digits[0] not in "01" and ("+1" + digits) not in out:
            out.append("+1" + digits)
    return out


class Enricher(Protocol):
    name: str

    def enrich(self, contact: Contact, channels: list) -> Enrichment | None: ...


def _yes(value: str) -> bool:
    return (value or "").strip().lower() in ("yes", "y", "true", "1")


class SeedEnricher:
    """Fixture keyed by external_id: stands in for the website lookup while the list is fictional."""

    name = "seed"

    def __init__(self, path: str | Path, clock: Clock):
        self.path = Path(path)
        self.clock = clock
        self.rows: dict[str, dict] = {}
        if self.path.exists():
            with self.path.open(newline="") as fh:
                for row in csv.DictReader(fh):
                    row = {k: (v or "").strip() for k, v in row.items() if k}
                    if row.get("external_id"):
                        self.rows[row["external_id"]] = row

    def enrich(self, contact: Contact, channels: list) -> Enrichment | None:
        row = self.rows.get(contact.external_id)
        if not row:
            return None
        services = [s.strip() for s in (row.get("services") or "").split(";") if s.strip()]
        return Enrichment(
            id=contact.id,
            contact_id=contact.id,
            source="seed",
            website=row.get("website", ""),
            summary=row.get("summary", ""),
            services=services,
            glp1_mentioned=_yes(row.get("glp1_mentioned", "")),
            weight_management_mentioned=_yes(row.get("weight_management_mentioned", "")),
            doctor_bio=row.get("doctor_bio", ""),
            patient_focus=row.get("patient_focus", ""),
            recent_note=row.get("recent_note", ""),
            languages=row.get("languages", ""),
            hook=row.get("hook", ""),
            source_note=row.get("source_note") or "seed fixture standing in for a web lookup",
            retrieved_at=iso(self.clock.now()),
            confidence="medium",
        )


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4", "div", "section", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, list[str]]:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - malformed HTML must not break enrichment
        pass
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip(), parser.links


def _sentences(text: str) -> list[str]:
    out = []
    for raw in re.split(r"(?<=[.!?])\s+|\n", text or ""):
        s = raw.strip()
        if 25 <= len(s) <= 300 and not s.lower().startswith(("cookie", "©", "copyright", "skip to")):
            out.append(s)
    return out


def _phrase_around(text: str, terms: tuple[str, ...], width: int = 70) -> str:
    lowered = text.lower()
    for term in terms:
        i = lowered.find(term)
        if i >= 0:
            start = max(0, lowered.rfind(" ", 0, max(0, i - width)) + 1)
            end = lowered.find(" ", min(len(text), i + len(term) + width))
            end = len(text) if end < 0 else end
            return re.sub(r"\s+", " ", text[start:end]).strip(" .,;:")
    return ""


def default_fetcher(url: str, timeout: int = 10) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if "html" not in (resp.headers.get("Content-Type") or "text/html"):
            return ""
        return resp.read(600_000).decode("utf-8", errors="replace")


class WebEnricher:
    """Fetches the homepage plus up to `max_pages` same-origin pages that look relevant. `fetcher(url) -> html`."""

    name = "web"

    def __init__(self, clock: Clock, fetcher=None, sleep=time.sleep, max_pages: int = 3, log=print):
        self.clock = clock
        self.fetcher = fetcher or default_fetcher
        self.sleep = sleep
        self.max_pages = max_pages
        self.log = log

    def _allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        robots = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse((self.fetcher(robots) or "").splitlines())
        except Exception:  # noqa: BLE001 - no robots.txt means no restriction
            return True
        return rp.can_fetch(USER_AGENT, url)

    def enrich(self, contact: Contact, channels: list) -> Enrichment | None:
        website = (contact.website or "").strip()
        if not website:
            return None
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
        base = urllib.parse.urlsplit(website)
        if not self._allowed(website):
            self.log(f"enrich: robots.txt disallows {website}; skipped")
            return None
        pages: list[tuple[str, str]] = []
        try:
            home_html = self.fetcher(website)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
            self.log(f"enrich: {website}: {exc}")
            return None
        home_text, links = html_to_text(home_html)
        pages.append((website, home_text))
        emails = extract_emails(home_html, home_text, links, website)
        phones_found = extract_phones(home_text)
        chosen = []
        for href in links:
            absolute = urllib.parse.urljoin(website, href)
            parts = urllib.parse.urlsplit(absolute)
            if parts.netloc != base.netloc or absolute in chosen or absolute.rstrip("/") == website.rstrip("/"):
                continue
            if any(h in parts.path.lower() for h in PAGE_HINTS):
                chosen.append(absolute)
            if len(chosen) >= self.max_pages:
                break
        for url in chosen:
            self.sleep(1.0)  # SRC-008: polite crawl, one request per second
            if not self._allowed(url):
                continue
            try:
                page_html = self.fetcher(url)
                text, page_links = html_to_text(page_html)
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
                continue
            pages.append((url, text))
            for e in extract_emails(page_html, text, page_links, url):
                if all(e["address"] != x["address"] for x in emails):
                    emails.append(e)
            for ph in extract_phones(text):
                if ph not in phones_found:
                    phones_found.append(ph)
        corpus = "\n".join(t for _, t in pages)
        lowered = corpus.lower()
        services = [k for k in SERVICE_KEYWORDS if k in lowered]
        glp1 = any(t in lowered for t in GLP1_TERMS)
        weight = any(t in lowered for t in WEIGHT_TERMS)
        sentences = [x for x in _sentences(home_text) if len(x.split()) >= 8] or _sentences(home_text)
        summary = (sentences[0] if sentences else "")[:200]
        last = contact.full_name.strip().split()[-1].lower() if contact.full_name.strip() else ""
        bio = next((s for s in _sentences(corpus) if last and last in s.lower()), "")[:220]
        phrase = _phrase_around(corpus, GLP1_TERMS) or _phrase_around(corpus, WEIGHT_TERMS)
        if phrase:
            hook = f'you mention "{phrase}"'
        elif services:
            hook = "you offer " + ", ".join(services[:3])
        else:
            hook = ""
        own_domain = base.netloc.lower().removeprefix("www.")
        emails.sort(key=lambda e: 0 if e["address"].split("@")[-1].endswith(own_domain) else 1)
        npi_phones = [ch.value for ch in channels if getattr(ch, "type", "") == "phone" and ch.value]
        if not phones_found or not npi_phones:
            phone_match = "unknown"
        elif any(p in phones_found for p in npi_phones):
            phone_match = "match"
        else:
            phone_match = "mismatch"
        return Enrichment(
            id=contact.id,
            contact_id=contact.id,
            source="web",
            website=website,
            emails=emails,
            phones_found=phones_found,
            phone_match=phone_match,
            summary=summary,
            services=services,
            glp1_mentioned=glp1,
            weight_management_mentioned=weight,
            doctor_bio=bio,
            recent_note="",
            hook=hook,
            source_note=f"fetched from {website} at {iso(self.clock.now())} ({len(pages)} page(s))",
            retrieved_at=iso(self.clock.now()),
            confidence="medium" if (glp1 or weight or bio) else "low",
        )


def enrich_contacts(store: Store, clock: Clock, enrichers: list, refresh: bool = False) -> dict:
    """Idempotent: a contact is enriched once unless `refresh`. Fills `contact.hook` when the seed left it empty."""
    counts = {"enriched_seed": 0, "enriched_web": 0, "skipped": 0, "unchanged": 0}
    for contact in list(store.contacts.values()):
        if contact.id in store.enrichments and not refresh:
            counts["unchanged"] += 1
            continue
        found = None
        for enricher in enrichers:
            try:
                found = enricher.enrich(contact, store.channels_for(contact.id))
            except Exception as exc:  # noqa: BLE001 - one bad site must not stop the batch
                store.error("enrichment_failed", str(exc), contact_id=contact.id, field="website")
                found = None
            if found:
                break
        if found is None:
            counts["skipped"] += 1
            continue
        existing = store.enrichments.get(contact.id)
        if existing and existing.source == "sheet":  # edits made in practices.xlsx win; only fill blanks
            changes = {
                k: v
                for k, v in found.to_dict().items()
                if k not in ("id", "created_at", "source", "source_note", "retrieved_at", "confidence", "updated_at")
                and not getattr(existing, k)
                and v
            }
            if changes:
                store.update(
                    "enrichments",
                    existing,
                    "fill_from_" + found.source,
                    changes,
                    actor=enricher.name,
                    contact_id=contact.id,
                )
            counts["unchanged"] += 1
            if not contact.hook.strip() and (existing.hook or found.hook):
                store.update(
                    "contacts",
                    contact,
                    "hook_from_enrichment",
                    {"hook": existing.hook or found.hook},
                    actor=enricher.name,
                )
            continue
        if existing:
            changes = {
                k: v for k, v in found.to_dict().items() if k not in ("id", "created_at") and getattr(existing, k) != v
            }
            if changes:
                store.update("enrichments", existing, "refresh", changes, actor=enricher.name, contact_id=contact.id)
        else:
            store.add("enrichments", found, actor=enricher.name, contact_id=contact.id)
        counts["enriched_" + found.source] += 1
        if not contact.hook.strip() and found.hook:
            store.update("contacts", contact, "hook_from_enrichment", {"hook": found.hook}, actor=enricher.name)
    return counts
