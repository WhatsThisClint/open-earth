# Agent Authoring

To create an agent, add two files:

```text
agents/<slug>.agent.yaml
agents/<slug>.md
```

Or use:

```powershell
uv run openearth scaffold-agent soil_scientist --role "Soil specialist" --description "Diagnoses soil constraints."
```

## Agent Manifest

```yaml
name: Soil Scientist
role: Soil and land capability specialist
description: Diagnoses soil constraints and land capability.
model: gpt-5.2
reasoning: medium
instructions: soil_scientist.md
tools:
  - web_search
  - python
mcp_servers:
  - qgis
skills: []
inputs:
  - soil_layers
  - land_use
outputs:
  - soil_findings
  - map_requests
```

## Prompt File

Keep prompts short at first:

```markdown
# Role

You are the Soil Scientist for Open Earth.

# Goals

- Diagnose soil constraints.
- Request maps when spatial evidence is needed.

# Output

Return findings, evidence, uncertainty, and recommended next actions.
```

## Best Practices

- Make roles real job roles, not tiny task bots.
- Keep tools minimal.
- Attach QGIS MCP sparingly.
- Put repeated procedures into `skills/`.
- Add agents to workflows only after their output contract is clear.


