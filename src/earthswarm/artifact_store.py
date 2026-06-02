from __future__ import annotations

import json
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ArtifactStore:
    """Run-scoped artifact ledger for maps, layers, evidence, reports, and slides."""

    SUBDIRS = ("layers", "maps", "tables", "evidence", "reports", "slides", "logs")

    def __init__(self, root: str | Path, workflow_slug: str):
        self.root = Path(root).resolve()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.run_id = f"{workflow_slug}-{stamp}-{uuid.uuid4().hex[:6]}"
        self.run_dir = self.root / self.run_id
        for name in self.SUBDIRS:
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)
        self.ledger_path = self.run_dir / "ledger.jsonl"
        self.summary_path = self.run_dir / "run_summary.json"

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "time": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": _jsonable(payload),
        }
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def write_summary(self, payload: dict[str, Any]) -> Path:
        self.summary_path.write_text(
            json.dumps(_jsonable(payload), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.summary_path


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value
