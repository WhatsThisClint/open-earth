# Agent Creation

Use the wizard:

```bash
openearth agent create
```

Use non-interactive creation for repeatable setup:

```bash
openearth agent create soil_scientist --role "Soil scientist" --description "Assesses soil constraints and erosion risk" --template domain --yes
```

Templates:

- `domain`: subject expert such as hydrogeologist, economist, soil scientist, or planner.
- `gis`: map and spatial analysis operator with optional QGIS MCP access.
- `writer`: report and presentation synthesis.
- `reviewer`: quality, evidence, and publication readiness checks.

After creation:

```bash
openearth validate
```

Keep direct QGIS MCP access limited. Most domain agents should produce `map_requests`; the GIS Analyst should perform map operations and write artifact paths.

