# Repository Patterns Used

## OpenSwarm

Used as the main architectural base:

- Python-first agent factories.
- Orchestrator plus specialist agents.
- Agent-specific instructions.
- Shared instructions.
- Agency Swarm / OpenAI Agents SDK live backend path.
- MCP server attachment patterns.

Implemented in:

- `earthswarm/agency_adapter.py`
- `agents/*.agent.yaml`
- `shared_instructions.md`

## pi-vs-claude-code

Used for the fast authoring and orchestration shape:

- Agent cards as small manifest files.
- Team/chain style workflow manifests.
- Fast local orchestration before model calls.
- Parallel branch execution.
- Per-run trace/visibility.

Implemented in:

- `workflows/*.workflow.yaml`
- `earthswarm/workflow_runner.py`
- `earthswarm/cli.py`

## Hermes Agent

Used for runtime discipline:

- Central MCP registry.
- Toolset-style thinking.
- Skills as reusable procedural memory.
- Artifact and run ledger discipline.
- Optional future path for memory, session search, cron, and long-running coordination.

Implemented in:

- `mcp_servers/*.mcp.yaml`
- `earthswarm/mcp_registry.py`
- `skills/*/SKILL.md`
- `earthswarm/artifact_store.py`


