from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import agency_adapter
from .agent_authoring import create_agent
from .auth import (
    OPENAI_CODEX_PROVIDER,
    AuthStore,
    login_openai_codex_device_code,
    normalize_auth_provider,
    refresh_openai_codex,
)
from .codex_cli import (
    CodexCliWorkflowRunner,
    codex_command_status,
    run_codex_login,
    run_codex_logout,
)
from .data_acquisition import DataAcquisitionCatalog
from .dashboard import start_dashboard
from .doctor import doctor_mcp, run_doctor, setup_qgis_mcp
from .env import load_project_env
from .evidence import CLAIM_TYPES, EVIDENCE_TYPES, REVIEW_STATUSES, EvidenceStore
from .field_validation import FIELD_METHODS, FieldValidationStore
from .graph_store import GraphStore
from .learning_memory import LEARNING_KINDS, LearningMemoryStore
from .loader import ManifestLoader
from .mcp_registry import McpRegistry
from .models import normalize_slug
from .nvidia_runner import NvidiaWorkflowRunner, nvidia_status
from .ollama_runner import OllamaWorkflowRunner, list_ollama_models, ollama_status
from .provider_router import ProviderRouter
from .project import copy_starter_template
from .review_queue import ACTION_STATUS, ReviewQueue
from .setup_guide import run_setup_guide
from .trace import make_tracer
from .trigger_engine import TriggerEngine, load_event
from .upstream import check_upstreams, format_upstream_report, statuses_to_json
from .workflow_runner import FastWorkflowRunner, WorkflowRunResult


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = Path(getattr(args, "root", ".")).resolve()
    load_project_env(root)
    try:
        return _dispatch(args, root)
    except (FileExistsError, FileNotFoundError, KeyError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=_program_name())
    parser.add_argument("--root", default=".", help="Open Earth project root")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init", help="Create a new Open Earth project from the packaged template")
    init_p.add_argument("project")
    init_p.add_argument("--force", action="store_true", help="Merge into an existing directory")

    list_p = sub.add_parser("list", help="List manifests")
    list_p.add_argument("kind", choices=["agents", "workflows", "mcps", "skills"])

    sub.add_parser("validate", help="Validate manifests")

    show_p = sub.add_parser("show", help="Show a manifest")
    show_p.add_argument("kind", choices=["agent", "workflow", "mcp"])
    show_p.add_argument("name")

    run_p = sub.add_parser("run", help="Run a workflow")
    run_p.add_argument("workflow")
    run_p.add_argument("--task", required=True)
    run_p.add_argument("--live", action="store_true", help="Run with Agency Swarm instead of dry-run mode")
    run_p.add_argument(
        "--backend",
        choices=["agency", "codex-cli", "codex-ollama", "ollama", "nvidia"],
        default="agency",
        help="Live execution backend",
    )
    run_p.add_argument("--model", help="Model for live backends that accept one, e.g. gpt-5.5 for codex-cli")
    run_p.add_argument("--codex-command", help="Command line used by --backend codex-cli")
    run_p.add_argument("--ollama-host", help="Ollama host URL for --backend ollama")
    run_p.add_argument("--nvidia-url", help="Invoke URL for --backend nvidia")
    run_p.add_argument("--no-stream", action="store_true", help="Disable streaming for backends that support it")
    run_p.add_argument("--artifact-root")
    run_p.add_argument("--json", action="store_true")
    run_p.add_argument("--quiet", action="store_true", help="Suppress live progress trace output")
    run_p.add_argument("--no-graph", action="store_true", help="Do not index this run in the local graph store")

    sub.add_parser("tui", help="Launch the Agency Swarm interactive terminal UI")
    dashboard_p = sub.add_parser("dashboard", help="Launch the local web dashboard")
    dashboard_p.add_argument("--host", default="127.0.0.1")
    dashboard_p.add_argument("--port", type=int, default=8765)
    dashboard_p.add_argument("--no-open", action="store_true", help="Do not open a browser automatically")
    dashboard_p.add_argument("--insecure", action="store_true", help="Allow binding outside loopback; use only on trusted networks")
    sub.add_parser("doctor", help="Diagnose local setup and project health")
    setup_p = sub.add_parser("setup", help="Show first-run setup status and exact next commands")
    setup_p.add_argument("--prefer", choices=["codex-cli", "agency"], default="codex-cli")

    ollama_p = sub.add_parser("ollama", help="Inspect local Ollama models")
    ollama_sub = ollama_p.add_subparsers(dest="ollama_command", required=True)
    ollama_sub.add_parser("list", help="List installed Ollama models")
    ollama_status_p = ollama_sub.add_parser("status", help="Check Ollama availability")
    ollama_status_p.add_argument("--host")

    nvidia_p = sub.add_parser("nvidia", help="Inspect NVIDIA API backend")
    nvidia_sub = nvidia_p.add_subparsers(dest="nvidia_command", required=True)
    nvidia_sub.add_parser("status", help="Check NVIDIA backend configuration")

    provider_p = sub.add_parser("providers", help="Inspect model/provider routes")
    provider_sub = provider_p.add_subparsers(dest="provider_command", required=True)
    provider_sub.add_parser("list", help="List provider profiles")
    provider_sub.add_parser("status", help="Check provider profile availability")

    data_p = sub.add_parser("data", help="Stage public datasets or record access blockers")
    data_sub = data_p.add_subparsers(dest="data_command", required=True)
    data_recipes = data_sub.add_parser("recipes", help="List deterministic acquisition recipes")
    data_recipes.add_argument("--json", action="store_true")
    data_stage = data_sub.add_parser("stage", help="Stage one acquisition recipe and create evidence")
    data_stage.add_argument("recipe")
    data_stage.add_argument("--aoi", default="", help="AOI label, bbox, or path")
    data_stage.add_argument("--time-period", default="", help="Requested date range or season")
    data_stage.add_argument("--output-dir", help="Override data/staged output root")
    data_stage.add_argument("--allow-download", action="store_true", help="Allow direct public downloads when recipe supports it")
    data_stage.add_argument("--note", default="")
    data_stage.add_argument("--json", action="store_true")

    graph_p = sub.add_parser("graph", help="Inspect and build the local Graph RAG store")
    graph_sub = graph_p.add_subparsers(dest="graph_command", required=True)
    graph_sub.add_parser("init", help="Create the local SQLite graph store")
    graph_sub.add_parser("status", help="Show graph store counts")
    graph_sub.add_parser("ingest-project", help="Index agents, workflows, MCPs, skills, and data catalogs")
    ingest_artifacts = graph_sub.add_parser("ingest-artifacts", help="Index run artifacts")
    ingest_artifacts.add_argument("--run", dest="run_id", help="Only ingest one run id")
    query_p = graph_sub.add_parser("query", help="Search graph nodes and document chunks")
    query_p.add_argument("query", nargs="?", default="")
    query_p.add_argument("--limit", type=int, default=10)
    show_p = graph_sub.add_parser("show", help="Show one node and its graph neighborhood")
    show_p.add_argument("identifier")
    export_p = graph_sub.add_parser("export", help="Export graph records as JSONL")
    export_p.add_argument("path", nargs="?")

    evidence_p = sub.add_parser("evidence", help="Inspect typed evidence and claims")
    evidence_sub = evidence_p.add_subparsers(dest="evidence_command", required=True)
    evidence_list = evidence_sub.add_parser("list", help="List evidence records")
    evidence_list.add_argument("--type", choices=["all", *sorted(EVIDENCE_TYPES)], default="all")
    evidence_list.add_argument("--status", choices=["all", *sorted(REVIEW_STATUSES)], default="all")
    evidence_list.add_argument("--limit", type=int, default=50)
    evidence_claims = evidence_sub.add_parser("claims", help="List claim records")
    evidence_claims.add_argument("--type", choices=["all", *sorted(CLAIM_TYPES)], default="all")
    evidence_claims.add_argument("--status", choices=["all", *sorted(REVIEW_STATUSES)], default="all")
    evidence_claims.add_argument("--limit", type=int, default=50)
    evidence_show = evidence_sub.add_parser("show", help="Show one evidence or claim record")
    evidence_show.add_argument("id")
    evidence_add = evidence_sub.add_parser("add", help="Add a manual evidence record")
    evidence_add.add_argument("--type", choices=sorted(EVIDENCE_TYPES), default="dataset")
    evidence_add.add_argument("--title", required=True)
    evidence_add.add_argument("--source", default="")
    evidence_add.add_argument("--source-url", default="")
    evidence_add.add_argument("--license", default="")
    evidence_add.add_argument("--crs", default="")
    evidence_add.add_argument("--spatial-extent", default="")
    evidence_add.add_argument("--resolution", default="")
    evidence_add.add_argument("--time-period", default="")
    evidence_add.add_argument("--local-path", default="")
    evidence_add.add_argument("--uncertainty", default="")
    evidence_add.add_argument("--created-by", default="human")

    trigger_p = sub.add_parser("trigger", help="Evaluate monitoring events against trigger rules")
    trigger_sub = trigger_p.add_subparsers(dest="trigger_command", required=True)
    trigger_sub.add_parser("list", help="List trigger rules")
    trigger_eval = trigger_sub.add_parser("evaluate", help="Evaluate one event and optionally create review items")
    trigger_eval.add_argument("--event", help="JSON/YAML event file")
    trigger_eval.add_argument("--source", help="Event source, e.g. rainfall or sensor")
    trigger_eval.add_argument("--metric", help="Metric name, e.g. rainfall_6h_mm")
    trigger_eval.add_argument("--value", help="Observed value")
    trigger_eval.add_argument("--location", help="Location or AOI label")
    trigger_eval.add_argument("--note", help="Free-form event note")
    trigger_eval.add_argument("--no-queue", action="store_true", help="Do not create human review items")
    trigger_eval.add_argument("--json", action="store_true")

    review_p = sub.add_parser("review", help="Manage the human review queue")
    review_sub = review_p.add_subparsers(dest="review_command", required=True)
    review_list = review_sub.add_parser("list", help="List review items")
    review_list.add_argument("--status", default="open", help="open, approved, rejected, corrected, needs_field_validation, uncertain, closed, all")
    review_list.add_argument("--limit", type=int, default=50)
    review_show = review_sub.add_parser("show", help="Show a review item and history")
    review_show.add_argument("id")
    for action_name in ("approve", "correct", "reject", "field-check", "uncertain", "close", "reopen"):
        action_p = review_sub.add_parser(action_name, help=f"Mark review item as {action_name}")
        action_p.add_argument("id")
        action_p.add_argument("--note", default="")
        action_p.add_argument("--actor", default="human")
        if action_name == "field-check":
            _add_field_creation_args(action_p, include_review=False)

    field_p = sub.add_parser("field", help="Manage field validation tasks")
    field_sub = field_p.add_subparsers(dest="field_command", required=True)
    field_list = field_sub.add_parser("list", help="List field validation tasks")
    field_list.add_argument("--status", default="open", help="open, in_progress, completed, cancelled, or all")
    field_list.add_argument("--limit", type=int, default=50)
    field_show = field_sub.add_parser("show", help="Show a field validation task and history")
    field_show.add_argument("id")
    field_create = field_sub.add_parser("create", help="Create a field validation task")
    _add_field_creation_args(field_create, include_review=True)
    field_start = field_sub.add_parser("start", help="Mark a field validation task as in progress")
    field_start.add_argument("id")
    field_start.add_argument("--note", default="")
    field_start.add_argument("--actor", default="human")
    field_cancel = field_sub.add_parser("cancel", help="Cancel a field validation task")
    field_cancel.add_argument("id")
    field_cancel.add_argument("--note", default="")
    field_cancel.add_argument("--actor", default="human")
    field_complete = field_sub.add_parser("complete", help="Complete a field validation task")
    field_complete.add_argument("id")
    field_complete.add_argument("--observation", required=True, action="append", help="Field observation; repeat for multiple notes")
    field_complete.add_argument("--confidence-update", default="")
    field_complete.add_argument("--attachment", action="append", default=[], help="Path, URL, or identifier for evidence")
    field_complete.add_argument("--note", default="")
    field_complete.add_argument("--actor", default="human")

    memory_p = sub.add_parser("memory", help="Capture human feedback, corrections, preferences, and lessons")
    memory_sub = memory_p.add_subparsers(dest="memory_command", required=True)
    memory_list = memory_sub.add_parser("list", help="List learning memory records")
    memory_list.add_argument("--kind", default="all", help="all or one learning kind")
    memory_list.add_argument("--status", default="active", help="active, archived, superseded, or all")
    memory_list.add_argument("--limit", type=int, default=50)
    memory_show = memory_sub.add_parser("show", help="Show a learning memory record and history")
    memory_show.add_argument("id")
    memory_add = memory_sub.add_parser("add", help="Add a learning memory record")
    memory_add.add_argument("--kind", choices=sorted(LEARNING_KINDS), default="human_feedback")
    memory_add.add_argument("--title", required=True)
    memory_add.add_argument("--body", required=True)
    memory_add.add_argument("--source", default="cli")
    memory_add.add_argument("--created-by", default="human")
    memory_add.add_argument("--confidence", default="medium")
    memory_add.add_argument("--applies-to", action="append", default=[], help="Graph node id this learning applies to")
    memory_add.add_argument("--tag", action="append", default=[])
    memory_add.add_argument("--evidence", action="append", default=[], help="Evidence reference, path, URL, run id, or graph node id")
    for status_name in ("archive", "supersede", "activate"):
        status_p = memory_sub.add_parser(status_name, help=f"Mark a learning record as {status_name}")
        status_p.add_argument("id")
        status_p.add_argument("--note", default="")
        status_p.add_argument("--actor", default="human")

    auth_p = sub.add_parser("auth", help="Manage ChatGPT/Codex and provider auth")
    auth_sub = auth_p.add_subparsers(dest="auth_command", required=True)
    auth_login = auth_sub.add_parser("login", help="Sign in with ChatGPT/Codex")
    auth_login.add_argument("--provider", default="codex-cli", help="codex-cli or openai-codex")
    auth_login.add_argument("--device-code", action="store_true", help="Use OpenAI Codex device-code OAuth")
    auth_login.add_argument("--no-browser", action="store_true", help="Print the sign-in URL without opening a browser")
    auth_login.add_argument("--command", dest="codex_auth_command", help="Codex CLI command for --provider codex-cli")
    auth_login.add_argument("--timeout", type=int, default=900)
    auth_status = auth_sub.add_parser("status", help="Show local auth status without printing secrets")
    auth_status.add_argument("--provider", default="all", help="all, codex-cli, or openai-codex")
    auth_status.add_argument("--command", dest="codex_auth_command", help="Codex CLI command for --provider codex-cli")
    auth_status.add_argument("--json", action="store_true")
    auth_refresh = auth_sub.add_parser("refresh", help="Refresh stored OpenAI Codex OAuth tokens")
    auth_refresh.add_argument("--provider", default=OPENAI_CODEX_PROVIDER)
    auth_refresh.add_argument("--json", action="store_true")
    auth_logout = auth_sub.add_parser("logout", help="Remove or revoke local auth")
    auth_logout.add_argument("--provider", default="codex-cli", help="codex-cli or openai-codex")
    auth_logout.add_argument("--command", dest="codex_auth_command", help="Codex CLI command for --provider codex-cli")

    bench_p = sub.add_parser("bench", help="Benchmark manifest load and dry-run speed")
    bench_p.add_argument("--workflow", default="diagnostic_report")
    bench_p.add_argument("--iterations", type=int, default=50)

    agent_p = sub.add_parser("agent", help="Create and manage agents")
    agent_sub = agent_p.add_subparsers(dest="agent_command", required=True)
    create_p = agent_sub.add_parser("create", help="Create a new agent manifest and prompt")
    _add_agent_create_args(create_p)

    mcp_p = sub.add_parser("mcp", help="Diagnose and configure MCP servers")
    mcp_sub = mcp_p.add_subparsers(dest="mcp_command", required=True)
    mcp_doctor = mcp_sub.add_parser("doctor", help="Check one MCP server without making tool calls")
    mcp_doctor.add_argument("name")
    mcp_setup = mcp_sub.add_parser("setup", help="Configure an MCP server manifest")
    mcp_setup.add_argument("name", choices=["qgis"])
    mcp_setup.add_argument("--transport", choices=["stdio", "sse", "streamable_http"])
    mcp_setup.add_argument("--command", dest="mcp_command_line")
    mcp_setup.add_argument("--arg", action="append", default=[])
    mcp_setup.add_argument("--url")
    mcp_setup.add_argument("--enable", action="store_true", default=None)
    mcp_setup.add_argument("--disable", action="store_false", dest="enable")
    mcp_setup.add_argument("--yes", action="store_true", help="Use supplied/default values without prompting")

    upstream_p = sub.add_parser("upstream", help="Watch upstream frameworks for relevant changes")
    upstream_sub = upstream_p.add_subparsers(dest="upstream_command", required=True)
    upstream_check = upstream_sub.add_parser("check", help="Report drift from OpenSwarm, Pi, Hermes, and related upstreams")
    upstream_check.add_argument("--config", help="Path to upstreams.yaml")
    upstream_check.add_argument("--state", help="Path to upstream state JSON")
    upstream_check.add_argument("--update-state", action="store_true", help="Record latest successful checks as reviewed local state")
    upstream_check.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    upstream_check.add_argument("--no-files", action="store_true", help="Skip GitHub compare-file lookup")

    sub.add_parser("agency-status", help="Check optional Agency Swarm adapter availability")
    sub.add_parser("agency-check", help="Instantiate the optional Agency Swarm agency without LLM calls")
    return parser


