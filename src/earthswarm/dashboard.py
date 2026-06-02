from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .agent_authoring import create_agent
from .codex_cli import CodexCliWorkflowRunner
from .data_acquisition import DataAcquisitionCatalog
from .evidence import EvidenceStore
from .field_validation import FieldValidationStore
from .graph_store import GraphStore
from .learning_memory import LearningMemoryStore
from .loader import ManifestLoader
from .models import normalize_slug
from .nvidia_runner import NvidiaWorkflowRunner
from .ollama_runner import OllamaWorkflowRunner, normalize_ollama_host, normalize_ollama_model
from .provider_router import ProviderRouter
from .review_queue import ACTION_STATUS, ReviewQueue
from .trace import RunTracer
from .trigger_engine import TriggerEngine
from .workflow_runner import FastWorkflowRunner, WorkflowRunResult


SESSION_HEADER = "X-OpenEarth-Session-Token"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class DashboardServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, root: str | Path, allow_public: bool = False):
        super().__init__(server_address, handler_class)
        self.root = Path(root).resolve()
        self.token = secrets.token_urlsafe(32)
        self.allow_public = allow_public
        self.started_at = time.time()


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib hook name
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        if not self._accepted_host():
            self._json({"detail": "Invalid Host header"}, status=HTTPStatus.BAD_REQUEST)
            return
        path, query = self._path_and_query()
        try:
            if path == "/api/status":
                self._json(_status_payload(self.server.root))
            elif path == "/api/agents":
                self._json({"agents": _agents_payload(self.server.root)})
            elif path.startswith("/api/agents/"):
                slug = _safe_slug(path.removeprefix("/api/agents/"))
                self._json(_agent_payload(self.server.root, slug))
            elif path == "/api/workflows":
                self._json({"workflows": _workflows_payload(self.server.root)})
            elif path == "/api/mcps":
                self._json({"mcps": _mcps_payload(self.server.root)})
            elif path == "/api/skills":
                self._json({"skills": _skills_payload(self.server.root)})
            elif path == "/api/providers":
                self._json({"default_provider": ProviderRouter(self.server.root).default_provider(), "profiles": ProviderRouter(self.server.root).status()})
            elif path == "/api/ollama/status":
                host = str(query.get("host", [""])[0])
                model = str(query.get("model", [""])[0])
                self._json(_ollama_status_payload(host=host, model=model))
            elif path == "/api/data/recipes":
                recipes = DataAcquisitionCatalog(self.server.root).recipes()
                self._json({"recipes": [asdict(recipe) for recipe in recipes.values()]})
            elif path == "/api/evidence":
                status = str(query.get("status", ["all"])[0])
                evidence_type = str(query.get("type", ["all"])[0])
                limit = int(query.get("limit", ["50"])[0])
                records = EvidenceStore(self.server.root).list_evidence(evidence_type=evidence_type, reviewer_status=status, limit=limit)
                self._json({"records": [asdict(record) for record in records], "counts": EvidenceStore(self.server.root).counts()})
            elif path == "/api/claims":
                status = str(query.get("status", ["all"])[0])
                claim_type = str(query.get("type", ["all"])[0])
                limit = int(query.get("limit", ["50"])[0])
                records = EvidenceStore(self.server.root).list_claims(claim_type=claim_type, reviewer_status=status, limit=limit)
                self._json({"records": [asdict(record) for record in records], "counts": EvidenceStore(self.server.root).counts()})
            elif path == "/api/graph/query":
                q = str(query.get("q", [""])[0])
                limit = int(query.get("limit", ["10"])[0])
                self._json({"results": GraphStore(self.server.root).query(q, limit=limit)})
            elif path == "/api/graph/network":
                q = str(query.get("q", [""])[0])
                limit = int(query.get("limit", ["48"])[0])
                edge_limit = int(query.get("edge_limit", ["140"])[0])
                self._json(GraphStore(self.server.root).network(q, limit=limit, edge_limit=edge_limit))
            elif path == "/api/review":
                status = str(query.get("status", ["open"])[0])
                limit = int(query.get("limit", ["50"])[0])
                items = [asdict(item) for item in ReviewQueue(self.server.root).list_items(status=status, limit=limit)]
                self._json({"items": items, "counts": ReviewQueue(self.server.root).counts()})
            elif path == "/api/field":
                status = str(query.get("status", ["open"])[0])
                limit = int(query.get("limit", ["50"])[0])
                store = FieldValidationStore(self.server.root)
                tasks = [asdict(task) for task in store.list_tasks(status=status, limit=limit)]
                self._json({"tasks": tasks, "counts": store.counts()})
            elif path == "/api/memory":
                kind = str(query.get("kind", ["all"])[0])
                status = str(query.get("status", ["active"])[0])
                limit = int(query.get("limit", ["50"])[0])
                store = LearningMemoryStore(self.server.root)
                records = [asdict(record) for record in store.list_records(kind=kind, status=status, limit=limit)]
                self._json({"records": records, "counts": store.counts()})
            elif path.startswith("/api/memory/"):
                record_id = path.removeprefix("/api/memory/")
                store = LearningMemoryStore(self.server.root)
                record = store.get(record_id)
                self._json({"record": asdict(record), "history": store.history(record_id)})
            elif path.startswith("/api/field/"):
                task_id = path.removeprefix("/api/field/")
                store = FieldValidationStore(self.server.root)
                task = store.get(task_id)
                self._json({"task": asdict(task), "history": store.history(task_id)})
            elif path.startswith("/api/review/"):
                item_id = path.removeprefix("/api/review/")
                item = ReviewQueue(self.server.root).get(item_id)
                self._json({"item": asdict(item), "history": ReviewQueue(self.server.root).history(item_id)})
            else:
                self._static(path)
        except Exception as exc:
            self._json({"detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        if not self._accepted_host() or not self._authorized():
            return
        path, _query = self._path_and_query()
        try:
            body = self._body()
            if path == "/api/agents":
                created = create_agent(
                    root=self.server.root,
                    name=str(body.get("name") or ""),
                    role=str(body.get("role") or ""),
                    description=str(body.get("description") or ""),
                    model=str(body.get("model") or "gpt-5.2"),
                    reasoning=str(body.get("reasoning") or "medium"),
                    mcps=list(body.get("mcp_servers") or []),
                    skills=list(body.get("skills") or []),
                    outputs=list(body.get("outputs") or []),
                    template=str(body.get("template") or "domain"),
                    interactive=False,
                )
                GraphStore(self.server.root).ingest_project()
                self._json({"ok": True, "agent": _agent_payload(self.server.root, created.slug)})
            elif path == "/api/validate":
                errors = ManifestLoader(self.server.root).validate()
                self._json({"ok": not errors, "errors": errors})
            elif path == "/api/graph/ingest":
                store = GraphStore(self.server.root)
                project_counts = store.ingest_project()
                artifact_counts = store.ingest_artifacts()
                self._json({"ok": True, "project": project_counts, "artifacts": artifact_counts, "stats": store.stats().as_dict()})
            elif path == "/api/data/stage":
                result = DataAcquisitionCatalog(self.server.root).stage(
                    str(body.get("recipe") or ""),
                    aoi=str(body.get("aoi") or ""),
                    time_period=str(body.get("time_period") or ""),
                    output_dir=str(body.get("output_dir") or "") or None,
                    allow_download=bool(body.get("allow_download")),
                    note=str(body.get("note") or ""),
                )
                GraphStore(self.server.root).ingest_project()
                self._json({"ok": True, "result": asdict(result)})
            elif path == "/api/evidence":
                record = EvidenceStore(self.server.root).add_evidence(
                    evidence_type=str(body.get("evidence_type") or body.get("type") or "dataset"),
                    title=str(body.get("title") or ""),
                    source=str(body.get("source") or ""),
                    source_url=str(body.get("source_url") or ""),
                    license=str(body.get("license") or ""),
                    crs=str(body.get("crs") or ""),
                    spatial_extent=str(body.get("spatial_extent") or ""),
                    resolution=str(body.get("resolution") or ""),
                    time_period=str(body.get("time_period") or ""),
                    local_path=str(body.get("local_path") or ""),
                    uncertainty=str(body.get("uncertainty") or ""),
                    created_by=str(body.get("created_by") or "human"),
                    metadata=dict(body.get("metadata") or {}),
                )
                GraphStore(self.server.root).ingest_project()
                self._json({"ok": True, "record": asdict(record)})
            elif path == "/api/trigger/evaluate":
                matches = TriggerEngine(self.server.root).evaluate(body, queue=not bool(body.get("no_queue")))
                self._json(
                    {
                        "ok": True,
                        "matches": [
                            {
                                "rule": asdict(match.rule),
                                "event": match.event,
                                "observed_value": match.observed_value,
                                "review_item_id": match.review_item_id,
                            }
                            for match in matches
                        ],
                    }
                )
            elif path == "/api/field":
                store = FieldValidationStore(self.server.root)
                review_item_id = str(body.get("review_item_id") or "")
                expected = _list(body.get("expected_evidence"))
                if review_item_id:
                    task = store.create_from_review(
                        review_item_id,
                        method=str(body.get("method") or "") or None,
                        title=str(body.get("title") or ""),
                        question=str(body.get("question") or ""),
                        location=str(body.get("location") or ""),
                        priority=str(body.get("priority") or ""),
                        assigned_to=str(body.get("assigned_to") or ""),
                        due_date=str(body.get("due_date") or ""),
                        expected_evidence=expected or None,
                        actor=str(body.get("actor") or "dashboard"),
                        note=str(body.get("note") or ""),
                    )
                else:
                    task = store.create_task(
                        method=str(body.get("method") or "field_notebook"),
                        title=str(body.get("title") or "Field validation task"),
                        question=str(body.get("question") or "What field evidence confirms, corrects, or rejects the current insight?"),
                        location=str(body.get("location") or ""),
                        priority=str(body.get("priority") or "medium"),
                        assigned_to=str(body.get("assigned_to") or ""),
                        due_date=str(body.get("due_date") or ""),
                        expected_evidence=expected,
                        actor=str(body.get("actor") or "dashboard"),
                        note=str(body.get("note") or ""),
                    )
                self._json({"ok": True, "task": asdict(task)})
            elif path == "/api/memory":
                record = LearningMemoryStore(self.server.root).add_record(
                    kind=str(body.get("kind") or "human_feedback"),
                    title=str(body.get("title") or ""),
                    body=str(body.get("body") or ""),
                    source=str(body.get("source") or "dashboard"),
                    created_by=str(body.get("created_by") or "human"),
                    confidence=str(body.get("confidence") or "medium"),
                    applies_to=_list(body.get("applies_to")),
                    tags=_list(body.get("tags")),
                    evidence_refs=_list(body.get("evidence_refs")),
                    payload=dict(body.get("payload") or {}),
                )
                GraphStore(self.server.root).ingest_project()
                self._json({"ok": True, "record": asdict(record)})
            elif path.startswith("/api/memory/"):
                parts = path.removeprefix("/api/memory/").split("/")
                if len(parts) != 2:
                    self._json({"detail": "Expected /api/memory/<id>/<action>"}, status=HTTPStatus.NOT_FOUND)
                    return
                record_id, action = parts
                status_map = {"archive": "archived", "supersede": "superseded", "activate": "active"}
                if action not in status_map:
                    raise ValueError(f"unknown memory action: {action}")
                record = LearningMemoryStore(self.server.root).set_status(
                    record_id,
                    status_map[action],
                    actor=str(body.get("actor") or "dashboard"),
                    note=str(body.get("note") or ""),
                )
                GraphStore(self.server.root).ingest_project()
                self._json({"ok": True, "record": asdict(record)})
            elif path.startswith("/api/field/"):
                parts = path.removeprefix("/api/field/").split("/")
                if len(parts) != 2:
                    self._json({"detail": "Expected /api/field/<id>/<action>"}, status=HTTPStatus.NOT_FOUND)
                    return
                task_id, action = parts
                store = FieldValidationStore(self.server.root)
                if action == "start":
                    task = store.start(task_id, note=str(body.get("note") or ""), actor=str(body.get("actor") or "dashboard"))
                elif action == "cancel":
                    task = store.cancel(task_id, note=str(body.get("note") or ""), actor=str(body.get("actor") or "dashboard"))
                elif action == "complete":
                    observations = body.get("observations")
                    if isinstance(observations, list):
                        observations_text = "\n".join(str(item) for item in observations if str(item).strip())
                    else:
                        observations_text = str(observations or body.get("observation") or "")
                    if not observations_text.strip():
                        raise ValueError("observations are required")
                    task = store.complete(
                        task_id,
                        observations=observations_text,
                        confidence_update=str(body.get("confidence_update") or ""),
                        attachments=_list(body.get("attachments")),
                        note=str(body.get("note") or ""),
                        actor=str(body.get("actor") or "dashboard"),
                    )
                else:
                    raise ValueError(f"unknown field action: {action}")
                self._json({"ok": True, "task": asdict(task)})
            elif path.startswith("/api/review/"):
                parts = path.removeprefix("/api/review/").split("/")
                if len(parts) != 2:
                    self._json({"detail": "Expected /api/review/<id>/<action>"}, status=HTTPStatus.NOT_FOUND)
                    return
                item_id, action = parts
                action = action.replace("-", "_")
                if action not in ACTION_STATUS:
                    raise ValueError(f"unknown review action: {action}")
                item = ReviewQueue(self.server.root).transition(
                    item_id,
                    action,
                    note=str(body.get("note") or ""),
                    actor=str(body.get("actor") or "dashboard"),
                )
                response: dict[str, Any] = {"ok": True, "item": asdict(item)}
                if action == "field_check":
                    task = FieldValidationStore(self.server.root).create_from_review(
                        item.id,
                        method=str(body.get("method") or "") or None,
                        title=str(body.get("title") or ""),
                        question=str(body.get("question") or ""),
                        location=str(body.get("location") or ""),
                        priority=str(body.get("priority") or ""),
                        assigned_to=str(body.get("assigned_to") or ""),
                        due_date=str(body.get("due_date") or ""),
                        expected_evidence=_list(body.get("expected_evidence")) or None,
                        actor=str(body.get("actor") or "dashboard"),
                        note=str(body.get("note") or ""),
                    )
                    response["field_task"] = asdict(task)
                self._json(response)
            elif path == "/api/run":
                workflow = str(body.get("workflow") or "diagnostic_report")
                task = str(body.get("task") or "")
                if not task.strip():
                    raise ValueError("task is required")
                loader = ManifestLoader(self.server.root)
                result, traces, run_meta = _dashboard_run(loader, workflow, task, body)
                store = GraphStore(self.server.root)
                store.ingest_project(loader)
                store.ingest_artifacts(run_id=result.run_id)
                self._json({"ok": True, "result": asdict(result), "trace": traces, "run": run_meta, "graph": store.stats().as_dict()})
            else:
                self._json({"detail": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._json({"detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_PUT(self) -> None:  # noqa: N802 - stdlib hook name
        if not self._accepted_host() or not self._authorized():
            return
        path, _query = self._path_and_query()
        try:
            if not path.startswith("/api/agents/"):
                self._json({"detail": "Not found"}, status=HTTPStatus.NOT_FOUND)
                return
            slug = _safe_slug(path.removeprefix("/api/agents/"))
            payload = self._body()
            _update_agent(self.server.root, slug, payload)
            GraphStore(self.server.root).ingest_project()
            errors = ManifestLoader(self.server.root).validate()
            self._json({"ok": not errors, "errors": errors, "agent": _agent_payload(self.server.root, slug)})
        except Exception as exc:
            self._json({"detail": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def _accepted_host(self) -> bool:
        if self.server.allow_public:
            return True
        host_header = self.headers.get("host", "")
        host = host_header.strip().split(":", 1)[0].strip("[]").lower()
        return host in LOOPBACK_HOSTS

    def _authorized(self) -> bool:
        token = self.headers.get(SESSION_HEADER, "")
        if secrets.compare_digest(token, self.server.token):
            return True
        self._json({"detail": "Unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
        return False

    def _path_and_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        return path, query

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("content-length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw.decode("utf-8") or "{}")
        if not isinstance(data, dict):
            raise ValueError("expected JSON object")
        return data

    def _json(self, payload: dict[str, Any], status: int | HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _static(self, path: str) -> None:
        static_name = "index.html" if path in {"", "/"} else path.lstrip("/")
        if "/" in static_name:
            static_name = static_name.split("/")[-1]
        if static_name not in {"index.html", "style.css", "app.js"}:
            static_name = "index.html"
        data = resources.files("earthswarm").joinpath("dashboard_static", static_name).read_bytes()
        if static_name == "index.html":
            html = data.decode("utf-8")
            injection = (
                f'<script>window.__OPENEARTH_SESSION_TOKEN__="{self.server.token}";'
                f'window.__OPENEARTH_PROJECT_ROOT__="{str(self.server.root).replace("\\", "\\\\")}";</script>'
            )
            data = html.replace("</head>", f"{injection}</head>", 1).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mimetypes.guess_type(static_name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_dashboard_server(
    root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_public: bool = False,
) -> DashboardServer:
    if host not in LOOPBACK_HOSTS and not allow_public:
        raise ValueError("refusing to bind dashboard outside loopback without --insecure")
    return DashboardServer((host, port), DashboardHandler, root=root, allow_public=allow_public)


def start_dashboard(
    root: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    allow_public: bool = False,
) -> None:
    server = make_dashboard_server(root, host=host, port=port, allow_public=allow_public)
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{actual_host}:{actual_port}/"
    print(f"Open Earth dashboard: {url}")
    print(f"Project: {server.root}")
    print("Session token is injected into the local page; keep this server on trusted machines.")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.")
    finally:
        server.server_close()


def _status_payload(root: Path) -> dict[str, Any]:
    loader = ManifestLoader(root)
    errors = loader.validate()
    graph_stats = GraphStore(root).stats().as_dict()
    return {
        "project_root": str(root),
        "valid": not errors,
        "errors": errors,
        "counts": {
            "agents": len(loader.agents()),
            "workflows": len(loader.workflows()),
            "mcps": len(loader.mcp_servers()),
            "skills": len(loader.skills()),
        },
        "graph": graph_stats,
        "review": ReviewQueue(root).counts(),
        "field": FieldValidationStore(root).counts(),
        "memory": LearningMemoryStore(root).counts(),
        "evidence": EvidenceStore(root).counts(),
        "providers": len(ProviderRouter(root).profiles()),
    }


def _agents_payload(root: Path) -> list[dict[str, Any]]:
    loader = ManifestLoader(root)
    return [_agent_payload(root, slug, include_prompt=False) for slug in loader.agents()]


def _agent_payload(root: Path, slug: str, include_prompt: bool = True) -> dict[str, Any]:
    loader = ManifestLoader(root)
    agent = loader.get_agent(slug)
    manifest_path = root / "agents" / f"{agent.slug}.agent.yaml"
    manifest = _read_yaml(manifest_path)
    payload = {
        "slug": agent.slug,
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "prompt_path": str(agent.prompt_path),
        "name": agent.name,
        "role": agent.role,
        "description": agent.description,
        "model": agent.model,
        "reasoning": agent.reasoning,
        "tools": agent.tools,
        "mcp_servers": agent.mcp_servers,
        "skills": agent.skills,
        "inputs": agent.inputs,
        "outputs": agent.outputs,
    }
    if include_prompt:
        payload["prompt"] = agent.prompt_path.read_text(encoding="utf-8", errors="replace")
    return payload


def _workflows_payload(root: Path) -> list[dict[str, Any]]:
    loader = ManifestLoader(root)
    return [asdict(workflow) for workflow in loader.workflows().values()]


def _mcps_payload(root: Path) -> list[dict[str, Any]]:
    loader = ManifestLoader(root)
    return [asdict(mcp) for mcp in loader.mcp_servers().values()]


def _skills_payload(root: Path) -> list[dict[str, Any]]:
    return [{"slug": slug, "path": str(path)} for slug, path in ManifestLoader(root).skills().items()]


def _dashboard_run(
    loader: ManifestLoader,
    workflow: str,
    task: str,
    body: dict[str, Any],
) -> tuple[WorkflowRunResult, list[str], dict[str, Any]]:
    mode = str(body.get("mode") or body.get("run_mode") or "").strip().lower()
    if not mode:
        mode = "live" if body.get("live") else "dry"
    backend = str(body.get("backend") or "").strip().lower()
    if mode in {"ollama", "codex-ollama", "codex-cli", "nvidia", "agency"} and not backend:
        backend = mode
        mode = "live"
    if mode not in {"dry", "live"}:
        raise ValueError(f"unsupported run mode: {mode}")

    traces: list[str] = []
    if mode == "dry":
        result = FastWorkflowRunner(loader).run(workflow, task, dry_run=True)
        return result, traces, {"mode": "dry", "backend": "dry", "model": "", "host": ""}

    backend = backend or "ollama"
    model = str(body.get("model") or "").strip()
    host = str(body.get("host") or body.get("ollama_host") or "").strip()
    codex_command = str(body.get("codex_command") or "").strip() or None
    nvidia_url = str(body.get("nvidia_url") or "").strip() or None
    tracer = RunTracer(enabled=True, sink=traces.append)
    tracer.emit(f"workflow: {workflow}")
    tracer.emit(f"backend: {backend}")
    if model:
        tracer.emit(f"model: {model}")

    if backend == "ollama":
        result = OllamaWorkflowRunner(loader, model=model or None, host=host or None, tracer=tracer).run(workflow, task)
        return result, traces, {
            "mode": "live",
            "backend": backend,
            "model": result.steps[0].details.get("model", model),
            "host": host,
        }
    if backend == "codex-ollama":
        selected_model = model or "minimax-m3:cloud"
        result = CodexCliWorkflowRunner(
            loader,
            command=codex_command,
            model=selected_model,
            use_oss=True,
            local_provider="ollama",
            tracer=tracer,
        ).run(workflow, task)
        return result, traces, {"mode": "live", "backend": backend, "model": selected_model, "host": host}
    if backend == "codex-cli":
        result = CodexCliWorkflowRunner(loader, command=codex_command, model=model or None, tracer=tracer).run(workflow, task)
        return result, traces, {"mode": "live", "backend": backend, "model": model, "host": ""}
    if backend == "nvidia":
        result = NvidiaWorkflowRunner(loader, model=model or None, invoke_url=nvidia_url, stream=False, tracer=tracer).run(workflow, task)
        return result, traces, {"mode": "live", "backend": backend, "model": model, "host": nvidia_url or ""}
    if backend == "agency":
        from .live_runner import AgencyWorkflowRunner

        result = AgencyWorkflowRunner(loader, tracer=tracer).run(workflow, task)
        return result, traces, {"mode": "live", "backend": backend, "model": model, "host": ""}
    raise ValueError(f"unsupported live backend: {backend}")


def _ollama_status_payload(host: str = "", model: str = "") -> dict[str, Any]:
    normalized_host = normalize_ollama_host(host or None)
    normalized_model = normalize_ollama_model(model or None)
    url = normalized_host + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "host": normalized_host,
            "model": normalized_model,
            "models": [],
            "model_available": False,
            "detail": f"Ollama is not reachable at {url}: {exc}",
            "command": f"ollama run {normalized_model}",
        }
    models = [str(item.get("name")) for item in payload.get("models", []) if isinstance(item, dict) and item.get("name")]
    available = normalized_model in models
    detail = (
        f"{normalized_model} is available."
        if available
        else f"{normalized_model} was not listed. Run `ollama run {normalized_model}` once, then check again."
    )
    return {
        "ok": True,
        "host": normalized_host,
        "model": normalized_model,
        "models": models,
        "model_available": available,
        "detail": detail,
        "command": f"ollama run {normalized_model}",
    }


def _update_agent(root: Path, slug: str, payload: dict[str, Any]) -> None:
    loader = ManifestLoader(root)
    agent = loader.get_agent(slug)
    manifest = dict(payload.get("manifest") or {})
    if not manifest:
        manifest = _read_yaml(root / "agents" / f"{agent.slug}.agent.yaml")
        for key in ("name", "role", "description", "model", "reasoning"):
            if key in payload:
                manifest[key] = str(payload.get(key) or "")
        for key in ("tools", "mcp_servers", "skills", "inputs", "outputs"):
            if key in payload:
                manifest[key] = _list(payload.get(key))
    manifest["instructions"] = Path(str(manifest.get("instructions") or agent.prompt_path.name)).name
    manifest_path = root / "agents" / f"{agent.slug}.agent.yaml"
    prompt_path = (manifest_path.parent / manifest["instructions"]).resolve()
    if prompt_path.parent != manifest_path.parent.resolve():
        raise ValueError("agent prompt must stay inside the agents directory")
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    if "prompt" in payload:
        prompt_path.write_text(str(payload.get("prompt") or ""), encoding="utf-8")


def _read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected mapping")
    return data


def _safe_slug(value: str) -> str:
    slug = normalize_slug(urllib.parse.unquote(value))
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError("invalid slug")
    return slug


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.splitlines() if item.strip()]
    return []
