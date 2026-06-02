# Graph RAG Memory

Open Earth includes a local-first Graph RAG store so the swarm can remember:

- agent roles, prompts, skills, MCP attachments, and workflow topology
- data-source catalog entries and provenance rules
- run summaries, ledgers, reports, evidence records, claim records, and other text artifacts

The v1 backend is SQLite at `.openearth/graph.sqlite`. It is intentionally small, fast, and private to the project.

## Commands

```bash
openearth graph init
openearth graph ingest-project
openearth graph ingest-artifacts
openearth graph status
openearth graph query groundwater
openearth graph show agent:hydrogeologist
openearth graph export
```

Workflow runs are indexed automatically unless you pass `--no-graph`:

```bash
openearth run diagnostic_report --task "Diagnose Kaprada groundwater and watershed conditions"
```

## What v1 Does

- Stores graph nodes for agents, workflows, steps, MCP servers, skills, data catalogs, data sources, data recipes, evidence, claims, runs, and artifacts.
- Stores graph edges such as `agent_uses_mcp`, `agent_has_skill`, `workflow_has_step`, `step_uses_agent`, and `run_produced_artifact`.
- Chunks text files for lightweight retrieval.
- Keeps everything local and out of Git by default through `.openearth/`.

## What v1 Does Not Do Yet

- No embeddings or semantic vector search.
- No spatial SQL.
- No multi-user permissions or hosted service mode.
- No automatic extraction of typed findings from reports.

## Production Evolution Notes

When Open Earth needs shared memory across users or projects, evolve the storage layer without changing the public authoring model.

Recommended next backend:

- PostgreSQL for durable project/user/tenant storage.
- PostGIS for study-area geometries, dataset footprints, watershed polygons, map extents, and intervention zones.
- pgvector for semantic retrieval over artifact chunks, report sections, dataset metadata, and agent notes.
- JSONB metadata columns for flexible manifest and provenance records.
- Alembic migrations for schema changes.

Suggested table mapping:

- `kg_nodes` -> `kg_nodes(id, project_id, kind, label, properties jsonb, geom geometry, created_at, updated_at)`
- `kg_edges` -> `kg_edges(id, project_id, source_id, target_id, kind, properties jsonb, created_at, updated_at)`
- `documents` -> `documents(id, project_id, node_id, path, title, content, metadata jsonb, created_at, updated_at)`
- `chunks` -> `chunks(id, document_id, ordinal, text, embedding vector, metadata jsonb, created_at)`
- `runs` can remain nodes or become a first-class table if scheduling, retries, and billing need richer queries.

Hybrid retrieval target:

1. Filter by project, workflow, AOI, date, and artifact type.
2. Use graph traversal to find relevant agents, data sources, maps, and prior runs.
3. Use vector search over chunks for report text and provenance.
4. Use PostGIS predicates for spatial relevance, for example AOI intersects dataset footprint.
5. Return compact context packs to the orchestrator or specialist agent.

Keep SQLite as the default for solo users. Add PostgreSQL as an optional configured backend once the app needs collaboration, hosted deployments, or large knowledge bases.

