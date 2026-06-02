# Environmental Data Acquisition

Use this skill whenever an agent needs real environmental data rather than a
general web summary.

## Workflow

1. Define the AOI, time window, required resolution, and decision use.
2. Check `data_sources/environmental_data_catalog.yaml` for preferred sources.
3. Prefer official/local sources. Use global open datasets as fallback.
4. Download or stage only bounded AOI/time-period data when supported.
5. Keep raw inputs unchanged under `data/raw`.
6. Put clipped, cleaned, or derived outputs under `data/processed`.
7. Write metadata under `data/metadata`.
8. Hand GIS-ready layers to the GIS Analyst and interpretation notes to domain
   specialists.

## Required Provenance

For every dataset, record:

- source title
- source organization
- URL
- access date
- spatial extent
- temporal extent
- resolution/scale
- license or terms note
- local path
- processing steps
- limitations

## Download Decision

- If a dataset requires login/API key, do not invent access. Record the blocker
  and exact account/API key required.
- If a source provides an API, prefer scripted repeatable download.
- If only a manual portal exists, record manual steps and use screenshots or
  metadata only when no automated path is available.
- If a dataset is too large, request a clipped AOI extract or choose a lower
  resolution fallback.

## Output

Return a compact data inventory plus blockers and next download commands.
