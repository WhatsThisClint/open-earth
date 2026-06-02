# Open Earth

Open Earth is a manifest-driven agent swarm kit for Earth analysis workflows: problem diagnosis, QGIS MCP map production, specialist review, reports, and presentation-ready synthesis.

The core stays fast and simple:

- Agents are YAML manifests plus Markdown prompts.
- Workflows are sequential and parallel YAML pipelines.
- Evidence and claims are typed, provenance-first records.
- Dry-run mode validates topology and writes artifacts without LLM calls.
- Local Graph RAG memory indexes agents, data sources, workflows, and run artifacts.
- Live mode is optional and uses Agency Swarm.
- QGIS MCP is local-first and configured by the user.

## Install

During local development:

```bash
uv sync --extra dev
uv run openearth --help
```

After publishing this repo to GitHub:

```bash
uv tool install "git+https://github.com/WhatsThisClint/open-earth"
openearth init my-earth-project
```

The legacy `earthswarm` command remains available as a compatibility alias.

Optional live backend:

```bash
uv tool install "git+https://github.com/WhatsThisClint/open-earth[agency]"
```

## Quickstart

```bash
openearth init watershed-diagnosis
cd watershed-diagnosis
openearth setup
openearth doctor
openearth validate
openearth list agents
openearth run diagnostic_report --task "Diagnose groundwater, watershed, and livelihood risks for this study area"
openearth dashboard
```

The dry run writes a ledger and summary under `artifacts/`.
It also records typed evidence/claims and indexes `.openearth/graph.sqlite`
unless you pass `--no-graph`.

`openearth setup` is the first command to run inside a project. It tells you
whether dry mode is ready, whether Codex CLI can run, which provider keys are
present, and what to do next for QGIS MCP.

Open Earth automatically reads a project `.env` file if present. Existing shell
environment variables are left unchanged.

## Live Agency Swarm Mode

Set a provider key, then run:

```bash
openearth run diagnostic_report --task "Diagnose the project area" --live
openearth tui
```

Useful keys include `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`, and `GOOGLE_API_KEY`.

## Live Ollama / Minimax Mode

Open Earth can run live workflows against Ollama's OpenAI-compatible local or
cloud models. First make sure Ollama can see the model:

```bash
ollama run minimax-m3:cloud
openearth providers status
```

Then run a live environmental diagnosis:

```bash
openearth run diagnostic_report \
  --task "Run a holistic environmental diagnosis for the study area. Stage or record authoritative AOI datasets where possible, preserve provenance and blockers, diagnose groundwater, watershed, soils, ecology, livelihood feasibility, intervention zones, maps needed, uncertainty, and report structure." \
  --live \
  --backend ollama \
  --model minimax-m3:cloud
```

Use `--backend codex-ollama` when you want Codex CLI orchestration while still
routing model calls through Ollama:

```bash
openearth run diagnostic_report \
  --task "Run a full evidence-first environmental diagnosis with data staging, map requests, review notes, and a report draft." \
  --live \
  --backend codex-ollama \
  --model minimax-m3:cloud
```

The recommended progression is: validate the project, stage or record data
recipes, run live, inspect evidence and draft claims, request field checks for
uncertain items, then rebuild graph memory:

```bash
openearth validate
openearth data recipes
openearth evidence list
openearth evidence claims
openearth review list
openearth graph ingest-artifacts
```

## ChatGPT/Codex Login

For the ChatGPT-style Codex route, sign in through the official Codex CLI:

```bash
openearth auth login --provider codex-cli
openearth auth status
openearth run diagnostic_report --task "Diagnose the project area" --live --backend codex-cli --model gpt-5.5
```

Open Earth also includes an OpenClaw-style OAuth profile store for future native
Codex harness work:

```bash
openearth auth login --provider openai-codex --device-code
openearth auth status --provider openai-codex
```

Those OAuth tokens are not treated as OpenAI Platform API keys; Agency Swarm live
mode still uses normal provider credentials.

## Create Agents

```bash
openearth agent create soil_scientist --role "Soil scientist" --description "Assesses soil constraints and erosion risk" --template domain --yes
openearth validate
```

Templates: `domain`, `gis`, `writer`, `reviewer`.

You can also create and edit agents in the local dashboard:

```bash
openearth dashboard
```

