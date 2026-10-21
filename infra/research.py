"""Research for REAL practices (brief steps 1-2): find the website, read it, extract a published email.

Everything here is polite and provenance-first: robots.txt is honoured, one request per second overall, pages are
cached for seven days, the Google Custom Search quota is capped below the free tier, and every fact we keep
records the page it came from and when (SRC-003, SRC-004, SRC-008).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .clock import Clock, iso
from .config import Config
from .email.enrich import USER_AGENT, WebEnricher
from .models import Channel, Contact, Enrichment
from .sourcing import short_hash
from .store import Store

ROOT = Path(__file__).resolve().parents[1]
CSE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"
CSE_DAILY_CAP = 95  # the free tier allows 100 queries/day; stay under it (SRC-008)
DIRECTORY_DOMAINS = (
    "healthgrades",
    "zocdoc",
    "yelp",
    "facebook",
    "instagram",
    "linkedin",
    "npino",
    "npidb",
    "doximity",
    "vitals",
    "webmd",
    "mapquest",
    "yellowpages",
    "bbb.org",
    "indeed",
    "glassdoor",
    "wellness.com",
    "sharecare",
    "caredash",
    "ratemds",
    "hipaaspace",
    "npiprofile",
    "findatopdoc",
    "usnews",
    "google.com",
    "apple.com",
    "bing.com",
    "wikipedia",
    "twitter",
    "x.com",
    "youtube",
    "tiktok",
    "cms.gov",
    "medicare.gov",
    "nppes",
)
LEGAL_SUFFIXES = ("pc", "pllc", "llc", "llp", "inc", "ltd", "md", "do", "pa", "corp", "co", "dba")


class QuotaExceeded(Exception):
    pass


@dataclass
class FinderResult:
    url: str
    source: str  # google_cse | sheet | guess
    query: str = ""
    candidates: list[dict] = field(default_factory=list)
    confidence: str = "low"  # low | medium | high


class WebsiteFinder(Protocol):
    name: str

    def find(
        self, practice_name: str, city: str, state: str, phone: str, *, last_name: str = "", website: str = ""
    ) -> FinderResult | None: ...


def _domain(url: str) -> str:
    return urllib.parse.urlsplit(url if "://" in url else "https://" + url).netloc.lower().removeprefix("www.")


def is_directory(url: str) -> bool:
    host = _domain(url)
    return any(d in host for d in DIRECTORY_DOMAINS)


def name_tokens(practice_name: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", (practice_name or "").lower())
    return [t for t in tokens if t not in LEGAL_SUFFIXES and len(t) > 2]


def practice_slug(practice_name: str) -> str:
    return "".join(name_tokens(practice_name))


# ---------------------------------------------------------------------------------------------------------------
# Finders
# ---------------------------------------------------------------------------------------------------------------
class SheetWebsiteFinder:
    """The practice sheet already knows the website (hand-entered or from an earlier run)."""

    name = "sheet"

    def find(self, practice_name, city, state, phone, *, last_name="", website="") -> FinderResult | None:
        website = (website or "").strip()
        if not website:
            return None
        if not website.startswith(("http://", "https://")):
            website = "https://" + website
        return FinderResult(
            url=website, source="sheet", query="", candidates=[{"url": website, "reason": "sheet"}], confidence="high"
        )


class CseQuota:
    """Daily counter persisted under data/research so re-runs never exceed the free tier."""

    def __init__(self, path: str | Path, clock: Clock, cap: int = CSE_DAILY_CAP):
        self.path = Path(path)
        self.clock = clock
        self.cap = cap

    def _today(self) -> str:
        return iso(self.clock.now())[:10]

    def read(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if data.get("date") == self._today():
                    return data
            except ValueError:
                pass
        return {"date": self._today(), "count": 0}

    def remaining(self) -> int:
        return max(0, self.cap - int(self.read().get("count", 0)))

    def take(self) -> None:
        data = self.read()
        if int(data.get("count", 0)) >= self.cap:
            raise QuotaExceeded(f"Google Custom Search daily cap reached ({self.cap}); try again tomorrow")
        data["count"] = int(data.get("count", 0)) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=1) + "\n")


def default_cse_transport(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode() or "{}")


class GoogleCseFinder:
    """Google Custom Search JSON API (Programmable Search Engine set to search the whole web)."""

    name = "google_cse"

    def __init__(self, key: str, cx: str, quota: CseQuota, transport=None, log=print):
        if not key or not cx:
            raise ValueError("GOOGLE_CSE_KEY and GOOGLE_CSE_CX are required for Google Custom Search")
        self.key = key
        self.cx = cx
        self.quota = quota
        self.transport = transport or default_cse_transport
        self.log = log

    def find(self, practice_name, city, state, phone, *, last_name="", website="") -> FinderResult | None:
        query = f'"{practice_name}" {city} {state}'.strip()
        self.quota.take()  # raises QuotaExceeded before any network call
        url = CSE_ENDPOINT + "?" + urllib.parse.urlencode({"key": self.key, "cx": self.cx, "q": query, "num": 10})
        data = self.transport(url)
        items = data.get("items") or []
        tokens = set(name_tokens(practice_name))
        last = (last_name or "").lower()
        candidates: list[dict] = []
        best: dict | None = None
        for item in items:
            link = item.get("link") or ""
            if not link:
                continue
            title = (item.get("title") or "").lower()
            snippet = (item.get("snippet") or "").lower()
            host = _domain(link)
            if is_directory(link):
                candidates.append({"url": link, "title": item.get("title", ""), "reason": "directory, skipped"})
                continue
            score = 0
            reasons = []
            blob = title + " " + snippet
            if tokens and all(t in blob for t in tokens):
                score += 3
                reasons.append("practice name in title/snippet")
            elif tokens and sum(t in blob for t in tokens) >= max(1, len(tokens) // 2):
                score += 2
                reasons.append("most of the practice name in title/snippet")
            if last and last in blob:
                score += 1
                reasons.append("doctor's last name in title/snippet")
            if tokens and any(t in host for t in tokens):
                score += 1
                reasons.append("practice name in the domain")
            cand = {
                "url": link,
                "title": item.get("title", ""),
                "score": score,
                "reason": "; ".join(reasons) or "no match",
            }
            candidates.append(cand)
            if score > 0 and (best is None or score > best["score"]):
                best = cand
        if best is None:
            self.log(f"research: no plausible website in search results for {practice_name!r}")
            return FinderResult(url="", source="google_cse", query=query, candidates=candidates, confidence="low")
        confidence = "medium" if best["score"] >= 3 else "low"
        return FinderResult(
            url=best["url"], source="google_cse", query=query, candidates=candidates, confidence=confidence
        )


class GuessFinder:
    """Last resort: <practice-slug>.com/.org, accepted only when the page shows the practice phone."""

    name = "guess"

    def __init__(self, fetcher, log=print):
        self.fetcher = fetcher
        self.log = log

    def find(self, practice_name, city, state, phone, *, last_name="", website="") -> FinderResult | None:
        slug = practice_slug(practice_name)
        if len(slug) < 5 or not phone:
            return None
        digits = re.sub(r"\D", "", phone)[-10:]
        tried = []
        for tld in (".com", ".org"):
            url = f"https://{slug}{tld}"
            tried.append({"url": url, "reason": "guessed"})
            try:
                html = self.fetcher(url) or ""
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
                continue
            if digits and digits in re.sub(r"\D", "", html):
                tried[-1]["reason"] = "guessed; practice phone found on the page"
                return FinderResult(url=url, source="guess", query=slug, candidates=tried, confidence="low")
        return None


class ChainFinder:
    name = "chain"

    def __init__(self, finders: list):
        self.finders = finders

    def find(self, practice_name, city, state, phone, *, last_name="", website="") -> FinderResult | None:
        last_result = None
        for finder in self.finders:
            result = finder.find(practice_name, city, state, phone, last_name=last_name, website=website)
            if result and result.url:
                return result
            last_result = result or last_result
        return last_result


# ---------------------------------------------------------------------------------------------------------------
# Fetching: cache + global throttle
# ---------------------------------------------------------------------------------------------------------------
class CachingFetcher:
    """Wraps a fetcher with a 7-day on-disk cache and a global one-request-per-second throttle (SRC-008)."""

    def __init__(
        self,
        fetcher,
        cache_dir: str | Path,
        clock: Clock,
        sleep=time.sleep,
        ttl_days: int = 7,
        min_interval: float = 1.0,
        log=print,
    ):
        self.fetcher = fetcher
        self.cache_dir = Path(cache_dir)
        self.clock = clock
        self.sleep = sleep
        self.ttl = ttl_days * 86400
        self.min_interval = min_interval
        self.log = log
        self._last = 0.0
        self.hits = 0
        self.fetches = 0

    def _path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def __call__(self, url: str) -> str:
        path = self._path(url)
        if path.exists() and (time.time() - path.stat().st_mtime) < self.ttl:
            self.hits += 1
            return path.read_text(errors="replace")
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            self.sleep(wait)
        self._last = time.monotonic()
        html = self.fetcher(url) or ""
        self.fetches += 1
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(html)
        return html


# ---------------------------------------------------------------------------------------------------------------
# The research pass
# ---------------------------------------------------------------------------------------------------------------
def _email_channel(store: Store, contact: Contact, address: str, page_url: str, now: str) -> Channel | None:
    address = address.strip().lower()
    if not address:
        return None
    ch_id = "ch_" + short_hash(contact.id, "email", address)
    if ch_id in store.channels:
        return store.channels[ch_id]
    channel = Channel(
        id=ch_id,
        contact_id=contact.id,
        type="email",
        value=address,
        raw_value=address,
        verified=False,
        source="website",
        source_url=page_url,
        retrieved_at=now,  # SRC-003: where and when we found it
    )
    store.add("channels", channel, actor="research", contact_id=contact.id)
    return channel


def research_contacts(
    store: Store,
    cfg: Config,
    clock: Clock,
    finder,
    enricher: WebEnricher,
    log=print,
    only_real: bool = True,
    max_sites: int = 0,
    refresh: bool = False,
    contact_ids: set[str] | None = None,
) -> dict:
    counts = {
        "considered": 0,
        "researched": 0,
        "website_found": 0,
        "email_found": 0,
        "phone_mismatch": 0,
        "skipped_quota": 0,
        "skipped": 0,
        "errors": 0,
    }
    quota_hit = False
    for contact in list(store.contacts.values()):
        if contact_ids and contact.id not in contact_ids and contact.external_id not in contact_ids:
            continue
        if only_real and (contact.source_tag or "") != "real":
            continue
        counts["considered"] += 1
        existing = store.enrichments.get(contact.id)
        if existing and existing.source == "web" and not refresh:
            counts["skipped"] += 1
            continue
        if max_sites and counts["researched"] >= max_sites:
            counts["skipped"] += 1
            continue
        phones = [ch.value for ch in store.channels_for(contact.id, "phone")]
        phone = phones[0] if phones else ""
        last_name = contact.full_name.strip().split()[-1] if contact.full_name.strip() else ""
        now = iso(clock.now())
        notes: list[str] = []
        result: FinderResult | None = None
        try:
            if quota_hit:
                result = SheetWebsiteFinder().find(
                    contact.practice_name,
                    contact.city,
                    contact.state,
                    phone,
                    last_name=last_name,
                    website=contact.website,
                )
                if result is None:
                    counts["skipped_quota"] += 1
                    continue
            else:
                result = finder.find(
                    contact.practice_name,
                    contact.city,
                    contact.state,
                    phone,
                    last_name=last_name,
                    website=contact.website,
                )
        except QuotaExceeded as exc:
            quota_hit = True
            log(f"research: {exc}")
            store.alert("research_quota", str(exc))
            counts["skipped_quota"] += 1
            continue
        except Exception as exc:  # noqa: BLE001 - one bad lookup must not stop the batch
            counts["errors"] += 1
            store.error("research_failed", f"website lookup: {exc}", contact_id=contact.id, field="website")
            continue
        counts["researched"] += 1
        if result is None or not result.url:
            query = result.query if result else ""
            notes.append(f"no website found{(' for query ' + query) if query else ''}")
            _record_note(store, contact, existing, notes, now, result)
            continue
        counts["website_found"] += 1
        if contact.website != result.url:
            store.update(
                "contacts", contact, "website_found", {"website": result.url}, actor="research", contact_id=contact.id
            )
        notes.append(f"website {result.url} via {result.source} ({result.confidence} confidence)")
        try:
            found = enricher.enrich(contact, store.channels_for(contact.id))
        except Exception as exc:  # noqa: BLE001
            counts["errors"] += 1
            store.error("research_failed", f"fetch {result.url}: {exc}", contact_id=contact.id, field="website")
            _record_note(store, contact, existing, notes + [f"fetch failed: {exc}"], now, result)
            continue
        if found is None:
            notes.append("site could not be read (robots.txt or fetch error)")
            _record_note(store, contact, existing, notes, now, result)
            continue
        found.website_source = result.source
        if found.phone_match == "mismatch":
            counts["phone_mismatch"] += 1
            notes.append("phone on the site does not match the NPI phone; low confidence")
            found.confidence = "low"
        elif found.phone_match == "match" and result.confidence != "low":
            found.confidence = "high"
        published = _pick_email(found.emails, result.url)
        if published:
            counts["email_found"] += 1
            notes.append(f"email {published['address']} published on {published['page_url']}")
            _email_channel(store, contact, published["address"], published["page_url"], now)
        else:
            notes.append("no published email on the pages read")
        found.research_note = "; ".join(notes)
        _upsert_web_enrichment(store, contact, existing, found)
        if not contact.hook.strip() and found.hook:
            store.update("contacts", contact, "hook_from_enrichment", {"hook": found.hook}, actor="research")
    return counts


def _pick_email(emails: list[dict], website: str) -> dict | None:
    if not emails:
        return None
    site = _domain(website)
    own = [e for e in emails if site and e["address"].split("@")[-1].lower().endswith(site.split(":")[0])]
    return (own or emails)[0]


def _record_note(
    store: Store, contact: Contact, existing: Enrichment | None, notes: list[str], now: str, result: FinderResult | None
) -> None:
    note = "; ".join(notes)
    if existing:
        store.update(
            "enrichments",
            existing,
            "research",
            {"research_note": note, "retrieved_at": now, "website_source": result.source if result else ""},
            actor="research",
            contact_id=contact.id,
        )
    else:
        store.add(
            "enrichments",
            Enrichment(
                id=contact.id,
                contact_id=contact.id,
                source="web",
                website=contact.website,
                research_note=note,
                source_note="research: " + note,
                retrieved_at=now,
                confidence="low",
                website_source=result.source if result else "",
                created_at=now,
                updated_at=now,
            ),
            actor="research",
            contact_id=contact.id,
        )


def _upsert_web_enrichment(store: Store, contact: Contact, existing: Enrichment | None, found: Enrichment) -> None:
    if existing is None:
        store.add("enrichments", found, actor="research", contact_id=contact.id)
        return
    data = found.to_dict()
    if existing.source == "sheet":  # hand edits in the practice sheet win; only fill blanks, always keep provenance
        changes = {
            k: v
            for k, v in data.items()
            if k not in ("id", "created_at", "source", "updated_at") and v and not getattr(existing, k)
        }
        for k in ("emails", "phones_found", "phone_match", "research_note", "website_source", "retrieved_at"):
            changes[k] = data[k]
    else:
        changes = {k: v for k, v in data.items() if k not in ("id", "created_at") and getattr(existing, k) != v}
    if changes:
        store.update("enrichments", existing, "research", changes, actor="research", contact_id=contact.id)


def approve_real(store: Store, clearances_path: str | Path, clock: Clock, cleared_by: str, log=print) -> dict:
    """Write clearances for every real contact whose email was published on the practice website."""
    import csv

    path = Path(clearances_path)
    existing: set[str] = set()
    rows: list[dict] = []
    if path.exists():
        with path.open(newline="") as fh:
            for row in csv.DictReader(fh):
                rows.append(row)
                ident = (row.get("identifier") or row.get("phone") or "").strip().lower()
                if ident:
                    existing.add(ident)
    added = []
    for contact in store.contacts.values():
        if (contact.source_tag or "") != "real":
            continue
        for ch in store.channels_for(contact.id, "email"):
            if ch.source != "website" or ch.value.lower() in existing:
                continue
            added.append(
                {
                    "identifier": ch.value.lower(),
                    "cleared_by": cleared_by,
                    "reason": "published on practice website; approved batch",
                    "cleared_at": iso(clock.now()),
                }
            )
            existing.add(ch.value.lower())
            store.audit(
                "channel",
                ch.id,
                "clearance_requested",
                {},
                {"identifier": ch.value, "by": cleared_by},
                actor=cleared_by,
                contact_id=contact.id,
            )
    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = ["identifier", "cleared_by", "reason", "cleared_at"]
        with path.open("w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                if "identifier" not in row and row.get("phone"):
                    row = {**row, "identifier": row["phone"]}
                writer.writerow({k: row.get(k, "") for k in header})
            writer.writerows(added)
    log(f"approve: {len(added)} website-published email(s) cleared in {path}; run `verify` (or the agent) to apply")
    return {"added": len(added), "path": str(path)}
