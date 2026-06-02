# Using Open Earth For A Real Diagnosis Project

1. Create a new project with `openearth init`.
2. Put source data, notes, and map inputs in a project-local data folder that you do not commit if it is sensitive.
3. Configure QGIS MCP only after you have confirmed the local MCP server works.
4. Start with `openearth run diagnostic_report --task "...your brief..."` in dry-run mode.
5. Add or edit agents for the disciplines you need: hydrogeology, soil, economics, governance, climate, or operations.
6. Add workflow steps only when they clarify the work division.
7. Run live mode when the manifest topology is clean.
8. Review `artifacts/<run-id>/` before sharing outputs.

For most projects, keep the first workflow simple:

- Hydrogeologist: groundwater and recharge diagnosis.
- Watershed Manager: runoff, drainage, treatment, and intervention zones.
- Economist: cost, feasibility, incentives, and livelihood impacts.
- GIS Analyst: layer checks and map exports.
- Report Writer: synthesis.
- QA Checker: evidence and map quality review.

Add more agents only when they have a distinct discipline, tool access, or review responsibility.

