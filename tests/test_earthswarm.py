from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
import base64
import urllib.request
from pathlib import Path
from unittest.mock import patch

from earthswarm.agent_authoring import create_agent
from earthswarm.auth import AuthStore, HttpResponse, OpenAICodexOAuthClient, login_openai_codex_device_code
from earthswarm.cli import main
from earthswarm.data_acquisition import DataAcquisitionCatalog
from earthswarm.env import load_project_env
from earthswarm.dashboard import SESSION_HEADER, make_dashboard_server
from earthswarm.evidence import EvidenceStore
from earthswarm.field_validation import FieldValidationStore
from earthswarm.graph_store import GraphStore
from earthswarm.learning_memory import LearningMemoryStore
from earthswarm.live_runner import AgencyWorkflowRunner
from earthswarm.loader import ManifestLoader
from earthswarm.nvidia_runner import NvidiaWorkflowRunner
from earthswarm.ollama_runner import OllamaWorkflowRunner
from earthswarm.project import copy_starter_template
from earthswarm.review_queue import ReviewQueue
from earthswarm.trigger_engine import TriggerEngine
from earthswarm.upstream import UpstreamStatus, check_upstreams
from earthswarm.workflow_runner import FastWorkflowRunner


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_ROOT = ROOT / "src" / "earthswarm" / "templates" / "earth_analysis"


def make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    copy_starter_template(project)
    return project


def test_template_manifests_validate():
    loader = ManifestLoader(TEMPLATE_ROOT)
    assert loader.validate() == []
    agents = loader.agents()
    assert "gis_analyst" in agents
    assert "hydrogeologist" in agents
    assert "qa_checker" in agents
    assert agents["hydrogeologist"].mcp_servers == []
    assert agents["gis_analyst"].mcp_servers == ["qgis"]
    assert "qgis_map_recipes" in agents["gis_analyst"].skills
    assert "qgis_map_recipes" in ManifestLoader(TEMPLATE_ROOT).skills()
    assert (TEMPLATE_ROOT / "skills" / "qgis_map_recipes" / "scripts" / "openearth_qgis_recipes.py").exists()
    assert "dem_elevation" in DataAcquisitionCatalog(TEMPLATE_ROOT).recipes()


def test_packaged_text_files_do_not_start_with_bom():
    text_suffixes = {".md", ".py", ".yaml", ".yml", ".json", ".toml", ".txt", ".css", ".js", ".html"}
    roots = [ROOT / "README.md", ROOT / "docs", ROOT / "src" / "earthswarm" / "templates"]
    checked = 0
    for root in roots:
        paths = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
        for path in paths:
            if path.suffix.lower() not in text_suffixes:
                continue
            checked += 1
            assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), f"{path} starts with a UTF-8 BOM"
    assert checked > 0


def test_diagnostic_report_dry_run(tmp_path):
    project = make_project(tmp_path)
    loader = ManifestLoader(project)
    result = FastWorkflowRunner(loader, artifact_root=tmp_path).run(
        "diagnostic_report",
        "Diagnose watershed and groundwater issues.",
    )
    assert result.workflow == "diagnostic_report"
    assert result.artifact_dir.exists()
    assert (result.artifact_dir / "ledger.jsonl").exists()
    assert (result.artifact_dir / "run_summary.json").exists()
    assert any(step.agent == "hydrogeologist" for step in result.steps)
    assert any(step.step_id.startswith("domain_diagnosis") for step in result.steps)


