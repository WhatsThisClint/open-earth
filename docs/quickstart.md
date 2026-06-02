# Quickstart

Create a project:

```bash
openearth init watershed-diagnosis
cd watershed-diagnosis
openearth validate
```

List what the template includes:

```bash
openearth list agents
openearth list workflows
openearth list mcps
```

Run a fast dry-run:

```bash
openearth run diagnostic_report --task "Diagnose groundwater and watershed issues in my study area"
```

Inspect the created `artifacts/<run-id>/ledger.jsonl` and `run_summary.json`.

Search the local graph memory:

```bash
openearth graph status
openearth graph query groundwater
openearth graph show agent:hydrogeologist
```

When Agency Swarm is installed and keys are configured:

```bash
openearth run diagnostic_report --task "Diagnose the study area" --live
openearth tui
```

