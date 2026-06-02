from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from subprocess import CompletedProcess

from .artifact_store import ArtifactStore
from .evidence import record_step_evidence
from .loader import ManifestLoader
from .live_runner import _compose_live_prompt, _summarize
from .trace import RunTracer
from .workflow_runner import StepResult, WorkflowRunResult


DEFAULT_CODEX_COMMAND = "codex"
DEFAULT_CODEX_MODEL = "gpt-5.5"
DEFAULT_CODEX_LOGIN_ARGS = "login"
LEGACY_CODEX_LOGIN_ARGS = "--login"


def split_command_line(command: str) -> list[str]:
    value = command.strip()
    if os.name == "nt" and '"' not in value and "'" not in value:
        return value.split()
    return shlex.split(value, posix=True)


def resolve_codex_command(command: str | list[str] | None = None) -> list[str]:
    if isinstance(command, list):
        if not command:
            raise ValueError("Codex command is empty")
        return command
    value = command or os.getenv("EARTHSWARM_CODEX_COMMAND") or DEFAULT_CODEX_COMMAND
    parts = split_command_line(value)
    if not parts:
        raise ValueError("Codex command is empty")
    return parts


def normalize_codex_model(model: str | None = None) -> str:
    value = (model or os.getenv("EARTHSWARM_CODEX_MODEL") or DEFAULT_CODEX_MODEL).strip()
    if value.startswith("openai/"):
        return value.split("/", 1)[1]
    return value