def _add_agent_create_args(parser: argparse.ArgumentParser, positional_required: bool = False) -> None:
    nargs = None if positional_required else "?"
    parser.add_argument("name", nargs=nargs)
    parser.add_argument("--role", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--model", default="gpt-5.2")
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--template", choices=["domain", "gis", "writer", "reviewer"], default="domain")
    parser.add_argument("--mcp", action="append", default=[])
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--output", action="append", default=[])
    parser.add_argument("--yes", action="store_true", help="Use supplied/default values without prompting")


def _add_field_creation_args(parser: argparse.ArgumentParser, *, include_review: bool) -> None:
    if include_review:
        parser.add_argument("--review", dest="review_item_id", default="", help="Review item id this field task validates")
    parser.add_argument("--method", choices=sorted(FIELD_METHODS), default="", help="Field validation method")
    parser.add_argument("--title", default="", help="Task title")
    parser.add_argument("--question", default="", help="Field question to answer")
    parser.add_argument("--location", default="", help="AOI, village, transect, well, or site label")
    parser.add_argument("--priority", default="", help="low, medium, high, or urgent")
    parser.add_argument("--assigned-to", default="", help="Field team, person, or partner")
    parser.add_argument("--due", dest="due_date", default="", help="Due date or field window")
    parser.add_argument("--expected", action="append", default=[], help="Expected evidence; repeat for multiple items")


def _dispatch(args: argparse.Namespace, root: Path) -> int:
    if args.command == "init":
        project = copy_starter_template(args.project, force=args.force)
        print(f"Created Open Earth project: {project}")
        print(f"Next: cd {project} && openearth validate")
        return 0
    if args.command == "doctor":
        return run_doctor(root)
    if args.command == "setup":
        return run_setup_guide(root, prefer=args.prefer)
    if args.command == "auth":
        return _cmd_auth(args)
    if args.command == "ollama":
        return _cmd_ollama(args)
    if args.command == "nvidia":
        return _cmd_nvidia(args)
    if args.command == "providers":
        return _cmd_providers(root, args)
    if args.command == "data":
        return _cmd_data(root, args)
    if args.command == "evidence":
        return _cmd_evidence(root, args)
    if args.command == "graph":
        return _cmd_graph(root, args)
    if args.command == "trigger":
        return _cmd_trigger(root, args)
    if args.command == "review":
        return _cmd_review(root, args)
    if args.command == "field":
        return _cmd_field(root, args)
    if args.command == "memory":
        return _cmd_memory(root, args)
    if args.command == "mcp":
        return _cmd_mcp(root, args)
    if args.command == "upstream":
        return _cmd_upstream(root, args)
    if args.command == "agent":
        return _cmd_agent_create(root, args)
    if args.command == "tui":
        return _cmd_tui(root)
    if args.command == "dashboard":
        start_dashboard(root, host=args.host, port=args.port, open_browser=not args.no_open, allow_public=args.insecure)
        return 0
    if args.command == "agency-status":
        print("available" if agency_adapter.is_available() else "not installed")
        return 0
    if args.command == "agency-check":
        if not agency_adapter.is_available():
            print("Agency Swarm adapter is not installed. Install with: uv sync --extra agency")
            return 1
        agency = agency_adapter.build_agency(root)
        print(f"{type(agency).__name__}: {getattr(agency, 'name', 'Open Earth')}")
        return 0

    loader = ManifestLoader(root)
    if args.command == "list":
        _cmd_list(loader, args.kind)
        return 0
    if args.command == "validate":
        return _cmd_validate(loader)
    if args.command == "show":
        _cmd_show(loader, args.kind, args.name)
        return 0
    if args.command == "run":
        return _cmd_run(loader, args)
    if args.command == "bench":
        return _cmd_bench(root, args.workflow, args.iterations)
    return 1


def _cmd_agent_create(root: Path, args: argparse.Namespace) -> int:
    created = create_agent(
        root=root,
        name=args.name,
        role=args.role,
        description=args.description,
        model=args.model,
        reasoning=args.reasoning,
        mcps=args.mcp,
        skills=args.skill,
        outputs=args.output or None,
        template=args.template,
        interactive=not args.yes,
    )
    print(f"created {created.manifest_path}")
    print(f"created {created.prompt_path}")
    return 0


def _cmd_mcp(root: Path, args: argparse.Namespace) -> int:
    if args.mcp_command == "doctor":
        return doctor_mcp(root, args.name)
    if args.mcp_command == "setup":
        path = setup_qgis_mcp(
            root=root,
            transport=args.transport,
            command=args.mcp_command_line,
            args=args.arg,
            url=args.url,
            enable=args.enable,
            interactive=not args.yes,
        )
        print(f"updated {path}")
        return 0
    return 1


def _cmd_tui(root: Path) -> int:
    if not agency_adapter.is_available():
        print("Agency Swarm adapter is not installed. Install with: uv sync --extra agency")
        return 1
    agency = agency_adapter.build_agency(root)
    agency.tui(show_reasoning=True, reload=False)
    return 0


def _cmd_upstream(root: Path, args: argparse.Namespace) -> int:
    if args.upstream_command == "check":
        statuses = check_upstreams(
            root=root,
            config_path=args.config,
            state_path=args.state,
            include_files=not args.no_files,
            update_state=args.update_state,
        )
        print(statuses_to_json(statuses) if args.json else format_upstream_report(statuses))
        return 0
    return 1


def _cmd_auth(args: argparse.Namespace) -> int:
    provider = normalize_auth_provider(getattr(args, "provider", None))
    if provider == "all":
        if getattr(args, "json", False):
            store = AuthStore()
            payload = {
                "codex_cli": _codex_cli_status_dict(getattr(args, "codex_auth_command", None)),
                OPENAI_CODEX_PROVIDER: store.status(OPENAI_CODEX_PROVIDER).public_dict(),
            }
            print(json.dumps(payload, indent=2, default=str))
            return 0
        _print_codex_cli_status(getattr(args, "codex_auth_command", None))
        _print_openai_codex_status()
        return 0

    if args.auth_command == "login":
        if provider == "codex-cli":
            completed = run_codex_login(getattr(args, "codex_auth_command", None), timeout_seconds=args.timeout)
            if completed.returncode != 0:
                print("Codex CLI login failed.")
                return completed.returncode or 1
            print("Codex CLI login finished.")
            print("You can now run: openearth run diagnostic_report --task \"...\" --live --backend codex-cli")
            return 0
        if provider != OPENAI_CODEX_PROVIDER:
            raise ValueError(f"unsupported auth provider: {provider}")

        def on_verification(prompt) -> None:
            print("Open this URL and sign in with ChatGPT:")
            print(f"  {prompt.verification_url}")
            print(f"Enter code: {prompt.user_code}")

        credential = login_openai_codex_device_code(
            open_browser=not args.no_browser,
            timeout_seconds=args.timeout,
            on_verification=on_verification,
            on_progress=lambda message: print(f"[auth] {message}"),
        )
        print(f"Signed in: {credential.profile_name or credential.email or credential.account_id or 'OpenAI Codex'}")
        print(f"Saved: {AuthStore().path_for(OPENAI_CODEX_PROVIDER)}")
        return 0

    if args.auth_command == "status":
        if provider == "codex-cli":
            if args.json:
                print(json.dumps(_codex_cli_status_dict(getattr(args, "codex_auth_command", None)), indent=2))
            else:
                _print_codex_cli_status(getattr(args, "codex_auth_command", None))
            return 0
        if provider != OPENAI_CODEX_PROVIDER:
            raise ValueError(f"unsupported auth provider: {provider}")
        status = AuthStore().status(provider)
        if args.json:
            print(json.dumps(status.public_dict(), indent=2, default=str))
        else:
            _print_status(status)
        return 0 if status.valid or not status.configured else 1

    if args.auth_command == "refresh":
        if provider != OPENAI_CODEX_PROVIDER:
            raise ValueError("refresh is only available for --provider openai-codex")
        credential = refresh_openai_codex()
        if args.json:
            print(json.dumps(credential.public_dict(), indent=2, default=str))
        else:
            print(f"Refreshed {OPENAI_CODEX_PROVIDER}: expires {credential.public_dict()['expires_at_iso'] or 'unknown'}")
        return 0

    if args.auth_command == "logout":
        if provider == "codex-cli":
            completed = run_codex_logout(getattr(args, "codex_auth_command", None))
            if completed.returncode != 0:
                print("Codex CLI logout failed.")
                return completed.returncode or 1
            print("Codex CLI logout finished.")
            return 0
        if provider != OPENAI_CODEX_PROVIDER:
            raise ValueError(f"unsupported auth provider: {provider}")
        removed = AuthStore().delete(OPENAI_CODEX_PROVIDER)
        print("Removed OpenAI Codex OAuth profile." if removed else "No OpenAI Codex OAuth profile found.")
        return 0

    return 1


def _cmd_ollama(args: argparse.Namespace) -> int:
    if args.ollama_command == "list":
        models = list_ollama_models()
        if not models:
            print("No local Ollama models found. Check: ollama list")
            return 1
        for model in models:
            print(model)
        return 0
    if args.ollama_command == "status":
        ok, detail = ollama_status(getattr(args, "host", None))
        print(("[ok] " if ok else "[fail] ") + detail)
        return 0 if ok else 1
    return 1


def _cmd_nvidia(args: argparse.Namespace) -> int:
    if args.nvidia_command == "status":
        ok, detail = nvidia_status()
        print(("[ok] " if ok else "[fail] ") + detail)
        return 0 if ok else 1
    return 1


def _cmd_providers(root: Path, args: argparse.Namespace) -> int:
    router = ProviderRouter(root)
    if args.provider_command == "list":
        profiles = router.profiles()
        if not profiles:
            print("No provider profiles found. Add providers.yaml")
            return 1
        default = router.default_provider()
        for profile in profiles:
            marker = "*" if profile.id == default else " "
            role = f" role={profile.role}" if profile.role else ""
            print(f"{marker} {profile.id:20} {profile.provider:12} {profile.runtime:24} model={profile.default_model}{role}")
        return 0
    if args.provider_command == "status":
        rows = router.status()
        if not rows:
            print("No provider profiles found. Add providers.yaml")
            return 1
        for row in rows:
            label = "[ok]" if row["available"] else "[warn]"
            print(f"{label} {row['id']:20} {row['runtime']:24} {row['detail']}")
        return 0
    return 1


def _cmd_data(root: Path, args: argparse.Namespace) -> int:
    catalog = DataAcquisitionCatalog(root)
    if args.data_command == "recipes":
        recipes = catalog.recipes()
        if args.json:
            print(json.dumps([asdict(recipe) for recipe in recipes.values()], indent=2, default=str))
            return 0
        if not recipes:
            print("No acquisition recipes found. Add data_sources/acquisition_recipes.yaml")
            return 1
        for recipe in recipes.values():
            download = "download" if recipe.can_download else "stage/blocker"
            print(f"{recipe.id:28} {recipe.theme:18} {download:13} {recipe.name}")
            if recipe.description:
                print(f"  {recipe.description}")
        return 0
    if args.data_command == "stage":
        result = catalog.stage(
            args.recipe,
            aoi=args.aoi,
            time_period=args.time_period,
            output_dir=args.output_dir,
            allow_download=args.allow_download,
            note=args.note,
        )
        GraphStore(root).ingest_project()
        if args.json:
            print(json.dumps(asdict(result), indent=2, default=str))
            return 0
        print(f"{result.recipe_id}: {result.status}")
        print(f"Stage:    {result.stage_dir}")
        print(f"Record:   {result.record_path}")
        print(f"Evidence: {result.evidence_id}")
        if result.local_path:
            print(f"Local:    {result.local_path}")
        for blocker in result.blockers:
            print(f"Blocker:  {blocker}")
        print(result.message)
        return 0
    return 1


def _cmd_evidence(root: Path, args: argparse.Namespace) -> int:
    store = EvidenceStore(root)
    if args.evidence_command == "list":
        records = store.list_evidence(evidence_type=args.type, reviewer_status=args.status, limit=args.limit)
        if not records:
            print("No evidence records.")
            return 0
        for record in records:
            source = f" <- {record.source}" if record.source else ""
            print(f"{record.id:28} {record.evidence_type:14} {record.reviewer_status:22} {record.title}{source}")
        return 0
    if args.evidence_command == "claims":
        records = store.list_claims(claim_type=args.type, reviewer_status=args.status, limit=args.limit)
        if not records:
            print("No claim records.")
            return 0
        for record in records:
            agent = f" @{record.agent}" if record.agent else ""
            print(f"{record.id:28} {record.claim_type:14} {record.confidence:8} {record.reviewer_status:22} {record.statement}{agent}")
        return 0
    if args.evidence_command == "show":
        if args.id.startswith("cl_"):
            print(json.dumps(asdict(store.get_claim(args.id)), indent=2, ensure_ascii=False, default=str))
        else:
            print(json.dumps(asdict(store.get_evidence(args.id)), indent=2, ensure_ascii=False, default=str))
        return 0
    if args.evidence_command == "add":
        record = store.add_evidence(
            evidence_type=args.type,
            title=args.title,
            source=args.source,
            source_url=args.source_url,
            license=args.license,
            crs=args.crs,
            spatial_extent=args.spatial_extent,
            resolution=args.resolution,
            time_period=args.time_period,
            local_path=args.local_path,
            uncertainty=args.uncertainty,
            created_by=args.created_by,
        )
        GraphStore(root).ingest_project()
        print(f"{record.id}: {record.evidence_type} {record.title}")
        return 0
    return 1


def _cmd_graph(root: Path, args: argparse.Namespace) -> int:
    store = GraphStore(root)
    if args.graph_command == "init":
        print(f"Graph store: {store.init()}")
        return 0
    if args.graph_command == "status":
        _print_graph_stats(store.stats())
        return 0
    if args.graph_command == "ingest-project":
        counts = store.ingest_project(ManifestLoader(root))
        print(f"Indexed project: {counts['nodes']} nodes, {counts['edges']} edges, {counts['documents']} documents")
        _print_graph_stats(store.stats())
        return 0
    if args.graph_command == "ingest-artifacts":
        counts = store.ingest_artifacts(run_id=args.run_id)
        print(f"Indexed artifacts: {counts['nodes']} nodes, {counts['edges']} edges, {counts['documents']} documents")
        _print_graph_stats(store.stats())
        return 0
    if args.graph_command == "query":
        for result in store.query(args.query, limit=args.limit):
            if result["type"] == "node":
                print(f"[node]  {result['id']}  ({result['kind']})")
                print(f"        {result['label']}")
            else:
                print(f"[chunk] {result['title']}  {result.get('path') or ''}")
                print(f"        {result['snippet']}")
        return 0
    if args.graph_command == "show":
        print(json.dumps(store.show(args.identifier), indent=2, ensure_ascii=False, default=str))
        return 0
    if args.graph_command == "export":
        print(f"Exported graph: {store.export_jsonl(args.path)}")
        return 0
    return 1


def _cmd_trigger(root: Path, args: argparse.Namespace) -> int:
    engine = TriggerEngine(root)
    if args.trigger_command == "list":
        rules = engine.rules()
        if not rules:
            print("No trigger rules found. Add triggers/*.trigger.yaml")
            return 1
        for rule in rules:
            op = rule.condition.get("op") or rule.condition.get("operator") or "all"
            value = rule.condition.get("value", "")
            print(f"{rule.id:28} {rule.severity:8} {rule.source:12} {rule.metric} {op} {value}")
            if rule.description:
                print(f"  {rule.description}")
        return 0
    if args.trigger_command == "evaluate":
        event = load_event(args.event) if args.event else _event_from_args(args)
        matches = engine.evaluate(event, queue=not args.no_queue)
        if args.json:
            print(json.dumps([_trigger_match_dict(match) for match in matches], indent=2, ensure_ascii=False, default=str))
            return 0
        if not matches:
            print("No trigger rules matched.")
            return 0
        print(f"Matched {len(matches)} trigger rule(s):")
        for match in matches:
            print(f"- {match.rule.id}: {match.rule.name} [{match.rule.severity}]")
            print(f"  observed: {match.observed_value}")
            if match.review_item_id:
                print(f"  review:   {match.review_item_id}")
        return 0
    return 1


def _cmd_review(root: Path, args: argparse.Namespace) -> int:
    queue = ReviewQueue(root)
    if args.review_command == "list":
        items = queue.list_items(status=args.status, limit=args.limit)
        if not items:
            print("No review items.")
            return 0
        for item in items:
            print(f"{item.id:28} {item.status:23} {item.severity:8} {item.title}")
        return 0
    if args.review_command == "show":
        item = queue.get(args.id)
        payload = asdict(item)
        payload["history"] = queue.history(args.id)
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0
    action = args.review_command.replace("-", "_")
    if action in ACTION_STATUS:
        item = queue.transition(args.id, action, note=args.note, actor=args.actor)
        print(f"{item.id}: {item.status}")
        if action == "field_check":
            task = FieldValidationStore(root).create_from_review(
                item.id,
                method=args.method or None,
                title=args.title,
                question=args.question,
                location=args.location,
                priority=args.priority,
                assigned_to=args.assigned_to,
                due_date=args.due_date,
                expected_evidence=args.expected or None,
                actor=args.actor,
                note=args.note,
            )
            print(f"field task: {task.id} ({task.method})")
        return 0
    return 1


def _cmd_field(root: Path, args: argparse.Namespace) -> int:
    store = FieldValidationStore(root)
    if args.field_command == "list":
        tasks = store.list_tasks(status=args.status, limit=args.limit)
        if not tasks:
            print("No field validation tasks.")
            return 0
        for task in tasks:
            location = f" @ {task.location}" if task.location else ""
            print(f"{task.id:28} {task.status:12} {task.method:18} {task.priority:8} {task.title}{location}")
        return 0
    if args.field_command == "show":
        task = store.get(args.id)
        payload = asdict(task)
        payload["history"] = store.history(args.id)
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0
    if args.field_command == "create":
        if args.review_item_id:
            task = store.create_from_review(
                args.review_item_id,
                method=args.method or None,
                title=args.title,
                question=args.question,
                location=args.location,
                priority=args.priority,
                assigned_to=args.assigned_to,
                due_date=args.due_date,
                expected_evidence=args.expected or None,
            )
        else:
            task = store.create_task(
                method=args.method or "field_notebook",
                title=args.title or "Field validation task",
                question=args.question or "What field evidence confirms, corrects, or rejects the current insight?",
                location=args.location,
                priority=args.priority or "medium",
                assigned_to=args.assigned_to,
                due_date=args.due_date,
                expected_evidence=args.expected,
            )
        print(f"{task.id}: {task.status} {task.title}")
        return 0
    if args.field_command == "start":
        task = store.start(args.id, actor=args.actor, note=args.note)
        print(f"{task.id}: {task.status}")
        return 0
    if args.field_command == "cancel":
        task = store.cancel(args.id, actor=args.actor, note=args.note)
        print(f"{task.id}: {task.status}")
        return 0
    if args.field_command == "complete":
        task = store.complete(
            args.id,
            observations="\n".join(args.observation),
            confidence_update=args.confidence_update,
            attachments=args.attachment,
            actor=args.actor,
            note=args.note,
        )
        print(f"{task.id}: {task.status}")
        return 0
    return 1


def _cmd_memory(root: Path, args: argparse.Namespace) -> int:
    store = LearningMemoryStore(root)
    if args.memory_command == "list":
        records = store.list_records(kind=args.kind, status=args.status, limit=args.limit)
        if not records:
            print("No learning memory records.")
            return 0
        for record in records:
            applies = f" -> {', '.join(record.applies_to[:2])}" if record.applies_to else ""
            print(f"{record.id:28} {record.status:10} {record.kind:16} {record.title}{applies}")
        return 0
    if args.memory_command == "show":
        record = store.get(args.id)
        payload = asdict(record)
        payload["history"] = store.history(args.id)
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0
    if args.memory_command == "add":
        record = store.add_record(
            kind=args.kind,
            title=args.title,
            body=args.body,
            source=args.source,
            created_by=args.created_by,
            confidence=args.confidence,
            applies_to=args.applies_to,
            tags=args.tag,
            evidence_refs=args.evidence,
        )
        print(f"{record.id}: {record.kind} {record.title}")
        GraphStore(root).ingest_project()
        return 0
    status_map = {"archive": "archived", "supersede": "superseded", "activate": "active"}
    if args.memory_command in status_map:
        record = store.set_status(args.id, status_map[args.memory_command], actor=args.actor, note=args.note)
        print(f"{record.id}: {record.status}")
        GraphStore(root).ingest_project()
        return 0
    return 1


def _event_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if not args.metric:
        raise ValueError("trigger evaluate needs --event or --metric")
    event: dict[str, Any] = {
        "source": args.source or "",
        "metric": args.metric,
        "value": _parse_event_value(args.value),
    }
    event[args.metric] = event["value"]
    if args.location:
        event["location"] = args.location
    if args.note:
        event["note"] = args.note
    return event


def _parse_event_value(value: str | None) -> Any:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _trigger_match_dict(match) -> dict[str, Any]:
    return {
        "rule": asdict(match.rule),
        "event": match.event,
        "observed_value": match.observed_value,
        "review_item_id": match.review_item_id,
    }


def _print_graph_stats(stats) -> None:
    print(f"Graph:     {stats.path}")
    print(f"Nodes:     {stats.nodes}")
    print(f"Edges:     {stats.edges}")
    print(f"Documents: {stats.documents}")
    print(f"Chunks:    {stats.chunks}")


def _print_codex_cli_status(command: str | None) -> None:
    ok, detail = codex_command_status(command)
    label = "[ok]" if ok else "[warn]"
    print(f"{label} Codex CLI: {detail}")
    print("     Sign in with: openearth auth login --provider codex-cli")


def _codex_cli_status_dict(command: str | None) -> dict[str, Any]:
    ok, detail = codex_command_status(command)
    return {"provider": "codex-cli", "available": ok, "detail": detail}


def _print_openai_codex_status() -> None:
    _print_status(AuthStore().status(OPENAI_CODEX_PROVIDER))


def _print_status(status) -> None:
    label = "[ok]" if status.valid else ("[warn]" if not status.configured else "[fail]")
    print(f"{label} {status.provider}: {status.detail}")
    if status.profile_name:
        print(f"     profile: {status.profile_name}")
    if status.chatgpt_plan_type:
        print(f"     plan:    {status.chatgpt_plan_type}")
    if status.expires_at:
        from .auth import format_epoch

        print(f"     expires: {format_epoch(status.expires_at)}")
    print(f"     store:   {status.path}")


def _cmd_list(loader: ManifestLoader, kind: str) -> None:
    if kind == "agents":
        for spec in loader.agents().values():
            print(f"{spec.slug:20} {spec.name:24} {spec.description}")
    elif kind == "workflows":
        for spec in loader.workflows().values():
            print(f"{spec.slug:24} {len(spec.steps):2d} steps  {spec.description}")
    elif kind == "mcps":
        for row in McpRegistry(loader).status():
            enabled = "on " if row["enabled"] else "off"
            print(f"{row['slug']:16} {enabled} {row['transport']:15} attached={','.join(row['attached_agents']) or '-'}")
    elif kind == "skills":
        for slug, path in loader.skills().items():
            print(f"{slug:28} {path}")


def _cmd_validate(loader: ManifestLoader) -> int:
    errors = loader.validate()
    if errors:
        print("Manifest validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Manifest validation passed.")
    return 0


def _cmd_show(loader: ManifestLoader, kind: str, name: str) -> None:
    if kind == "agent":
        item: Any = loader.get_agent(name)
    elif kind == "workflow":
        item = loader.get_workflow(name)
    else:
        item = loader.mcp_servers()[normalize_slug(name)]
    print(json.dumps(asdict(item), indent=2, default=str))


def _cmd_run(loader: ManifestLoader, args: argparse.Namespace) -> int:
    tracer = make_tracer(enabled=args.live and not args.quiet and not args.json)
    if args.live:
        tracer.emit(f"workflow: {args.workflow}")
        tracer.emit(f"backend: {args.backend}")
        if args.model:
            tracer.emit(f"model: {args.model}")
        if args.backend == "codex-cli":
            result = CodexCliWorkflowRunner(
                loader,
                artifact_root=args.artifact_root,
                command=args.codex_command,
                model=args.model,
                tracer=tracer,
            ).run(args.workflow, args.task)
        elif args.backend == "codex-ollama":
            result = CodexCliWorkflowRunner(
                loader,
                artifact_root=args.artifact_root,
                command=args.codex_command,
                model=args.model or "qwen3.5:9b",
                use_oss=True,
                local_provider="ollama",
                tracer=tracer,
            ).run(args.workflow, args.task)
        elif args.backend == "ollama":
            result = OllamaWorkflowRunner(
                loader,
                artifact_root=args.artifact_root,
                model=args.model,
                host=args.ollama_host,
                tracer=tracer,
            ).run(args.workflow, args.task)
        elif args.backend == "nvidia":
            result = NvidiaWorkflowRunner(
                loader,
                artifact_root=args.artifact_root,
                model=args.model,
                invoke_url=args.nvidia_url,
                stream=not args.no_stream,
                tracer=tracer,
            ).run(args.workflow, args.task)
        else:
            from .live_runner import AgencyWorkflowRunner

            result = AgencyWorkflowRunner(loader, artifact_root=args.artifact_root, tracer=tracer).run(args.workflow, args.task)
    else:
        if args.backend != "agency":
            raise ValueError("--backend is only used with --live")
        result = FastWorkflowRunner(loader, artifact_root=args.artifact_root).run(args.workflow, args.task, dry_run=True)
    if not args.no_graph:
        _record_run_graph(loader, result, quiet=args.quiet or args.json)
    if args.json:
        print(json.dumps(asdict(result), indent=2, default=str))
        return 0
    _print_run_result(result)
    return 0


def _record_run_graph(loader: ManifestLoader, result: WorkflowRunResult, quiet: bool = False) -> None:
    try:
        store = GraphStore(loader.root)
        store.ingest_project(loader)
        store.ingest_artifacts(run_id=result.run_id)
    except Exception as exc:  # pragma: no cover - graph indexing should never break a run
        if not quiet:
            print(f"[graph warn] could not index run: {exc}")
        return
    if not quiet:
        print(f"[graph] indexed run memory: {store.path}")


def _print_run_result(result: WorkflowRunResult) -> None:
    print(f"Workflow: {result.workflow}")
    print(f"Run:      {result.run_id}")
    print(f"Artifacts:{result.artifact_dir}")
    print(f"Elapsed:  {result.elapsed_ms:.2f} ms")
    print()
    for step in result.steps:
        print(f"[{step.status}] {step.step_id:24} {step.agent:20} {step.elapsed_ms:7.2f} ms")
        print(f"  {step.summary}")


def _cmd_bench(root: Path, workflow: str, iterations: int) -> int:
    load_times: list[float] = []
    run_times: list[float] = []
    with tempfile.TemporaryDirectory(prefix="earthswarm-bench-") as temp_dir:
        for _ in range(iterations):
            start = time.perf_counter()
            loader = ManifestLoader(root)
            loader.validate()
            load_times.append((time.perf_counter() - start) * 1000)
            start = time.perf_counter()
            FastWorkflowRunner(loader, artifact_root=temp_dir).run(workflow, "Benchmark dry run")
            run_times.append((time.perf_counter() - start) * 1000)
    print(f"iterations: {iterations}")
    print(f"load p50:   {statistics.median(load_times):.2f} ms")
    print(f"load max:   {max(load_times):.2f} ms")
    print(f"run p50:    {statistics.median(run_times):.2f} ms")
    print(f"run max:    {max(run_times):.2f} ms")
    print("mode:       dry-run, no LLM calls")
    return 0


def _program_name() -> str:
    stem = Path(sys.argv[0]).stem.lower()
    return "openearth" if stem == "openearth" else "earthswarm"


if __name__ == "__main__":
    raise SystemExit(main())
