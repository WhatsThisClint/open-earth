from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def normalize_slug(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


@dataclass(frozen=True)
class AgentSpec:
    slug: str
    name: str
    role: str
    description: str
    prompt_path: Path
    model: str = "gpt-5.2"
    reasoning: str = "medium"
    tools: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        return self.prompt_path.read_text(encoding="utf-8")

    @classmethod
    def from_mapping(cls, path: Path, data: dict[str, Any]) -> "AgentSpec":
        slug = normalize_slug(data.get("slug") or path.name.removesuffix(".agent.yaml"))
        prompt_ref = data.get("instructions") or data.get("prompt")
        if not prompt_ref:
            raise ValueError(f"{path}: agent requires 'instructions' or 'prompt'")
        prompt_path = (path.parent / str(prompt_ref)).resolve()
        return cls(
            slug=slug,
            name=str(data.get("name") or slug.replace("_", " ").title()),
            role=str(data.get("role") or ""),
            description=str(data.get("description") or ""),
            prompt_path=prompt_path,
            model=str(data.get("model") or "gpt-5.2"),
            reasoning=str(data.get("reasoning") or "medium"),
            tools=list(data.get("tools") or []),
            mcp_servers=list(data.get("mcp_servers") or []),
            skills=list(data.get("skills") or []),
            inputs=list(data.get("inputs") or []),
            outputs=list(data.get("outputs") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.name:
            errors.append(f"agent {self.slug}: missing name")
        if not self.description:
            errors.append(f"agent {self.slug}: missing description")
        if not self.prompt_path.exists():
            errors.append(f"agent {self.slug}: prompt file not found: {self.prompt_path}")
        return errors


@dataclass(frozen=True)
class McpServerSpec:
    slug: str
    name: str
    transport: str
    enabled: bool = False
    command: str | None = None
    args: list[str] = field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 120
    cache_tools: bool = True
    approval: str = "ask"
    tool_policy: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    @classmethod
    def from_mapping(cls, path: Path, data: dict[str, Any]) -> "McpServerSpec":
        slug = normalize_slug(data.get("slug") or path.name.removesuffix(".mcp.yaml"))
        return cls(
            slug=slug,
            name=str(data.get("name") or slug),
            transport=str(data.get("transport") or "stdio"),
            enabled=bool(data.get("enabled", False)),
            command=data.get("command"),
            args=list(data.get("args") or []),
            url=data.get("url"),
            env=dict(data.get("env") or {}),
            headers=dict(data.get("headers") or {}),
            timeout_seconds=int(data.get("timeout_seconds") or 120),
            cache_tools=bool(data.get("cache_tools", True)),
            approval=str(data.get("approval") or "ask"),
            tool_policy=dict(data.get("tool_policy") or {}),
            notes=str(data.get("notes") or ""),
        )

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.transport in {"stdio", "local"} and not self.command:
            errors.append(f"mcp {self.slug}: stdio transport requires command")
        if self.transport in {"sse", "streamable_http", "hosted"} and not self.url:
            errors.append(f"mcp {self.slug}: {self.transport} transport requires url")
        return errors


@dataclass(frozen=True)
class WorkflowSpec:
    slug: str
    name: str
    description: str
    steps: list[dict[str, Any]]
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, path: Path, data: dict[str, Any]) -> "WorkflowSpec":
        slug = normalize_slug(data.get("slug") or path.name.removesuffix(".workflow.yaml"))
        return cls(
            slug=slug,
            name=str(data.get("name") or slug.replace("_", " ").title()),
            description=str(data.get("description") or ""),
            steps=list(data.get("steps") or []),
            inputs=list(data.get("inputs") or []),
            outputs=list(data.get("outputs") or []),
            metadata=dict(data.get("metadata") or {}),
        )

    def validate(self, known_agents: set[str]) -> list[str]:
        errors: list[str] = []
        if not self.steps:
            errors.append(f"workflow {self.slug}: no steps")
        seen_ids: set[str] = set()
        for idx, step in enumerate(self.steps, start=1):
            step_id = str(step.get("id") or f"step_{idx}")
            if step_id in seen_ids:
                errors.append(f"workflow {self.slug}: duplicate step id {step_id}")
            seen_ids.add(step_id)
            if "parallel" in step:
                for branch in step.get("parallel") or []:
                    agent = normalize_slug(str(branch.get("agent") or ""))
                    if agent and agent not in known_agents:
                        errors.append(f"workflow {self.slug}:{step_id}: unknown agent {agent}")
            else:
                agent = normalize_slug(str(step.get("agent") or ""))
                if agent and agent not in known_agents:
                    errors.append(f"workflow {self.slug}:{step_id}: unknown agent {agent}")
        return errors

