from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import project_state_file
from .review_queue import ReviewItem, ReviewQueue


FIELD_METHODS = {
    "field_transect": ["photos", "gps_track", "condition_notes"],
    "well_check": ["well_id", "water_level", "photo", "owner_or_operator_notes"],
    "stream_obs": ["stream_stage", "flow_condition", "photo", "crossing_condition"],
    "soil_check": ["texture", "moisture", "erosion_signs", "photo"],
    "community_input": ["respondent_group", "reported_change", "confidence_notes"],
    "field_notebook": ["observation_note", "photo_or_attachment"],
}

FIELD_STATUSES = {"open", "in_progress", "completed", "cancelled"}


@dataclass(frozen=True)
class FieldTask:
    id: str
    status: str
    review_item_id: str
    method: str
    title: str
    question: str
    location: str
    priority: str
    assigned_to: str
    due_date: str
    geometry: dict[str, Any]
    expected_evidence: list[str]
    observations: str
    attachments: list[str]
    confidence_update: str
    created_at: str
    updated_at: str
    completed_at: str


class FieldValidationStore:
    """Local store for field validation tasks linked to review items."""

    def __init__(self, root: str | Path, path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(path).resolve() if path else project_state_file(self.root, "field_validation.sqlite")

    def init(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS field_tasks (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    review_item_id TEXT NOT NULL DEFAULT '',
                    method TEXT NOT NULL,
                    title TEXT NOT NULL,
                    question TEXT NOT NULL,
                    location TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'medium',
                    assigned_to TEXT NOT NULL DEFAULT '',
                    due_date TEXT NOT NULL DEFAULT '',
                    geometry_json TEXT NOT NULL DEFAULT '{}',
                    expected_evidence_json TEXT NOT NULL DEFAULT '[]',
                    observations TEXT NOT NULL DEFAULT '',
                    attachments_json TEXT NOT NULL DEFAULT '[]',
                    confidence_update TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_field_status ON field_tasks(status);
                CREATE INDEX IF NOT EXISTS idx_field_review ON field_tasks(review_item_id);

                CREATE TABLE IF NOT EXISTS field_events (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_field_events_task ON field_events(task_id);
                """
            )
        return self.path

    def create_task(
        self,
        *,
        review_item_id: str = "",
        method: str = "field_notebook",
        title: str = "",
        question: str = "",
        location: str = "",
        priority: str = "medium",
        assigned_to: str = "",
        due_date: str = "",
        geometry: dict[str, Any] | None = None,
        expected_evidence: list[str] | None = None,
        actor: str = "system",
        note: str = "",
    ) -> FieldTask:
        self.init()
        method = _normalize_method(method)
        task_id = f"fv_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        now = _now()
        expected = expected_evidence or list(FIELD_METHODS[method])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO field_tasks (
                    id, status, review_item_id, method, title, question,
                    location, priority, assigned_to, due_date, geometry_json,
                    expected_evidence_json, created_at, updated_at
                )
                VALUES (?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    review_item_id,
                    method,
                    title or f"Field validation: {method.replace('_', ' ')}",
                    question or "Validate this signal and record direct evidence.",
                    location,
                    priority or "medium",
                    assigned_to,
                    due_date,
                    _json(geometry or {}),
                    _json(expected),
                    now,
                    now,
                ),
            )
            self._add_event(conn, task_id, "created", actor, note, now)
        return self.get(task_id)

    def create_from_review(
        self,
        review_item_id: str,
        *,
        method: str | None = None,
        title: str = "",
        question: str = "",
        location: str = "",
        priority: str = "",
        assigned_to: str = "",
        due_date: str = "",
        expected_evidence: list[str] | None = None,
        actor: str = "system",
        note: str = "",
    ) -> FieldTask:
        review = ReviewQueue(self.root).get(review_item_id)
        event = review.payload.get("event") if isinstance(review.payload.get("event"), dict) else {}
        recommended_methods = review.recommendation.get("field_validation") or []
        selected_method = method or (recommended_methods[0] if recommended_methods else "field_notebook")
        selected_location = location or str(event.get("location") or "")
        selected_question = question or str(review.recommendation.get("suggested_action") or review.title)
        selected_priority = priority or review.severity or "medium"
        return self.create_task(
            review_item_id=review.id,
            method=selected_method,
            title=title or f"Validate: {review.title}",
            question=selected_question,
            location=selected_location,
            priority=selected_priority,
            assigned_to=assigned_to,
            due_date=due_date,
            expected_evidence=expected_evidence,
            actor=actor,
            note=note,
        )

    def list_tasks(self, status: str = "open", limit: int = 50) -> list[FieldTask]:
        self.init()
        status = status.strip().lower()
        limit = max(1, int(limit))
        with self._connect() as conn:
            if status == "all":
                rows = conn.execute("SELECT * FROM field_tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM field_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
        return [_task_from_row(row) for row in rows]

    def get(self, task_id: str) -> FieldTask:
        self.init()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM field_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"field task not found: {task_id}")
        return _task_from_row(row)

    def history(self, task_id: str) -> list[dict[str, Any]]:
        self.init()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, action, actor, note, created_at
                FROM field_events
                WHERE task_id = ?
                ORDER BY created_at ASC
                """,
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def start(self, task_id: str, *, note: str = "", actor: str = "human") -> FieldTask:
        return self._set_status(task_id, "in_progress", action="started", note=note, actor=actor)

    def cancel(self, task_id: str, *, note: str = "", actor: str = "human") -> FieldTask:
        return self._set_status(task_id, "cancelled", action="cancelled", note=note, actor=actor)

    def complete(
        self,
        task_id: str,
        *,
        observations: str,
        confidence_update: str = "",
        attachments: list[str] | None = None,
        note: str = "",
        actor: str = "human",
    ) -> FieldTask:
        self.init()
        task = self.get(task_id)
        now = _now()
        combined_attachments = list(task.attachments)
        for attachment in attachments or []:
            if attachment and attachment not in combined_attachments:
                combined_attachments.append(attachment)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE field_tasks
                SET status = 'completed',
                    observations = ?,
                    confidence_update = ?,
                    attachments_json = ?,
                    updated_at = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (observations, confidence_update, _json(combined_attachments), now, now, task_id),
            )
            self._add_event(conn, task_id, "completed", actor, note or observations[:240], now)
        return self.get(task_id)

    def counts(self) -> dict[str, int]:
        self.init()
        with self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM field_tasks GROUP BY status ORDER BY status").fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def _set_status(self, task_id: str, status: str, *, action: str, note: str, actor: str) -> FieldTask:
        if status not in FIELD_STATUSES:
            raise ValueError(f"unknown field task status: {status}")
        self.init()
        self.get(task_id)
        now = _now()
        with self._connect() as conn:
            conn.execute("UPDATE field_tasks SET status = ?, updated_at = ? WHERE id = ?", (status, now, task_id))
            self._add_event(conn, task_id, action, actor, note, now)
        return self.get(task_id)

    def _add_event(self, conn: sqlite3.Connection, task_id: str, action: str, actor: str, note: str, created_at: str) -> None:
        conn.execute(
            """
            INSERT INTO field_events (id, task_id, action, actor, note, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (f"event:{uuid.uuid4().hex}", task_id, action, actor, note, created_at),
        )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def suggested_method(review: ReviewItem) -> str:
    methods = review.recommendation.get("field_validation") or []
    if methods:
        try:
            return _normalize_method(str(methods[0]))
        except ValueError:
            return "field_notebook"
    return "field_notebook"


def _task_from_row(row: sqlite3.Row) -> FieldTask:
    return FieldTask(
        id=str(row["id"]),
        status=str(row["status"]),
        review_item_id=str(row["review_item_id"]),
        method=str(row["method"]),
        title=str(row["title"]),
        question=str(row["question"]),
        location=str(row["location"]),
        priority=str(row["priority"]),
        assigned_to=str(row["assigned_to"]),
        due_date=str(row["due_date"]),
        geometry=_loads_dict(row["geometry_json"]),
        expected_evidence=_loads_list(row["expected_evidence_json"]),
        observations=str(row["observations"]),
        attachments=_loads_list(row["attachments_json"]),
        confidence_update=str(row["confidence_update"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        completed_at=str(row["completed_at"]),
    )


def _normalize_method(method: str) -> str:
    normalized = method.strip().lower().replace("-", "_")
    aliases = {
        "stream": "stream_obs",
        "stream_observation": "stream_obs",
        "field_notebook_and_sensors": "field_notebook",
        "notebook": "field_notebook",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in FIELD_METHODS:
        raise ValueError(f"unknown field validation method: {method}")
    return normalized


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
