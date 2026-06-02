# Dashboard

Open Earth includes a local dashboard for configuring agents without leaving the project.

```bash
openearth dashboard
```

Open the printed local URL, normally `http://127.0.0.1:8765/`.

The dashboard can:

- list and edit agent manifests
- edit agent prompt Markdown
- create new agents from templates
- inspect workflows and MCP servers
- inspect provider/model routes
- stage data acquisition recipes and record blockers as evidence
- inspect evidence and claim records
- validate the project
- rebuild and query Graph RAG memory
- run dry or live workflows and index their artifacts
- select live backends such as Ollama, Codex CLI, Codex + Ollama, NVIDIA, or Agency Swarm
- check whether an Ollama host can see the selected model before running

The dashboard follows the same principle as the CLI: manifests remain the source of truth. Saving an agent writes `agents/<slug>.agent.yaml` and `agents/<slug>.md`, then runs validation.

## Run Workflows

Use the Run tab to choose:

- `Dry run` for fast manifest validation with no model calls
- `Live Ollama` for models served by Ollama, including `minimax-m3:cloud`
- `Live Codex + Ollama` for Codex CLI orchestration with Ollama model routing
- `Live Codex CLI` for the official Codex CLI backend
- `Live NVIDIA` for the hosted NVIDIA chat route
- `Live Agency` for the optional Agency Swarm backend

For Ollama, keep the host as `http://127.0.0.1:11434` unless your Ollama server
is elsewhere. Set the model field, then click **Check Ollama**. If the model is
not listed, run this once in a terminal:

```bash
ollama run minimax-m3:cloud
```

Live runs can take several minutes. When a run finishes, the dashboard shows the
backend, model, run id, artifact directory, trace messages, step summaries, and
updated graph counts.

## Security Notes

- The dashboard binds to `127.0.0.1` by default.
- Write APIs require a per-process session token injected into the page.
- Binding outside loopback requires `--insecure` and should only be used on trusted networks.
- The dashboard does not expose secret values.

## Hermes-Inspired Patterns Used

Hermes' dashboard uses a local backend, a static SPA, session-token-protected API calls, and backend routes that reuse CLI/runtime code. Open Earth adopts those patterns, but keeps v1 lightweight: no Node build step, no plugin system, and no bundled terminal chat pane yet.

Future dashboard evolution:

- workflow visual editor
- MCP manifest editor with safe connection tests
- provider/model route editor for saving route changes back to `providers.yaml`
- artifact browser with map previews
- Graph RAG explorer with node neighborhoods
- live run monitor with streaming progress
- optional React/Vite app once the UI outgrows the static bundle

