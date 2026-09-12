"""Durable storage and process ownership for investigation sessions.

The store is deliberately synchronous.  SQLite transactions are short and the
manager calls these methods from the event loop only for small writes; this
keeps the event ordering and commit-before-publish rule easy to audit.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


Json = dict[str, Any]
TERMINAL = {"completed", "failed", "interrupted"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class InvestigationStore:
    """SQLite store with an adjacent exclusive process lock."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        configured = path or os.environ.get("NIGHTWATCH_INVESTIGATION_DB")
        self.path = Path(configured or Path(__file__).resolve().parents[1] / ".data" / "investigations.sqlite3").expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = Path(str(self.path) + ".lock")
        self._lock_file = self.lock_path.open("a+")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as error:
            self._lock_file.close()
            raise RuntimeError(f"investigation database is already in use: {self.lock_path}") from error
        self._mutex = threading.RLock()
        self._closed = False
        try:
            self.db = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA busy_timeout=5000")
            self.db.execute("PRAGMA journal_mode=WAL")
            self._schema()
        except Exception:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            raise

    def _schema(self) -> None:
        with self.db:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    created_seq INTEGER NOT NULL UNIQUE,
                    request_id TEXT NOT NULL UNIQUE,
                    request_body TEXT NOT NULL,
                    trigger_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    closed_at TEXT,
                    status TEXT NOT NULL CHECK(status IN ('running','completed','failed','interrupted')),
                    outcome TEXT,
                    summary_zh TEXT NOT NULL,
                    report_json TEXT,
                    evidence_json TEXT NOT NULL,
                    usage_json TEXT NOT NULL,
                    context_json TEXT NOT NULL,
                    context_available INTEGER NOT NULL DEFAULT 0,
                    context_complete INTEGER NOT NULL DEFAULT 0,
                    event_seq INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_investigation
                    ON sessions(status) WHERE status = 'running';
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    investigation_id TEXT NOT NULL REFERENCES sessions(id),
                    seq INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    UNIQUE(investigation_id, seq)
                );
                CREATE INDEX IF NOT EXISTS events_investigation_seq ON events(investigation_id, seq);
                """
            )

    def close(self) -> None:
        with getattr(self, "_mutex", threading.RLock()):
            if getattr(self, "_closed", True):
                return
            self.db.close()
            self._closed = True
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _row_json(row: sqlite3.Row, key: str, default=None):
        value = row[key]
        return default if value is None else json.loads(value)

    @staticmethod
    def _summary(row: sqlite3.Row) -> Json:
        return {
            "id": row["id"], "created_seq": row["created_seq"], "status": row["status"],
            "outcome": row["outcome"], "created_at": row["created_at"],
            "started_at": row["started_at"], "closed_at": row["closed_at"],
            "trigger": json.loads(row["trigger_json"]), "summary_zh": row["summary_zh"],
            "event_seq": row["event_seq"],
        }

    def _append_event_tx(self, investigation_id: str, event_type: str, payload: Json, at: str | None = None) -> Json:
        row = self.db.execute("SELECT event_seq FROM sessions WHERE id = ?", (investigation_id,)).fetchone()
        if row is None:
            raise KeyError(investigation_id)
        seq = int(row["event_seq"]) + 1
        event_at = at or now()
        cursor = self.db.execute(
            "INSERT INTO events(investigation_id, seq, type, at, payload_json) VALUES (?, ?, ?, ?, ?)",
            (investigation_id, seq, event_type, event_at, json_text(payload)),
        ).lastrowid
        self.db.execute("UPDATE sessions SET event_seq = ? WHERE id = ?", (seq, investigation_id))
        return {"cursor": int(cursor), "investigation_id": investigation_id, "seq": seq,
                "type": event_type, "at": event_at, "payload": copy.deepcopy(payload)}

    def recover_running(self) -> list[Json]:
        """Archive sessions left running by a process that did not shut down."""
        recovered = []
        with self._mutex, self.db:
            rows = self.db.execute("SELECT * FROM sessions WHERE status = 'running' ORDER BY created_seq").fetchall()
            for row in rows:
                closed = now()
                evidence = json.loads(row["evidence_json"])
                report = self._report(
                    row["id"], "interrupted", row["summary_zh"] + "；服務重新啟動，調查未完成。",
                    row["started_at"], closed, None, evidence,
                    ["服務在調查完成前重新啟動；保存的 context 與工具事件可能不完整。"],
                )
                self.db.execute(
                    "UPDATE sessions SET status='interrupted', outcome='interrupted', closed_at=?, summary_zh=?, report_json=?, context_complete=0 WHERE id=?",
                    (closed, report["summary_zh"], json_text(report), row["id"]),
                )
                recovered.append(self._append_event_tx(row["id"], "investigation.finished", {
                    "status": "interrupted", "outcome": "interrupted", "reason": report["summary_zh"]
                }, closed))
        return recovered

    @staticmethod
    def _report(investigation_id: str, outcome: str, summary_zh: str, started_at: str,
                closed_at: str, agent_report: Json | None, evidence: list[Json], limitations: list[str]) -> Json:
        return {
            "investigation_id": investigation_id, "outcome": outcome, "summary_zh": summary_zh,
            "started_at": started_at, "closed_at": closed_at,
            "agent_report": copy.deepcopy(agent_report),
            "evidence_ids": [item["id"] for item in evidence if isinstance(item, dict) and "id" in item],
            "limitations": list(dict.fromkeys(limitations)),
        }

    def create(self, investigation_id: str, request_id: str, request_body: Json, trigger: Json) -> tuple[str, Json]:
        """Return ``('created'|'replay', summary)`` or raise a typed store error."""
        with self._mutex:
            with self.db:
                existing = self.db.execute("SELECT * FROM sessions WHERE request_id = ?", (request_id,)).fetchone()
                if existing is not None:
                    if json.loads(existing["request_body"]) != request_body:
                        raise RequestConflict(existing["id"])
                    return "replay", self._summary(existing)
                active = self.db.execute("SELECT id FROM sessions WHERE status = 'running'").fetchone()
                if active is not None:
                    raise ActiveInvestigation(active["id"])
                row = self.db.execute("SELECT COALESCE(MAX(created_seq), 0) + 1 FROM sessions").fetchone()
                created_seq = int(row[0])
                at = now()
                context = {"instructions": None, "tools": [], "opening": {},
                           "model": {"name": "", "settings": {}}, "messages": []}
                self.db.execute(
                    """INSERT INTO sessions(id, created_seq, request_id, request_body, trigger_json,
                       created_at, started_at, status, outcome, summary_zh, evidence_json,
                       usage_json, context_json) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', NULL, ?, '[]', '{}', ?)""",
                    (investigation_id, created_seq, request_id, json_text(request_body), json_text(trigger),
                     at, at, "調查已建立，正在準備模型與觀測資料。", json_text(context)),
                )
                self._append_event_tx(investigation_id, "investigation.started", {
                    "trigger": copy.deepcopy(trigger), "request_id": request_id
                }, at)
                current = self.db.execute("SELECT * FROM sessions WHERE id = ?", (investigation_id,)).fetchone()
                return "created", self._summary(current)

    def update_context(self, investigation_id: str, context: Json, complete: bool = False) -> None:
        with self._mutex, self.db:
            if self.db.execute("SELECT 1 FROM sessions WHERE id=? AND status='running'", (investigation_id,)).fetchone() is None:
                return
            self.db.execute("UPDATE sessions SET context_json=?, context_available=1, context_complete=? WHERE id=?",
                            (json_text(context), int(complete), investigation_id))

    def append_event(self, investigation_id: str, event_type: str, payload: Json) -> Json:
        with self._mutex, self.db:
            if self.db.execute("SELECT 1 FROM sessions WHERE id=? AND status='running'", (investigation_id,)).fetchone() is None:
                return {}
            event = self._append_event_tx(investigation_id, event_type, payload)
            if event_type == "observation.recorded" and isinstance(payload.get("evidence"), dict):
                row = self.db.execute("SELECT evidence_json FROM sessions WHERE id=?", (investigation_id,)).fetchone()
                evidence = json.loads(row["evidence_json"])
                evidence.append(copy.deepcopy(payload["evidence"]))
                self.db.execute("UPDATE sessions SET evidence_json=? WHERE id=?", (json_text(evidence), investigation_id))
            return event

    def finish(self, investigation_id: str, status: str, outcome: str, summary_zh: str,
               report: Json | None, evidence: list[Json], usage: Json, context: Json | None,
               limitations: list[str], context_complete: bool = True) -> Json:
        if status not in TERMINAL:
            raise ValueError(status)
        with self._mutex, self.db:
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (investigation_id,)).fetchone()
            if row is None:
                raise KeyError(investigation_id)
            if row["status"] != "running":
                return self._summary(row)
            if status in {"interrupted", "failed"}:
                # Tool observations are persisted before the model returns.
                # A cancelled/failed runner may still hold an empty result list.
                evidence = json.loads(row["evidence_json"])
            closed = now()
            actual_report = self._report(investigation_id, outcome, summary_zh, row["started_at"], closed,
                                          report, evidence, limitations)
            if context is None:
                self.db.execute(
                    """UPDATE sessions SET status=?, outcome=?, summary_zh=?, closed_at=?, report_json=?,
                       evidence_json=?, usage_json=?, context_complete=? WHERE id=?""",
                    (status, outcome, summary_zh, closed, json_text(actual_report), json_text(evidence),
                     json_text(usage), int(context_complete), investigation_id),
                )
            else:
                self.db.execute(
                    """UPDATE sessions SET status=?, outcome=?, summary_zh=?, closed_at=?, report_json=?,
                       evidence_json=?, usage_json=?, context_json=?, context_available=1,
                       context_complete=? WHERE id=?""",
                    (status, outcome, summary_zh, closed, json_text(actual_report), json_text(evidence),
                     json_text(usage), json_text(context), int(context_complete), investigation_id),
                )
            # This is in the same transaction as the terminal row update.  A
            # subscriber can never observe the event before its report exists.
            event = self._append_event_tx(investigation_id, "investigation.finished", {
                "status": status, "outcome": outcome, "reason": summary_zh
            }, closed)
            return event

    def detail(self, investigation_id: str) -> Json | None:
        with self._mutex:
            row = self.db.execute("SELECT * FROM sessions WHERE id=?", (investigation_id,)).fetchone()
            if row is None:
                return None
            return {**self._summary(row), "request_id": row["request_id"],
                    "request": json.loads(row["request_body"]), "report": self._row_json(row, "report_json"),
                    "evidence": json.loads(row["evidence_json"]), "usage": json.loads(row["usage_json"]),
                    "context_available": bool(row["context_available"]),
                    "context_complete": bool(row["context_complete"])}

    def context(self, investigation_id: str) -> Json | None:
        with self._mutex:
            row = self.db.execute("SELECT context_json, context_complete FROM sessions WHERE id=?", (investigation_id,)).fetchone()
            if row is None:
                return None
            return {"complete": bool(row["context_complete"]), "context": json.loads(row["context_json"])}

    def list(self, limit: int, before: int | None = None) -> Json:
        with self._mutex:
            args: list[Any] = []
            where = ""
            if before is not None:
                where = "WHERE created_seq < ?"
                args.append(before)
            rows = self.db.execute(f"SELECT * FROM sessions {where} ORDER BY created_seq DESC LIMIT ?", (*args, limit + 1)).fetchall()
            items = [self._summary(row) for row in rows[:limit]]
            return {"items": items, "next_before": items[-1]["created_seq"] if len(rows) > limit else None}

    def events(self, investigation_id: str, after: int, limit: int) -> Json | None:
        with self._mutex:
            if self.db.execute("SELECT 1 FROM sessions WHERE id=?", (investigation_id,)).fetchone() is None:
                return None
            rows = self.db.execute(
                "SELECT cursor, investigation_id, seq, type, at, payload_json FROM events WHERE investigation_id=? AND seq>? ORDER BY seq LIMIT ?",
                (investigation_id, after, limit + 1),
            ).fetchall()
            items = [{"cursor": row["cursor"], "investigation_id": row["investigation_id"], "seq": row["seq"],
                      "type": row["type"], "at": row["at"], "payload": json.loads(row["payload_json"])} for row in rows[:limit]]
            return {"items": items, "next_after": items[-1]["seq"] if len(rows) > limit else None}

    def events_after_cursor(self, cursor: int, limit: int = 100) -> list[Json]:
        with self._mutex:
            rows = self.db.execute(
                "SELECT cursor, investigation_id, seq, type, at, payload_json FROM events WHERE cursor>? ORDER BY cursor LIMIT ?",
                (cursor, limit),
            ).fetchall()
            return [{"cursor": row["cursor"], "investigation_id": row["investigation_id"], "seq": row["seq"],
                     "type": row["type"], "at": row["at"], "payload": json.loads(row["payload_json"])} for row in rows]

    def cursor(self) -> int:
        with self._mutex:
            row = self.db.execute("SELECT COALESCE(MAX(cursor), 0) FROM events").fetchone()
            return int(row[0])

    def state_base(self) -> Json:
        with self._mutex:
            cursor = self.cursor()
            active = self.db.execute("SELECT * FROM sessions WHERE status='running'").fetchone()
            latest = self.db.execute("SELECT id FROM sessions WHERE status IN ('completed','failed','interrupted') ORDER BY created_seq DESC LIMIT 1").fetchone()
            return {"cursor": cursor, "active_investigation_id": active["id"] if active else None,
                    "active_investigation": self._summary(active) if active else None,
                    "last_completed_investigation_id": latest["id"] if latest else None}


class ActiveInvestigation(Exception):
    def __init__(self, investigation_id: str):
        self.investigation_id = investigation_id
        super().__init__(investigation_id)


class RequestConflict(Exception):
    def __init__(self, investigation_id: str):
        self.investigation_id = investigation_id
        super().__init__(investigation_id)
