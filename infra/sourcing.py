"""Sourcing stage (SRC-*). v0 ships one adapter: a CSV seed (SRC-001).

Web and directory adapters plug into the same SourceAdapter shape later (SRC-005).
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Protocol

from .clock import Clock, iso
from .models import Channel, Contact, ContactStatus, Suppression
from .store import Store
from .verify import normalize_e164


def short_hash(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def norm_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"^(dr\.?|doctor)\s+", "", value)
    value = re.sub(r"[^a-z0-9 ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


class SourceAdapter(Protocol):
    name: str

    def fetch(self) -> list[dict]: ...


class CsvSeedAdapter:
    """SRC-001: a CSV of doctors. Column names follow seeds/test_doctors.csv."""

    name = "csv_seed"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self) -> list[dict]:
        if self.path.suffix.lower() == ".xlsx":  # the practice sheet is a seed too (same row shape)
            return SheetSeedAdapter(self.path).fetch()
        with self.path.open(newline="") as fh:
            rows = []
            for row in csv.DictReader(fh):
                row = {key: (value or "").strip() for key, value in row.items() if key}
                row["source"] = row.get("source") or f"csv:{self.path.name}"  # SRC-003: keep the upstream source
                row["source_url"] = row.get("source_url") or str(self.path)
                rows.append(row)
            return rows


class SheetSeedAdapter:
    """SRC-001 from the practice workbook (data/practices.xlsx). Rows look exactly like CsvSeedAdapter's.

    `name` stays "csv_seed" on purpose: contact ids derive from (adapter name, external_id), so a practice that
    first arrived through a CSV keeps its id when the sheet becomes the source of truth.
    """

    name = "csv_seed"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def fetch(self) -> list[dict]:
        from .sheet import read_sheet_rows

        rows = []
        for sheet_row in read_sheet_rows(self.path):
            row = {
                "external_id": sheet_row.get("external_id", ""),
                "name": sheet_row.get("doctor_name", ""),
                "credentials": sheet_row.get("credentials", ""),
                "specialty": sheet_row.get("specialty", ""),
                "practice_name": sheet_row.get("practice_name", ""),
                "practice_size": sheet_row.get("practice_size", ""),
                "address": sheet_row.get("address", ""),
                "city": sheet_row.get("city", ""),
                "state": sheet_row.get("state", ""),
                "phone": sheet_row.get("phone", ""),
                "email": sheet_row.get("email", ""),
                "time_zone": sheet_row.get("time_zone", "") or "America/New_York",
                "line_type": "",
                "pronunciation_hint": sheet_row.get("pronunciation_hint", ""),
                "hook": sheet_row.get("hook", ""),
                "website": sheet_row.get("website", ""),
                "scenario": "",
                "npi": sheet_row.get("npi", ""),
                "source_tag": sheet_row.get("source_tag", ""),
                "source": sheet_row.get("source", "") or f"sheet:{self.path.name}",
                "source_url": sheet_row.get("source_url", "") or str(self.path),
                "retrieved_at": sheet_row.get("retrieved_at", ""),
            }
            if not row["external_id"] and not (row["practice_name"] or row["name"]):
                continue
            rows.append(row)
        return rows


def contact_id_for(adapter_name: str, row: dict) -> str:
    external_id = row.get("external_id", "")
    if external_id:
        return "c_" + short_hash(adapter_name, external_id)
    return "c_" + short_hash(
        norm_text(row.get("name", "")),
        norm_text(row.get("practice_name", "")),
        row.get("phone", ""),
        row.get("email", ""),
    )


def ingest(store: Store, adapter: SourceAdapter, clock: Clock, default_tz: str) -> dict:
    """Idempotent (SRC-007): re-running the same seed creates nothing new."""
    now = iso(clock.now())
    counts = {"rows": 0, "contacts_created": 0, "contacts_updated": 0, "channels_created": 0}
    for row in adapter.fetch():
        counts["rows"] += 1
        cid = contact_id_for(adapter.name, row)
        fields = {
            "full_name": row.get("name", ""),
            "credentials": row.get("credentials", ""),
            "specialty": row.get("specialty", ""),
            "practice_name": row.get("practice_name", ""),
            "practice_size": row.get("practice_size", ""),
            "address": row.get("address", ""),
            "city": row.get("city", ""),
            "state": row.get("state", ""),
            "time_zone": row.get("time_zone") or default_tz,
            "external_id": row.get("external_id", ""),
            "lead_source": row["source"],
            "pronunciation_hint": row.get("pronunciation_hint", ""),
            "hook": row.get("hook", ""),
            "website": row.get("website", ""),
            "source_tag": row.get("source_tag", "") or ("dummy" if row.get("external_id", "").startswith("t_") else ""),
            "test_scenario": row.get("scenario", ""),
        }
        contact = store.contacts.get(cid)
        if contact is None:
            contact = Contact(id=cid, status=ContactStatus.SOURCED, created_at=now, updated_at=now, **fields)
            store.add("contacts", contact, actor=adapter.name)
            counts["contacts_created"] += 1
        else:
            changed = {k: v for k, v in fields.items() if getattr(contact, k) != v}
            if changed:
                store.update("contacts", contact, "re-sourced", changed, actor=adapter.name)
                counts["contacts_updated"] += 1

        for kind, raw in (("phone", row.get("phone", "")), ("email", row.get("email", ""))):
            if not raw:
                continue
            ch_id = "ch_" + short_hash(cid, kind, raw)
            if ch_id in store.channels:
                continue
            channel = Channel(
                id=ch_id,
                contact_id=cid,
                type=kind,
                value=raw,
                raw_value=raw,
                line_type=row.get("line_type") or "unknown",
                source=row["source"],
                source_url=row["source_url"],
                retrieved_at=now,  # SRC-003: retrieval timestamp per field
            )
            store.add("channels", channel, actor=adapter.name)
            counts["channels_created"] += 1
    return counts


def load_suppressions(store: Store, path: str | Path, clock: Clock) -> int:
    """Seeded do-not-contact entries (VER-006). Idempotent."""
    path = Path(path)
    if not path.exists():
        return 0
    added = 0
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            kind = (row.get("identifier_type") or "phone").strip()
            identifier = (row.get("identifier") or "").strip()
            if kind == "phone":
                identifier = normalize_e164(identifier) or identifier
            if not identifier:
                continue
            sid = "sup_" + short_hash(kind, identifier)
            if sid in store.suppressions:
                continue
            store.add(
                "suppressions",
                Suppression(
                    id=sid,
                    identifier=identifier,
                    identifier_type=kind,
                    reason=(row.get("reason") or "").strip(),
                    source=(row.get("source") or str(path)).strip(),
                    created_at=iso(clock.now()),
                ),
                actor="seed",
            )
            added += 1
    return added


def load_clearances(path: str | Path) -> dict[str, dict]:
    """CMP-003: the authorized clearance list. Keyed by E.164 phone or lowercased email (`identifier` or `phone`)."""
    path = Path(path)
    if not path.exists():
        return {}
    out = {}
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("identifier") or row.get("phone") or "").strip()
            if not raw:
                continue
            key = raw.lower() if "@" in raw else normalize_e164(raw)
            if key:
                out[key] = {k: (v or "").strip() for k, v in row.items()}
    return out


# ---------------------------------------------------------------------------------------------------------------
# CMS NPI Registry adapter (brief step 1): independent family and internal medicine practices in Greater Boston.
# Public, free, no scraping: https://npiregistry.cms.hhs.gov/api-page . We query organizations (NPI-2) so the
# practice name, its published business line and the authorized official (usually the lead physician) come
# straight from the record. Independence and GLP-1 relevance still need the website check and Anirudh's approval.
# ---------------------------------------------------------------------------------------------------------------
NPI_API = "https://npiregistry.cms.hhs.gov/api/"
NPI_USER_AGENT = "between-visits-infra/0.1 (+https://github.com/UmaSanskriti/between-visits-workflow)"
GREATER_BOSTON = [
    "Boston",
    "Cambridge",
    "Brookline",
    "Somerville",
    "Newton",
    "Quincy",
    "Medford",
    "Arlington",
    "Watertown",
    "Waltham",
    "Belmont",
    "Chelsea",
    "Everett",
    "Malden",
    "Revere",
    "Dedham",
    "Needham",
    "Wellesley",
    "Milton",
    "Braintree",
    "Lexington",
    "Winchester",
    "Melrose",
    "Jamaica Plain",
    "Dorchester",
    "Roxbury",
    "Allston",
    "Brighton",
    "Charlestown",
    "Hyde Park",
    "Roslindale",
    "West Roxbury",
    "Mattapan",
    "East Boston",
    "South Boston",
]
PRIMARY_CARE_TAXONOMIES = ["Family Medicine", "Internal Medicine"]
# Organizations that are not independent practices (hospital- or system-owned, or not a practice at all).
SYSTEM_KEYWORDS = [
    "harvard",
    "faculty",
    "beth is",
    "physicians at",
    "physicians of",
    "medical faculty",
    "hospital",
    "health system",
    "medical center",
    "mass general",
    "brigham",
    "beth israel",
    "lahey",
    "tufts",
    "atrius",
    "harvard vanguard",
    "steward",
    "boston medical",
    "cambridge health alliance",
    "health alliance",
    "community health",
    "neighborhood health",
    "university",
    "college",
    "veterans",
    " va ",
    "urgent care",
    "counseling",
    "behavioral",
    "psych",
    "rehab",
    "nursing",
    "hospice",
    "home health",
    "laboratory",
    "pharmacy",
    "insurance",
    "health plan",
    "dialysis",
    "imaging",
    "radiology",
    "ambulance",
    "foundation",
    "church",
]
CLINICIAN_CREDENTIALS = ("MD", "DO", "NP", "PA", "M.D.", "D.O.")


KEEP_UPPER = {"PC", "PLLC", "LLC", "LLP", "INC", "MD", "DO", "PA", "NP", "II", "III", "IV", "DBA", "LTD", "CO"}
TOLL_FREE_PREFIXES = ("+1800", "+1888", "+1877", "+1866", "+1855", "+1844", "+1833")
CORPORATE_TITLES = (
    "managed care",
    "payor",
    "payer",
    "chief",
    "ceo",
    "cfo",
    "coo",
    "svp",
    "vp",
    "vice president",
    "billing",
    "credential",
)


def _title(value: str) -> str:
    out = []
    for w in (value or "").strip().split():
        bare = w.strip(".,")
        out.append(w if bare.upper() in KEEP_UPPER else w.capitalize())
    return " ".join(out)


class NpiRegistryAdapter:
    """SRC-005 adapter. `transport(url) -> dict` can be swapped for tests."""

    name = "npi_registry"

    def __init__(
        self,
        cities=None,
        taxonomies=None,
        state: str = "MA",
        page_size: int = 200,
        max_rows: int = 0,
        transport=None,
        sleep=time.sleep,
        log=print,
    ):
        self.cities = list(cities or GREATER_BOSTON)
        self.taxonomies = list(taxonomies or PRIMARY_CARE_TAXONOMIES)
        self.state = state
        self.page_size = min(max(page_size, 1), 200)
        self.max_rows = max_rows
        self.transport = transport or self._http
        self.sleep = sleep
        self.log = log
        self.skipped: dict[str, int] = {}

    def _http(self, url: str) -> dict:
        req = urllib.request.Request(url, headers={"User-Agent": NPI_USER_AGENT, "Accept": "application/json"})
        for attempt in range(3):  # SRC-008: back off on errors
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode() or "{}")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                if attempt == 2:
                    raise
                self.log(f"npi: {exc}; retrying")
                self.sleep(2.0 * (attempt + 1))
        return {}

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def fetch(self) -> list[dict]:
        rows: list[dict] = []
        seen: set[str] = set()
        city_set = {c.lower() for c in self.cities}
        retrieved_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for taxonomy in self.taxonomies:
            for city in self.cities:
                skip = 0
                while True:
                    params = {
                        "version": "2.1",
                        "enumeration_type": "NPI-2",
                        "taxonomy_description": taxonomy,
                        "city": city,
                        "state": self.state,
                        "limit": self.page_size,
                        "skip": skip,
                    }
                    url = NPI_API + "?" + urllib.parse.urlencode(params)
                    data = self.transport(url)
                    results = data.get("results") or []
                    for r in results:
                        row = self._row(r, taxonomy, city_set, url, retrieved_at)
                        if row is None:
                            continue
                        key = row["phone"] + "|" + row["practice_name"].lower()
                        if key in seen:
                            self._skip("duplicate")
                            continue
                        seen.add(key)
                        rows.append(row)
                        if self.max_rows and len(rows) >= self.max_rows:
                            return rows
                    if len(results) < self.page_size:
                        break
                    skip += self.page_size
                    self.sleep(0.5)  # SRC-008: polite paging
                self.sleep(0.2)
        return rows

    def _row(self, r: dict, taxonomy: str, city_set: set[str], url: str, retrieved_at: str) -> dict | None:
        basic = r.get("basic") or {}
        org = (basic.get("organization_name") or "").strip()
        if not org:
            self._skip("no_org_name")
            return None
        loc = next((a for a in r.get("addresses") or [] if a.get("address_purpose") == "LOCATION"), None)
        if not loc or (loc.get("state") or "").upper() != self.state.upper():
            self._skip("location_outside_state")
            return None
        city = _title(loc.get("city") or "")
        if city.lower() not in city_set:
            self._skip("location_outside_area")  # the API's city filter also matches mailing addresses
            return None
        primary = (
            next((t for t in r.get("taxonomies") or [] if t.get("primary")), None) or (r.get("taxonomies") or [{}])[0]
        )
        specialty = (primary.get("desc") or "").strip()
        if specialty.lower() not in {t.lower() for t in self.taxonomies}:
            self._skip("primary_taxonomy_other")
            return None
        lowered = " " + org.lower() + " "
        if any(k in lowered for k in SYSTEM_KEYWORDS):
            self._skip("not_independent_or_not_a_practice")
            return None
        phone = normalize_e164(loc.get("telephone_number") or "")
        if not phone:
            self._skip("no_phone")
            return None
        if phone.startswith(TOLL_FREE_PREFIXES):
            self._skip("toll_free_number")  # a corporate line, not a practice's published main line
            return None
        first = _title(basic.get("authorized_official_first_name") or "")
        last = _title(basic.get("authorized_official_last_name") or "")
        cred = (basic.get("authorized_official_credential") or "").replace(".", "").upper().strip()
        title = (basic.get("authorized_official_title_or_position") or "").strip()
        if any(k in title.lower() for k in CORPORATE_TITLES):
            self._skip("corporate_official")  # a managed-care or finance officer signals a larger organization
            return None
        official_is_clinician = any(cred.startswith(c.replace(".", "")) for c in CLINICIAN_CREDENTIALS)
        return {
            "external_id": f"npi:{r.get('number')}",
            "name": f"{first} {last}".strip() if official_is_clinician else "",
            "credentials": cred if official_is_clinician else "",
            "specialty": specialty.lower(),
            "practice_name": _title(org),
            "practice_size": "",
            "address": _title(loc.get("address_1") or ""),
            "city": city,
            "state": self.state.upper(),
            "phone": phone,
            "email": "",
            "time_zone": "America/New_York",
            "line_type": "",
            "pronunciation_hint": "",
            "hook": "",
            "scenario": "",
            "source": "npi_registry",
            "source_url": url,
            "retrieved_at": retrieved_at,
            "npi": str(r.get("number") or ""),
            "contact_name": f"{first} {last}".strip(),
            "contact_title": title,
            "independent_guess": "unknown",  # website check (brief step 1) and Anirudh's approval decide
        }


SOURCED_COLUMNS = [
    "external_id",
    "name",
    "credentials",
    "specialty",
    "practice_name",
    "practice_size",
    "address",
    "city",
    "state",
    "phone",
    "email",
    "time_zone",
    "line_type",
    "pronunciation_hint",
    "hook",
    "scenario",
    "source",
    "source_url",
    "retrieved_at",
    "npi",
    "contact_name",
    "contact_title",
    "independent_guess",
]


def write_sourced_csv(rows: list[dict], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SOURCED_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path
