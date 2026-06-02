from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from .loader import ManifestLoader
from .models import AgentSpec, McpServerSpec


def is_available() -> bool:
    return importlib.util.find_spec("agency_swarm") is not None and importlib.util.find_spec("agents.mcp") is not None


def build_mcp_server(spec: McpServerSpec) -> Any:
    """Convert an MCP manifest to an Agency Swarm/OpenAI Agents MCP object."""
    if spec.transport == "hosted":
        from agency_swarm import HostedMCPTool

        config = {
            "type": "mcp",
            "server_label": spec.name,
            "server_url": spec.url,
            "require_approval": "always" if spec.approval == "ask" else spec.approval,
        }
        if spec.headers:
            config["headers"] = spec.headers
        return HostedMCPTool(tool_config=config)

    if spec.transport in {"stdio", "local"}:
        from agents.mcp import MCPServerStdio

        return MCPServerStdio(
            name=spec.name,
            params={"command": spec.command, "args": spec.args, "env": spec.env},
            cache_tools_list=spec.cache_tools,
            client_session_timeout_seconds=spec.timeout_seconds,
            tool_filter=spec.tool_policy or None,
        )

    if spec.transport == "sse":
        from agents.mcp import MCPServerSse

        return MCPServerSse(
            name=spec.name,
            params={"url": spec.url, "headers": spec.headers},
            cache_tools_list=spec.cache_tools,
        )

    if spec.transport == "streamable_http":
        from agents.mcp import MCPServerStreamableHttp

        return MCPServerStreamableHttp(
            name=spec.name,
            params={"url": spec.url, "headers": spec.headers},
            cache_tools_list=spec.cache_tools,
        )

    raise ValueError(f"unsupported MCP transport: {spec.transport}")


def build_agent(spec: AgentSpec, mcp_servers: list[McpServerSpec]):
    """Build an Agency Swarm Agent from an Open Earth agent manifest."""
    from agency_swarm import Agent, ModelSettings
    from openai.types.shared import Reasoning

    hosted_tools = []
    local_mcp_servers = []
    for server in mcp_servers:
        if not server.enabled:
            continue
        built = build_mcp_server(server)
        if server.transport == "hosted":
            hosted_tools.append(built)
        else:
            local_mcp_servers.append(built)

    return Agent(
        name=spec.name,
        description=spec.description,
        instructions=spec.prompt,
        files_folder=str(spec.prompt_path.parent / "files"),
        tools=hosted_tools,
        mcp_servers=local_mcp_servers,
        model=spec.model,
        model_settings=ModelSettings(
            reasoning=Reasoning(effort=spec.reasoning, summary="auto"),
            truncation="auto",
        ),
    )


def build_agency(root: str | Path):
    """Build a default Agency Swarm agency from manifests.

    The first agent named `orchestrator` is the router. Other agents are
    specialists. This mirrors OpenSwarm's simple orchestrator-to-specialists
    topology while letting manifests own the roster.
    """
    from agency_swarm import Agency
    from agency_swarm.tools import Handoff, SendMessage

    loader = ManifestLoader(root)
    agents = loader.agents()
    mcps = loader.mcp_servers()
    built = {
        slug: build_agent(spec, [mcps[name] for name in spec.mcp_servers if name in mcps])
        for slug, spec in agents.items()
    }
    orchestrator = built.get("orchestrator") or next(iter(built.values()))
    all_agents = list(built.values())
    send_message_flows = [
        (orchestrator, specialist, SendMessage)
        for specialist in all_agents
        if specialist is not orchestrator
    ]
    # OpenSwarm patches Agency Swarm to allow dual communication tools on the
    # same edge. This adapter stays patch-free, so orchestrator -> specialist
    # uses SendMessage and specialist -> any other agent uses Handoff.
    handoff_flows = [
        (a > b, Handoff)
        for a in all_agents
        for b in all_agents
        if a is not b and a is not orchestrator
    ]
    return Agency(
        *all_agents,
        communication_flows=send_message_flows + handoff_flows,
        name="Open Earth",
        shared_instructions=str(Path(root) / "shared_instructions.md"),
    )
