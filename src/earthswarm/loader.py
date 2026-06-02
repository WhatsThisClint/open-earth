from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .data_acquisition import DataAcquisitionCatalog
from .models import AgentSpec, McpServerSpec, WorkflowSpec, normalize_slug
from .provider_router import ProviderRouter


class ManifestLoader:
    """Loads Open Earth manifests from a project root."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.agents_dir = self.root / "agents"
        self.workflows_dir = self.root / "workflows"
        self.mcp_dir = self.root / "mcp_servers"
        self.skills_dir = self.root / "skills"

    def _read_yaml(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{path}: expected mapping")
        return data

    def agents(self) -> dict[str, AgentSpec]:
        specs: dict[str, AgentSpec] = {}
        for path in sorted(self.agents_dir.glob("*.agent.yaml")):
            spec = AgentSpec.from_mapping(path, self._read_yaml(path))
            specs[spec.slug] = spec
        return specs

    def workflows(self) -> dict[str, WorkflowSpec]:
        specs: dict[str, WorkflowSpec] = {}
        for path in sorted(self.workflows_dir.glob("*.workflow.yaml")):
            spec = WorkflowSpec.from_mapping(path, self._read_yaml(path))
            specs[spec.slug] = spec
        return specs

    def mcp_servers(self) -> dict[str, McpServerSpec]:
        specs: dict[str, McpServerSpec] = {}
        for path in sorted(self.mcp_dir.glob("*.mcp.yaml")):
            spec = McpServerSpec.from_mapping(path, self._read_yaml(path))
            specs[spec.slug] = spec
        return specs

    def skills(self) -> dict[str, Path]:
        result: dict[str, Path] = {}
        if not self.skills_dir.exists():
            return result
        for skill_file in sorted(self.skills_dir.glob("*/SKILL.md")):
            result[normalize_slug(skill_file.parent.name)] = skill_file.resolve()
        return result

    def get_agent(self, slug: str) -> AgentSpec:
        agents = self.agents()
        key = normalize_slug(slug)
        if key not in agents:
            raise KeyError(f"unknown agent: {slug}")
        return agents[key]

    def get_workflow(self, slug: str) -> WorkflowSpec:
        workflows = self.workflows()
        key = normalize_slug(slug)
        if key not in workflows:
            raise KeyError(f"unknown workflow: {slug}")
        return workflows[key]

    def validate(self) -> list[str]:
        errors: list[str] = []
        agents = self.agents()
        mcps = self.mcp_servers()
        skills = self.skills()
        for agent in agents.values():
            errors.extend(agent.validate())
            for server in agent.mcp_servers:
                if normalize_slug(server) not in mcps:
                    errors.append(f"agent {agent.slug}: unknown MCP server {server}")
            for skill in agent.skills:
                if normalize_slug(skill) not in skills:
                    errors.append(f"agent {agent.slug}: unknown skill {skill}")
        for mcp in mcps.values():
            errors.extend(mcp.validate())
        for workflow in self.workflows().values():
            errors.extend(workflow.validate(set(agents)))
        errors.extend(self._validate_data_catalog())
        errors.extend(DataAcquisitionCatalog(self.root).validate())
        errors.extend(ProviderRouter(self.root).validate())
        return errors

    def _validate_data_catalog(self) -> list[str]:
        path = self.root / "data_sources" / "environmental_data_catalog.yaml"
        if not path.exists():
            return []
        errors: list[str] = []
        try:
            data = self._read_yaml(path)
        except Exception as exc:
            return [str(exc)]
        sources = data.get("sources") or []
        if not isinstance(sources, list):
            return [f"{path}: sources must be a list"]
        seen: set[str] = set()
        for index, source in enumerate(sources, start=1):
            if not isinstance(source, dict):
                errors.append(f"{path}: source {index} must be a mapping")
                continue
            source_id = normalize_slug(str(source.get("id") or ""))
            if not source_id:
                errors.append(f"{path}: source {index} missing id")
            if source_id in seen:
                errors.append(f"{path}: duplicate source id {source_id}")
            seen.add(source_id)
            for key in ("name", "organization", "url", "access", "themes"):
                if not source.get(key):
                    errors.append(f"{path}: source {source_id or index} missing {key}")
        return errors