def test_graph_store_ingests_project_and_artifacts(tmp_path):
    project = make_project(tmp_path)
    loader = ManifestLoader(project)
    LearningMemoryStore(project).add_record(
        kind="correction",
        title="Prefer source dates on map layouts",
        body="Human reviewer asked that every map layout include dataset dates and access dates.",
        applies_to=["agent:gis_analyst", "skill:qgis_map_recipes"],
        tags=["maps", "qa"],
    )
    result = FastWorkflowRunner(loader, artifact_root=project / "artifacts").run(
        "diagnostic_report",
        "Diagnose watershed and groundwater issues.",
    )

    store = GraphStore(project)
    project_counts = store.ingest_project(loader)
    artifact_counts = store.ingest_artifacts(run_id=result.run_id)
    stats = store.stats()
    evidence = EvidenceStore(project).list_evidence(limit=10)
    claims = EvidenceStore(project).list_claims(limit=10)

    assert project_counts["nodes"] > 0
    assert artifact_counts["documents"] > 0
    assert stats.nodes >= project_counts["nodes"]
    assert evidence
    assert claims
    assert any(result.run_id == item.run_id for item in evidence)
    assert store.query("groundwater", limit=3)
    network = store.network("hydrogeologist", limit=10)
    assert any(node["id"] == "agent:hydrogeologist" for node in network["nodes"])
    assert network["edges"]
    memory_network = store.network("source dates", limit=10)
    assert any(node["kind"] == "learning_record" for node in memory_network["nodes"])
    assert any(edge["kind"] == "correction_for" for edge in memory_network["edges"])
    hydro = store.show("agent:hydrogeologist")
    assert hydro["node"]["kind"] == "agent"


def test_trigger_engine_creates_review_item(tmp_path):
    project = make_project(tmp_path)
    matches = TriggerEngine(project).evaluate(
        {
            "source": "rainfall",
            "metric": "rainfall_6h_mm",
            "rainfall_6h_mm": 130,
            "location": "Upper watershed",
        }
    )
    assert len(matches) == 1
    assert matches[0].rule.id == "high_rainfall_6h"
    assert matches[0].review_item_id

    queue = ReviewQueue(project)
    items = queue.list_items()
    assert items[0].title == "High rainfall detected"
    updated = queue.transition(items[0].id, "field_check", note="Check stream crossing")
    assert updated.status == "needs_field_validation"
    task = FieldValidationStore(project).create_from_review(items[0].id, note="Check stream crossing")
    assert task.method == "stream_obs"
    assert task.review_item_id == items[0].id


def test_cli_init_validate_list_run_and_bench(tmp_path, capsys):
    project = tmp_path / "new-project"
    assert main(["init", str(project)]) == 0
    assert (project / "agents" / "gis_analyst.agent.yaml").exists()
    assert (project / "upstreams.yaml").exists()
    assert (project / "providers.yaml").exists()
    assert (project / ".openearth").exists()
    assert (project / "data_sources" / "acquisition_recipes.yaml").exists()

    assert main(["--root", str(project), "validate"]) == 0
    assert "Manifest validation passed" in capsys.readouterr().out

    assert main(["--root", str(project), "list", "agents"]) == 0
    assert "gis_analyst" in capsys.readouterr().out

    assert main(["--root", str(project), "run", "diagnostic_report", "--task", "Diagnose a basin"]) == 0
    assert "Workflow: diagnostic_report" in capsys.readouterr().out

    assert main(["--root", str(project), "setup"]) == 0
    assert "Dry-run mode is ready" in capsys.readouterr().out

    assert main(["--root", str(project), "bench", "--iterations", "2"]) == 0
    assert "mode:       dry-run" in capsys.readouterr().out


