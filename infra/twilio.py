"""Minimal Twilio REST client (stdlib only) for the trial-account bridge.

Used for account checks (`twilio check`). Note: Twilio trial accounts cannot hand a call to Vapi: the trial
blocks <Dial><Sip>, <Dial><Number> and <Stream>, and the shared trial number cannot be imported into Vapi.
An upgraded (pay-as-you-go) Twilio account with its own number imports into Vapi with `vapi import-twilio`.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.twilio.com/2010-04-01"
USER_AGENT = "between-visits-infra/0.1 (+https://github.com/UmaSanskriti/between-visits-workflow)"


class TwilioError(Exception):
    pass


class TwilioClient:
    """`transport(method, path, form) -> (status, json)` can be swapped for tests."""

    def __init__(self, account_sid: str, auth_token: str, transport=None):
        if not (account_sid and auth_token):
            raise TwilioError("TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN are not set")
        self.sid = account_sid
        self._auth = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
        self.transport = transport or self._http

    def _http(self, method: str, path: str, form: dict | None = None) -> tuple[int, object]:
        data = urllib.parse.urlencode(form).encode() if form is not None else None
        req = urllib.request.Request(
            f"{API}/Accounts/{self.sid}/{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Basic {self._auth}",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                **({"Content-Type": "application/x-www-form-urlencoded"} if data is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, {"message": raw}

    def request(self, method: str, path: str, form: dict | None = None):
        status, payload = self.transport(method, path, form)
        if status >= 400:
            msg = payload.get("message") if isinstance(payload, dict) else payload
            code = payload.get("code") if isinstance(payload, dict) else ""
            raise TwilioError(f"{method} {path} -> {status} {code or ''}: {msg}")
        return payload

    def account(self) -> dict:
        return self.request("GET", ".json")

    def incoming_numbers(self) -> list[dict]:
        return self.request("GET", "IncomingPhoneNumbers.json").get("incoming_phone_numbers", [])

    def verified_caller_ids(self) -> list[dict]:
        return self.request("GET", "OutgoingCallerIds.json").get("outgoing_caller_ids", [])

    def place_call(self, to_number: str, from_number: str, twiml: str, status_callback: str = "") -> dict:
        form = {"To": to_number, "From": from_number, "Twiml": twiml}
        if status_callback:
            form["StatusCallback"] = status_callback
            form["StatusCallbackEvent"] = "initiated ringing answered completed"
        return self.request("POST", "Calls.json", form)

    def get_call(self, call_sid: str) -> dict:
        return self.request("GET", f"Calls/{call_sid}.json")
