from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from .loader import ManifestLoader
from .models import normalize_slug
from .paths import project_state_file


TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".csv", ".py"}
MAX_DOCUMENT_CHARS = 250_000
CHUNK_CHARS = 1_600
CHUNK_OVERLAP = 180


@dataclass(frozen=True)
class GraphStats:
    path: Path
    nodes: int
    edges: int
    documents: int
    chunks: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "nodes": self.nodes,
            "edges": self.edges,
            "documents": self.documents,
            "chunks": self.chunks,
        }


class GraphStore:
    """Local-first graph/RAG store for Open Earth projects.

    The v1 store deliberately uses SQLite so a new project has memory and
    retrieval without requiring a database server. It stores the graph as nodes
    and edges, plus chunked documents for prompt/context retrieval.
    """

    def __init__(self, root: str | Path, path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(path).resolve() if path else project_state_file(self.root, "graph.sqlite")

    def init(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS kg_nodes (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    label TEXT NOT NULL,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_kg_nodes_kind ON kg_nodes(kind);
                CREATE INDEX IF NOT EXISTS idx_kg_nodes_label ON kg_nodes(label);

                CREATE TABLE IF NOT EXISTS kg_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id);
                CREATE INDEX IF NOT EXISTS idx_kg_edges_kind ON kg_edges(kind);

                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    node_id TEXT,
                    path TEXT,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_documents_node ON documents(node_id);
                CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);

                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
                """
            )
        return self.path

    def stats(self) -> GraphStats:
        self.init()
        with self._connect() as conn:
            return GraphStats(
                path=self.path,
                nodes=self._count(conn, "kg_nodes"),
                edges=self._count(conn, "kg_edges"),
                documents=self._count(conn, "documents"),
                chunks=self._count(conn, "chunks"),
            )

    def upsert_node(
        self,
        kind: str,
        label: str,
        properties: dict[str, Any] | None = None,
        node_id: str | None = None,
    ) -> str:
        self.init()
        normalized_kind = normalize_slug(kind)
        node_id = node_id or _readable_id(normalized_kind, label)
        now = _now()
        props = _json(properties or {})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kg_nodes (id, kind, label, properties_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    label = excluded.label,
                    properties_json = excluded.properties_json,
                    updated_at = excluded.updated_at
                """,
                (node_id, normalized_kind, label, props, now, now),
            )
        return node_id

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        kind: str,
        properties: dict[str, Any] | None = None,
        edge_id: str | None = None,
    ) -> str:
        self.init()
        props = properties or {}
        edge_id = edge_id or "edge:" + _hash("|".join([source_id, normalize_slug(kind), target_id, _json(props)]))
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kg_edges (id, source_id, target_id, kind, properties_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_id = excluded.source_id,
                    target_id = excluded.target_id,
                    kind = excluded.kind,
                    properties_json = excluded.properties_json,
                    updated_at = excluded.updated_at
                """,
                (edge_id, source_id, target_id, normalize_slug(kind), _json(props), now, now),
            )
        return edge_id

    def add_document(
        self,
        *,
        title: str,
        content: str,
        path: str | Path | None = None,
        node_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        document_id: str | None = None,
    ) -> str:
        self.init()
        path_text = _relative_path(self.root, path) if path else ""
        document_id = document_id or "doc:" + _hash(f"{path_text}|{title}")
        now = _now()
        clean_content = content[:MAX_DOCUMENT_CHARS]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (id, node_id, path, title, content, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    node_id = excluded.node_id,
                    path = excluded.path,
                    title = excluded.title,
                    content = excluded.content,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (document_id, node_id, path_text, title, clean_content, _json(metadata or {}), now, now),
            )
            conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            for ordinal, chunk in enumerate(_chunks(clean_content), start=1):
                chunk_id = f"{document_id}:chunk:{ordinal:04d}"
                conn.execute(
                    """
                    INSERT INTO chunks (id, document_id, ordinal, text, metadata_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (chunk_id, document_id, ordinal, chunk, _json({"title": title, "path": path_text}), now),
                )
        return document_id

    def query(self, text: str, limit: int = 10) -> list[dict[str, Any]]:
        self.init()
        needle = text.strip().lower()
        limit = max(1, int(limit))
        with self._connect() as conn:
            if not needle:
                rows = conn.execute(
                    """
                    SELECT id, kind, label, properties_json
                    FROM kg_nodes
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                return [_node_result(row) for row in rows]

            like = f"%{needle}%"
            node_rows = conn.execute(
                """
                SELECT id, kind, label, properties_json
                FROM kg_nodes
                WHERE lower(id) LIKE ?
                   OR lower(kind) LIKE ?
                   OR lower(label) LIKE ?
                   OR lower(properties_json) LIKE ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (like, like, like, like, limit),
            ).fetchall()
            chunk_rows = conn.execute(
                """
                SELECT
                    chunks.id AS chunk_id,
                    chunks.text AS chunk_text,
                    documents.id AS document_id,
                    documents.node_id AS node_id,
                    documents.title AS title,
                    documents.path AS path
                FROM chunks
                JOIN documents ON documents.id = chunks.document_id
                WHERE lower(chunks.text) LIKE ?
                   OR lower(documents.title) LIKE ?
                   OR lower(documents.path) LIKE ?
                ORDER BY documents.updated_at DESC, chunks.ordinal ASC
                LIMIT ?
                """,
                (like, like, like, limit),
            ).fetchall()
        results = [_node_result(row) for row in node_rows]
        results.extend(_chunk_result(row, needle) for row in chunk_rows)
        return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]

    def show(self, identifier: str) -> dict[str, Any]:
        self.init()
        target = identifier.strip()
        if not target:
            raise ValueError("graph show requires a node id or label")
        with self._connect() as conn:
            node = conn.execute(
                """
                SELECT id, kind, label, properties_json, created_at, updated_at
                FROM kg_nodes
                WHERE id = ?
                """,
                (target,),
            ).fetchone()
            if node is None:
                like = f"%{target.lower()}%"
                node = conn.execute(
                    """
                    SELECT id, kind, label, properties_json, created_at, updated_at
                    FROM kg_nodes
                    WHERE lower(label) LIKE ? OR lower(id) LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (like, like),
                ).fetchone()
            if node is None:
                raise KeyError(f"graph node not found: {identifier}")
            node_id = str(node["id"])
            outgoing = conn.execute(
                """
                SELECT id, source_id, target_id, kind, properties_json
                FROM kg_edges
                WHERE source_id = ?
                ORDER BY kind, target_id
                """,
                (node_id,),
            ).fetchall()
            incoming = conn.execute(
                """
                SELECT id, source_id, target_id, kind, properties_json
                FROM kg_edges
                WHERE target_id = ?
                ORDER BY kind, source_id
                """,
                (node_id,),
            ).fetchall()
            documents = conn.execute(
                """
                SELECT id, title, path, metadata_json, updated_at
                FROM documents
                WHERE node_id = ?
                ORDER BY updated_at DESC
                """,
                (node_id,),
            ).fetchall()
        return {
            "node": _row_to_dict(node),
            "outgoing": [_row_to_dict(row) for row in outgoing],
            "incoming": [_row_to_dict(row) for row in incoming],
            "documents": [_row_to_dict(row) for row in documents],
        }

    def network(self, text: str = "", limit: int = 48, edge_limit: int = 140) -> dict[str, Any]:
        self.init()
        needle = text.strip().lower()
        limit = max(1, int(limit))
        edge_limit = max(1, int(edge_limit))
        with self._connect() as conn:
            if needle:
                like = f"%{needle}%"
                seed_rows = conn.execute(
                    """
                    SELECT id
                    FROM kg_nodes
                    WHERE lower(id) LIKE ?
                       OR lower(kind) LIKE ?
                       OR lower(label) LIKE ?
                       OR lower(properties_json) LIKE ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (like, like, like, like, limit),
                ).fetchall()
                doc_rows = conn.execute(
                    """
                    SELECT DISTINCT documents.node_id AS id
                    FROM chunks
                    JOIN documents ON documents.id = chunks.document_id
                    WHERE documents.node_id IS NOT NULL
                      AND documents.node_id != ''
                      AND (
                        lower(chunks.text) LIKE ?
                        OR lower(documents.title) LIKE ?
                        OR lower(documents.path) LIKE ?
                      )
                    ORDER BY documents.updated_at DESC
                    LIMIT ?
                    """,
                    (like, like, like, limit),
                ).fetchall()
                seed_ids = _unique([str(row["id"]) for row in seed_rows + doc_rows])[:limit]
            else:
                rows = conn.execute(
                    """
                    SELECT id
                    FROM kg_nodes
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                seed_ids = [str(row["id"]) for row in rows]

            if not seed_ids:
                return {"query": text, "seed_ids": [], "nodes": [], "edges": []}

            seed_clause = _in_clause(seed_ids)
            edge_rows = conn.execute(
                f"""
                SELECT id, source_id, target_id, kind, properties_json
                FROM kg_edges
                WHERE source_id IN ({seed_clause})
                   OR target_id IN ({seed_clause})
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (*seed_ids, *seed_ids, edge_limit),
            ).fetchall()
            related_ids = _unique(
                seed_ids
                + [str(row["source_id"]) for row in edge_rows]
                + [str(row["target_id"]) for row in edge_rows]
            )
            related_clause = _in_clause(related_ids)
            node_rows = conn.execute(
                f"""
                SELECT id, kind, label, properties_json
                FROM kg_nodes
                WHERE id IN ({related_clause})
                """,
                related_ids,
            ).fetchall()
        seed_set = set(seed_ids)
        nodes = [_network_node(row, seed=bool(str(row["id"]) in seed_set)) for row in node_rows]
        edges = [_network_edge(row) for row in edge_rows]
        return {"query": text, "seed_ids": seed_ids, "nodes": nodes, "edges": edges}

    def export_jsonl(self, path: str | Path | None = None) -> Path:
        self.init()
        output = Path(path).resolve() if path else project_state_file(self.root, "graph_export.jsonl")
        output.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn, output.open("w", encoding="utf-8") as handle:
            for table in ("kg_nodes", "kg_edges", "documents", "chunks"):
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                for row in rows:
                    handle.write(json.dumps({"table": table, "record": _row_to_dict(row)}, ensure_ascii=False) + "\n")
        return output

    def ingest_project(self, loader: ManifestLoader | None = None) -> dict[str, int]:
        loader = loader or ManifestLoader(self.root)
        self.init()
        counts = {"nodes": 0, "edges": 0, "documents": 0}

        for slug, mcp in loader.mcp_servers().items():
            self.upsert_node(
                "mcp",
                mcp.name,
                {
                    "slug": slug,
                    "transport": mcp.transport,
                    "enabled": mcp.enabled,
                    "approval": mcp.approval,
                    "notes": mcp.notes,
                },
                node_id=f"mcp:{slug}",
            )
            counts["nodes"] += 1

        for slug, skill_path in loader.skills().items():
            node_id = self.upsert_node(
                "skill",
                slug.replace("_", " ").title(),
                {"slug": slug, "path": _relative_path(self.root, skill_path)},
                node_id=f"skill:{slug}",
            )
            counts["nodes"] += 1
            counts["documents"] += self._add_skill_documents(skill_path, node_id=node_id, slug=slug)

        for slug, agent in loader.agents().items():
            node_id = self.upsert_node(
                "agent",
                agent.name,
                {
                    "slug": slug,
                    "role": agent.role,
                    "description": agent.description,
                    "model": agent.model,
                    "reasoning": agent.reasoning,
                    "tools": agent.tools,
                    "mcp_servers": agent.mcp_servers,
                    "skills": agent.skills,
                    "inputs": agent.inputs,
                    "outputs": agent.outputs,
                    "metadata": agent.metadata,
                },
                node_id=f"agent:{slug}",
            )
            counts["nodes"] += 1
            counts["documents"] += self._add_path_document(agent.prompt_path, node_id=node_id, title=f"Agent prompt: {agent.name}")
            for mcp_slug in agent.mcp_servers:
                self.add_edge(node_id, f"mcp:{normalize_slug(mcp_slug)}", "agent_uses_mcp")
                counts["edges"] += 1
            for skill_slug in agent.skills:
                self.add_edge(node_id, f"skill:{normalize_slug(skill_slug)}", "agent_has_skill")
                counts["edges"] += 1

        for slug, workflow in loader.workflows().items():
            workflow_id = self.upsert_node(
                "workflow",
                workflow.name,
                {
                    "slug": slug,
                    "description": workflow.description,
                    "inputs": workflow.inputs,
                    "outputs": workflow.outputs,
                    "metadata": workflow.metadata,
                },
                node_id=f"workflow:{slug}",
            )
            counts["nodes"] += 1
            for index, step in enumerate(workflow.steps, start=1):
                step_id = str(step.get("id") or f"step_{index}")
                step_node_id = self.upsert_node(
                    "workflow_step",
                    f"{workflow.name}: {step_id}",
                    {"workflow": slug, "step": step},
                    node_id=f"workflow:{slug}:step:{normalize_slug(step_id)}",
                )
                counts["nodes"] += 1
                self.add_edge(workflow_id, step_node_id, "workflow_has_step", {"order": index})
                counts["edges"] += 1
                for agent_slug in _agents_for_step(step):
                    self.add_edge(step_node_id, f"agent:{agent_slug}", "step_uses_agent")
                    counts["edges"] += 1
                for dependency in step.get("depends_on") or []:
                    self.add_edge(
                        step_node_id,
                        f"workflow:{slug}:step:{normalize_slug(str(dependency))}",
                        "step_depends_on",
                    )
                    counts["edges"] += 1

        counts["nodes"] += self._ingest_data_catalog()
        recipe_counts = self._ingest_acquisition_recipes()
        counts["nodes"] += recipe_counts["nodes"]
        counts["edges"] += recipe_counts["edges"]
        trigger_counts = self._ingest_triggers()
        counts["nodes"] += trigger_counts["nodes"]
        counts["edges"] += trigger_counts["edges"]
        evidence_counts = self._ingest_evidence_and_claims()
        counts["nodes"] += evidence_counts["nodes"]
        counts["edges"] += evidence_counts["edges"]
        loop_counts = self._ingest_review_and_field()
        counts["nodes"] += loop_counts["nodes"]
        counts["edges"] += loop_counts["edges"]
        learning_counts = self._ingest_learning_memory()
        counts["nodes"] += learning_counts["nodes"]
        counts["edges"] += learning_counts["edges"]
        return counts

    def ingest_artifacts(self, run_id: str | None = None) -> dict[str, int]:
        self.init()
        artifact_root = self.root / "artifacts"
        counts = {"nodes": 0, "edges": 0, "documents": 0}
        if not artifact_root.exists():
            return counts
        run_dirs = [artifact_root / run_id] if run_id else sorted(path for path in artifact_root.iterdir() if path.is_dir())
        for run_dir in run_dirs:
            if not run_dir.exists():
                continue
            summary = _read_json(run_dir / "run_summary.json")
            workflow = str(summary.get("workflow") or _workflow_from_run_id(run_dir.name))
            run_node_id = self.upsert_node(
                "run",
                run_dir.name,
                {
                    "run_id": run_dir.name,
                    "workflow": workflow,
                    "artifact_dir": _relative_path(self.root, run_dir),
                    "elapsed_ms": summary.get("elapsed_ms"),
                    "steps": summary.get("steps") or [],
                },
                node_id=f"run:{run_dir.name}",
            )
            counts["nodes"] += 1
            if workflow:
                workflow_id = f"workflow:{normalize_slug(workflow)}"
                self.upsert_node("workflow", workflow.replace("_", " ").title(), {"slug": workflow}, node_id=workflow_id)
                self.add_edge(run_node_id, workflow_id, "run_of_workflow")
                counts["edges"] += 1
            for file_path in _artifact_files(run_dir):
                rel = _relative_path(self.root, file_path)
                artifact_node_id = self.upsert_node(
                    "artifact",
                    rel,
                    {"path": rel, "run_id": run_dir.name, "suffix": file_path.suffix.lower()},
                    node_id="artifact:" + _hash(rel),
                )
                counts["nodes"] += 1
                self.add_edge(run_node_id, artifact_node_id, "run_produced_artifact")
                counts["edges"] += 1
                counts["documents"] += self._add_path_document(file_path, node_id=artifact_node_id, title=f"Artifact: {rel}")
        return counts

    def _add_path_document(self, path: Path, *, node_id: str, title: str) -> int:
        if not path.exists() or path.suffix.lower() not in TEXT_SUFFIXES:
            return 0
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return 0
        self.add_document(
            title=title,
            content=content,
            path=path,
            node_id=node_id,
            metadata={"source": "file", "path": _relative_path(self.root, path)},
        )
        return 1

    def _add_skill_documents(self, skill_path: Path, *, node_id: str, slug: str) -> int:
        count = self._add_path_document(skill_path, node_id=node_id, title=f"Skill: {slug}")
        skill_dir = skill_path.parent
        for path in sorted(skill_dir.rglob("*")):
            if path == skill_path or not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            title = f"Skill file: {slug}/{path.relative_to(skill_dir)}"
            count += self._add_path_document(path, node_id=node_id, title=title)
        return count

    def _ingest_acquisition_recipes(self) -> dict[str, int]:
        counts = {"nodes": 0, "edges": 0}
        try:
            from .data_acquisition import DataAcquisitionCatalog

            recipes = DataAcquisitionCatalog(self.root).recipes()
        except Exception:
            return counts
        for recipe in recipes.values():
            node_id = self.upsert_node(
                "data_recipe",
                recipe.name,
                {
                    "id": recipe.id,
                    "theme": recipe.theme,
                    "description": recipe.description,
                    "source_id": recipe.source_id,
                    "authority": recipe.authority,
                    "access_url": recipe.access_url,
                    "access_method": recipe.access_method,
                    "auth": recipe.auth,
                    "license": recipe.license,
                    "resolution": recipe.resolution,
                    "crs": recipe.crs,
                    "time_period": recipe.time_period,
                    "can_download": recipe.can_download,
                    "blockers": recipe.blockers,
                    "provenance_required": recipe.provenance_required,
                },
                node_id=f"data_recipe:{recipe.id}",
            )
            counts["nodes"] += 1
            if recipe.source_id:
                self.add_edge(node_id, f"data_source:{recipe.source_id}", "recipe_uses_source")
                counts["edges"] += 1
        return counts

    def _ingest_data_catalog(self) -> int:
        catalog_path = self.root / "data_sources" / "environmental_data_catalog.yaml"
        if not catalog_path.exists():
            return 0
        try:
            data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return 0
        catalog_id = self.upsert_node(
            "data_catalog",
            "Environmental Data Catalog",
            {
                "path": _relative_path(self.root, catalog_path),
                "purpose": data.get("purpose"),
                "general_rules": data.get("general_rules") or [],
            },
            node_id="data_catalog:environmental",
        )
        self.add_document(
            title="Environmental Data Catalog",
            content=catalog_path.read_text(encoding="utf-8", errors="replace"),
            path=catalog_path,
            node_id=catalog_id,
            metadata={"source": "data_catalog"},
        )
        count = 1
        for source in data.get("sources") or []:
            if not isinstance(source, dict):
                continue
            source_slug = normalize_slug(str(source.get("id") or source.get("name") or "source"))
            source_id = self.upsert_node(
                "data_source",
                str(source.get("name") or source_slug),
                source,
                node_id=f"data_source:{source_slug}",
            )
            self.add_edge(catalog_id, source_id, "catalog_lists_source")
            count += 1
        return count

    def _ingest_triggers(self) -> dict[str, int]:
        counts = {"nodes": 0, "edges": 0}
        triggers_dir = self.root / "triggers"
        if not triggers_dir.exists():
            return counts
        for path in sorted(triggers_dir.glob("*.trigger.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(data, dict):
                continue
            for rule in data.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                slug = normalize_slug(str(rule.get("id") or rule.get("name") or "trigger"))
                node_id = self.upsert_node(
                    "trigger_rule",
                    str(rule.get("name") or slug.replace("_", " ").title()),
                    {
                        "slug": slug,
                        "path": _relative_path(self.root, path),
                        "source": rule.get("source"),
                        "metric": rule.get("metric"),
                        "severity": rule.get("severity"),
                        "condition": rule.get("condition") or rule.get("conditions"),
                        "review": rule.get("review") or {},
                    },
                    node_id=f"trigger_rule:{slug}",
                )
                counts["nodes"] += 1
                review = rule.get("review") if isinstance(rule.get("review"), dict) else {}
                workflow = review.get("workflow")
                if workflow:
                    self.add_edge(node_id, f"workflow:{normalize_slug(str(workflow))}", "trigger_suggests_workflow")
                    counts["edges"] += 1
                for agent_slug in review.get("agents") or []:
                    self.add_edge(node_id, f"agent:{normalize_slug(str(agent_slug))}", "trigger_routes_to_agent")
                    counts["edges"] += 1
        return counts

    def _ingest_evidence_and_claims(self) -> dict[str, int]:
        counts = {"nodes": 0, "edges": 0}
        try:
            from .evidence import EvidenceStore

            store = EvidenceStore(self.root)
            evidence_records = store.list_evidence(evidence_type="all", reviewer_status="all", limit=1000)
            claim_records = store.list_claims(claim_type="all", reviewer_status="all", limit=1000)
        except Exception:
            return counts

        for evidence in evidence_records:
            node_id = self.upsert_node(
                "evidence",
                evidence.title,
                {
                    "id": evidence.id,
                    "evidence_type": evidence.evidence_type,
                    "source": evidence.source,
                    "source_url": evidence.source_url,
                    "access_date": evidence.access_date,
                    "license": evidence.license,
                    "crs": evidence.crs,
                    "spatial_extent": evidence.spatial_extent,
                    "resolution": evidence.resolution,
                    "time_period": evidence.time_period,
                    "local_path": evidence.local_path,
                    "uncertainty": evidence.uncertainty,
                    "reviewer_status": evidence.reviewer_status,
                    "created_by": evidence.created_by,
                    "run_id": evidence.run_id,
                    "metadata": evidence.metadata,
                },
                node_id=f"evidence:{evidence.id}",
            )
            counts["nodes"] += 1
            if evidence.run_id:
                self.add_edge(node_id, f"run:{evidence.run_id}", "evidence_from_run")
                counts["edges"] += 1
            if evidence.created_by.startswith("agent:"):
                self.add_edge(node_id, evidence.created_by, "evidence_created_by_agent")
                counts["edges"] += 1
            if evidence.local_path:
                artifact_id = "artifact:" + _hash(evidence.local_path)
                self.add_edge(node_id, artifact_id, "evidence_has_artifact")
                counts["edges"] += 1

        for claim in claim_records:
            node_id = self.upsert_node(
                "claim",
                claim.statement[:96],
                {
                    "id": claim.id,
                    "claim_type": claim.claim_type,
                    "statement": claim.statement,
                    "status": claim.status,
                    "confidence": claim.confidence,
                    "evidence_ids": claim.evidence_ids,
                    "agent": claim.agent,
                    "workflow": claim.workflow,
                    "run_id": claim.run_id,
                    "uncertainty": claim.uncertainty,
                    "reviewer_status": claim.reviewer_status,
                    "metadata": claim.metadata,
                },
                node_id=f"claim:{claim.id}",
            )
            counts["nodes"] += 1
            for evidence_id in claim.evidence_ids:
                self.add_edge(node_id, f"evidence:{evidence_id}", "claim_supported_by_evidence")
                counts["edges"] += 1
            if claim.agent:
                self.add_edge(node_id, f"agent:{normalize_slug(claim.agent)}", "claim_made_by_agent")
                counts["edges"] += 1
            if claim.workflow:
                self.add_edge(node_id, f"workflow:{normalize_slug(claim.workflow)}", "claim_from_workflow")
                counts["edges"] += 1
            if claim.run_id:
                self.add_edge(node_id, f"run:{claim.run_id}", "claim_from_run")
                counts["edges"] += 1
        return counts

    def _ingest_review_and_field(self) -> dict[str, int]:
        counts = {"nodes": 0, "edges": 0}
        try:
            from .field_validation import FieldValidationStore
            from .review_queue import ReviewQueue

            review_items = ReviewQueue(self.root).list_items(status="all", limit=500)
            field_tasks = FieldValidationStore(self.root).list_tasks(status="all", limit=500)
        except Exception:
            return counts

        for item in review_items:
            node_id = self.upsert_node(
                "review_item",
                item.title,
                {
                    "id": item.id,
                    "status": item.status,
                    "severity": item.severity,
                    "source": item.source,
                    "recommendation": item.recommendation,
                    "payload": item.payload,
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                },
                node_id=f"review_item:{item.id}",
            )
            counts["nodes"] += 1
            workflow = item.recommendation.get("workflow") if isinstance(item.recommendation, dict) else ""
            if workflow:
                self.add_edge(node_id, f"workflow:{normalize_slug(str(workflow))}", "review_routes_to_workflow")
                counts["edges"] += 1
            agents = item.recommendation.get("agents") if isinstance(item.recommendation, dict) else []
            for agent_slug in agents or []:
                self.add_edge(node_id, f"agent:{normalize_slug(str(agent_slug))}", "review_requests_agent")
                counts["edges"] += 1

        for task in field_tasks:
            task_id = self.upsert_node(
                "field_task",
                task.title,
                {
                    "id": task.id,
                    "status": task.status,
                    "review_item_id": task.review_item_id,
                    "method": task.method,
                    "question": task.question,
                    "location": task.location,
                    "priority": task.priority,
                    "assigned_to": task.assigned_to,
                    "due_date": task.due_date,
                    "expected_evidence": task.expected_evidence,
                    "observations": task.observations,
                    "confidence_update": task.confidence_update,
                    "created_at": task.created_at,
                    "updated_at": task.updated_at,
                    "completed_at": task.completed_at,
                },
                node_id=f"field_task:{task.id}",
            )
            counts["nodes"] += 1
            if task.review_item_id:
                self.add_edge(task_id, f"review_item:{task.review_item_id}", "field_task_validates_review")
                counts["edges"] += 1
        return counts

    def _ingest_learning_memory(self) -> dict[str, int]:
        counts = {"nodes": 0, "edges": 0}
        try:
            from .learning_memory import LearningMemoryStore

            records = LearningMemoryStore(self.root).list_records(kind="all", status="all", limit=1000)
        except Exception:
            return counts

        for record in records:
            node_id = self.upsert_node(
                "learning_record",
                record.title,
                {
                    "id": record.id,
                    "kind": record.kind,
                    "status": record.status,
                    "body": record.body,
                    "source": record.source,
                    "created_by": record.created_by,
                    "confidence": record.confidence,
                    "applies_to": record.applies_to,
                    "tags": record.tags,
                    "evidence_refs": record.evidence_refs,
                    "payload": record.payload,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                },
                node_id=f"learning:{record.id}",
            )
            counts["nodes"] += 1
            for target_id in record.applies_to:
                self.add_edge(node_id, target_id, _learning_edge_kind(record.kind))
                counts["edges"] += 1
            for evidence_ref in record.evidence_refs:
                if ":" in evidence_ref:
                    self.add_edge(node_id, evidence_ref, "learning_supported_by")
                    counts["edges"] += 1
            for tag in record.tags:
                tag_slug = normalize_slug(tag)
                tag_id = self.upsert_node("memory_tag", tag, {"tag": tag}, node_id=f"memory_tag:{tag_slug}")
                counts["nodes"] += 1
                self.add_edge(node_id, tag_id, "learning_has_tag")
                counts["edges"] += 1
        return counts

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _count(self, conn: sqlite3.Connection, table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _agents_for_step(step: dict[str, Any]) -> Iterable[str]:
    if "parallel" in step:
        for branch in step.get("parallel") or []:
            agent = normalize_slug(str(branch.get("agent") or ""))
            if agent:
                yield agent
        return
    agent = normalize_slug(str(step.get("agent") or ""))
    if agent:
        yield agent


def _artifact_files(run_dir: Path) -> Iterable[Path]:
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def _chunk_result(row: sqlite3.Row, needle: str) -> dict[str, Any]:
    text = str(row["chunk_text"])
    return {
        "type": "chunk",
        "id": row["chunk_id"],
        "document_id": row["document_id"],
        "node_id": row["node_id"],
        "title": row["title"],
        "path": row["path"],
        "snippet": _snippet(text, needle),
        "score": _score(text, needle),
    }


def _node_result(row: sqlite3.Row) -> dict[str, Any]:
    text = " ".join([str(row["id"]), str(row["kind"]), str(row["label"]), str(row["properties_json"])])
    return {
        "type": "node",
        "id": row["id"],
        "kind": row["kind"],
        "label": row["label"],
        "properties": _loads(row["properties_json"]),
        "snippet": row["label"],
        "score": _score(text, row["label"].lower()),
    }


def _network_node(row: sqlite3.Row, *, seed: bool) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "kind": str(row["kind"]),
        "label": str(row["label"]),
        "properties": _loads(row["properties_json"]),
        "seed": seed,
    }


def _network_edge(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "source": str(row["source_id"]),
        "target": str(row["target_id"]),
        "kind": str(row["kind"]),
        "properties": _loads(row["properties_json"]),
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in list(result):
        if key.endswith("_json"):
            result[key.removesuffix("_json")] = _loads(result.pop(key))
    return result


def _chunks(text: str) -> Iterable[str]:
    clean = text.strip()
    if not clean:
        return
    start = 0
    while start < len(clean):
        end = min(len(clean), start + CHUNK_CHARS)
        yield clean[start:end].strip()
        if end >= len(clean):
            break
        start = max(0, end - CHUNK_OVERLAP)


def _snippet(text: str, needle: str, width: int = 280) -> str:
    lower = text.lower()
    index = lower.find(needle) if needle else 0
    if index < 0:
        index = 0
    start = max(0, index - width // 3)
    end = min(len(text), start + width)
    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""
    return prefix + " ".join(text[start:end].split()) + suffix


def _score(text: str, needle: str) -> int:
    if not needle:
        return 1
    return max(1, text.lower().count(needle))


def _workflow_from_run_id(run_id: str) -> str:
    parts = run_id.split("-")
    return parts[0] if parts else run_id


def _learning_edge_kind(kind: str) -> str:
    mapping = {
        "human_feedback": "feedback_about",
        "correction": "correction_for",
        "preference": "preference_for",
        "lesson": "lesson_about",
        "decision": "decision_about",
        "assumption": "assumption_about",
        "field_insight": "field_insight_about",
        "model_note": "model_note_about",
        "workflow_note": "workflow_note_about",
    }
    return mapping.get(kind, "learning_about")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:20]


def _readable_id(kind: str, label: str) -> str:
    slug = normalize_slug(label)[:48].strip("_")
    return f"{kind}:{slug or _hash(label)}"


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _in_clause(values: list[str]) -> str:
    if not values:
        raise ValueError("IN clause needs at least one value")
    return ",".join("?" for _ in values)


def _relative_path(root: Path, path: str | Path) -> str:
    target = Path(path).resolve()
    try:
        return str(target.relative_to(root))
    except ValueError:
        return str(target)
