from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .paths import project_state_file


EVIDENCE_TYPES = {
    "dataset",
    "map",
    "report_section",
    "field_note",
    "model_output",
    "analysis_output",
    "claim_support",
    "blocker",
}

CLAIM_TYPES = {
    "finding",
    "diagnosis",
    "risk",
    "recommendation",
    "assumption",
    "blocker",
    "uncertainty",
}

REVIEW_STATUSES = {
    "open",
    "approved",
    "corrected",
    "rejected",
    "uncertain",
    "needs_field_validation",
    "closed",
}


@dataclass(frozen=True)
class EvidenceRecord:
    id: str
    evidence_type: str
    title: str
    source: str
    source_url: str
    access_date: str
    license: str
    crs: str
    spatial_extent: str
    resolution: str
    time_period: str
    local_path: str
    uncertainty: str
    reviewer_status: str
    created_by: str
    run_id: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ClaimRecord:
    id: str
    claim_type: str
    statement: str
    status: str
    confidence: str
    evidence_ids: list[str]
    agent: str
    workflow: str
    run_id: str
    uncertainty: str
    reviewer_status: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str


class EvidenceStore:
    """Typed evidence and claim memory for an Open Earth project."""

    def __init__(self, root: str | Path, path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(path).resolve() if path else project_state_file(self.root, "evidence.sqlite")

    def init(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS evidence_records (
                    id TEXT PRIMARY KEY,
                    evidence_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    access_date TEXT NOT NULL DEFAULT '',
                    license TEXT NOT NULL DEFAULT '',
                    crs TEXT NOT NULL DEFAULT '',
                    spatial_extent TEXT NOT NULL DEFAULT '',
                    resolution TEXT NOT NULL DEFAULT '',
                    time_period TEXT NOT NULL DEFAULT '',
                    local_path TEXT NOT NULL DEFAULT '',
                    uncertainty TEXT NOT NULL DEFAULT '',
                    reviewer_status TEXT NOT NULL DEFAULT 'open',
                    created_by TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence_records(evidence_type);
                CREATE INDEX IF NOT EXISTS idx_evidence_status ON evidence_records(reviewer_status);
                CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence_records(run_id);

                CREATE TABLE IF NOT EXISTS claim_records (
                    id TEXT PRIMARY KEY,
                    claim_type TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    confidence TEXT NOT NULL DEFAULT 'medium',
                    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
                    agent TEXT NOT NULL DEFAULT '',
                    workflow TEXT NOT NULL DEFAULT '',
                    run_id TEXT NOT NULL DEFAULT '',
                    uncertainty TEXT NOT NULL DEFAULT '',
                    reviewer_status TEXT NOT NULL DEFAULT 'open',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_claim_type ON claim_records(claim_type);
                CREATE INDEX IF NOT EXISTS idx_claim_status ON claim_records(status);
                CREATE INDEX IF NOT EXISTS idx_claim_reviewer_status ON claim_records(reviewer_status);
                CREATE INDEX IF NOT EXISTS idx_claim_run ON claim_records(run_id);
                """
            )
        return self.path

    def add_evidence(
        self,
        *,
        evidence_type: str,
        title: str,
        source: str = "",
        source_url: str = "",
        access_date: str = "",
        license: str = "",
        crs: str = "",
        spatial_extent: str = "",
        resolution: str = "",
        time_period: str = "",
        local_path: str | Path = "",
        uncertainty: str = "",
        reviewer_status: str = "open",
        created_by: str = "",
        run_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        self.init()
        evidence_type = _normalize_choice(evidence_type, EVIDENCE_TYPES, "evidence type")
        reviewer_status = _normalize_choice(reviewer_status, REVIEW_STATUSES, "reviewer status")
        if not title.strip():
            raise ValueError("evidence title is required")
        now = _now()
        record_id = f"ev_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        local_path_text = _relative_path(self.root, local_path) if local_path else ""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evidence_records (
                    id, evidence_type, title, source, source_url, access_date,
                    license, crs, spatial_extent, resolution, time_period,
                    local_path, uncertainty, reviewer_status, created_by, run_id,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    evidence_type,
                    title.strip(),
                    source.strip(),
                    source_url.strip(),
                    access_date.strip(),
                    license.strip(),
                    crs.strip(),
                    spatial_extent.strip(),
                    resolution.strip(),
                    time_period.strip(),
                    local_path_text,
                    uncertainty.strip(),
                    reviewer_status,
                    created_by.strip(),
                    run_id.strip(),
                    _json(metadata or {}),
                    now,
                    now,
                ),
            )
        return self.get_evidence(record_id)

    def add_claim(
        self,
        *,
        statement: str,
        claim_type: str = "finding",
        status: str = "draft",
        confidence: str = "medium",
        evidence_ids: list[str] | None = None,
        agent: str = "",
        workflow: str = "",
        run_id: str = "",
        uncertainty: str = "",
        reviewer_status: str = "open",
        metadata: dict[str, Any] | None = None,
    ) -> ClaimRecord:
        self.init()
        claim_type = _normalize_choice(claim_type, CLAIM_TYPES, "claim type")
        reviewer_status = _normalize_choice(reviewer_status, REVIEW_STATUSES, "reviewer status")
        status = status.strip().lower().replace("-", "_") or "draft"
        if not statement.strip():
            raise ValueError("claim statement is required")
        now = _now()
        record_id = f"cl_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:6]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO claim_records (
                    id, claim_type, statement, status, confidence, evidence_ids_json,
                    agent, workflow, run_id, uncertainty, reviewer_status,
                    metadata_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    claim_type,
                    statement.strip(),
                    status,
                    confidence.strip() or "medium",
                    _json(_clean_list(evidence_ids or [])),
                    agent.strip(),
                    workflow.strip(),
                    run_id.strip(),
                    uncertainty.strip(),
                    reviewer_status,
                    _json(metadata or {}),
                    now,
                    now,
                ),
            )
        return self.get_claim(record_id)

    def list_evidence(
        self,
        *,
        evidence_type: str = "all",
        reviewer_status: str = "all",
        limit: int = 50,
    ) -> list[EvidenceRecord]:
        self.init()
        clauses: list[str] = []
        params: list[Any] = []
        if evidence_type != "all":
            clauses.append("evidence_type = ?")
            params.append(_normalize_choice(evidence_type, EVIDENCE_TYPES, "evidence type"))
        if reviewer_status != "all":
            clauses.append("reviewer_status = ?")
            params.append(_normalize_choice(reviewer_status, REVIEW_STATUSES, "reviewer status"))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM evidence_records {where} ORDER BY created_at DESC LIMIT ?",
                (*params, max(1, int(limit))),
            ).fetchall()
        return [_evidence_from_row(row) for row in rows]

    def list_claims(
        self,
        *,
        claim_type: str = "all",
        reviewer_status: str = "all",
        limit: int = 50,
    ) -> list[ClaimRecord]:
        self.init()
        clauses: list[str] = []
        params: list[Any] = []
        if claim_type != "all":
            clauses.append("claim_type = ?")
            params.append(_normalize_choice(claim_type, CLAIM_TYPES, "claim type"))
        if reviewer_status != "all":
            clauses.append("reviewer_status = ?")
            params.append(_normalize_choice(reviewer_status, REVIEW_STATUSES, "reviewer status"))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM claim_records {where} ORDER BY created_at DESC LIMIT ?",
                (*params, max(1, int(limit))),
            ).fetchall()
        return [_claim_from_row(row) for row in rows]

    def get_evidence(self, evidence_id: str) -> EvidenceRecord:
        self.init()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evidence_records WHERE id = ?", (evidence_id,)).fetchone()
        if row is None:
            raise KeyError(f"evidence record not found: {evidence_id}")
        return _evidence_from_row(row)

    def get_claim(self, claim_id: str) -> ClaimRecord:
        self.init()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM claim_records WHERE id = ?", (claim_id,)).fetchone()
        if row is None:
            raise KeyError(f"claim record not found: {claim_id}")
        return _claim_from_row(row)

    def counts(self) -> dict[str, int]:
        self.init()
        with self._connect() as conn:
            evidence = conn.execute(
                "SELECT evidence_type, COUNT(*) AS count FROM evidence_records GROUP BY evidence_type ORDER BY evidence_type"
            ).fetchall()
            claims = conn.execute(
                "SELECT claim_type, COUNT(*) AS count FROM claim_records GROUP BY claim_type ORDER BY claim_type"
            ).fetchall()
        return {
            "evidence": {str(row["evidence_type"]): int(row["count"]) for row in evidence},
            "claims": {str(row["claim_type"]): int(row["count"]) for row in claims},
        }

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def record_step_evidence(
    *,
    root: str | Path,
    workflow: str,
    run_id: str,
    step_id: str,
    agent: str,
    summary: str,
    output_paths: list[str] | None = None,
    status: str = "draft",
    backend: str = "dry_run",
    metadata: dict[str, Any] | None = None,
) -> tuple[EvidenceRecord, ClaimRecord]:
    """Persist an agent step as typed evidence plus a draft claim."""

    store = EvidenceStore(root)
    first_output = (output_paths or [""])[0]
    evidence = store.add_evidence(
        evidence_type="model_output",
        title=f"{agent} output for {step_id}",
        source=f"agent:{agent}",
        local_path=first_output,
        uncertainty="Model-generated output; requires evidence review before use as a final claim.",
        reviewer_status="open",
        created_by=agent,
        run_id=run_id,
        metadata={
            "workflow": workflow,
            "step_id": step_id,
            "backend": backend,
            "outputs": output_paths or [],
            **(metadata or {}),
        },
    )
    claim = store.add_claim(
        claim_type="finding",
        statement=summary,
        status=status,
        confidence="low" if backend == "dry_run" else "medium",
        evidence_ids=[evidence.id],
        agent=agent,
        workflow=workflow,
        run_id=run_id,
        uncertainty="Review the cited evidence and provenance before promoting this claim.",
        reviewer_status="open",
        metadata={"step_id": step_id, "backend": backend, **(metadata or {})},
    )
    return evidence, claim


def _evidence_from_row(row: sqlite3.Row) -> EvidenceRecord:
    return EvidenceRecord(
        id=str(row["id"]),
        evidence_type=str(row["evidence_type"]),
        title=str(row["title"]),
        source=str(row["source"]),
        source_url=str(row["source_url"]),
        access_date=str(row["access_date"]),
        license=str(row["license"]),
        crs=str(row["crs"]),
        spatial_extent=str(row["spatial_extent"]),
        resolution=str(row["resolution"]),
        time_period=str(row["time_period"]),
        local_path=str(row["local_path"]),
        uncertainty=str(row["uncertainty"]),
        reviewer_status=str(row["reviewer_status"]),
        created_by=str(row["created_by"]),
        run_id=str(row["run_id"]),
        metadata=_loads_dict(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _claim_from_row(row: sqlite3.Row) -> ClaimRecord:
    return ClaimRecord(
        id=str(row["id"]),
        claim_type=str(row["claim_type"]),
        statement=str(row["statement"]),
        status=str(row["status"]),
        confidence=str(row["confidence"]),
        evidence_ids=_loads_list(row["evidence_ids_json"]),
        agent=str(row["agent"]),
        workflow=str(row["workflow"]),
        run_id=str(row["run_id"]),
        uncertainty=str(row["uncertainty"]),
        reviewer_status=str(row["reviewer_status"]),
        metadata=_loads_dict(row["metadata_json"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _normalize_choice(value: str, allowed: set[str], label: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    if normalized not in allowed:
        raise ValueError(f"unknown {label}: {value}")
    return normalized


def _clean_list(values: list[str]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


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
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_path(root: Path, path: str | Path) -> str:
    target = Path(path).resolve()
    try:
        return str(target.relative_to(root))
    except ValueError:
        return str(target)

