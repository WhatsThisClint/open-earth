from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifact_store import ArtifactStore
from .evidence import record_step_evidence
from .loader import ManifestLoader
from .models import AgentSpec, normalize_slug


@dataclass
class StepResult:
    step_id: str
    agent: str
    status: str
    elapsed_ms: float
    summary: str
    outputs: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowRunResult:
    workflow: str
    run_id: str
    artifact_dir: Path
    elapsed_ms: float
    steps: list[StepResult]


class FastWorkflowRunner:
    """Fast manifest runner.

    This is deliberately not an LLM runner. It validates topology, creates the
    artifact ledger, executes independent branches concurrently, and returns a
    trace. That gives you a fast development loop before plugging in real agent
    calls through Agency Swarm or another backend.
    """

    def __init__(self, loader: ManifestLoader, artifact_root: str | Path | None = None):
        self.loader = loader
        self.artifact_root = Path(artifact_root or loader.root / "artifacts")

    def run(self, workflow_slug: str, task: str, dry_run: bool = True) -> WorkflowRunResult:
        errors = self.loader.validate()
        if errors:
            raise ValueError("manifest validation failed:\n" + "\n".join(f"- {e}" for e in errors))

        workflow = self.loader.get_workflow(workflow_slug)
        store = ArtifactStore(self.artifact_root, workflow.slug)
        start = time.perf_counter()
        results: list[StepResult] = []
        context: dict[str, Any] = {"original_task": task, "step_outputs": {}}

        store.append("workflow_start", {"workflow": workflow.slug, "task": task, "dry_run": dry_run})

        for index, step in enumerate(workflow.steps, start=1):
            step_id = str(step.get("id") or f"step_{index}")
            if "parallel" in step:
                branch_results = self._run_parallel_step(step_id, step, context, dry_run)
                results.extend(branch_results)
                context["step_outputs"][step_id] = [r.summary for r in branch_results]
                store.append("parallel_step_complete", {"step_id": step_id, "results": branch_results})
                for branch_result in branch_results:
                    self._record_structured_output(store, workflow.slug, branch_result, backend="dry_run")
                continue

            result = self._run_single_step(step_id, step, context, dry_run)
            results.append(result)
            context["step_outputs"][step_id] = result.summary
            store.append("step_complete", {"step_id": step_id, "result": result})
            self._record_structured_output(store, workflow.slug, result, backend="dry_run")

        elapsed_ms = (time.perf_counter() - start) * 1000
        run = WorkflowRunResult(
            workflow=workflow.slug,
            run_id=store.run_id,
            artifact_dir=store.run_dir,
            elapsed_ms=elapsed_ms,
            steps=results,
        )
        store.append("workflow_complete", {"elapsed_ms": elapsed_ms})
        store.write_summary(
            {
                "workflow": run.workflow,
                "run_id": run.run_id,
                "artifact_dir": run.artifact_dir,
                "elapsed_ms": run.elapsed_ms,
                "steps": run.steps,
            }
        )
        return run

    def _run_parallel_step(
        self,
        step_id: str,
        step: dict[str, Any],
        context: dict[str, Any],
        dry_run: bool,
    ) -> list[StepResult]:
        branches = list(step.get("parallel") or [])
        max_workers = int(step.get("max_workers") or min(4, max(1, len(branches))))
        results: list[StepResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    self._run_agent_task,
                    step_id=f"{step_id}.{i}",
                    agent_slug=normalize_slug(str(branch.get("agent"))),
                    task=str(branch.get("task") or step.get("task") or ""),
                    context=context,
                    dry_run=dry_run,
                    metadata=branch,
                ): branch
                for i, branch in enumerate(branches, start=1)
            }
            for future in as_completed(futures):
                results.append(future.result())
        return sorted(results, key=lambda item: item.step_id)

    def _run_single_step(
        self,
        step_id: str,
        step: dict[str, Any],
        context: dict[str, Any],
        dry_run: bool,
    ) -> StepResult:
        agent_slug = normalize_slug(str(step.get("agent") or "orchestrator"))
        return self._run_agent_task(
            step_id=step_id,
            agent_slug=agent_slug,
            task=str(step.get("task") or ""),
            context=context,
            dry_run=dry_run,
            metadata=step,
        )

    def _run_agent_task(
        self,
        step_id: str,
        agent_slug: str,
        task: str,
        context: dict[str, Any],
        dry_run: bool,
        metadata: dict[str, Any],
    ) -> StepResult:
        start = time.perf_counter()
        agent = self.loader.get_agent(agent_slug)
        summary = self._dry_run_summary(agent, task, context) if dry_run else self._not_configured(agent)
        return StepResult(
            step_id=step_id,
            agent=agent.slug,
            status="dry_run" if dry_run else "not_configured",
            elapsed_ms=(time.perf_counter() - start) * 1000,
            summary=summary,
            outputs=list(metadata.get("produces") or agent.outputs),
            details={
                "agent_name": agent.name,
                "model": agent.model,
                "reasoning": agent.reasoning,
                "tools": agent.tools,
                "mcp_servers": agent.mcp_servers,
                "skills": agent.skills,
                "task": task,
            },
        )

    def _dry_run_summary(self, agent: AgentSpec, task: str, context: dict[str, Any]) -> str:
        task_text = task or context.get("original_task") or "(no task supplied)"
        return (
            f"{agent.name} would handle: {task_text}. "
            f"Tools={len(agent.tools)}, MCPs={agent.mcp_servers or 'none'}, "
            f"outputs={agent.outputs or 'not specified'}."
        )

    def _not_configured(self, agent: AgentSpec) -> str:
        return (
            f"{agent.name} is loaded, but no live LLM backend is configured in this "
            "runtime yet. Use dry-run now or wire the Agency Swarm adapter."
        )

    def _record_structured_output(
        self,
        store: ArtifactStore,
        workflow_slug: str,
        result: StepResult,
        *,
        backend: str,
    ) -> None:
        evidence, claim = record_step_evidence(
            root=self.loader.root,
            workflow=workflow_slug,
            run_id=store.run_id,
            step_id=result.step_id,
            agent=result.agent,
            summary=result.summary,
            output_paths=result.outputs,
            status="draft",
            backend=backend,
            metadata={"step_status": result.status, "details": result.details},
        )
        store.append(
            "structured_output_recorded",
            {
                "step_id": result.step_id,
                "evidence_id": evidence.id,
                "claim_id": claim.id,
            },
        )
