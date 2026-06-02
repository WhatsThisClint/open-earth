from __future__ import annotations

import os
from pathlib import Path

from .auth import OPENAI_CODEX_PROVIDER, AuthStore
from .codex_cli import codex_command_status
from .doctor import API_KEYS
from .loader import ManifestLoader
from .models import normalize_slug
from .nvidia_runner import nvidia_status
from .ollama_runner import ollama_status
from .project import has_project_markers


def run_setup_guide(root: str | Path, *, prefer: str = "codex-cli") -> int:
    root_path = Path(root).resolve()
    print("Open Earth setup")
    print(f"Root: {root_path}")
    print()

    if not has_project_markers(root_path):
        print("[fail] No Open Earth project found here.")
        print("Next:")
        print("  openearth init my-earth-project")
        print("  cd my-earth-project")
        print("  openearth setup")
        return 1

    loader = ManifestLoader(root_path)
    errors = loader.validate()
    if errors:
        print("[fail] Project manifests need attention:")
        for error in errors:
            print(f"  - {error}")
        print()
        print("Fix those first, then rerun: openearth setup")
        return 1

    print("[ok] Project manifests validate")
    print(f"[ok] Agents: {len(loader.agents())}, workflows: {len(loader.workflows())}, MCPs: {len(loader.mcp_servers())}")
    print("[ok] Dry-run mode is ready")
    print("     Try: openearth run diagnostic_report --task \"Diagnose my study area\"")
    print()

    _print_codex_section(prefer)
    print()
    _print_ollama_section()
    print()
    _print_nvidia_section()
    print()
    _print_agency_section()
    print()
    _print_qgis_section(loader)
    print()
    _print_provider_section(root_path)
    return 0


def _print_codex_section(prefer: str) -> None:
    ok, detail = codex_command_status()
    if ok:
        print(f"[ok] Codex CLI backend: {detail}")
        print("     Sign in: openearth auth login --provider codex-cli")
        print("     Run:     openearth run diagnostic_report --task \"...\" --live --backend codex-cli --model gpt-5.5")
    else:
        label = "[needed]" if prefer == "codex-cli" else "[warn]"
        print(f"{label} Codex CLI backend: {detail}")
        print("     Install/update: npm install -g @openai/codex")
        print("     Then sign in:   openearth auth login --provider codex-cli")

    oauth = AuthStore().status(OPENAI_CODEX_PROVIDER)
    if oauth.valid:
        print(f"[ok] OpenAI Codex OAuth profile: {oauth.profile_name or oauth.email or 'configured'}")
    else:
        print("[info] OpenAI Codex OAuth profile: optional future native harness route")
        print("       Optional: openearth auth login --provider openai-codex --device-code")


def _print_agency_section() -> None:
    keys = [key for key in API_KEYS if os.getenv(key)]
    if keys:
        print(f"[ok] API-key live route: {', '.join(keys)} present")
        print("     Run: openearth run diagnostic_report --task \"...\" --live --backend agency")
    else:
        print("[info] API-key live route: no provider keys found")
        print(f"       Set one of: {', '.join(API_KEYS)}")
        print("       Agency Swarm live mode uses provider keys, not ChatGPT OAuth tokens.")


def _print_ollama_section() -> None:
    ok, detail = ollama_status()
    if ok:
        print(f"[ok] Ollama local models: {detail}")
        print("     Direct local run: openearth run diagnostic_report --task \"...\" --live --backend ollama --model minimax-m3:cloud")
        print("     Codex+Ollama run: openearth run diagnostic_report --task \"...\" --live --backend codex-ollama --model minimax-m3:cloud")
    else:
        print(f"[info] Ollama local models: {detail}")
        print("       Install/pull models with: ollama run minimax-m3:cloud")


def _print_nvidia_section() -> None:
    ok, detail = nvidia_status()
    if ok:
        print(f"[ok] NVIDIA backend: {detail}")
        print("     Run: openearth run diagnostic_report --task \"...\" --live --backend nvidia --model moonshotai/kimi-k2.6")
    else:
        print(f"[info] NVIDIA backend: {detail}")
        print("       Set NVIDIA_API_KEY in .env or your shell to enable --backend nvidia")


def _print_qgis_section(loader: ManifestLoader) -> None:
    servers = loader.mcp_servers()
    qgis = servers.get("qgis")
    if not qgis:
        print("[info] QGIS MCP: no qgis.mcp.yaml found")
        return
    if qgis.enabled:
        print(f"[ok] QGIS MCP manifest enabled via {qgis.transport}")
        print("     Check: openearth mcp doctor qgis")
    else:
        print("[info] QGIS MCP manifest exists but is disabled")
        print("       Configure when QGIS MCP is ready:")
        print("       openearth mcp setup qgis --transport stdio --command qgis-mcp --enable --yes")
    attached = [
        agent.slug
        for agent in loader.agents().values()
        if normalize_slug("qgis") in {normalize_slug(server) for server in agent.mcp_servers}
    ]
    if attached:
        print(f"     Attached agents: {', '.join(attached)}")


def _print_provider_section(root: Path) -> None:
    providers = root / "providers.yaml"
    if providers.exists():
        print(f"[ok] Provider route notes: {providers}")
    else:
        print("[info] Provider route notes: providers.yaml not found; new templates include it")
    print()
    print("Recommended first-use order:")
    print("  1. Run dry mode once and inspect artifacts/")
    print("  2. Sign in with Codex CLI for ChatGPT-backed live runs")
    print("  3. Enable QGIS MCP only after your local QGIS MCP command or URL works")