def codex_command_status(command: str | list[str] | None = None) -> tuple[bool, str]:
    parts = resolve_codex_command(command)
    executable = parts[0]
    exists = Path(executable).exists() if any(sep in executable for sep in ("\\", "/")) else bool(shutil.which(executable))
    if not exists:
        return False, f"command not found: {executable}"
    try:
        completed = subprocess.run(
            parts + ["--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except PermissionError as exc:
        if os.name == "nt":
            return (
                False,
                "command was found but Windows refused to run it. Install the npm Codex CLI with "
                "`npm install -g @openai/codex`, or set EARTHSWARM_CODEX_COMMAND to a runnable codex command.",
            )
        return False, f"command found but could not run: {exc}"
    except Exception as exc:
        return False, f"command found but could not run: {exc}"
    if completed.returncode != 0:
        return False, _compact(completed.stderr or completed.stdout or f"exit {completed.returncode}")
    return True, _compact(completed.stdout or "available")


def run_codex_login(command: str | list[str] | None = None, *, timeout_seconds: int = 1800) -> subprocess.CompletedProcess[str]:
    parts = resolve_codex_command(command)
    login_args = _login_args()
    completed = _run_interactive(parts + login_args, timeout_seconds=timeout_seconds)
    if completed.returncode == 0 or login_args == [LEGACY_CODEX_LOGIN_ARGS]:
        return completed
    if _looks_like_unknown_login_command(completed):
        return _run_interactive(parts + [LEGACY_CODEX_LOGIN_ARGS], timeout_seconds=timeout_seconds)
    return completed


def _run_interactive(command: list[str], *, timeout_seconds: int) -> CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def run_codex_logout(command: str | list[str] | None = None, *, timeout_seconds: int = 120) -> subprocess.CompletedProcess[str]:
    parts = resolve_codex_command(command)
    return subprocess.run(
        parts + ["logout"],
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )


def _login_args() -> list[str]:
    configured = os.getenv("EARTHSWARM_CODEX_LOGIN_ARGS", DEFAULT_CODEX_LOGIN_ARGS).strip()
    if not configured:
        return [DEFAULT_CODEX_LOGIN_ARGS]
    return split_command_line(configured)


def _looks_like_unknown_login_command(completed: CompletedProcess[str]) -> bool:
    text = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
    return completed.returncode != 0 and any(
        marker in text
        for marker in (
            "unknown command",
            "unrecognized",
            "unexpected argument",
            "invalid subcommand",
            "usage:",
        )
    )


class CodexCliWorkflowRunner:
    """Run an Open Earth workflow through the official Codex CLI.

    The Codex CLI owns its own ChatGPT sign-in and credential cache. Open Earth
    only shells out to the configured command, captures the output, and records
    the artifact ledger.
    """

    def __init__(
        self,
        loader: ManifestLoader,
        artifact_root: str | Path | None = None,
        *,
        command: str | None = None,
        model: str | None = None,
        use_oss: bool = False,
        local_provider: str | None = None,
        timeout_seconds: int = 3600,
        tracer: RunTracer | None = None,
    ):
        self.loader = loader
        self.artifact_root = Path(artifact_root or loader.root / "artifacts")
        self.command = resolve_codex_command(command)
        self.model = normalize_codex_model(model)
        self.use_oss = use_oss
        self.local_provider = local_provider
        self.timeout_seconds = timeout_seconds
        self.tracer = tracer

    def run(self, workflow_slug: str, task: str) -> WorkflowRunResult:
        errors = self.loader.validate()
        if errors:
            raise ValueError("manifest validation failed:\n" + "\n".join(f"- {e}" for e in errors))
        self._trace(f"checking Codex CLI backend for model {self.model}")
        ok, detail = codex_command_status(self.command)
        if not ok:
            raise RuntimeError(f"Codex CLI backend is not ready: {detail}")
        self._trace(f"Codex CLI ready: {detail}")

        workflow = self.loader.get_workflow(workflow_slug)
        store = ArtifactStore(self.artifact_root, workflow.slug)
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
            "codex_cli_workflow_start",
            {
                "workflow": workflow.slug,
                "model": self.model,
                "command": self.command[0],
                "task": task,
            },
        )
        start = time.perf_counter()

        command = self.command + [
            "exec",
            "--skip-git-repo-check",
        ]
        if self.use_oss:
            command.append("--oss")
        if self.local_provider:
            command.extend(["--local-provider", self.local_provider])
        command.extend(["--model", self.model, "-"])
        mode = "Codex+Ollama" if self.use_oss else "Codex CLI"
        self._trace(f"starting {mode} subprocess")
        self._trace("prompt is being sent through stdin to avoid Windows command-length limits")
        try:
            completed = subprocess.run(
                command,
                cwd=self.loader.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                input=prompt,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            store.append("codex_cli_workflow_error", {"error": f"timed out after {self.timeout_seconds}s"})
            raise RuntimeError(f"Codex CLI run timed out after {self.timeout_seconds}s") from exc

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._trace(f"subprocess finished with exit code {completed.returncode}")
        logs_path = store.run_dir / "logs" / "codex_cli_stderr.txt"
        stderr = completed.stderr or ""
        stdout = completed.stdout or ""
        if stderr:
            logs_path.write_text(stderr, encoding="utf-8")
        if completed.returncode != 0:
            store.append(
                "codex_cli_workflow_error",
                {
                    "exit_code": completed.returncode,
                    "stderr": str(logs_path) if stderr else "",
                },
            )
            detail = _compact(stderr or stdout or f"exit {completed.returncode}")
            raise RuntimeError(f"Codex CLI run failed: {detail}")

        output = stdout.strip() or "(empty Codex CLI response)"
        report_path = store.run_dir / "reports" / "codex_cli_response.md"
        report_path.write_text(output, encoding="utf-8")
        self._trace(f"response saved: {report_path}")
        step = StepResult(
            step_id="codex_cli_live",
            agent="orchestrator",
            status="live",
            elapsed_ms=elapsed_ms,
            summary=_summarize(output),
            outputs=[str(report_path)],
            details={"backend": "codex_cli", "model": self.model, "workflow": workflow.slug},
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
            backend="codex_cli",
            metadata={"details": step.details},
        )
        store.append("structured_output_recorded", {"evidence_id": evidence.id, "claim_id": claim.id})
        store.append(
            "codex_cli_workflow_complete",
            {"elapsed_ms": elapsed_ms, "output": str(report_path), "stderr": str(logs_path) if stderr else ""},
        )
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


def _compact(text: str, limit: int = 800) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
