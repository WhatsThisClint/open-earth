from __future__ import annotations

from dataclasses import asdict

from .loader import ManifestLoader
from .models import McpServerSpec, normalize_slug


class McpRegistry:
    """Small registry around MCP manifests.

    This mirrors Hermes' "manage MCP servers centrally" idea, without pulling in
    Hermes' full runtime. The optional Agency Swarm adapter consumes these specs.
    """

    def __init__(self, loader: ManifestLoader):
        self.loader = loader

    def for_agent(self, agent_slug: str) -> list[McpServerSpec]:
        agent = self.loader.get_agent(agent_slug)
        all_servers = self.loader.mcp_servers()
        return [all_servers[normalize_slug(name)] for name in agent.mcp_servers]

    def status(self) -> list[dict]:
        rows = []
        agents = self.loader.agents()
        attached_by: dict[str, list[str]] = {}
        for agent in agents.values():
            for server in agent.mcp_servers:
                attached_by.setdefault(normalize_slug(server), []).append(agent.slug)
        for spec in self.loader.mcp_servers().values():
            row = asdict(spec)
            row["attached_agents"] = attached_by.get(spec.slug, [])
            rows.append(row)
        return rows

