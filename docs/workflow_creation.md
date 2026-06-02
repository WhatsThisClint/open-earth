# Workflow Creation

Workflows live in `workflows/*.workflow.yaml`.

Sequential step:

```yaml
- id: write_report
  agent: report_writer
  task: Synthesize specialist findings into a report draft.
  produces:
    - report_draft
```

Parallel step:

```yaml
- id: domain_diagnosis
  max_workers: 3
  parallel:
    - agent: hydrogeologist
      task: Diagnose groundwater drivers.
      produces: [hydrogeology_findings]
    - agent: economist
      task: Diagnose livelihood and feasibility drivers.
      produces: [economic_findings]
```

Dry-run mode executes the manifest topology locally and concurrently. Live mode sends the workflow description and user task to the Agency Swarm orchestrator.

Recommended workflow shape:

1. Orchestrator frames the diagnosis.
2. Domain agents work in parallel.
3. GIS Analyst creates or checks maps.
4. Report Writer synthesizes outputs.
5. QA Checker validates evidence, maps, and readiness.

