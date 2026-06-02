# Architecture

## Design Aim

The main requirement is not a fixed set of agents. It is a fast authoring system:

1. Create an agent quickly.
2. Attach tools and MCP servers.
3. Place the agent into a workflow.
4. Run and inspect artifacts.
5. Iterate prompts, skills, and tools.

## Why This Shape

### From OpenSwarm

OpenSwarm has the best implementation base because it is Python-first and already uses specialist agents with an orchestrator. That fits QGIS, geospatial processing, reports, and presentations.

Kept:

- agent prompt files
- specialist folders/manifests
- orchestrator-to-specialist topology
- optional Agency Swarm adapter
- MCP connection patterns

### From Pi

Pi's repo is fast because orchestration is mostly manifest-driven and subprocess isolated. Creating an agent is just a small card; creating a chain is just a YAML file.

Kept:

- manifest-first authoring
- workflow YAML
- parallel branches
- fast local runner
- persistent artifact trace

### From Hermes

Hermes is too large to be the first base, but its runtime ideas are valuable.

Borrowed:

- central MCP registry
- skills as reusable procedures
- toolset thinking
- artifact/ledger discipline

Later:

- memory
- session search
- cron/scheduled monitoring
- stronger MCP lifecycle handling
- kanban-style long-running projects

## Runtime Layers

```text
ManifestLoader
  loads agents, workflows, MCP servers, skills

FastWorkflowRunner
  validates topology, creates run artifacts, runs parallel branches locally

ArtifactStore
  creates artifacts/<run_id> and ledger.jsonl

McpRegistry
  maps agent manifests to MCP server manifests

Agency Adapter
  optional bridge into Agency Swarm/OpenSwarm live execution
```

## First Live Execution Path

1. Confirm QGIS MCP command and transport.
2. Enable `mcp_servers/qgis.mcp.yaml`.
3. Install `openearth[agency]`.
4. Add a CLI command that calls `agency_adapter.build_agency(root)`.
5. Run a single-agent GIS Analyst task first.
6. Then run the `map_first_analysis` workflow.

## Artifact Contract

Every real run should write:

- `ledger.jsonl`: events, decisions, assumptions, produced files.
- `layers/`: derived or copied spatial layers.
- `maps/`: exported map images/PDFs.
- `tables/`: analysis tables.
- `evidence/`: citations and source extracts.
- `reports/`: report drafts/finals.
- `slides/`: presentation outputs.


