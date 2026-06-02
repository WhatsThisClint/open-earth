# Open Earth North-Star Architecture

This document turns the workflow sketch into a buildable architecture for Open Earth.

The goal is not "many agents talking." The goal is a disciplined environmental diagnosis system that can acquire evidence, make maps, reason across disciplines, ask humans for judgment, record uncertainty, and improve from validated outcomes.

## What The Diagram Gets Right

- It separates data sources, tools, agents, outputs, human review, field validation, memory, and learning.
- It treats maps as evidence products, not decoration.
- It keeps humans in the loop for correction, contradiction, uncertainty, and field validation.
- It includes both live signals and static evidence, which is necessary for water, climate, and landscape work.
- It shows the knowledge graph as the memory spine rather than a generic chat history.

## Critique

- Too many agents can become noise. Every specialist needs a typed output contract, otherwise the orchestrator receives prose blobs instead of usable evidence.
- Triggers must not become diagnoses. A rainfall or sensor threshold should create a review item, not automatically declare flood risk.
- "Learning layer" is dangerous if it silently rewrites risk logic. Prompt and model improvements should be suggested, reviewed, and versioned.
- Live data needs freshness, units, CRS, license, and provenance checks. Without those, the system will confidently mix incompatible evidence.
- QGIS access should stay concentrated. Let GIS and QA agents operate QGIS directly; other agents should request maps through handoffs.
- Human review needs an audit trail. Approve, correct, reject, uncertainty, and field-check decisions must be stored as first-class events.
- Field validation is not just a task list. It needs location, method, expected observation, responsible person/team, due date, evidence upload, and final confidence update.
- The chatbot/dashboard should not bypass the orchestrator for serious analysis. It should submit tasks, show evidence, and expose review controls.
- Model outputs need confidence levels tied to evidence quality, not just model self-assessment.
- Data downloads must be bounded by AOI and task. A "download everything" agent will become slow, expensive, and legally messy.

## Enhanced System Layers

### 1. Data Plane

Sources:

- satellite imagery
- DEM/elevation/slope
- soil, geology, drainage, surface water, groundwater
- rainfall and weather forecasts
- water-level and soil-moisture sensors
- field observations
- online portals, APIs, and document repositories

Required metadata:

- source URL or API
- access date
- license/terms
- spatial extent
- CRS
- resolution
- time period
- local path
- uncertainty/caveat

### 2. Trigger Plane

Purpose:

- convert live/static events into reviewable signals
- detect thresholds, anomalies, missing data, stale sensors, and risk flags
- create human review items, not final conclusions

Current implementation:

```bash
openearth trigger list
openearth trigger evaluate --source rainfall --metric rainfall_6h_mm --value 130 --location "Upper watershed"
openearth review list
```

Rules live in:

```text
triggers/*.trigger.yaml
```

### 3. Orchestration Plane

The orchestrator should:

- frame the decision problem
- choose workflows and agents
- request map production
- route uncertain claims to human review
- request field validation
- decide when outputs are ready

It should not:

- hide data gaps
- allow agents to overwrite each other's evidence silently
- treat a model response as a validated observation

### 4. Specialist Agent Plane

Recommended agent families:

- data steward
- GIS analyst
- remote sensing analyst
- climate/hydrology analyst
- hydrogeologist
- watershed manager
- soil scientist
- ecologist
- economist/livelihood analyst
- risk analyst
- report writer
- QA checker
- communications agent

Each agent should produce structured outputs:

- findings
- evidence used
- map requests
- data requests
- assumptions
- uncertainty
- recommended next action

### 5. Tool Plane

Tools include:

- QGIS MCP
- Python scripts
- geoprocessing tools
- online portals
- APIs
- hydrological models
- remote sensing feeds
- weather services
- sensor streams
- databases
- document repositories

Rule:

Tools should be invoked through explicit approvals and artifact ledgers. Map and data-processing tools need provenance.

Current map-making implementation:

- `skills/qgis_map_recipes/SKILL.md` gives small models a fixed map-making protocol.
- `skills/qgis_map_recipes/map_recipes.yaml` lists recipe ids, inputs, outputs, and cautions.
- `skills/qgis_map_recipes/scripts/openearth_qgis_recipes.py` contains reusable PyQGIS code for terrain, drainage, groundwater, erosion, LULC, settlement exposure, and field-validation maps.
- GIS Analyst and QA Checker reference this skill by default.

