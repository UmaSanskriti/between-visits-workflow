"""Google Sheet backend against a fake transport: requests, round trip, tab creation, errors, selection rule."""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime

import pytest

from infra import sheet as sheetmod
from infra.clock import FixedClock
from infra.gsheet import API, GoogleSheetBackend, GoogleSheetError, column_letter
from infra.store import Store

NOW = datetime(2026, 10, 20, 14, tzinfo=UTC)


class FakeSheetsApi:
    """Enough of the Sheets API v4 to exercise the backend: one spreadsheet, optional tab, a value grid."""

    def __init__(self, tabs=("practices",), fail_status=None):
        self.tabs = list(tabs)
        self.values: list[list[str]] = []
        self.calls: list[tuple[str, str, object]] = []
        self.fail_status = fail_status

    def __call__(self, method, url, body, headers):
        payload = json.loads(body.decode()) if body else None
        self.calls.append((method, url, payload))
        assert headers["Authorization"] == "Bearer fake-token"
        if self.fail_status:
            return self.fail_status, {"error": {"message": "nope"}}
        path = url[len(API) + 1 :].split("?")[0]  # "sheet-1", "sheet-1/values/...", "sheet-1:batchUpdate"
        if method == "GET" and path == "sheet-1":
            return 200, {"sheets": [{"properties": {"title": t}} for t in self.tabs]}
        if method == "GET" and "/values/" in path:
            return 200, {"values": self.values}
        if method == "POST" and path.endswith(":clear"):
            self.values = []
            return 200, {}
        if method == "PUT" and "/values/" in path:
            self.values = [list(r) for r in payload["values"]]
            return 200, {"updatedRows": len(self.values)}
        if method == "POST" and path.endswith("/values:batchUpdate"):
            for item in payload["data"]:
                cell = item["range"].split("!")[1]
                col = ord(cell[0]) - 65
                row = int(cell[1:]) - 1
                while len(self.values) <= row:
                    self.values.append([])
                while len(self.values[row]) <= col:
                    self.values[row].append("")
                self.values[row][col] = item["values"][0][0]
            return 200, {}
        if method == "POST" and path.endswith(":batchUpdate"):
            for req in payload["requests"]:
                self.tabs.append(req["addSheet"]["properties"]["title"])
            return 200, {}
        return 404, {"error": {"message": "unknown " + method + " " + path}}


def backend(tmp_path, api):
    return GoogleSheetBackend(
        "sheet-1",
        None,
        sidecar=tmp_path / "practices.google.sheet.json",
        columns=sheetmod.COLUMNS,
        transport=api,
        token_provider=lambda: "fake-token",
        sleep=lambda s: None,
        log=lambda *a: None,
    )


def seeded_store(tmp_path):
    from conftest import CLEARANCES, SEED, SUPPRESSIONS

    from infra.brain import MockBrain
    from infra.config import load_config
    from infra.pipeline import run_pipeline
    from infra.voice import MockVoiceProvider

    cfg = load_config(
        "config.toml",
        env={"BV_OUTREACH_CHANNEL": "email", "BV_DATA_DIR": str(tmp_path / "d"), "BV_EXPORT_DIR": str(tmp_path / "e")},
    )
    clock = FixedClock(NOW)
    store = Store(cfg.data_dir, clock).load()
    run_pipeline(
        store,
        cfg,
        clock,
        MockBrain(store, clock),
        MockVoiceProvider(cfg.webhook_secret, clock),
        SEED,
        CLEARANCES,
        SUPPRESSIONS,
        stages=("ingest", "verify", "enrich"),
        trigger="test",
    )
    return store, clock


def test_column_letters():
    assert (
        column_letter(1) == "A" and column_letter(26) == "Z" and column_letter(27) == "AA" and column_letter(28) == "AB"
    )
    assert column_letter(len(sheetmod.COLUMNS)) == "AB"  # 28 columns


