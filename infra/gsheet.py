"""Google Sheet backend for the practice database (Sheets API v4, service account, stdlib HTTP).

`google-auth` only signs the JWT; every HTTP call goes through `transport(method, url, body, headers)` so the
API surface is explicit and tests can capture it. The sync rules live in infra/sheet.py; this module only knows
how to read and write a grid.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Protocol

SCOPE = "https://www.googleapis.com/auth/spreadsheets"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets"
USER_AGENT = "between-visits-infra/0.1 (+https://github.com/UmaSanskriti/between-visits-workflow)"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class GoogleSheetError(Exception):
    pass


class SheetBackend(Protocol):
    """What infra/sheet.py needs from any storage of the practice grid."""

    name: str
    sidecar: Path

    def exists(self) -> bool: ...

    def read_rows(self) -> list[dict]: ...

    def write_rows(self, rows: list[dict]) -> int: ...

    def write_back_ids(self, assignments: dict[int, str]) -> None: ...

    def fingerprint(self) -> str: ...

    def describe(self) -> str: ...


def column_letter(index: int) -> str:
    """1 -> A, 27 -> AA."""
    out = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def service_account_info(key_file: str | Path | None) -> dict:
    if not key_file:
        raise GoogleSheetError("GOOGLE_SERVICE_ACCOUNT_FILE is not set (path to the service-account JSON key)")
    path = Path(key_file).expanduser()
    if not path.exists():
        raise GoogleSheetError(f"GOOGLE_SERVICE_ACCOUNT_FILE points to a missing file: {path}")
    info = json.loads(path.read_text())
    for field in ("client_email", "private_key"):
        if field not in info:
            raise GoogleSheetError(f"service-account key file is missing {field!r}")
    return info


def default_transport(method: str, url: str, body: bytes | None, headers: dict) -> tuple[int, object]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "null"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode(errors="replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"error": {"message": raw[:300]}}


def mint_access_token(info: dict, transport=default_transport, now: float | None = None) -> tuple[str, float]:
    """Sign a JWT with the service-account key and swap it for a bearer token. Returns (token, expires_at)."""
    from google.auth import crypt, jwt

    issued = int(now if now is not None else time.time())
    signer = crypt.RSASigner.from_service_account_info(info)
    assertion = jwt.encode(
        signer,
        {"iss": info["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": issued, "exp": issued + 3600},
    )
    body = urllib.parse.urlencode(
        {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion.decode()}
    ).encode()
    status, payload = transport(
        "POST", TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT}
    )
    if status >= 400 or not isinstance(payload, dict) or "access_token" not in payload:
        raise GoogleSheetError(f"token request failed ({status}): {payload}")
    return payload["access_token"], issued + int(payload.get("expires_in", 3600)) - 60


class GoogleSheetBackend:
    name = "google"

    def __init__(
        self,
        spreadsheet_id: str,
        key_file: str | Path | None,
        *,
        sidecar: Path,
        columns: list[str],
        tab: str = "practices",
        transport=None,
        token_provider=None,
        sleep=time.sleep,
        log=print,
    ):
        if not spreadsheet_id:
            raise GoogleSheetError("GOOGLE_SHEET_ID is not set (the long id in the spreadsheet URL)")
        self.spreadsheet_id = spreadsheet_id
        self.key_file = key_file
        self.sidecar = Path(sidecar)
        self.columns = list(columns)
        self.tab = tab
        self.transport = transport or default_transport
        self.token_provider = token_provider
        self.sleep = sleep
        self.log = log
        self._info: dict | None = None
        self._token: str = ""
        self._token_expires: float = 0.0
        self._tab_checked = False

    # ---- identity ----
    @property
    def url(self) -> str:
        return f"https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"

    def describe(self) -> str:
        return self.url

    @property
    def client_email(self) -> str:
        if self.token_provider is not None and self.key_file is None:
            return ""
        return self._load_info().get("client_email", "")

    def _load_info(self) -> dict:
        if self._info is None:
            self._info = service_account_info(self.key_file)
        return self._info

    def _bearer(self) -> str:
        if self.token_provider is not None:
            return self.token_provider()
        if not self._token or time.time() >= self._token_expires:
            self._token, self._token_expires = mint_access_token(self._load_info(), self.transport)
        return self._token

    # ---- HTTP ----
    def _request(self, method: str, path: str, body: dict | None = None, params: dict | None = None):
        url = f"{API}/{self.spreadsheet_id}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(4):
            headers = {
                "Authorization": f"Bearer {self._bearer()}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
            if data is not None:
                headers["Content-Type"] = "application/json"
            status, payload = self.transport(method, url, data, headers)
            if status in RETRY_STATUSES and attempt < 3:
                self.sleep(1.5 * (2**attempt))
                continue
            if status == 403:
                who = self.client_email or "the service account"
                raise GoogleSheetError(
                    f"Google Sheets returned 403 for {self.url}: share the spreadsheet with {who} as Editor "
                    f"(and make sure the Google Sheets API is enabled for its project)"
                )
            if status == 404:
                raise GoogleSheetError(f"spreadsheet {self.spreadsheet_id} not found (check GOOGLE_SHEET_ID)")
            if status >= 400:
                message = payload.get("error", {}).get("message") if isinstance(payload, dict) else payload
                raise GoogleSheetError(f"{method} {path} -> {status}: {message}")
            return payload
        raise GoogleSheetError(f"{method} {path}: gave up after retries")

    # ---- grid ----
    def _range(self, first_row: int = 1, last_row: int | None = None) -> str:
        last_col = column_letter(len(self.columns))
        tail = f"{last_col}{last_row}" if last_row else last_col
        return f"{self.tab}!A{first_row}:{tail}"

    def ensure_tab(self, create: bool = True) -> bool:
        """True when the tab exists (creating it with a frozen header row when asked)."""
        meta = self._request("GET", "", params={"fields": "sheets.properties"})
        titles = [s.get("properties", {}).get("title") for s in (meta or {}).get("sheets", [])]
        if self.tab in titles:
            self._tab_checked = True
            return True
        if not create:
            return False
        self._request(
            "POST",
            ":batchUpdate",
            {
                "requests": [
                    {
                        "addSheet": {
                            "properties": {
                                "title": self.tab,
                                "gridProperties": {"frozenRowCount": 1, "columnCount": max(len(self.columns), 26)},
                            }
                        }
                    }
                ]
            },
        )
        self.log(f"gsheet: created tab {self.tab!r} in {self.url}")
        self._tab_checked = True
        return True

    def _values(self) -> list[list[str]]:
        if not self._tab_checked and not self.ensure_tab(create=False):
            return []
        payload = self._request("GET", f"/values/{urllib.parse.quote(self._range())}")
        return [[str(v) if v is not None else "" for v in row] for row in (payload or {}).get("values", [])]

    def exists(self) -> bool:
        try:
            values = self._values()
        except GoogleSheetError:
            raise
        return bool(values) and any(cell.strip() for cell in values[0])

    def read_rows(self) -> list[dict]:
        values = self._values()
        if not values:
            return []
        header = [cell.strip() for cell in values[0]]
        rows: list[dict] = []
        for raw in values[1:]:
            if not any(cell.strip() for cell in raw):
                continue
            padded = raw + [""] * (len(header) - len(raw))
            rows.append({header[j]: padded[j].strip() for j in range(len(header)) if header[j]})
        return rows

    def write_rows(self, rows: list[dict]) -> int:
        self.ensure_tab(create=True)
        values = [list(self.columns)] + [[str(row.get(col, "") or "") for col in self.columns] for row in rows]
        self._request("POST", f"/values/{urllib.parse.quote(self._range())}:clear", {})
        self._request(
            "PUT",
            f"/values/{urllib.parse.quote(self._range(1, len(values)))}",
            {"range": self._range(1, len(values)), "majorDimension": "ROWS", "values": values},
            params={"valueInputOption": "RAW"},
        )
        return len(rows)

    def write_back_ids(self, assignments: dict[int, str]) -> None:
        if not assignments:
            return
        col = column_letter(self.columns.index("external_id") + 1)
        self._request(
            "POST",
            "/values:batchUpdate",
            {
                "valueInputOption": "RAW",
                "data": [
                    {"range": f"{self.tab}!{col}{row_idx}", "majorDimension": "ROWS", "values": [[external_id]]}
                    for row_idx, external_id in sorted(assignments.items())
                ],
            },
        )

    def fingerprint(self) -> str:
        values = self._values()
        if not values:
            return ""
        return hashlib.sha1(json.dumps(values, separators=(",", ":")).encode()).hexdigest()[:16]
