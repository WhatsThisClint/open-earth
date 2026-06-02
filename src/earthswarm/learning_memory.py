from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import project_state_file


LEARNING_KINDS = {
    "human_feedback",
    "correction",
    "preference",
    "lesson",
    "decision",
    "assumption",
    "field_insight",
    "model_note",
    "workflow_note",
}
LEARNING_STATUSES = {"active", "archived", "superseded"}


@dataclass(frozen=True)
class LearningRecord:
    id: str
    kind: str
    status: str
    title: str
    body: str
    source: str
    created_by: str
    confidence: str
    applies_to: list[str]
    tags: list[str]
    evidence_refs: list[str]
    payload: dict[str, Any]
    created_at: str
    updated_at: str


class LearningMemoryStore:
    """Human feedback and lessons learned, kept auditable and graph-indexable."""

    def __init__(self, root: str | Path, path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(path).resolve() if path else project_state_file(self.root, "learning_memory.sqlite")

    def init(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS learning_records (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT 'human',
                    confidence TEXT NOT NULL DEFAULT 'medium',
                    applies_to_json TEXT NOT NULL DEFAULT '[]',
                    tags_json TEXT NOT NULL DEFAULT '[]',
                    evidence_refs_json TEXT NOT NULL DEFAULT '[]',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_kind ON learning_records(kind);
                CREATE INDEX IF NOT EXISTS idx_learning_status ON learning_records(status);

                CREATE TABLE IF NOT EXISTS learning_events (
                    id TEXT PRIMARY KEY,
                    record_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_learning_events_record ON learning_events(record_id);
                """
            )
        return self.path

    def add_record(
        self,
        *,
        kind: str,
        title: str,
        body: str,
        source: str = "",
        created_by: str = "human",
        confidence: str = "medium",
        applies_to: list[str] | None = None,
        tags: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> LearningRecord:
        self.init()
        normalized_kind = _normalize_kind(kind)
        if not title.strip():
            raise ValueError("learning memory title is required")
        if not body.strip():
            raise ValueError("learning memory body is required")
        now = _now()
        record_id = f"learn_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO learning_records (
                    id, kind, status, title, body, source, created_by, confidence,
                    applies_to_json, tags_json, evidence_refs_json, payload_json,
                    created_at, updated_at
                )
                VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    normalized_kind,
                    title.strip(),
                    body.strip(),
                    source.strip(),
                    created_by.strip() or "human",
                    confidence.strip() or "medium",
                    _json(_clean_list(applies_to or [])),
                    _json(_clean_list(tags or [])),
                    _json(_clean_list(evidence_refs or [])),
                    _json(payload or {}),
                    now,
                    now,
                ),
            )
            self._add_event(conn, record_id, "created", created_by or "human", "", now)
        return self.get(record_id)

    def list_records(self, *, kind: str = "all", status: str = "active", limit: int = 50) -> list[LearningRecord]:
        self.init()
        kind = kind.strip().lower().replace("-", "_")
        status = status.strip().lower()
        limit = max(1, int(limit))
        clauses = []
        params: list[Any] = []
        if kind != "all":
            clauses.append("kind = ?")
            params.append(_normalize_kind(kind))
        if status != "all":
            if status not in LEARNING_STATUSES:
                raise ValueError(f"unknown learning status: {status}")
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM learning_records {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def get(self, record_id: str) -> LearningRecord:
        self.init()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM learning_records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise KeyError(f"learning record not found: {record_id}")
        return _record_from_row(row)

    def history(self, record_id: str) -> list[dict[str, Any]]:
        self.init()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, record_id, action, actor, note, created_at
                FROM learning_events
                WHERE record_id = ?
                ORDER BY created_at ASC
                """,
                (record_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_status(self, record_id: str, status: str, *, actor: str = "human", note: str = "") -> LearningRecord:
        self.init()
        status = status.strip().lower()
        if status not in LEARNING_STATUSES:
            raise ValueError(f"unknown learning status: {status}")
        self.get(record_id)
        now = _now()
        with self._connect() as conn:
            conn.execute("UPDATE learning_records SET status = ?, updated_at = ? WHERE id = ?", (status, now, record_id))
            self._add_event(conn, record_id, status, actor, note, now)
        return self.get(record_id)

    def counts(self) -> dict[str, int]:
        self.init()
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM learning_records GROUP BY status ORDER BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def _add_event(self, conn: sqlite3.Connection, record_id: str, action: str, actor: str, note: str, created_at: str) -> None:
        conn.execute(
            """
            INSERT INTO learning_events (id, record_id, action, actor, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (f"event:{uuid.uuid4().hex}", record_id, action, actor, note, created_at),
        )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _record_from_row(row: sqlite3.Row) -> LearningRecord:
    return LearningRecord(
        id=str(row["id"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        title=str(row["title"]),
        body=str(row["body"]),
        source=str(row["source"]),
        created_by=str(row["created_by"]),
        confidence=str(row["confidence"]),
        applies_to=_loads_list(row["applies_to_json"]),
        tags=_loads_list(row["tags_json"]),
        evidence_refs=_loads_list(row["evidence_refs_json"]),
        payload=_loads_dict(row["payload_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _normalize_kind(kind: str) -> str:
    normalized = kind.strip().lower().replace("-", "_")
    if normalized not in LEARNING_KINDS:
        raise ValueError(f"unknown learning memory kind: {kind}")
    return normalized


def _clean_list(values: list[str]) -> list[str]:
    result = []
    for value in values:
        clean = str(value).strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads_dict(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _loads_list(value: str) -> list[str]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
