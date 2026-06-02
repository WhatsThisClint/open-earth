from __future__ import annotations

from pathlib import Path


STATE_DIR_NAME = ".openearth"
LEGACY_STATE_DIR_NAME = ".earthswarm"


def project_state_dir(root: str | Path, *, filename: str | None = None) -> Path:
    """Return the runtime state directory for an Open Earth project.

    New projects use `.openearth`. Existing projects that already have a
    `.earthswarm/<filename>` database keep reading that file until the user
    migrates it, so older work is not orphaned by the rename.
    """

    root_path = Path(root).resolve()
    current = root_path / STATE_DIR_NAME
    legacy = root_path / LEGACY_STATE_DIR_NAME
    if filename and not current.exists() and (legacy / filename).exists():
        return legacy
    return current


def project_state_file(root: str | Path, filename: str) -> Path:
    return project_state_dir(root, filename=filename) / filename

