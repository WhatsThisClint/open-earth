# Open Earth Analysis Template

This project is a starter swarm for Earth diagnosis workflows that produce findings, maps, reports, and review notes.

## Quickstart

```bash
openearth setup
openearth validate
openearth list agents
openearth run diagnostic_report --task "Diagnose groundwater and watershed risks for my study area"
openearth dashboard
```

The legacy `earthswarm` command remains available as a compatibility alias.

Dry-run mode is the default. It validates the workflow topology, runs parallel branches locally, writes a run ledger under `artifacts/`, records typed evidence/claims, and indexes local graph memory under `.openearth/graph.sqlite`.

Use `openearth setup` whenever you are unsure what is still missing. It checks
dry-run readiness, Codex CLI, provider keys, and QGIS MCP configuration.
If a `.env` file exists, Open Earth reads it before running commands.

## Dashboard

```bash
openearth dashboard
```

The local dashboard lets you edit agent YAML and prompt Markdown, create agents
from templates, validate the project, query graph memory, and run dry workflows.

## Trigger And Review Loop

```bash
openearth trigger list
openearth trigger evaluate --source rainfall --metric rainfall_6h_mm --value 130 --location "Upper watershed"
openearth review list
openearth review field-check <review-id> --note "Ask field team to check stream crossing"
openearth field list
openearth field complete <field-task-id> --observation "Observed high stream stage" --confidence-update "Supports the rainfall trigger"
```

Trigger rules live in `triggers/*.trigger.yaml`. A matched trigger creates a
human review item by default. A field-check review action creates a structured
field validation task with method, location, expected evidence, observations,
attachments, and confidence update fields.

## Graph RAG Memory

```bash
openearth graph status
openearth graph ingest-project
openearth graph ingest-artifacts
openearth graph query groundwater
openearth graph show agent:hydrogeologist
```

Use this to find prior evidence, data-source notes, agent prompts, and run outputs. SQLite is the default local store; PostgreSQL, PostGIS, and pgvector are the production evolution path for shared spatial and semantic memory.

## Evidence And Data Recipes

```bash
openearth data recipes
openearth data stage dem_elevation --aoi "Kaprada taluka, Gujarat"
openearth evidence list
openearth evidence claims
```

Data recipes stage public datasets where safe direct downloads are available, or
record a blocker when a portal, credential, AOI detail, or license check is
needed. Both paths create typed evidence so downstream claims stay traceable.

## Learning Memory

Capture human feedback, corrections, preferences, decisions, assumptions, and
lessons so they become graph nodes connected to agents, workflows, skills, runs,
maps, field tasks, or artifacts:

```bash
openearth memory add --kind preference --title "Prefer compact field maps" --body "Keep field maps simple, with road access and minimal labels." --applies-to skill:qgis_map_recipes --tag maps
openearth memory list
openearth graph query "compact field maps"
```

Learning memory is auditable. It improves retrieval and review context without
silently rewriting prompts or risk logic.

## Live Mode

Install the optional backend, set at least one provider API key, then run:

```bash
openearth run diagnostic_report --task "Diagnose the study area" --live
openearth tui
```

Useful environment variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `OPENROUTER_API_KEY`
- `GOOGLE_API_KEY`

For ChatGPT/Codex login instead of a manually copied API key, use the Codex CLI
backend:

```bash
openearth auth login --provider codex-cli
openearth run diagnostic_report --task "Diagnose the study area" --live --backend codex-cli --model gpt-5.5
```

`providers.yaml` keeps Codex CLI, API-key, future OpenAI Codex OAuth, Ollama, and
OpenRouter routes separate so agent manifests stay easy to change.

## Ollama / Minimax Live Mode

This template defaults `providers.yaml` to the `ollama_local` route with
`minimax-m3:cloud`. Confirm Ollama can resolve the model:

```bash
ollama run minimax-m3:cloud
openearth providers status
```

Then move from dry-run to a live workflow:

```bash
openearth run diagnostic_report \
  --task "Run a holistic environmental diagnosis for the study area. Stage or record authoritative AOI datasets where possible, preserve provenance and blockers, diagnose groundwater, watershed, soils, ecology, livelihood feasibility, intervention zones, maps needed, uncertainty, and report structure." \
  --live \
  --backend ollama \
  --model minimax-m3:cloud
```

Use `codex-ollama` when you want Codex orchestration with Ollama model calls:

```bash
openearth run diagnostic_report \
  --task "Run a full evidence-first environmental diagnosis with data staging, map requests, review notes, and a report draft." \
  --live \
  --backend codex-ollama \
  --model minimax-m3:cloud
```

After live runs, inspect the evidence trail before treating outputs as final:

```bash
openearth evidence list
openearth evidence claims
openearth review list
openearth graph ingest-artifacts
```

## Create A New Agent

```bash
openearth agent create soil_scientist --role "Soil scientist" --description "Assesses soil constraints and erosion risk" --template domain --yes
openearth validate
```

Use `--template gis`, `--template writer`, or `--template reviewer` for common agent shapes.

## Map Recipe Skills

Use `skills/qgis_map_recipes` when a smaller model needs to produce maps. It
contains a recipe registry, a `MapJob` contract, blocker rules, and reusable
PyQGIS code in `scripts/openearth_qgis_recipes.py`.

The GIS Analyst and QA Checker already reference this skill. Recipe ids include
`aoi_locator_map`, `dem_clip`, `terrain_slope_map`,
`watershed_delineation_map`, `drainage_watershed_map`,
`groundwater_potential_map`, `erosion_risk_screening_map`,
`lulc_vegetation_water_map`, `ndvi_vegetation_map`,
`settlement_exposure_map`, `intervention_opportunity_map`, and
`field_validation_map`.

## QGIS MCP

QGIS is external to Open Earth. Configure the local MCP connection when your QGIS MCP server is ready:

```bash
openearth mcp setup qgis --transport stdio --command qgis-mcp --enable --yes
openearth mcp doctor qgis
```

Keep QGIS attached first to the GIS Analyst and QA Checker. Other agents should request map work through the workflow rather than editing maps directly.

## Upstream Watch

Check whether the frameworks Open Earth learns from have changed:

```bash
openearth upstream check
```

When you have reviewed a new snapshot, record it locally:

```bash
openearth upstream check --update-state
```

This is a review aid only. It never changes agents, workflows, or package code automatically.


