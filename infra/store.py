"""JSON-file system of record for v0 (DB-001) with an append-only audit log (DB-002).

One file per table under data/. Swapping this for PostgreSQL later means replacing this
module only; every stage talks to the Store, never to files.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .clock import Clock, iso
from .models import TABLES, Channel, Contact, Record

LOG_FILES = ["gate_decisions", "errors", "dlq", "alerts", "runs"]
MAX_LOG_ROWS = 2000


class Store:
    def __init__(self, data_dir: Path | str, clock: Clock):
        self.dir = Path(data_dir)
        self.clock = clock
        self.tables: dict[str, dict[str, Record]] = {name: {} for name in TABLES}
        self.logs: dict[str, list[dict]] = {name: [] for name in LOG_FILES}
        self.audit_log: list[dict] = []
        self.state: dict = {"paused": False}
        self.brain_inbox: dict[str, dict] = {}

    # ---- table accessors -------------------------------------------------
    @property
    def contacts(self) -> dict[str, Contact]:
        return self.tables["contacts"]  # type: ignore[return-value]

    @property
    def channels(self) -> dict[str, Channel]:
        return self.tables["channels"]  # type: ignore[return-value]

    @property
    def call_attempts(self):
        return self.tables["call_attempts"]

    @property
    def transcripts(self):
        return self.tables["transcripts"]

    @property
    def interviews(self):
        return self.tables["interviews"]

    @property
    def suppressions(self):
        return self.tables["suppressions"]

    @property
    def consent_records(self):
        return self.tables["consent_records"]

    @property
    def scripts(self):
        return self.tables["scripts"]

    # ---- persistence -----------------------------------------------------
    def load(self) -> Store:
        for name, cls in TABLES.items():
            path = self.dir / f"{name}.json"
            if path.exists():
                raw = json.loads(path.read_text())
                self.tables[name] = {key: cls.from_dict(value) for key, value in raw.items()}
        for name in LOG_FILES:
            path = self.dir / f"{name}.json"
            if path.exists():
                self.logs[name] = json.loads(path.read_text())
        audit = self.dir / "audit_log.jsonl"
        if audit.exists():
            self.audit_log = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
        state = self.dir / "state.json"
        if state.exists():
            self.state = json.loads(state.read_text())
        inbox = self.dir / "brain_inbox.json"
        if inbox.exists():
            self.brain_inbox = json.loads(inbox.read_text())
        return self

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        for name, rows in self.tables.items():
            self._write(self.dir / f"{name}.json", {key: row.to_dict() for key, row in rows.items()})
        for name, rows in self.logs.items():
            self._write(self.dir / f"{name}.json", rows[-MAX_LOG_ROWS:])
        with (self.dir / "audit_log.jsonl").open("w") as fh:
            for entry in self.audit_log:
                fh.write(json.dumps(entry, sort_keys=True) + "\n")
        self._write(self.dir / "state.json", self.state)
        self._write(self.dir / "brain_inbox.json", self.brain_inbox)

    @staticmethod
    def _write(path: Path, payload) -> None:
        path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")

    def reset(self) -> None:
        if self.dir.exists():
            shutil.rmtree(self.dir)
        self.__init__(self.dir, self.clock)  # type: ignore[misc]

    # ---- mutation helpers (every change is audited) ----------------------
    def add(self, table: str, record: Record, actor: str = "system", **corr) -> Record:
        self.tables[table][record.id] = record  # type: ignore[attr-defined]
        if hasattr(record, "created_at") and not record.created_at:
            record.created_at = iso(self.clock.now())
        after = {"id": record.id}  # type: ignore[attr-defined]
        for key in ("status", "type", "outcome", "identifier_type"):
            if hasattr(record, key):
                after[key] = getattr(record, key)
        self.audit(table, record.id, "create", {}, after, actor, **corr)  # type: ignore[attr-defined]
        return record

    def update(self, table: str, record: Record, action: str, changes: dict, actor: str = "system", **corr):
        before = {key: getattr(record, key) for key in changes}
        if all(before[key] == value for key, value in changes.items()):
            return record
        for key, value in changes.items():
            setattr(record, key, value)
        if hasattr(record, "updated_at"):
            record.updated_at = iso(self.clock.now())
        self.audit(table, record.id, action, before, changes, actor, **corr)  # type: ignore[attr-defined]
        return record

    def audit(self, entity, entity_id, action, before=None, after=None, actor="system", **corr) -> dict:
        entry = {
            "timestamp": iso(self.clock.now()),
            "entity": entity,
            "entity_id": entity_id,
            "action": action,
            "before": before or {},
            "after": after or {},
            "actor": actor,
        }
        entry.update({key: value for key, value in corr.items() if value})
        self.audit_log.append(entry)
        return entry

    def error(self, error_type: str, detail: str, call_id: str = "", contact_id: str = "", field: str = "") -> dict:
        entry = {
            "timestamp": iso(self.clock.now()),
            "call_id": call_id,
            "contact_id": contact_id,
            "error_type": error_type,
            "field": field,
            "detail": detail,
        }
        self.logs["errors"].append(entry)
        return entry

    def alert(self, alert_type: str, detail: str, **extra) -> dict:
        entry = {"timestamp": iso(self.clock.now()), "type": alert_type, "detail": detail, **extra}
        self.logs["alerts"].append(entry)
        return entry

    def dead_letter(self, entry: dict) -> dict:
        entry = {"timestamp": iso(self.clock.now()), **entry}
        self.logs["dlq"].append(entry)
        return entry

    # ---- queries ---------------------------------------------------------
    def channels_for(self, contact_id: str, kind: str | None = None) -> list[Channel]:
        rows = [ch for ch in self.channels.values() if ch.contact_id == contact_id]
        if kind:
            rows = [ch for ch in rows if ch.type == kind]
        return rows

    def is_suppressed(self, contact: Contact) -> tuple[bool, str]:
        identifiers = {(s.identifier_type, s.identifier) for s in self.suppressions.values()}
        if ("contact", contact.id) in identifiers:
            return True, f"contact:{contact.id}"
        for ch in self.channels_for(contact.id):
            if (ch.type, ch.value) in identifiers or (ch.type, ch.raw_value) in identifiers:
                return True, f"{ch.type}:{ch.value}"
        return False, ""

    def attempts_for(self, contact_id: str) -> list:
        rows = [a for a in self.call_attempts.values() if a.contact_id == contact_id]
        return sorted(rows, key=lambda a: a.attempt_number)