def test_export_import_round_trip_through_the_api(tmp_path):
    store, clock = seeded_store(tmp_path)
    api = FakeSheetsApi()
    gs = backend(tmp_path, api)
    rows = sheetmod.export_sheet(store, gs, clock)
    assert rows == len(store.contacts)
    assert api.values[0] == sheetmod.COLUMNS  # exact header, in order
    assert all(len(r) == len(sheetmod.COLUMNS) for r in api.values)
    methods = [(m, u.split("/spreadsheets/sheet-1")[1].split("?")[0]) for m, u, _ in api.calls]
    assert ("POST", "/values/" + urllib.parse.quote("practices!A1:AB") + ":clear") in methods
    put = next(c for c in api.calls if c[0] == "PUT")
    assert "valueInputOption=RAW" in put[1] and put[2]["majorDimension"] == "ROWS"
    side = sheetmod.read_sidecar(gs)
    assert side["backend"] == "google" and side["fingerprint"] == gs.fingerprint() and side["rows"] == rows
    assert not sheetmod.changed_since_export(gs)
    # edit a cell "in Google": the hook of t_03, then the conflict rule imports it before the next export
    header = api.values[0]
    row = next(r for r in api.values[1:] if r[header.index("external_id")] == "t_03")
    row[header.index("hook")] = "you opened a Saturday clinic"
    assert sheetmod.changed_since_export(gs)
    result = sheetmod.sync_sheet(store, gs, clock, log=lambda *a: None)
    assert result["imported"]["updated"] >= 1 and result["backend"] == "google"
    contact = next(c for c in store.contacts.values() if c.external_id == "t_03")
    assert contact.hook == "you opened a Saturday clinic"
    assert store.enrichments[contact.id].hook == "you opened a Saturday clinic"
    assert store.enrichments[contact.id].source == "sheet"


def test_new_row_gets_an_id_written_back_via_batch_update(tmp_path):
    store, clock = seeded_store(tmp_path)
    api = FakeSheetsApi()
    gs = backend(tmp_path, api)
    sheetmod.export_sheet(store, gs, clock)
    header = api.values[0]
    new = [""] * len(header)
    new[header.index("source_tag")] = "real"
    new[header.index("doctor_name")] = "Ada Lovelace"
    new[header.index("practice_name")] = "Lovelace Family Medicine"
    new[header.index("specialty")] = "family medicine"
    new[header.index("phone")] = "617-555-0177"
    new[header.index("email")] = "office@lovelace.example"
    api.values.append(new)
    counts = sheetmod.import_sheet(store, gs, clock, log=lambda *a: None)
    assert counts["created"] == 1
    created = next(c for c in store.contacts.values() if c.full_name == "Ada Lovelace")
    assert created.external_id.startswith("sheet:") and created.source_tag == "real"
    batch = next(c for c in api.calls if c[0] == "POST" and c[1].endswith("/values:batchUpdate"))
    assert batch[2]["data"][0]["values"] == [[created.external_id]]
    assert api.values[-1][header.index("external_id")] == created.external_id
    assert [ch.type for ch in store.channels_for(created.id)] == ["phone", "email"]
    assert all(not ch.verified for ch in store.channels_for(created.id))


def test_missing_tab_is_created_on_first_write(tmp_path):
    store, clock = seeded_store(tmp_path)
    api = FakeSheetsApi(tabs=("Sheet1",))
    gs = backend(tmp_path, api)
    assert gs.exists() is False
    sheetmod.export_sheet(store, gs, clock)
    assert "practices" in api.tabs
    add = next(c for c in api.calls if c[0] == "POST" and c[1].endswith(":batchUpdate") and "/values" not in c[1])
    assert add[2]["requests"][0]["addSheet"]["properties"]["gridProperties"]["frozenRowCount"] == 1
    assert gs.exists() is True


def test_403_names_the_service_account_to_share_with(tmp_path):
    key = tmp_path / "sa.json"
    key.write_text(json.dumps({"client_email": "bot@proj.iam.gserviceaccount.com", "private_key": "x"}))
    gs = GoogleSheetBackend(
        "sheet-1",
        key,
        sidecar=tmp_path / "s.json",
        columns=sheetmod.COLUMNS,
        transport=FakeSheetsApi(fail_status=403),
        token_provider=lambda: "fake-token",
        sleep=lambda s: None,
    )
    with pytest.raises(GoogleSheetError) as exc:
        gs.read_rows()
    assert "bot@proj.iam.gserviceaccount.com" in str(exc.value) and "Editor" in str(exc.value)