## Small-Model Map Skills

The template includes `skills/qgis_map_recipes`, a deterministic QGIS recipe
library for map production. Small models should not invent GIS steps; they
should choose a recipe, fill a `MapJob`, and run or hand off the included
PyQGIS code.

Included recipes:

- `terrain_slope_map`
- `dem_clip`
- `watershed_delineation_map`
- `drainage_watershed_map`
- `groundwater_potential_map`
- `erosion_risk_screening_map`
- `lulc_vegetation_water_map`
- `ndvi_vegetation_map`
- `settlement_exposure_map`
- `intervention_opportunity_map`
- `field_validation_map`

The GIS Analyst and QA Checker include this skill by default.

## Graph RAG Memory

```bash
openearth graph ingest-project
openearth graph ingest-artifacts
openearth graph status
openearth graph query groundwater
openearth graph show agent:hydrogeologist
```

The dashboard Graph tab shows a visual knowledge graph with typed nodes and
edges, plus search results for document chunks. The default graph store is local
SQLite. Future production deployments can use PostgreSQL, PostGIS, and pgvector
without changing the YAML agent/workflow authoring model. See `docs/graph_rag.md`.

## Evidence And Data Recipes

```bash
openearth data recipes
openearth data stage dem_elevation --aoi "Kaprada taluka, Gujarat"
openearth evidence list
openearth evidence claims
```

Data recipes stage public datasets when a safe direct download is available, or
write a blocker record when credentials, portals, AOI details, or licensing are
still needed. Either way, Open Earth creates typed evidence so the diagnosis can
say what is known, what is staged locally, and what is blocked.

## Learning Memory

Human feedback, corrections, preferences, decisions, assumptions, and field
insights are captured as learning memory and indexed into the graph:

```bash
openearth memory add --kind correction --title "Map layouts need source dates" --body "Human reviewer asked that every map include dataset dates and access dates." --applies-to agent:gis_analyst --tag maps
openearth memory list
openearth graph query "source dates"
```

This is intentionally auditable. Learning records influence future retrieval and
review, but they do not silently rewrite agents or risk logic.

## Trigger And Review Loop

```bash
openearth trigger list
openearth trigger evaluate --source rainfall --metric rainfall_6h_mm --value 130 --location "Upper watershed"
openearth review list
openearth review field-check <review-id> --note "Ask field team to verify stream crossing"
openearth field list
openearth field start <field-task-id> --note "Field team mobilized"
openearth field complete <field-task-id> --observation "Stream crossing shows active bank erosion" --confidence-update "Supports runoff concern"
```

Trigger rules live in `triggers/*.trigger.yaml`. Field tasks are stored locally
with method, location, expected evidence, observations, attachments, and a final
confidence update. The dashboard also exposes review and field-task controls.
See `docs/open_earth_north_star.md` for the enhanced architecture and critique.

## QGIS MCP

QGIS is not bundled. Configure your local MCP server when it is ready:

```bash
openearth mcp setup qgis --transport stdio --command qgis-mcp --enable --yes
openearth mcp doctor qgis
```

Start with QGIS access on the GIS Analyst and QA Checker only. Domain specialists should request map work through workflow handoffs.

## Upstream Watch

Open Earth can watch OpenSwarm, Pi, Hermes, and Agency Swarm for relevant updates:

```bash
openearth upstream check
```

This reports upstream drift, watched-file changes, and review recommendations. It never edits Open Earth code automatically. See `docs/upstream_watch.md`.

## Project Structure

```text
agents/          agent manifests and prompts
workflows/       chain and parallel workflow manifests
mcp_servers/     MCP server connection manifests
skills/          reusable procedural domain skills
artifacts/       generated run ledgers, maps, reports, and slides
data_sources/    source catalogs and provenance rules
providers.yaml   model/auth/runtime route notes
upstreams.yaml   watched upstream framework sources
.openearth/      local graph/evidence memory and runtime state, ignored by Git
```

## Reference Patterns

- OpenSwarm: Agency Swarm backend and orchestrator plus specialists.
- Pi examples: fast chain/team manifests and visible run traces.
- Hermes Agent: doctor commands, MCP discipline, skills, security, and packaging hygiene.

See `docs/` for authoring, QGIS, workflow, and troubleshooting guides.

