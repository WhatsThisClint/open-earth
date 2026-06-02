# Contributing

EarthSwarm is a small Python package plus a starter Earth-analysis project template.

## Local Development

```bash
uv sync --extra dev
uv run pytest
uv run earthswarm init .tmp-earthswarm
uv run earthswarm --root .tmp-earthswarm validate
uv run earthswarm upstream check --config upstreams.yaml --no-files
```

For live Agency Swarm checks:

```bash
uv sync --extra dev --extra agency
uv run --extra agency earthswarm --root src/earthswarm/templates/earth_analysis agency-check
```

## Design Principles

- Keep dry-run mode fast and dependency-light.
- Keep agent authoring as YAML plus Markdown.
- Keep QGIS and other MCP servers external and configurable.
- Add dependencies only when they are needed by the base CLI.
- Prefer tests that use fake MCP servers or mocks over real external services.
- Use `earthswarm upstream check` to review upstream improvements before adapting patterns or dependency versions.

## Release Notes

The first public release should be installed from GitHub with `uv tool install`. PyPI publishing can come later after the CLI and template stabilize.
