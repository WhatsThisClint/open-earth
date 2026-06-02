# Troubleshooting

## `openearth validate` fails

Check that every agent prompt file exists, every workflow agent name exists, and every referenced MCP server or skill has a matching manifest.

## Live mode says Agency Swarm is missing

Install the optional extra:

```bash
uv sync --extra agency
```

For a tool install after GitHub publishing:

```bash
uv tool install "git+https://github.com/WhatsThisClint/open-earth[agency]"
```

## QGIS MCP is not found

Run:

```bash
openearth mcp doctor qgis
```

If the command is missing, configure it:

```bash
openearth mcp setup qgis --transport stdio --command qgis-mcp --enable --yes
```

For HTTP/SSE MCP servers, use `--transport streamable_http --url http://localhost:8000/mcp` or `--transport sse --url http://localhost:8000/sse`.

## API keys are missing

Dry-run mode does not need API keys. Live mode needs at least one model provider key in your environment.

## Artifacts look stale

Every run writes a new timestamped folder under `artifacts/`. The directory is ignored by git and can be deleted when you no longer need old run outputs.

