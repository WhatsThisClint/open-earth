from __future__ import annotations

import json
import os
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


DEFAULT_NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DEFAULT_NVIDIA_MODEL = "moonshotai/kimi-k2.6"


def nvidia_api_key() -> str | None:
    return (os.getenv("NVIDIA_API_KEY") or os.getenv("NVAPI_KEY") or "").strip() or None


def nvidia_model(model: str | None = None) -> str:
    value = (model or os.getenv("EARTHSWARM_NVIDIA_MODEL") or DEFAULT_NVIDIA_MODEL).strip()
    if value.startswith("nvidia/"):
        return value.split("/", 1)[1]
    return value


def nvidia_url() -> str:
    return (os.getenv("NVIDIA_INVOKE_URL") or DEFAULT_NVIDIA_URL).strip()


def nvidia_status() -> tuple[bool, str]:
    if not nvidia_api_key():
        return False, "NVIDIA_API_KEY is not set"
    return True, f"configured for {nvidia_model()} at {nvidia_url()}"


class NvidiaWorkflowRunner:
    """Run an Open Earth workflow through NVIDIA's OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        loader: ManifestLoader,
        artifact_root: str | Path | None = None,
        *,
        model: str | None = None,
        invoke_url: str | None = None,
        stream: bool | None = None,
        timeout_seconds: int = 3600,
        tracer: RunTracer | None = None,
    ):
        self.loader = loader
        self.artifact_root = Path(artifact_root or loader.root / "artifacts")
        self.model = nvidia_model(model)
        self.invoke_url = invoke_url or nvidia_url()
        self.stream = bool(stream if stream is not None else _env_bool("NVIDIA_STREAM", default=True))
        self.timeout_seconds = timeout_seconds
        self.tracer = tracer

    def run(self, workflow_slug: str, task: str) -> WorkflowRunResult:
        errors = self.loader.validate()
        if errors:
            raise ValueError("manifest validation failed:\n" + "\n".join(f"- {e}" for e in errors))
        key = nvidia_api_key()
        if not key:
            raise RuntimeError("NVIDIA_API_KEY is not set. Add it to .env or your shell environment.")

        workflow = self.loader.get_workflow(workflow_slug)
        store = ArtifactStore(self.artifact_root, workflow.slug)
        self._trace(f"NVIDIA backend model: {self.model}")
        self._trace(f"streaming: {self.stream}")
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
        store.append(
            "nvidia_workflow_start",
            {
                "workflow": workflow.slug,
                "model": self.model,
                "stream": self.stream,
                "task": task,
            },
        )
        start = time.perf_counter()
        self._trace("sending request to NVIDIA chat completions endpoint")
        output = self._complete(prompt, key)
        elapsed_ms = (time.perf_counter() - start) * 1000
        self._trace("NVIDIA response complete")

        report_path = store.run_dir / "reports" / "nvidia_response.md"
        report_path.write_text(output, encoding="utf-8")
        self._trace(f"response saved: {report_path}")
        step = StepResult(
            step_id="nvidia_live",
            agent="orchestrator",
            status="live",
            elapsed_ms=elapsed_ms,
            summary=_summarize(output),
            outputs=[str(report_path)],
            details={"backend": "nvidia", "model": self.model, "workflow": workflow.slug},
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
            backend="nvidia",
            metadata={"details": step.details},
        )
        store.append("structured_output_recorded", {"evidence_id": evidence.id, "claim_id": claim.id})
        store.append("nvidia_workflow_complete", {"elapsed_ms": elapsed_ms, "output": str(report_path)})
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

    def _complete(self, prompt: str, key: str) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(os.getenv("NVIDIA_MAX_TOKENS", "16384")),
            "temperature": float(os.getenv("NVIDIA_TEMPERATURE", "1.0")),
            "top_p": float(os.getenv("NVIDIA_TOP_P", "1.0")),
            "stream": self.stream,
            "chat_template_kwargs": {"thinking": _env_bool("NVIDIA_THINKING", default=True)},
        }
        request = urllib.request.Request(
            self.invoke_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "text/event-stream" if self.stream else "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                if self.stream:
                    return _read_sse_response(response, tracer=self.tracer)
                body = json.loads(response.read().decode("utf-8", errors="replace"))
                return _extract_chat_completion(body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"NVIDIA request failed: HTTP {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"NVIDIA endpoint is not reachable: {exc}") from exc

    def _trace(self, message: str) -> None:
        if self.tracer:
            self.tracer.emit(message)


def _read_sse_response(response: Any, *, tracer: RunTracer | None = None) -> str:
    chunks: list[str] = []
    seen_chunks = 0
    last_trace = time.perf_counter()
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line.removeprefix("data:").strip()
        if data == "[DONE]":
            break
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        chunk = _extract_stream_chunk(payload)
        if chunk:
            chunks.append(chunk)
            seen_chunks += 1
            now = time.perf_counter()
            if tracer and (seen_chunks == 1 or now - last_trace >= 2.0):
                tracer.emit(f"streaming response... chunks received: {seen_chunks}")
                last_trace = now
    if tracer:
        tracer.emit(f"streaming complete; chunks received: {seen_chunks}")
    return "".join(chunks).strip() or "(empty NVIDIA response)"


def _extract_stream_chunk(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    delta = first.get("delta") or {}
    if not isinstance(delta, dict):
        return ""
    value = (
        delta.get("content")
        or delta.get("reasoning_content")
        or delta.get("reasoning")
        or delta.get("thinking")
        or ""
    )
    return str(value) if value is not None else ""


def _extract_chat_completion(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message") or {}
            if isinstance(message, dict):
                value = (
                    message.get("content")
                    or message.get("reasoning_content")
                    or message.get("reasoning")
                    or message.get("thinking")
                )
                if value:
                    return _extract_output(value)
    return _extract_output(payload)


def _env_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
