from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore
from .evidence import record_step_evidence
from .loader import ManifestLoader
from .live_runner import _compose_live_prompt, _extract_output, _summarize
from .trace import RunTracer
from .workflow_runner import StepResult, WorkflowRunResult


DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.5:9b"


def ollama_host() -> str:
    return os.getenv("OLLAMA_HOST") or os.getenv("OLLAMA_BASE_URL") or DEFAULT_OLLAMA_HOST


def normalize_ollama_host(value: str | None = None) -> str:
    host = (value or ollama_host()).strip().rstrip("/")
    if host.endswith("/v1"):
        host = host[:-3]
    return host


def normalize_ollama_model(model: str | None = None) -> str:
    value = (model or os.getenv("EARTHSWARM_OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL).strip()
    if value.startswith("ollama/"):
        return value.split("/", 1)[1]
    return value


def list_ollama_models() -> list[str]:
    try:
        completed = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        return []
    return [line.split()[0] for line in lines[1:] if line.split()]


def ollama_status(host: str | None = None) -> tuple[bool, str]:
    try:
        models = list_ollama_models()
    except Exception as exc:
        return False, f"ollama command failed: {exc}"
    if models:
        return True, "models: " + ", ".join(models)

    url = normalize_ollama_host(host) + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return False, f"Ollama is not reachable at {url}: {exc}"
    except Exception as exc:
        return False, f"Ollama responded unexpectedly at {url}: {exc}"
    names = [item.get("name") for item in payload.get("models", []) if isinstance(item, dict) and item.get("name")]
    if not names:
        return False, "Ollama is reachable but no models are installed"
    return True, "models: " + ", ".join(names)


class OllamaWorkflowRunner:
    """Run an Open Earth workflow through the local Ollama REST API."""

    def __init__(
        self,
        loader: ManifestLoader,
        artifact_root: str | Path | None = None,
        *,
        model: str | None = None,
        host: str | None = None,
        timeout_seconds: int = 3600,
        tracer: RunTracer | None = None,
    ):
        self.loader = loader
        self.artifact_root = Path(artifact_root or loader.root / "artifacts")
        self.model = normalize_ollama_model(model)
        self.host = normalize_ollama_host(host)
        self.timeout_seconds = timeout_seconds
        self.tracer = tracer

    def run(self, workflow_slug: str, task: str) -> WorkflowRunResult:
        errors = self.loader.validate()
        if errors:
            raise ValueError("manifest validation failed:\n" + "\n".join(f"- {e}" for e in errors))

        workflow = self.loader.get_workflow(workflow_slug)
        store = ArtifactStore(self.artifact_root, workflow.slug)
        self._trace(f"Ollama backend model: {self.model}")
        self._trace(f"Ollama host: {self.host}")
        self._trace(f"run id: {store.run_id}")
        self._trace(f"artifacts: {store.run_dir}")
        prompt = _compose_live_prompt(
            workflow.name,
            workflow.description,
            workflow.steps,
            task,
            agents=self.loader.agents(),
            project_root=self.loader.root,
        )
        prompt += (
            "\n\nYou are running through a direct Ollama backend. You can reason, plan, "
            "and write output artifacts requested in the final answer, but you cannot directly call "
            "QGIS MCP tools or shell commands from this backend. If real downloads or QGIS operations "
            "are needed, provide exact commands or route the task through --backend codex-cli or "
            "--backend codex-ollama."
        )
        store.append(
            "ollama_workflow_start",
            {"workflow": workflow.slug, "model": self.model, "host": self.host, "task": task},
        )
        start = time.perf_counter()
        self._trace("sending request to Ollama /api/generate")
        output = self._generate(prompt)
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._trace("Ollama response received")

        report_path = store.run_dir / "reports" / "ollama_response.md"
        report_path.write_text(output, encoding="utf-8")
        self._trace(f"response saved: {report_path}")
        step = StepResult(
            step_id="ollama_live",
            agent="orchestrator",
            status="live",
            elapsed_ms=elapsed_ms,
            summary=_summarize(output),
            outputs=[str(report_path)],
            details={"backend": "ollama", "model": self.model, "workflow": workflow.slug},
        )
        result = WorkflowRunResult(
            workflow=workflow.slug,
            run_id=store.run_id,
            artifact_dir=store.run_dir,
            elapsed_ms=elapsed_ms,
            steps=[step],
        )
        evidence, claim = record_step_evidence(
            root=self.loader.root,
            workflow=workflow.slug,
            run_id=store.run_id,
            step_id=step.step_id,
            agent=step.agent,
            summary=step.summary,
            output_paths=step.outputs,
            status="draft",
            backend="ollama",
            metadata={"details": step.details},
        )
        store.append("structured_output_recorded", {"evidence_id": evidence.id, "claim_id": claim.id})
        store.append("ollama_workflow_complete", {"elapsed_ms": elapsed_ms, "output": str(report_path)})
        store.write_summary(
            {
                "workflow": result.workflow,
                "run_id": result.run_id,
                "artifact_dir": result.artifact_dir,
                "elapsed_ms": result.elapsed_ms,
                "steps": result.steps,
            }
        )
        return result

    def _trace(self, message: str) -> None:
        if self.tracer:
            self.tracer.emit(message)

    def _generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.2,
            },
        }
        request = urllib.request.Request(
            self.host + "/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama request failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama is not reachable at {self.host}: {exc}") from exc
        return _extract_output(body.get("response", ""))
