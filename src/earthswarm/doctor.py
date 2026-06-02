from __future__ import annotations

import os
import platform
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import yaml

from . import agency_adapter
from .auth import OPENAI_CODEX_PROVIDER, AuthStore
from .codex_cli import codex_command_status
from .loader import ManifestLoader
from .models import normalize_slug
from .project import has_project_markers


API_KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY")


def run_doctor(root: str | Path) -> int:
    root_path = Path(root).resolve()
    issues = 0
    print("Open Earth doctor")
    print(f"Root: {root_path}")
    print()

    issues += _check(
        sys.version_info >= (3, 12),
        f"Python {platform.python_version()}",
        "Python 3.12 or newer is required.",
    )
    issues += _check(True, "openearth package import", "Package imported.")

    present_keys = [key for key in API_KEYS if os.getenv(key)]
    if present_keys:
        print(f"[ok] API keys present: {', '.join(present_keys)}")
    else:
        print(f"[warn] API keys: none of {', '.join(API_KEYS)} are set")

    if agency_adapter.is_available():
        print("[ok] Agency Swarm backend available")
    else:
        print("[warn] Agency Swarm backend not installed; live mode and tui are unavailable")

    codex_ok, codex_detail = codex_command_status()
    if codex_ok:
        print(f"[ok] Codex CLI: {codex_detail}")
    else:
        print(f"[warn] Codex CLI: {codex_detail}")

    codex_oauth = AuthStore().status(OPENAI_CODEX_PROVIDER)
    if codex_oauth.valid:
        label = codex_oauth.profile_name or codex_oauth.email or codex_oauth.account_id or "configured"
        print(f"[ok] ChatGPT/Codex OAuth profile: {label}")
    elif codex_oauth.configured:
        print(f"[warn] ChatGPT/Codex OAuth profile: {codex_oauth.detail}")
    else:
        print("[warn] ChatGPT/Codex OAuth profile: not configured")

    if has_project_markers(root_path):
        loader = ManifestLoader(root_path)
        errors = loader.validate()
        if errors:
            issues += 1
            print("[fail] Project manifests")
            for error in errors:
                print(f"  - {error}")
        else:
            print("[ok] Project manifests validate")
            print(f"[ok] Agents: {len(loader.agents())}, workflows: {len(loader.workflows())}, MCPs: {len(loader.mcp_servers())}")
    else:
        print("[warn] No Open Earth project found here. Run: openearth init <project>")

    return 1 if issues else 0


def doctor_mcp(root: str | Path, name: str) -> int:
    loader = ManifestLoader(root)
    servers = loader.mcp_servers()
    slug = normalize_slug(name)
    if slug not in servers:
        print(f"[fail] MCP server not found: {name}")
        return 1
    spec = servers[slug]
    print(f"MCP doctor: {spec.slug}")
    print(f"Transport: {spec.transport}")
    print(f"Enabled: {spec.enabled}")
    print(f"Approval: {spec.approval}")

    if not spec.enabled:
        print("[warn] MCP server is disabled. Enable it after setup when you are ready.")
        return 0
    if spec.transport in {"stdio", "local"}:
        return _doctor_stdio(spec.command)
    if spec.transport in {"sse", "streamable_http", "hosted"}:
        return _doctor_url(spec.url)
    print(f"[fail] Unsupported transport: {spec.transport}")
    return 1


def setup_qgis_mcp(
    root: str | Path,
    transport: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    url: str | None = None,
    enable: bool | None = None,
    interactive: bool = False,
) -> Path:
    mcp_path = Path(root).resolve() / "mcp_servers" / "qgis.mcp.yaml"
    if not mcp_path.exists():
        raise FileNotFoundError(f"QGIS MCP manifest not found: {mcp_path}")
    data = yaml.safe_load(mcp_path.read_text(encoding="utf-8")) or {}
    if interactive:
        transport = _ask("Transport (stdio/sse/streamable_http)", transport or data.get("transport") or "stdio")
        if transport in {"stdio", "local"}:
            command = _ask("Command", command or data.get("command") or "qgis-mcp")
        else:
            url = _ask("URL", url or data.get("url") or "http://localhost:8000/mcp")
        enable = _ask("Enable now? (y/n)", "y" if enable else "n").lower().startswith("y")

    data["transport"] = transport or data.get("transport") or "stdio"
    if data["transport"] in {"stdio", "local"}:
        data["command"] = command or data.get("command") or "qgis-mcp"
        data["args"] = args if args is not None else list(data.get("args") or [])
        data["url"] = None
    else:
        data["url"] = url or data.get("url") or "http://localhost:8000/mcp"
        data["command"] = None
        data["args"] = []
    if enable is not None:
        data["enabled"] = bool(enable)
    data.setdefault("approval", "ask")
    data.setdefault("cache_tools", True)
    data.setdefault("timeout_seconds", 180)
    data.setdefault("tool_policy", {"allowed_tool_names": [], "blocked_tool_names": []})
    mcp_path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return mcp_path


def _doctor_stdio(command: str | None) -> int:
    if not command:
        print("[fail] stdio MCP requires a command")
        return 1
    exists = Path(command).exists() if any(sep in command for sep in ("\\", "/")) else bool(shutil.which(command))
    if exists:
        print(f"[ok] Command found: {command}")
        return 0
    print(f"[fail] Command not found on PATH: {command}")
    return 1


def _doctor_url(url: str | None) -> int:
    if not url:
        print("[fail] HTTP MCP transport requires a URL")
        return 1
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        print(f"[fail] Invalid URL: {url}")
        return 1
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=2) as response:
            print(f"[ok] URL reachable: HTTP {response.status}")
            return 0
    except urllib.error.HTTPError as exc:
        print(f"[ok] URL reachable: HTTP {exc.code}")
        return 0
    except Exception as exc:
        print(f"[fail] URL not reachable: {exc}")
        return 1


def _check(ok: bool, label: str, detail: str) -> int:
    if ok:
        print(f"[ok] {label}")
        return 0
    print(f"[fail] {label}: {detail}")
    return 1


def _ask(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default
