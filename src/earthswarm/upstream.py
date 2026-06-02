from __future__ import annotations

import fnmatch
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable

import yaml

from .paths import project_state_file


FetchCommit = Callable[[str, str], dict[str, Any]]
FetchCompare = Callable[[str, str, str], dict[str, Any]]


@dataclass(frozen=True)
class UpstreamSpec:
    name: str
    repo: str
    branch: str = "main"
    url: str = ""
    kind: str = "pattern"
    last_reviewed_sha: str = ""
    watch_reason: str = ""
    watched_paths: list[str] = field(default_factory=list)
    upgrade_notes: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "UpstreamSpec":
        return cls(
            name=str(data["name"]),
            repo=str(data["repo"]),
            branch=str(data.get("branch") or "main"),
            url=str(data.get("url") or f"https://github.com/{data['repo']}"),
            kind=str(data.get("kind") or "pattern"),
            last_reviewed_sha=str(data.get("last_reviewed_sha") or ""),
            watch_reason=str(data.get("watch_reason") or ""),
            watched_paths=list(data.get("watched_paths") or []),
            upgrade_notes=list(data.get("upgrade_notes") or []),
        )


@dataclass(frozen=True)
class UpstreamStatus:
    name: str
    repo: str
    branch: str
    status: str
    latest_sha: str = ""
    latest_date: str = ""
    latest_message: str = ""
    latest_url: str = ""
    reviewed_sha: str = ""
    compare_url: str = ""
    changed_files: list[str] = field(default_factory=list)
    watched_changed_files: list[str] = field(default_factory=list)
    watch_reason: str = ""
    upgrade_notes: list[str] = field(default_factory=list)
    error: str = ""


def default_config_path(root: str | Path) -> Path | None:
    candidate = Path(root).resolve() / "upstreams.yaml"
    return candidate if candidate.exists() else None


def load_upstreams(config_path: str | Path | None = None) -> list[UpstreamSpec]:
    if config_path:
        data = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
    else:
        with resources.as_file(resources.files("earthswarm").joinpath("templates", "earth_analysis", "upstreams.yaml")) as path:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rows = data.get("upstreams") or []
    if not isinstance(rows, list):
        raise ValueError("upstreams.yaml requires an 'upstreams' list")
    return [UpstreamSpec.from_mapping(row) for row in rows]


def check_upstreams(
    root: str | Path = ".",
    config_path: str | Path | None = None,
    state_path: str | Path | None = None,
    include_files: bool = True,
    update_state: bool = False,
    fetch_commit: FetchCommit | None = None,
    fetch_compare: FetchCompare | None = None,
) -> list[UpstreamStatus]:
    root_path = Path(root).resolve()
    config = Path(config_path).resolve() if config_path else default_config_path(root_path)
    specs = load_upstreams(config)
    state_file = Path(state_path).resolve() if state_path else project_state_file(root_path, "upstream_state.json")
    state = _read_state(state_file)
    fetch_commit = fetch_commit or fetch_github_commit
    fetch_compare = fetch_compare or fetch_github_compare

    statuses: list[UpstreamStatus] = []
    for spec in specs:
        reviewed_sha = _reviewed_sha(spec, state)
        try:
            latest = fetch_commit(spec.repo, spec.branch)
            latest_sha = str(latest.get("sha") or "")
            commit = latest.get("commit") or {}
            committer = commit.get("committer") or commit.get("author") or {}
            latest_date = str(committer.get("date") or "")
            latest_message = str(commit.get("message") or "").splitlines()[0]
            latest_url = str(latest.get("html_url") or spec.url)
            compare_url = ""
            changed_files: list[str] = []
            watched_changed_files: list[str] = []
            if include_files and reviewed_sha and latest_sha and not _same_sha(reviewed_sha, latest_sha):
                compare = fetch_compare(spec.repo, reviewed_sha, latest_sha)
                compare_url = str(compare.get("html_url") or f"https://github.com/{spec.repo}/compare/{reviewed_sha}...{latest_sha}")
                changed_files = [str(row.get("filename")) for row in compare.get("files") or [] if row.get("filename")]
                watched_changed_files = _filter_watched_files(changed_files, spec.watched_paths)
            status = _status(reviewed_sha, latest_sha)
            statuses.append(
                UpstreamStatus(
                    name=spec.name,
                    repo=spec.repo,
                    branch=spec.branch,
                    status=status,
                    latest_sha=latest_sha,
                    latest_date=latest_date,
                    latest_message=latest_message,
                    latest_url=latest_url,
                    reviewed_sha=reviewed_sha,
                    compare_url=compare_url,
                    changed_files=changed_files,
                    watched_changed_files=watched_changed_files,
                    watch_reason=spec.watch_reason,
                    upgrade_notes=spec.upgrade_notes,
                )
            )
        except Exception as exc:
            statuses.append(
                UpstreamStatus(
                    name=spec.name,
                    repo=spec.repo,
                    branch=spec.branch,
                    status="error",
                    reviewed_sha=reviewed_sha,
                    watch_reason=spec.watch_reason,
                    upgrade_notes=spec.upgrade_notes,
                    error=str(exc),
                )
            )

    if update_state:
        write_state(state_file, statuses)
    return statuses


