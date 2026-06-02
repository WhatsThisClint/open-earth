# QGIS MCP Setup Notes

`mcp_servers/qgis.mcp.yaml` is the integration point.

Default:

```yaml
enabled: false
transport: stdio
command: qgis-mcp
args: []
```

Change it to match your local QGIS MCP server.

Examples of possible shapes:

```yaml
transport: stdio
command: python
args:
  - path/to/qgis_mcp_server.py
```

```yaml
transport: sse
url: http://localhost:8000/sse
```

```yaml
transport: streamable_http
url: http://localhost:8000/mcp
```

## Recommended Tool Ownership

Start with QGIS on:

- GIS Analyst: create/inspect/export maps.
- QA Checker: verify map quality and project consistency.

Avoid giving every domain agent direct QGIS access at first. Let hydrogeology, economics, and watershed agents request maps through the GIS Analyst. This keeps context smaller and workflow behavior easier to debug.

## First Test

Once enabled, test a single GIS task before running the full workflow:

1. List layers.
2. Inspect CRS and extents.
3. Export one simple map.
4. Record the map path in the artifact ledger.


