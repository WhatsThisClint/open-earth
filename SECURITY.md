# Security

EarthSwarm is local-first and does not include telemetry. Projects can still process sensitive maps, reports, credentials, and local files, so treat every swarm workspace as sensitive.

## Supported Versions

Until the first stable release, security fixes target the latest `main` branch only.

## Reporting Issues

When this project is published, report vulnerabilities through private GitHub security advisories if enabled, or by contacting the maintainer directly. Do not open public issues for secrets, path traversal, command execution, or data exposure reports.

## Runtime Safety Defaults

- MCP servers default to approval-required tool use.
- QGIS is external and disabled in the starter template until configured.
- Doctor commands must not print API key values.
- Generated artifacts are ignored by git by default.
- Live mode is optional and requires installing the `agency` extra.
