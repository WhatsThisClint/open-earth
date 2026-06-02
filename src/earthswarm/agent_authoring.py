from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import normalize_slug


ROLE_TEMPLATES = {
    "domain": {
        "tools": ["web_search", "python"],
        "outputs": ["findings", "evidence_needs"],
        "body": (
            "# Role\n\n"
            "You are {name}, a domain specialist in an Earth analysis swarm.\n\n"
            "# Responsibilities\n\n"
            "- Diagnose the task from your discipline's perspective.\n"
            "- State evidence, assumptions, uncertainty, and data gaps.\n"
            "- Request GIS/map work through the GIS Analyst when needed.\n"
            "- Produce concise outputs that a report writer can use.\n"
        ),
    },
    "gis": {
        "tools": ["web_search", "python"],
        "outputs": ["map_requests", "map_paths", "gis_findings"],
        "body": (
            "# Role\n\n"
            "You are {name}, the GIS and map production specialist.\n\n"
            "# Responsibilities\n\n"
            "- Inspect layers, CRS, extents, and map readiness.\n"
            "- Use QGIS MCP tools when enabled and approved.\n"
            "- Export maps into the run artifact directory.\n"
            "- Record map paths, assumptions, and visual QA notes.\n"
        ),
    },
    "writer": {
        "tools": ["web_search"],
        "outputs": ["report", "presentation_outline"],
        "body": (
            "# Role\n\n"
            "You are {name}, the synthesis and report writing specialist.\n\n"
            "# Responsibilities\n\n"
            "- Turn specialist findings into a structured, readable report.\n"
            "- Preserve uncertainty, caveats, and source needs.\n"
            "- Keep recommendations actionable for decision makers.\n"
        ),
    },
    "reviewer": {
        "tools": ["web_search"],
        "outputs": ["review_notes", "release_decision"],
        "body": (
            "# Role\n\n"
            "You are {name}, the quality and consistency checker.\n\n"
            "# Responsibilities\n\n"
            "- Check claims, maps, units, data gaps, and internal consistency.\n"
            "- Flag unsupported conclusions and missing artifacts.\n"
            "- Decide whether the deliverable is ready to share.\n"
        ),
    },
}


@dataclass(frozen=True)
class CreatedAgent:
    slug: str
    manifest_path: Path
    prompt_path: Path


def create_agent(
    root: str | Path,
    name: str | None = None,
    role: str = "",
    description: str = "",
    model: str = "gpt-5.2",
    reasoning: str = "medium",
    mcps: list[str] | None = None,
    skills: list[str] | None = None,
    outputs: list[str] | None = None,
    template: str = "domain",
    interactive: bool = False,
) -> CreatedAgent:
    if interactive:
        name = name or _ask("Agent name")
        role = role or _ask("Role or discipline", default="Domain specialist")
        description = description or _ask("Short description", default=role)
        model = _ask("Model", default=model)
        reasoning = _ask("Reasoning", default=reasoning)
        template = _ask("Template (domain/gis/writer/reviewer)", default=template)

    if not name:
        raise ValueError("agent name is required")

    slug = normalize_slug(name)
    root_path = Path(root).resolve()
    agent_dir = root_path / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = agent_dir / f"{slug}.agent.yaml"
    prompt_path = agent_dir / f"{slug}.md"
    if manifest_path.exists() or prompt_path.exists():
        raise FileExistsError(f"agent already exists: {slug}")

    selected = ROLE_TEMPLATES.get(template, ROLE_TEMPLATES["domain"])
    display_name = name.replace("_", " ").replace("-", " ").title()
    role_text = role or selected.get("role", display_name)
    description_text = description or role_text
    mcp_list = [normalize_slug(item) for item in (mcps or []) if item]
    skill_list = [normalize_slug(item) for item in (skills or []) if item]
    output_list = outputs or list(selected["outputs"])

    manifest = {
        "name": display_name,
        "role": role_text,
        "description": description_text,
        "model": model,
        "reasoning": reasoning,
        "instructions": f"{slug}.md",
        "tools": list(selected["tools"]),
        "mcp_servers": mcp_list,
        "skills": skill_list,
        "inputs": [],
        "outputs": output_list,
        "metadata": {"template": template},
    }
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    prompt_path.write_text(
        selected["body"].format(name=display_name).rstrip()
        + "\n\n# Current Focus\n\n"
        + description_text
        + "\n",
        encoding="utf-8",
    )
    return CreatedAgent(slug=slug, manifest_path=manifest_path, prompt_path=prompt_path)


def _ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default