Current data/evidence implementation:

- `data_sources/acquisition_recipes.yaml` lists deterministic dataset staging recipes.
- `openearth data stage <recipe>` records a staged dataset or an explicit access blocker.
- `openearth evidence list` and `openearth evidence claims` expose typed evidence and claim records.
- Workflow steps create draft claims connected to model-output evidence, so prose does not bypass review.

### 6. Evidence And Output Plane

Generated outputs:

- map layers
- map exports
- risk maps
- field checklists
- report drafts
- dashboard updates
- chatbot answers

Every output should have:

- generating agent
- source inputs
- method notes
- local path
- confidence/uncertainty
- review status

### 7. Human Review Plane

Current implementation:

```bash
openearth review list
openearth review show <review-id>
openearth review approve <review-id> --note "Looks consistent with field notes"
openearth review correct <review-id> --note "Rainfall station is outside AOI"
openearth review field-check <review-id> --note "Validate stream crossing"
openearth review uncertain <review-id> --note "DEM resolution too coarse"
openearth review reject <review-id> --note "Sensor appears faulty"
```

Human review decisions should become graph memory, not disappear into chat.

### 8. Field Validation Plane

Current implementation:

```bash
openearth review field-check <review-id> --note "Validate stream crossing"
openearth field list
openearth field show <field-task-id>
openearth field start <field-task-id> --note "Field team assigned"
openearth field complete <field-task-id> --observation "Observed high stream stage" --confidence-update "Supports rainfall trigger"
```

Field validation supports:

- field transects
- well checks
- stream observations
- soil checks
- community input
- field notebook entries and sensor photos

Current schema:

```text
field_task_id
review_item_id
location
geometry
method
question
expected_evidence
assigned_to
status
observations
attachments
confidence_update
```

The dashboard can create a field task from a review item and mark field tasks as
started, completed, or cancelled. CLI completion is better for real field notes
because it can include precise observations and attachments.

### 9. Knowledge Graph And Feedback Plane

The graph should store:

- human corrections
- human feedback and preferences
- decisions and assumptions
- verified insights
- failed assumptions
- field observations
- monitoring data
- sensor data
- generated maps
- model outputs
- risk events
- reports
- design logic
- intervention outcomes

Current implementation:

- SQLite graph memory in `.openearth/graph.sqlite`
- nodes for agents, workflows, MCPs, data sources, data recipes, evidence, claims, triggers, review items, field tasks, learning records, memory tags, runs, and artifacts
- chunked document retrieval
- learning memory store in `.openearth/learning_memory.sqlite`

Learning memory commands:

```bash
openearth memory add --kind correction --title "Map layouts need source dates" --body "Every map should include dataset date and access date." --applies-to agent:gis_analyst --tag maps
openearth memory list
openearth graph query "source dates"
```

Future production backend:

- PostgreSQL
- PostGIS
- pgvector

### 10. Learning Plane

Learning should be controlled:

- better retrieval
- prompt improvements
- updated examples
- model refinement
- risk model updates
- orchestration improvements

Do not auto-update production logic without review. Create proposed changes, run benchmarks, and store the decision.

## Typed Contracts To Add Next

- `EvidenceSource`
- `DataDownload`
- `MapJob`
- `MapRequest`
- `MapArtifact`
- `Finding`
- `RiskSignal`
- `ReviewItem`
- `FieldValidationTask`
- `InterventionOption`
- `ReportSection`

These contracts are the difference between a swarm that feels smart and a system that can be trusted.

## Implementation Roadmap

### Now

- Manifest-based agents and workflows
- QGIS MCP config
- local dashboard
- Graph RAG memory
- trigger rules
- human review queue

### Next

- workflow visual editor
- MCP editor in dashboard
- artifact browser with map previews
- field validation task model
- data download adapters for specific authoritative sources
- review items indexed into graph memory

### Later

- scheduled monitors
- streaming sensor adapters
- PostGIS/pgvector backend
- multi-user hosted dashboard
- role-based review and approval
- report and slide generation UI
- intervention outcome tracking

## Product Principle

Open Earth should feel like a professional environmental analysis cockpit, not a generic agent playground. The user should see evidence, maps, uncertainty, review state, and next actions at every step.
