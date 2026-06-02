# Validation Results

Run date: 2026-05-17

## Local Checks

Manifest validation:

```powershell
uv run openearth validate
```

Result:

```text
Manifest validation passed.
```

Tests:

```powershell
uv run --extra dev pytest -q
```

Result:

```text
3 passed in 0.24s
```

Dry-run:

```powershell
uv run openearth run diagnostic_report --task "Diagnose groundwater decline and watershed intervention options for a semi-arid farming block"
```

Result:

```text
Workflow completed in about 121 ms and created an artifact run directory.
```

Fast benchmark:

```powershell
uv run openearth bench --iterations 50
```

Result:

```text
load p50: 16.13 ms
load max: 34.44 ms
run p50: 103.52 ms
run max: 160.89 ms
mode: dry-run, no LLM calls
```

Agency Swarm adapter:

```powershell
uv run --extra agency openearth agency-check
```

Result:

```text
Agency: Open Earth
```

Notes:

- The Pi-style manifest runner is fast because it does no LLM calls and keeps orchestration local.
- The OpenSwarm/Agency Swarm live backend installs and can instantiate the manifest-defined agency.
- LiteLLM emits optional Bedrock/SageMaker warnings when Agency Swarm imports; these do not block the current OpenAI/LiteLLM path.

