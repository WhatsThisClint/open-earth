from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from .loader import ManifestLoader
from .paths import project_state_dir


REQUIRED_PROJECT_DIRS = ("agents", "workflows", "mcp_servers", "skills", "data_sources")


def load_project(root: str | Path = ".") -> ManifestLoader:
    """Load and validate an Open Earth project."""
    loader = ManifestLoader(root)
    errors = loader.validate()
    if errors:
        raise ValueError("project validation failed:\n" + "\n".join(f"- {error}" for error in errors))
    return loader


def template_root():
    return resources.files("earthswarm").joinpath("templates", "earth_analysis")


def has_project_markers(root: str | Path) -> bool:
    path = Path(root)
    return any((path / name).exists() for name in REQUIRED_PROJECT_DIRS)


def copy_starter_template(destination: str | Path, force: bool = False) -> Path:
    """Copy the packaged Earth analysis starter template into destination."""
    dest = Path(destination).resolve()
    if dest.exists() and any(dest.iterdir()) and not force:
        raise FileExistsError(f"{dest} already exists and is not empty. Use --force to merge into it.")
    dest.mkdir(parents=True, exist_ok=True)

    with resources.as_file(template_root()) as src_path:
        for item in Path(src_path).iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=force)
            else:
                if target.exists() and not force:
                    raise FileExistsError(f"{target} already exists. Use --force to overwrite it.")
                shutil.copy2(item, target)
    project_state_dir(dest).mkdir(parents=True, exist_ok=True)
    (dest / "artifacts").mkdir(parents=True, exist_ok=True)
    return dest
