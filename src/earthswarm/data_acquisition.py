from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .evidence import EvidenceStore
from .models import normalize_slug


@dataclass(frozen=True)
class AcquisitionRecipe:
    id: str
    name: str
    theme: str
    description: str
    source_id: str
    authority: str
    access_url: str
    access_method: str
    auth: str
    license: str
    resolution: str
    crs: str
    time_period: str
    can_download: bool
    download_url: str
    output_hint: str
    blockers: list[str]
    provenance_required: list[str]
    steps: list[str]


@dataclass(frozen=True)
class AcquisitionResult:
    recipe_id: str
    status: str
    stage_dir: Path
    record_path: Path
    evidence_id: str
    local_path: str
    blockers: list[str]
    message: str


class DataAcquisitionCatalog:
    """Deterministic dataset staging recipes for small-model workflows."""

    def __init__(self, root: str | Path, path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.path = Path(path).resolve() if path else self.root / "data_sources" / "acquisition_recipes.yaml"

    def recipes(self) -> dict[str, AcquisitionRecipe]:
        if not self.path.exists():
            return {}
        data = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"{self.path}: expected mapping")
        result: dict[str, AcquisitionRecipe] = {}
        for item in data.get("recipes") or []:
            if not isinstance(item, dict):
                continue
            recipe = _recipe_from_mapping(item)
            result[recipe.id] = recipe
        return result

    def get(self, recipe_id: str) -> AcquisitionRecipe:
        recipes = self.recipes()
        key = normalize_slug(recipe_id)
        if key not in recipes:
            raise KeyError(f"unknown acquisition recipe: {recipe_id}")
        return recipes[key]

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.path.exists():
            return errors
        try:
            recipes = self.recipes()
        except Exception as exc:
            return [f"{self.path}: {exc}"]
        if not recipes:
            errors.append(f"{self.path}: no acquisition recipes")
        for recipe in recipes.values():
            if not recipe.name:
                errors.append(f"data recipe {recipe.id}: missing name")
            if not recipe.source_id:
                errors.append(f"data recipe {recipe.id}: missing source_id")
            if not recipe.access_url:
                errors.append(f"data recipe {recipe.id}: missing access_url")
            if recipe.can_download and not recipe.download_url:
                errors.append(f"data recipe {recipe.id}: can_download requires download_url")
        return errors

    def stage(
        self,
        recipe_id: str,
        *,
        aoi: str = "",
        time_period: str = "",
        output_dir: str | Path | None = None,
        allow_download: bool = False,
        note: str = "",
    ) -> AcquisitionResult:
        recipe = self.get(recipe_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        base_dir = Path(output_dir).resolve() if output_dir else self.root / "data" / "staged"
        stage_dir = base_dir / recipe.id / stamp
        stage_dir.mkdir(parents=True, exist_ok=True)
        downloaded_path = ""
        blockers = list(recipe.blockers)
        status = "blocked"
        message = "Recipe staged with blockers; manual or credentialed download is required."

        if allow_download and recipe.can_download:
            try:
                downloaded_path = str(_download(recipe.download_url, stage_dir))
                blockers = []
                status = "staged"
                message = "Dataset downloaded or copied into the staging folder."
            except Exception as exc:
                blockers = [f"Download failed: {exc}", *blockers]
                status = "blocked"
                message = "Download was attempted but failed; blockers were recorded."
        elif recipe.can_download:
            blockers = ["Pass --allow-download to fetch this public recipe.", *blockers]

        record = {
            "recipe_id": recipe.id,
            "name": recipe.name,
            "theme": recipe.theme,
            "status": status,
            "aoi": aoi,
            "time_period": time_period or recipe.time_period,
            "source_id": recipe.source_id,
            "authority": recipe.authority,
            "access_url": recipe.access_url,
            "access_method": recipe.access_method,
            "auth": recipe.auth,
            "license": recipe.license,
            "resolution": recipe.resolution,
            "crs": recipe.crs,
            "local_path": downloaded_path,
            "stage_dir": str(stage_dir),
            "blockers": blockers,
            "provenance_required": recipe.provenance_required,
            "steps": recipe.steps,
            "note": note,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        record_path = stage_dir / "stage_record.json"
        record_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

        evidence = EvidenceStore(self.root).add_evidence(
            evidence_type="dataset" if status == "staged" else "blocker",
            title=f"{recipe.name} record",
            source=recipe.authority or recipe.source_id,
            source_url=recipe.access_url,
            access_date=stamp[:8],
            license=recipe.license,
            crs=recipe.crs,
            spatial_extent=aoi,
            resolution=recipe.resolution,
            time_period=time_period or recipe.time_period,
            local_path=downloaded_path or record_path,
            uncertainty="; ".join(blockers) if blockers else "",
            reviewer_status="open",
            created_by="data_acquisition_recipe",
            metadata=record,
        )
        return AcquisitionResult(
            recipe_id=recipe.id,
            status=status,
            stage_dir=stage_dir,
            record_path=record_path,
            evidence_id=evidence.id,
            local_path=downloaded_path,
            blockers=blockers,
            message=message,
        )


def _recipe_from_mapping(data: dict[str, Any]) -> AcquisitionRecipe:
    return AcquisitionRecipe(
        id=normalize_slug(str(data.get("id") or data.get("name") or "")),
        name=str(data.get("name") or ""),
        theme=str(data.get("theme") or ""),
        description=str(data.get("description") or ""),
        source_id=normalize_slug(str(data.get("source_id") or "")),
        authority=str(data.get("authority") or ""),
        access_url=str(data.get("access_url") or ""),
        access_method=str(data.get("access_method") or ""),
        auth=str(data.get("auth") or ""),
        license=str(data.get("license") or ""),
        resolution=str(data.get("resolution") or ""),
        crs=str(data.get("crs") or ""),
        time_period=str(data.get("time_period") or ""),
        can_download=bool(data.get("can_download", False)),
        download_url=str(data.get("download_url") or ""),
        output_hint=str(data.get("output_hint") or ""),
        blockers=[str(item) for item in data.get("blockers") or []],
        provenance_required=[str(item) for item in data.get("provenance_required") or []],
        steps=[str(item) for item in data.get("steps") or []],
    )


def _download(url: str, stage_dir: Path) -> Path:
    if url.startswith("file://"):
        source = Path(url.removeprefix("file://")).resolve()
        target = stage_dir / source.name
        shutil.copy2(source, target)
        return target
    filename = Path(urllib.parse.urlparse(url).path).name if "://" in url else Path(url).name
    if not filename:
        filename = "download.bin"
    target = stage_dir / filename
    request = urllib.request.Request(url, headers={"User-Agent": "openearth-data-acquisition"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response, target.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc
    return target
