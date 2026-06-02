from __future__ import annotations

import os
from pathlib import Path


def load_project_env(root: str | Path, *, override: bool = False) -> Path | None:
    """Load simple KEY=VALUE pairs from a project .env file.

    This intentionally supports the common .env subset used by Open Earth
    templates and avoids adding another runtime dependency. Existing environment
    variables win unless override=True.
    """

    path = Path(root).resolve() / ".env"
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or (not override and key in os.environ):
            continue
        os.environ[key] = _strip_quotes(value.strip())
    return path


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
