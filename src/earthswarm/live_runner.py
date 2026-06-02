from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from . import agency_adapter
from .artifact_store import ArtifactStore
from .evidence import record_step_evidence
from .loader import ManifestLoader
from .trace import RunTracer
from .workflow_runner import StepResult, WorkflowRunResult


class AgencyWorkflowRunner:
    """Run an Open Earth workflow through the Agency Swarm backend."""

    def __init__(self, loader: ManifestLoader, artifact_root: str | Path | None = None, tracer: RunTracer | None = None):
        self.loader = loader
        self.artifact_root = Path(artifact_root or loader.root / "artifacts")
        self.tracer = tracer

    def run(self, workflow_slug: str, task: str) -> WorkflowRunResult:
        errors = self.loader.validate()
        if errors:
            raise ValueError("manifest validation failed:\n" + "\n".join(f"- {e}" for e in errors))
        if not agency_adapter.is_available():
            raise RuntimeError(
                "Agency Swarm backend is not installed. Install with: "
                "uv sync --extra agency or pip install 'openearth[agency]'"
            )

        workflow = self.loader.get_workflow(workflow_slug)
        store = ArtifactStore(self.artifact_root, workflow.slug)
        self._trace("Agency Swarm backend selected")
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
        store.append("live_workflow_start", {"workflow": workflow.slug, "task": task})
        start = time.perf_counter()

        self._trace("building Agency Swarm agency")
        agency = agency_adapter.build_agency(self.loader.root)
        self._trace("calling agency.get_response_sync")
        response = agency.get_response_sync(prompt)
        output = _extract_output(response)
        self._trace("Agency Swarm response received")

        report_path = store.run_dir / "reports" / "live_response.md"
        report_path.write_text(output, encoding="utf-8")
        self._trace(f"response saved: {report_path}")
        elapsed_ms = (time.perf_counter() - start) * 1000
        step = StepResult(
            step_id="agency_live",
            agent="orchestrator",
            status="live",
            elapsed_ms=elapsed_ms,
            summary=_summarize(output),
            outputs=[str(report_path)],
            details={"backend": "agency_swarm", "workflow": workflow.slug},
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
            backend="agency_swarm",
            metadata={"details": step.details},
        )
        store.append("structured_output_recorded", {"evidence_id": evidence.id, "claim_id": claim.id})
        store.append("live_workflow_complete", {"elapsed_ms": elapsed_ms, "output": str(report_path)})
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


def _compose_live_prompt(
    name: str,
    description: str,
    steps: list[dict[str, Any]],
    task: str,
    *,
    agents: dict[str, Any] | None = None,
    project_root: str | Path | None = None,
) -> str:
    step_lines = []
    for index, step in enumerate(steps, start=1):
        if "parallel" in step:
            parallel_agents = ", ".join(str(branch.get("agent")) for branch in step.get("parallel") or [])
            step_lines.append(f"{index}. Parallel specialists: {parallel_agents}")
        else:
            step_lines.append(f"{index}. {step.get('agent', 'orchestrator')}: {step.get('task', '')}")
    roster = _format_agent_roster(agents or {})
    project_notes = (
        f"\nProject root: {Path(project_root).resolve()}\n"
        "Use project-relative folders when creating artifacts: data/raw, data/processed, data/metadata, artifacts.\n"
        if project_root
        else ""
    )
    return (
        "Run this Open Earth workflow through the available specialist agents.\n\n"
        f"Workflow: {name}\n"
        f"Description: {description}\n"
        f"User task: {task}\n\n"
        f"{project_notes}"
        "Agent roster and responsibilities:\n"
        + roster
        + "\n\n"
        "Workflow steps:\n"
        + "\n".join(step_lines)
        + "\n\n"
        "When data are needed, first identify authoritative sources, download only bounded AOI/time-period data, "
        "record source URL, access date, license/terms, processing steps, and uncertainty in artifacts. "
        "Do not invent data. If a dataset requires login or an API key, explain exactly what is needed and continue "
        "with open substitutes when defensible.\n\n"
        "Return a concise final answer with findings, downloaded/created artifact notes, caveats, and recommended next steps."
    )


def _format_agent_roster(agents: dict[str, Any]) -> str:
    if not agents:
        return "- Use the workflow agents named in each step."
    lines: list[str] = []
    for slug, spec in agents.items():
        tools = ", ".join(getattr(spec, "tools", []) or []) or "none"
        mcps = ", ".join(getattr(spec, "mcp_servers", []) or []) or "none"
        skills = ", ".join(getattr(spec, "skills", []) or []) or "none"
        outputs = ", ".join(getattr(spec, "outputs", []) or []) or "not specified"
        lines.append(
            f"- {slug}: {spec.name} | {spec.role}. {spec.description} "
            f"Tools: {tools}. MCPs: {mcps}. Skills: {skills}. Expected outputs: {outputs}."
        )
    return "\n".join(lines)


def _extract_output(response: Any) -> str:
    value = getattr(response, "final_output", None)
    if value is None:
        value = getattr(response, "output", None)
    if value is None:
        value = response
    return str(value).strip() or "(empty live response)"


def _summarize(text: str, limit: int = 500) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."