def test_retries_on_429_then_succeeds(tmp_path):
    api = FakeSheetsApi()
    api.values = [sheetmod.COLUMNS, ["dummy", "t_01"] + [""] * (len(sheetmod.COLUMNS) - 2)]
    attempts = {"n": 0}
    real = api.__call__

    def flaky(method, url, body, headers):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return 429, {"error": {"message": "quota"}}
        return real(method, url, body, headers)

    gs = backend(tmp_path, flaky)
    rows = gs.read_rows()
    assert rows and rows[0]["external_id"] == "t_01" and attempts["n"] >= 2


def test_fingerprint_tracks_values(tmp_path):
    api = FakeSheetsApi()
    gs = backend(tmp_path, api)
    assert gs.fingerprint() == ""
    api.values = [sheetmod.COLUMNS, ["dummy", "t_01"] + [""] * 26]
    first = gs.fingerprint()
    api.values[1][3] = "Someone Else"
    assert first and gs.fingerprint() != first


def test_backend_selection_rule(tmp_path):
    key = tmp_path / "sa.json"
    key.write_text(json.dumps({"client_email": "bot@x", "private_key": "k"}))
    env_x = {"BV_SHEET": str(tmp_path / "p.xlsx")}
    assert sheetmod.get_backend(None, env_x).name == "xlsx"
    env_g = {"GOOGLE_SHEET_ID": "abc", "GOOGLE_SERVICE_ACCOUNT_FILE": str(key)}
    gs = sheetmod.get_backend(None, env_g)
    assert gs.name == "google" and gs.describe() == "https://docs.google.com/spreadsheets/d/abc"
    assert gs.sidecar.name == "practices.google.sheet.json"
    assert sheetmod.get_backend(None, {**env_g, "BV_SHEET_BACKEND": "xlsx", **env_x}).name == "xlsx"
    with pytest.raises(GoogleSheetError):
        sheetmod.get_backend(None, {"BV_SHEET_BACKEND": "google", "GOOGLE_SHEET_ID": "abc"})
    with pytest.raises(GoogleSheetError):
        sheetmod.get_backend(None, {"BV_SHEET_BACKEND": "google", "GOOGLE_SERVICE_ACCOUNT_FILE": str(key)})
    assert sheetmod.watch_interval(gs) == 10.0 and sheetmod.watch_interval(sheetmod.get_backend(None, env_x)) == 3.0


def test_token_minting_uses_the_signed_jwt(tmp_path, monkeypatch):
    from infra import gsheet

    class FakeSigner:
        @classmethod
        def from_service_account_info(cls, info):
            return cls()

    captured = {}

    def fake_encode(signer, payload):
        captured.update(payload)
        return b"signed.jwt"

    monkeypatch.setattr("google.auth.crypt.RSASigner", FakeSigner)
    monkeypatch.setattr("google.auth.jwt.encode", fake_encode)
    seen = {}

    def transport(method, url, body, headers):
        seen.update({"method": method, "url": url, "body": urllib.parse.parse_qs(body.decode())})
        return 200, {"access_token": "tok", "expires_in": 3600}

    token, expires = gsheet.mint_access_token({"client_email": "bot@x", "private_key": "k"}, transport, now=1000)
    assert token == "tok" and expires == 1000 + 3600 - 60
    assert seen["url"] == gsheet.TOKEN_URL and seen["body"]["assertion"] == ["signed.jwt"]
    assert captured["iss"] == "bot@x" and captured["scope"] == gsheet.SCOPE and captured["exp"] == 1000 + 3600


def test_sheet_seed_adapter_reads_from_the_google_backend(tmp_path):
    from infra.sourcing import CsvSeedAdapter, SheetSeedAdapter

    store, clock = seeded_store(tmp_path)
    api = FakeSheetsApi()
    gs = backend(tmp_path, api)
    sheetmod.export_sheet(store, gs, clock)
    rows = SheetSeedAdapter(gs).fetch()
    assert len(rows) == len(store.contacts) and rows[0]["source"]
    assert CsvSeedAdapter(gs).fetch() == rows
