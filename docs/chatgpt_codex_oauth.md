# ChatGPT And Codex Auth

Open Earth keeps model, auth, and runtime as separate choices:

- `codex-cli` uses the official Codex CLI login and runs workflows through `codex exec`.
- `openai_api_key` uses Agency Swarm with normal API-key credentials.
- `openai-codex` stores an OpenClaw-style ChatGPT/Codex OAuth profile for a future native Codex harness.
- `ollama_local` and `openrouter` are OpenAI-compatible routes you can configure later.

## Recommended Codex Path

```bash
npm install -g @openai/codex
openearth auth login --provider codex-cli
openearth auth status --provider codex-cli
openearth run diagnostic_report --task "Diagnose the study area" --live --backend codex-cli --model gpt-5.5
```

Set `EARTHSWARM_CODEX_COMMAND` if your Codex executable is not simply `codex`.
Set `EARTHSWARM_CODEX_MODEL` to change the default model.

## OpenAI Codex OAuth Store

```bash
openearth auth login --provider openai-codex --device-code
openearth auth status --provider openai-codex
openearth auth refresh --provider openai-codex
openearth auth logout --provider openai-codex
```

The stored file lives under `~/.openearth/auth/openai-codex.json` unless
`OPENEARTH_HOME` or the legacy `EARTHSWARM_HOME` is set. Status and doctor
commands redact token material.

These OAuth tokens are not OpenAI Platform API keys. Do not pass them to Agency
Swarm as `OPENAI_API_KEY`; a runtime has to explicitly understand this auth
route.

## Why This Split Exists

OpenClaw separates provider/model/runtime/auth because Codex subscription usage
is not the same as direct API-key usage. Open Earth follows the same rule:
agent manifests can keep simple model strings, while project-level route config
decides whether a run uses Agency Swarm, Codex CLI, or a future native Codex
harness.