def fetch_github_commit(repo: str, branch: str) -> dict[str, Any]:
    return _github_json(f"https://api.github.com/repos/{repo}/commits/{branch}")


def fetch_github_compare(repo: str, base: str, head: str) -> dict[str, Any]:
    return _github_json(f"https://api.github.com/repos/{repo}/compare/{base}...{head}")


def format_upstream_report(statuses: list[UpstreamStatus]) -> str:
    lines = ["Upstream check", ""]
    for status in statuses:
        label = status.status.replace("_", " ")
        lines.append(f"{status.name} ({status.repo}) - {label}")
        if status.error:
            lines.append(f"  error: {status.error}")
        if status.latest_sha:
            lines.append(f"  latest:  {status.latest_sha[:12]} {status.latest_date}")
        if status.reviewed_sha:
            lines.append(f"  reviewed: {status.reviewed_sha[:12]}")
        if status.latest_message:
            lines.append(f"  message: {status.latest_message}")
        if status.compare_url:
            lines.append(f"  compare: {status.compare_url}")
        if status.watched_changed_files:
            lines.append("  watched files changed:")
            for file_name in status.watched_changed_files[:10]:
                lines.append(f"    - {file_name}")
        elif status.changed_files:
            lines.append(f"  changed files: {len(status.changed_files)} total, none matched watched paths")
        if status.watch_reason:
            lines.append(f"  why watch: {status.watch_reason}")
        if status.status == "update_available":
            lines.append("  action: review changes, run tests and benchmarks, then adapt Open Earth intentionally.")
        elif status.status == "untracked":
            lines.append("  action: review once, then run with --update-state to record a baseline.")
        lines.append("")
    lines.append("No code was changed. This command only reports upstream drift.")
    return "\n".join(lines)


def statuses_to_json(statuses: list[UpstreamStatus]) -> str:
    return json.dumps([asdict(status) for status in statuses], indent=2)


def write_state(path: str | Path, statuses: list[UpstreamStatus]) -> Path:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "upstreams": {
            status.name: {
                "repo": status.repo,
                "branch": status.branch,
                "sha": status.latest_sha,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            for status in statuses
            if status.latest_sha and status.status != "error"
        },
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return state_path


def _github_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "earthswarm-upstream-check"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub request failed: HTTP {exc.code} {body[:200]}") from exc


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _reviewed_sha(spec: UpstreamSpec, state: dict[str, Any]) -> str:
    upstreams = state.get("upstreams") or {}
    state_row = upstreams.get(spec.name) or upstreams.get(spec.repo) or {}
    return str(state_row.get("sha") or spec.last_reviewed_sha or "")


def _same_sha(left: str, right: str) -> bool:
    return bool(left and right and (left == right or left.startswith(right) or right.startswith(left)))


def _status(reviewed_sha: str, latest_sha: str) -> str:
    if not latest_sha:
        return "error"
    if not reviewed_sha:
        return "untracked"
    if _same_sha(reviewed_sha, latest_sha):
        return "current"
    return "update_available"


def _filter_watched_files(files: list[str], patterns: list[str]) -> list[str]:
    if not patterns:
        return []
    return [file_name for file_name in files if any(_path_matches(file_name, pattern) for pattern in patterns)]


def _path_matches(file_name: str, pattern: str) -> bool:
    file_norm = file_name.replace("\\", "/")
    pattern_norm = pattern.replace("\\", "/")
    if pattern_norm.endswith("/"):
        return file_norm.startswith(pattern_norm)
    return fnmatch.fnmatch(file_norm, pattern_norm) or file_norm == pattern_norm or file_norm.startswith(pattern_norm.rstrip("/") + "/")
