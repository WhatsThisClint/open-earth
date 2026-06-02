# Upstream Watch

Open Earth borrows patterns from OpenSwarm, Pi, Hermes, and Agency Swarm. It should learn from those projects, but it should not automatically rewrite itself when they change.

Use:

```bash
openearth upstream check
```

The command reads `upstreams.yaml`, checks the latest GitHub commit for each watched repo, compares it to the reviewed SHA or local state, and reports:

- whether an upstream has changed
- latest commit metadata
- compare URL
- watched files that changed
- why that upstream matters
- the recommended review action

It does not edit Open Earth code.

## Record A Baseline

After reviewing an upstream snapshot:

```bash
openearth upstream check --update-state
```

This writes `.openearth/upstream_state.json` with the latest checked commits. You can keep that local, or commit it if your team wants shared review state.

## Project Configuration

`upstreams.yaml` has one row per watched project:

```yaml
upstreams:
  - name: OpenSwarm
    repo: VRSEN/OpenSwarm
    branch: main
    last_reviewed_sha: abc123
    watched_paths:
      - swarm.py
      - run_utils.py
      - shared_tools/
```

Use `watched_paths` to focus on files likely to affect Open Earth. For example, Hermes messaging-platform changes are usually irrelevant, but Hermes MCP, doctor, and security changes are worth checking.

## Review Loop

When updates appear:

1. Open the compare URL.
2. Read only watched or relevant changed files first.
3. Decide whether Open Earth should adopt a pattern or dependency update.
4. Run `openearth validate`, `openearth bench`, and `pytest`.
5. Update `last_reviewed_sha` or run `--update-state` after the review.

The included `upstream-watch` GitHub Action runs weekly and prints JSON for automation-friendly review.

