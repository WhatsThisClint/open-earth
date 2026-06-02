# Install

## Development

```bash
uv sync --extra dev
uv run openearth --help
uv run pytest
```

## GitHub Install

After publishing the repository:

```bash
uv tool install "git+https://github.com/WhatsThisClint/open-earth"
openearth init my-earth-project
cd my-earth-project
openearth setup
```

With live Agency Swarm support:

```bash
uv tool install "git+https://github.com/WhatsThisClint/open-earth[agency]"
```

## Provider Keys

Open Earth does not require keys for validation or dry-run mode. Live mode needs at least one configured provider key, usually one of:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `OPENROUTER_API_KEY`
- `GOOGLE_API_KEY`

Keep keys in your shell environment or in an uncommitted `.env` file.

## ChatGPT/Codex Login

If you want the OpenClaw-style â€œsign in with ChatGPTâ€ experience, the most
usable v1 route is the official Codex CLI:

```bash
npm install -g @openai/codex
openearth auth login --provider codex-cli
openearth run diagnostic_report --task "Diagnose the project area" --live --backend codex-cli --model gpt-5.5
```

Open Earth also ships a local `openai-codex` OAuth profile store:

```bash
openearth auth login --provider openai-codex --device-code
openearth auth status --provider openai-codex
```

That profile is stored under `~/.openearth/auth/` by default and is intended for
a native Codex harness. Agency Swarm live mode still expects ordinary provider
credentials such as `OPENAI_API_KEY`.