def test_cli_data_provider_and_evidence_commands(tmp_path, capsys):
    project = make_project(tmp_path)

    assert main(["--root", str(project), "providers", "list"]) == 0
    assert "ollama_local" in capsys.readouterr().out

    assert main(["--root", str(project), "providers", "status"]) == 0
    assert "codex_cli" in capsys.readouterr().out

    assert main(["--root", str(project), "data", "recipes"]) == 0
    assert "dem_elevation" in capsys.readouterr().out

    assert (
        main(
            [
                "--root",
                str(project),
                "data",
                "stage",
                "dem_elevation",
                "--aoi",
                "Kaprada taluka",
                "--note",
                "Need DEM for slope map.",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "blocked" in output
    assert "Evidence:" in output

    assert main(["--root", str(project), "evidence", "list"]) == 0
    output = capsys.readouterr().out
    assert "DEM and elevation staging record" in output
    evidence_id = output.split()[0]

    assert main(["--root", str(project), "evidence", "show", evidence_id]) == 0
    assert '"evidence_type": "blocker"' in capsys.readouterr().out


def test_cli_graph_commands(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "graph", "init"]) == 0
    assert "Graph store" in capsys.readouterr().out

    assert main(["--root", str(project), "graph", "ingest-project"]) == 0
    assert "Indexed project" in capsys.readouterr().out

    assert main(["--root", str(project), "graph", "query", "hydrogeologist"]) == 0
    assert "hydrogeologist" in capsys.readouterr().out.lower()

    assert main(["--root", str(project), "graph", "show", "agent:hydrogeologist"]) == 0
    assert '"kind": "agent"' in capsys.readouterr().out

    export_path = tmp_path / "graph.jsonl"
    assert main(["--root", str(project), "graph", "export", str(export_path)]) == 0
    assert export_path.exists()


def test_cli_memory_commands(tmp_path, capsys):
    project = make_project(tmp_path)
    assert (
        main(
            [
                "--root",
                str(project),
                "memory",
                "add",
                "--kind",
                "preference",
                "--title",
                "Prefer compact field maps",
                "--body",
                "Human reviewer prefers field maps with minimal labels and clear road access.",
                "--applies-to",
                "skill:qgis_map_recipes",
                "--tag",
                "maps",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "preference" in output

    assert main(["--root", str(project), "memory", "list"]) == 0
    output = capsys.readouterr().out
    assert "Prefer compact field maps" in output
    record_id = output.split()[0]

    assert main(["--root", str(project), "memory", "show", record_id]) == 0
    assert '"kind": "preference"' in capsys.readouterr().out

    assert main(["--root", str(project), "memory", "archive", record_id, "--note", "No longer relevant"]) == 0
    assert "archived" in capsys.readouterr().out


def test_cli_trigger_and_review_commands(tmp_path, capsys):
    project = make_project(tmp_path)
    assert main(["--root", str(project), "trigger", "list"]) == 0
    assert "high_rainfall_6h" in capsys.readouterr().out

    assert (
        main(
            [
                "--root",
                str(project),
                "trigger",
                "evaluate",
                "--source",
                "rainfall",
                "--metric",
                "rainfall_6h_mm",
                "--value",
                "130",
                "--location",
                "Upper watershed",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "Matched 1 trigger" in output

    assert main(["--root", str(project), "review", "list"]) == 0
    output = capsys.readouterr().out
    assert "High rainfall detected" in output
    review_id = output.split()[0]

    assert main(["--root", str(project), "review", "field-check", review_id, "--note", "Check stream crossing"]) == 0
    output = capsys.readouterr().out
    assert "needs_field_validation" in output
    assert "field task:" in output

    assert main(["--root", str(project), "field", "list", "--status", "all"]) == 0
    output = capsys.readouterr().out
    assert "Validate: High rainfall detected" in output
    field_id = output.split()[0]

    assert main(["--root", str(project), "field", "start", field_id, "--note", "Field team mobilized"]) == 0
    assert "in_progress" in capsys.readouterr().out

    assert (
        main(
            [
                "--root",
                str(project),
                "field",
                "complete",
                field_id,
                "--observation",
                "Stream crossing had high flow and bank erosion.",
                "--confidence-update",
                "Confirms runoff concern.",
            ]
        )
        == 0
    )
    assert "completed" in capsys.readouterr().out


def test_dashboard_api_lists_and_updates_agents(tmp_path):
    project = make_project(tmp_path)
    server = make_dashboard_server(project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status = _dashboard_json(base, "/api/status", server.token)
        assert status["valid"] is True
        agents = _dashboard_json(base, "/api/agents", server.token)["agents"]
        assert any(agent["slug"] == "hydrogeologist" for agent in agents)
        providers = _dashboard_json(base, "/api/providers", server.token)
        assert any(profile["id"] == "ollama_local" for profile in providers["profiles"])
        recipes = _dashboard_json(base, "/api/data/recipes", server.token)
        assert any(recipe["id"] == "dem_elevation" for recipe in recipes["recipes"])
        staged = _dashboard_json(
            base,
            "/api/data/stage",
            server.token,
            method="POST",
            body={"recipe": "dem_elevation", "aoi": "Kaprada taluka", "note": "Need terrain evidence."},
        )
        assert staged["result"]["status"] == "blocked"
        evidence = _dashboard_json(base, "/api/evidence?status=all", server.token)
        assert any(record["id"] == staged["result"]["evidence_id"] for record in evidence["records"])

        hydro = _dashboard_json(base, "/api/agents/hydrogeologist", server.token)
        payload = {
            "name": hydro["name"],
            "role": hydro["role"],
            "description": "Dashboard edited groundwater specialist.",
            "model": hydro["model"],
            "reasoning": hydro["reasoning"],
            "tools": hydro["tools"],
            "mcp_servers": hydro["mcp_servers"],
            "skills": hydro["skills"],
            "outputs": hydro["outputs"],
            "prompt": hydro["prompt"],
        }
        updated = _dashboard_json(base, "/api/agents/hydrogeologist", server.token, method="PUT", body=payload)
        assert updated["ok"] is True
        assert updated["agent"]["description"] == "Dashboard edited groundwater specialist."

        trigger = _dashboard_json(
            base,
            "/api/trigger/evaluate",
            server.token,
            method="POST",
            body={"source": "rainfall", "metric": "rainfall_6h_mm", "value": 130, "rainfall_6h_mm": 130},
        )
        assert trigger["matches"][0]["rule"]["id"] == "high_rainfall_6h"
        review = _dashboard_json(base, "/api/review?status=open", server.token)
        assert review["items"][0]["title"] == "High rainfall detected"
        network = _dashboard_json(base, "/api/graph/network?q=hydrogeologist&limit=10", server.token)
        assert any(node["id"] == "agent:hydrogeologist" for node in network["nodes"])
        memory = _dashboard_json(
            base,
            "/api/memory",
            server.token,
            method="POST",
            body={
                "kind": "human_feedback",
                "title": "Use local names in maps",
                "body": "Human reviewer wants village and watershed names preserved where available.",
                "applies_to": ["agent:gis_analyst"],
                "tags": ["maps"],
            },
        )
        assert memory["record"]["kind"] == "human_feedback"
        memory_list = _dashboard_json(base, "/api/memory?status=active", server.token)
        assert memory_list["records"][0]["title"] == "Use local names in maps"
        field_created = _dashboard_json(
            base,
            f"/api/review/{review['items'][0]['id']}/field-check",
            server.token,
            method="POST",
            body={"note": "Send field team", "actor": "test"},
        )
        assert field_created["field_task"]["method"] == "stream_obs"
        field_tasks = _dashboard_json(base, "/api/field?status=open", server.token)
        assert field_tasks["tasks"][0]["title"] == "Validate: High rainfall detected"
        completed = _dashboard_json(
            base,
            f"/api/field/{field_tasks['tasks'][0]['id']}/complete",
            server.token,
            method="POST",
            body={"observations": "Observed high stream stage.", "confidence_update": "Supports trigger."},
        )
        assert completed["task"]["status"] == "completed"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_agent_create_non_interactive(tmp_path):
    project = make_project(tmp_path)
    created = create_agent(
        project,
        name="soil_scientist",
        role="Soil scientist",
        description="Assesses soils and erosion risk.",
        template="domain",
    )
    assert created.manifest_path.exists()
    assert created.prompt_path.exists()
    loader = ManifestLoader(project)
    assert loader.validate() == []
    assert "soil_scientist" in loader.agents()


def test_project_env_loads_without_overriding_existing_values(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    (project / ".env").write_text(
        """
EARTHSWARM_CODEX_COMMAND="C:\\Users\\ferns\\AppData\\Roaming\\npm\\codex.cmd"
EARTHSWARM_CODEX_MODEL=gpt-5.5
EXISTING_VALUE=from_file
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING_VALUE", "from_env")
    load_project_env(project)
    assert "codex.cmd" in __import__("os").environ["EARTHSWARM_CODEX_COMMAND"]
    assert __import__("os").environ["EARTHSWARM_CODEX_MODEL"] == "gpt-5.5"
    assert __import__("os").environ["EXISTING_VALUE"] == "from_env"


def test_cli_agent_create_non_interactive(tmp_path):
    project = make_project(tmp_path)
    code = main(
        [
            "--root",
            str(project),
            "agent",
            "create",
            "soil_scientist",
            "--role",
            "Soil scientist",
            "--description",
            "Assesses soils and erosion risk.",
            "--template",
            "domain",
            "--yes",
        ]
    )
    assert code == 0
    assert (project / "agents" / "soil_scientist.agent.yaml").exists()


def test_qgis_mcp_setup_and_stdio_doctor(tmp_path, capsys):
    project = make_project(tmp_path)
    code = main(
        [
            "--root",
            str(project),
            "mcp",
            "setup",
            "qgis",
            "--transport",
            "stdio",
            "--command",
            sys.executable,
            "--enable",
            "--yes",
        ]
    )
    assert code == 0
    assert main(["--root", str(project), "mcp", "doctor", "qgis"]) == 0
    assert "Command found" in capsys.readouterr().out


def test_http_mcp_doctor_uses_reachability_not_real_qgis(tmp_path):
    project = make_project(tmp_path)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib hook name
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format, *args):  # noqa: A002 - stdlib hook name
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/mcp"
        assert (
            main(
                [
                    "--root",
                    str(project),
                    "mcp",
                    "setup",
                    "qgis",
                    "--transport",
                    "streamable_http",
                    "--url",
                    url,
                    "--enable",
                    "--yes",
                ]
            )
            == 0
        )
        assert main(["--root", str(project), "mcp", "doctor", "qgis"]) == 0
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_live_runner_records_mocked_agency_response(tmp_path, monkeypatch):
    project = make_project(tmp_path)

    class FakeAgency:
        def get_response_sync(self, prompt):
            assert "diagnostic_report" in prompt or "Diagnostic" in prompt
            return type("Response", (), {"final_output": "Final diagnosis with map notes."})()

    from earthswarm import live_runner

    monkeypatch.setattr(live_runner.agency_adapter, "is_available", lambda: True)
    monkeypatch.setattr(live_runner.agency_adapter, "build_agency", lambda root: FakeAgency())

    loader = ManifestLoader(project)
    result = AgencyWorkflowRunner(loader, artifact_root=tmp_path / "artifacts").run(
        "diagnostic_report",
        "Diagnose groundwater risk.",
    )
    assert result.steps[0].status == "live"
    report_path = Path(result.steps[0].outputs[0])
    assert report_path.exists()
    assert "Final diagnosis" in report_path.read_text(encoding="utf-8")
    summary = json.loads((result.artifact_dir / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["workflow"] == "diagnostic_report"


def test_package_wheel_installs_and_template_initializes(tmp_path):
    dist = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    wheel = next(dist.glob("openearth-*.whl"))
    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True, stdout=subprocess.PIPE, text=True)
    project = tmp_path / "installed-project"
    subprocess.run([str(python), "-m", "openearth.cli", "init", str(project)], check=True)
    subprocess.run([str(python), "-m", "earthswarm.cli", "--root", str(project), "validate"], check=True)
    assert (project / "upstreams.yaml").exists()
    assert (project / ".openearth").exists()


def test_openai_codex_device_login_stores_redacted_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EARTHSWARM_HOME", str(tmp_path / "home"))
    calls: list[str] = []
    poll_count = 0

    access_token = _fake_jwt(
        {
            "exp": 4_102_444_800,
            "https://api.openai.com/profile": {"email": "analyst@example.com"},
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct_123",
                "chatgpt_plan_type": "pro",
            },
        }
    )

    def fake_request(url: str, method: str, headers: dict[str, str], body: bytes) -> HttpResponse:
        nonlocal poll_count
        calls.append(url)
        if url.endswith("/api/accounts/deviceauth/usercode"):
            return HttpResponse(200, json.dumps({"device_auth_id": "device-1", "user_code": "ABCD-EFGH", "interval": 1}))
        if url.endswith("/api/accounts/deviceauth/token"):
            poll_count += 1
            if poll_count == 1:
                return HttpResponse(403, json.dumps({"error": "authorization_pending"}))
            return HttpResponse(200, json.dumps({"authorization_code": "auth-code", "code_verifier": "verifier"}))
        if url.endswith("/oauth/token"):
            return HttpResponse(
                200,
                json.dumps(
                    {
                        "access_token": access_token,
                        "refresh_token": "refresh-token-123",
                        "expires_in": 3600,
                    }
                ),
            )
        return HttpResponse(500, "{}")

    client = OpenAICodexOAuthClient(request_fn=fake_request, sleep_fn=lambda _: None, now_fn=lambda: 1000)
    prompts = []
    credential = login_openai_codex_device_code(
        store=AuthStore(),
        client=client,
        open_browser=False,
        on_verification=lambda prompt: prompts.append(prompt),
    )
    assert credential.email == "analyst@example.com"
    assert prompts[0].user_code == "ABCD-EFGH"
    assert any(call.endswith("/oauth/token") for call in calls)

    status = AuthStore().status("openai-codex", now=1000)
    assert status.valid
    assert status.profile_name == "analyst@example.com"
    public = AuthStore().read("openai-codex").public_dict()
    assert "..." in public["access_token"]
    assert "refresh-token-123" not in json.dumps(public)


def test_cli_auth_status_and_codex_cli_live_backend(tmp_path, capsys):
    project = make_project(tmp_path)
    fake_codex = tmp_path / "fake_codex.py"
    fake_codex.write_text(
        """
import sys
if "--version" in sys.argv:
    print("codex fake 1.0")
    raise SystemExit(0)
if "--login" in sys.argv or "login" in sys.argv:
    print("login ok")
    raise SystemExit(0)
if "exec" in sys.argv:
    assert "--skip-git-repo-check" in sys.argv
    assert "-" in sys.argv
    stdin_prompt = sys.stdin.read()
    assert "Workflow: Diagnostic Report" in stdin_prompt
    assert "Diagnose a basin" in stdin_prompt
    model = sys.argv[sys.argv.index("--model") + 1]
    print(f"Codex {model} diagnosis complete.")
    raise SystemExit(0)
print("unknown", sys.argv, file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    command = f'"{sys.executable}" "{fake_codex}"'

    assert main(["auth", "status", "--provider", "codex-cli", "--command", command]) == 0
    assert "codex fake 1.0" in capsys.readouterr().out

    assert main(["auth", "login", "--provider", "codex-cli", "--command", command]) == 0
    assert "Codex CLI login finished" in capsys.readouterr().out

    code = main(
        [
            "--root",
            str(project),
            "run",
            "diagnostic_report",
            "--task",
            "Diagnose a basin",
            "--live",
            "--backend",
            "codex-cli",
            "--codex-command",
            command,
            "--model",
            "openai/gpt-5.5",
        ]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "codex_cli_live" in output
    assert "Codex gpt-5.5 diagnosis complete" in output


def test_ollama_runner_records_response(tmp_path):
    project = make_project(tmp_path)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps({"response": "Local Ollama diagnosis."}).encode("utf-8")

    def fake_urlopen(request, timeout):
        assert request.full_url.endswith("/api/generate")
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "qwen3.5:9b"
        assert "Workflow: Diagnostic Report" in body["prompt"]
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        loader = ManifestLoader(project)
        result = OllamaWorkflowRunner(loader, artifact_root=tmp_path / "artifacts", model="ollama/qwen3.5:9b").run(
            "diagnostic_report",
            "Diagnose a local watershed.",
        )

    assert result.steps[0].details["backend"] == "ollama"
    report_path = Path(result.steps[0].outputs[0])
    assert "Local Ollama diagnosis" in report_path.read_text(encoding="utf-8")


def test_codex_runner_uses_utf8_and_handles_empty_stdout(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if "--version" in command:
            return subprocess.CompletedProcess(command, 0, stdout="codex-cli 0.130.0\n", stderr="")
        if "exec" in command:
            assert kwargs["encoding"] == "utf-8"
            assert kwargs["errors"] == "replace"
            assert kwargs["input"]
            return subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)
        raise AssertionError(command)

    import earthswarm.codex_cli as codex_cli

    monkeypatch.setattr(codex_cli.subprocess, "run", fake_run)
    loader = ManifestLoader(project)
    result = codex_cli.CodexCliWorkflowRunner(
        loader,
        artifact_root=tmp_path / "artifacts",
        command="codex",
    ).run("diagnostic_report", "Diagnose a basin")

    report_path = Path(result.steps[0].outputs[0])
    assert "(empty Codex CLI response)" in report_path.read_text(encoding="utf-8")
    assert any("--version" in call[0] for call in calls)


def test_nvidia_runner_records_streamed_response(tmp_path, monkeypatch):
    project = make_project(tmp_path)
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def __iter__(self):
            yield b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n'
            yield b'data: {"choices":[{"delta":{"content":"Kaprada"}}]}\n'
            yield b"data: [DONE]\n"

    def fake_urlopen(request, timeout):
        assert request.full_url == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.data.decode("utf-8"))
        assert body["model"] == "moonshotai/kimi-k2.6"
        assert body["stream"] is True
        assert "Workflow: Diagnostic Report" in body["messages"][0]["content"]
        return FakeResponse()

    with patch("urllib.request.urlopen", fake_urlopen):
        loader = ManifestLoader(project)
        result = NvidiaWorkflowRunner(loader, artifact_root=tmp_path / "artifacts").run(
            "diagnostic_report",
            "Diagnose a watershed.",
        )

    assert result.steps[0].details["backend"] == "nvidia"
    report_path = Path(result.steps[0].outputs[0])
    assert "Hello Kaprada" in report_path.read_text(encoding="utf-8")


def test_cli_live_run_prints_progress_trace(tmp_path, monkeypatch, capsys):
    project = make_project(tmp_path)

    class FakeRunner:
        def __init__(self, loader, artifact_root=None, model=None, host=None, tracer=None):
            self.loader = loader
            self.tracer = tracer

        def run(self, workflow, task):
            if self.tracer:
                self.tracer.emit("fake backend started")
            return FastWorkflowRunner(self.loader, artifact_root=tmp_path / "artifacts").run(workflow, task)

    import earthswarm.cli as cli

    monkeypatch.setattr(cli, "OllamaWorkflowRunner", FakeRunner)
    assert (
        main(
            [
                "--root",
                str(project),
                "run",
                "diagnostic_report",
                "--task",
                "Trace this",
                "--live",
                "--backend",
                "ollama",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "[openearth]" in output
    assert "backend: ollama" in output
    assert "fake backend started" in output


def _fake_jwt(payload: dict) -> str:
    def segment(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{segment({'alg': 'none'})}.{segment(payload)}.signature"


def _dashboard_json(base: str, path: str, token: str, method: str = "GET", body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={SESSION_HEADER: token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def test_upstream_check_detects_watched_changes_and_writes_state(tmp_path):
    config = tmp_path / "upstreams.yaml"
    config.write_text(
        """
version: 1
upstreams:
  - name: Example
    repo: example/repo
    branch: main
    last_reviewed_sha: aaa111
    watch_reason: Test upstream.
    watched_paths:
      - runtime/
      - pyproject.toml
    upgrade_notes:
      - Review carefully.
""",
        encoding="utf-8",
    )

    def fake_commit(repo: str, branch: str):
        assert repo == "example/repo"
        assert branch == "main"
        return {
            "sha": "bbb222",
            "html_url": "https://github.com/example/repo/commit/bbb222",
            "commit": {
                "message": "Improve runtime",
                "committer": {"date": "2026-05-17T00:00:00Z"},
            },
        }

    def fake_compare(repo: str, base: str, head: str):
        assert (base, head) == ("aaa111", "bbb222")
        return {
            "html_url": "https://github.com/example/repo/compare/aaa111...bbb222",
            "files": [{"filename": "runtime/scheduler.py"}, {"filename": "docs/readme.md"}],
        }

    state = tmp_path / ".earthswarm" / "upstream_state.json"
    statuses = check_upstreams(
        root=tmp_path,
        config_path=config,
        state_path=state,
        fetch_commit=fake_commit,
        fetch_compare=fake_compare,
        update_state=True,
    )
    assert statuses[0].status == "update_available"
    assert statuses[0].watched_changed_files == ["runtime/scheduler.py"]
    assert state.exists()
    assert "bbb222" in state.read_text(encoding="utf-8")


def test_cli_upstream_check_json_uses_reporter(tmp_path, monkeypatch, capsys):
    status = UpstreamStatus(
        name="Example",
        repo="example/repo",
        branch="main",
        status="current",
        latest_sha="aaa111",
        reviewed_sha="aaa111",
    )
    import earthswarm.cli as cli

    monkeypatch.setattr(cli, "check_upstreams", lambda **kwargs: [status])
    assert main(["--root", str(tmp_path), "upstream", "check", "--json", "--no-files"]) == 0
    output = capsys.readouterr().out
    assert '"name": "Example"' in output
    assert '"status": "current"' in output
