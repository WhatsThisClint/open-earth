from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import project_state_file


ACTION_STATUS = {
    "approve": "approved",
    "correct": "corrected",
    "reject": "rejected",
    "field_check": "needs_field_validation",
    "uncertain": "uncertain",
    "close": "closed",
    "reopen": "open",
}


@dataclass(frozen=True)
class ReviewItem:
    id: str
    status: str
    item_type: str
    title: str
    severity: str
    source: str
    payload: dict[str, Any]
    recommendation: dict[str, Any]
    created_at: str
    updated_at: str


class ReviewQueue:
    """Local human-review queue for trigger hits, uncertain findings, and corrections."""

    def __init__(self, root: str | Path, path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(path).resolve() if path else project_state_file(self.root, "review_queue.sqlite")

    def init(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_items (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    recommendation_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_review_status ON review_items(status);
                CREATE INDEX IF NOT EXISTS idx_review_severity ON review_items(severity);

                CREATE TABLE IF NOT EXISTS review_events (
                    id TEXT PRIMARY KEY,
                    item_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_review_events_item ON review_events(item_id);
                """
            )
        return self.path

    def add_item(
        self,
        *,
        title: str,
        severity: str = "medium",
        item_type: str = "trigger",
        source: str = "",
        payload: dict[str, Any] | None = None,
        recommendation: dict[str, Any] | None = None,
    ) -> ReviewItem:
        self.init()
        now = _now()
        item_id = f"rv_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO review_items (
                    id, status, item_type, title, severity, source,
                    payload_json, recommendation_json, created_at, updated_at
                )
                VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item_id,
                    item_type,
                    title,
                    severity,
                    source,
                    _json(payload or {}),
                    _json(recommendation or {}),
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO review_events (id, item_id, action, actor, note, created_at)
                VALUES (?, ?, 'created', 'system', '', ?)
                """,
                (f"event:{uuid.uuid4().hex}", item_id, now),
            )
        return self.get(item_id)

    def list_items(self, status: str = "open", limit: int = 50) -> list[ReviewItem]:
        self.init()
        status = status.strip().lower()
        limit = max(1, int(limit))
        with self._connect() as conn:
            if status == "all":
                rows = conn.execute(
                    "SELECT * FROM review_items ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM review_items WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
        return [_item_from_row(row) for row in rows]

    def get(self, item_id: str) -> ReviewItem:
        self.init()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM review_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(f"review item not found: {item_id}")
        return _item_from_row(row)

    def history(self, item_id: str) -> list[dict[str, Any]]:
        self.init()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, item_id, action, actor, note, created_at
                FROM review_events
                WHERE item_id = ?
                ORDER BY created_at ASC
                """,
                (item_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def transition(self, item_id: str, action: str, *, note: str = "", actor: str = "human") -> ReviewItem:
        self.init()
        action_key = action.strip().lower().replace("-", "_")
        if action_key not in ACTION_STATUS:
            raise ValueError(f"unknown review action: {action}")
        self.get(item_id)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE review_items SET status = ?, updated_at = ? WHERE id = ?",
                (ACTION_STATUS[action_key], now, item_id),
            )
            conn.execute(
                """
                INSERT INTO review_events (id, item_id, action, actor, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"event:{uuid.uuid4().hex}", item_id, action_key, actor, note, now),
            )
        return self.get(item_id)

    def counts(self) -> dict[str, int]:
        self.init()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM review_items GROUP BY status ORDER BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _item_from_row(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        id=str(row["id"]),
        status=str(row["status"]),
        item_type=str(row["item_type"]),
        title=str(row["title"]),
        severity=str(row["severity"]),
        source=str(row["source"]),
        payload=_loads(row["payload_json"]),
        recommendation=_loads(row["recommendation_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
