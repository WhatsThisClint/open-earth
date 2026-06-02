from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import processing  # type: ignore
    from PyQt5.QtGui import QColor  # type: ignore
    from qgis.analysis import QgsRasterCalculator, QgsRasterCalculatorEntry  # type: ignore
    from qgis.core import (  # type: ignore
        QgsColorRampShader,
        QgsCoordinateReferenceSystem,
        QgsGraduatedSymbolRenderer,
        QgsLayoutExporter,
        QgsLayoutItemLabel,
        QgsLayoutItemLegend,
        QgsLayoutItemMap,
        QgsLayoutItemScaleBar,
        QgsLayoutPoint,
        QgsLayoutSize,
        QgsPrintLayout,
        QgsProject,
        QgsRasterLayer,
        QgsRasterShader,
        QgsRendererRange,
        QgsSingleBandPseudoColorRenderer,
        QgsSymbol,
        QgsUnitTypes,
        QgsVectorLayer,
    )
except Exception:  # pragma: no cover - this file is meant to run inside QGIS.
    processing = None
    QColor = None
    QgsRasterCalculator = None
    QgsRasterCalculatorEntry = None
    QgsColorRampShader = None
    QgsCoordinateReferenceSystem = None
    QgsGraduatedSymbolRenderer = None
    QgsLayoutExporter = None
    QgsLayoutItemLabel = None
    QgsLayoutItemLegend = None
    QgsLayoutItemMap = None
    QgsLayoutItemScaleBar = None
    QgsLayoutPoint = None
    QgsLayoutSize = None
    QgsPrintLayout = None
    QgsProject = None
    QgsRasterLayer = None
    QgsRasterShader = None
    QgsRendererRange = None
    QgsSingleBandPseudoColorRenderer = None
    QgsSymbol = None
    QgsUnitTypes = None
    QgsVectorLayer = None


@dataclass
class MapJob:
    recipe: str
    title: str
    inputs: dict[str, str]
    outputs_dir: str
    aoi_name: str = ""
    crs: str = "EPSG:4326"
    parameters: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    project_root: str = "."

    @classmethod
    def from_any(cls, value: "MapJob | dict[str, Any]") -> "MapJob":
        if isinstance(value, cls):
            return value
        return cls(
            recipe=str(value.get("recipe") or ""),
            title=str(value.get("title") or value.get("recipe") or "Open Earth Map"),
            inputs={str(k): str(v) for k, v in dict(value.get("inputs") or {}).items() if str(v).strip()},
            outputs_dir=str(value.get("outputs_dir") or "artifacts/maps"),
            aoi_name=str(value.get("aoi_name") or ""),
            crs=str(value.get("crs") or "EPSG:4326"),
            parameters=dict(value.get("parameters") or {}),
            notes=str(value.get("notes") or ""),
            project_root=str(value.get("project_root") or "."),
        )


RECIPES = {
    "aoi_locator_map": {
        "required": ["aoi"],
        "function": "build_aoi_locator_map",
    },
    "dem_clip": {
        "required": ["aoi", "dem"],
        "function": "build_dem_clip",
    },
    "terrain_slope_map": {
        "required": ["aoi", "dem"],
        "function": "build_terrain_slope_map",
    },
    "watershed_delineation_map": {
        "required": ["aoi"],
        "function": "build_watershed_delineation_map",
    },
    "drainage_watershed_map": {
        "required": ["aoi", "drainage"],
        "function": "build_drainage_watershed_map",
    },
    "groundwater_potential_map": {
        "required": ["aoi"],
        "function": "build_groundwater_potential_map",
    },
    "erosion_risk_screening_map": {
        "required": ["aoi"],
        "function": "build_erosion_risk_screening_map",
    },
    "lulc_vegetation_water_map": {
        "required": ["aoi"],
        "function": "build_lulc_vegetation_water_map",
    },
    "ndvi_vegetation_map": {
        "required": ["aoi", "ndvi"],
        "function": "build_ndvi_vegetation_map",
    },
    "settlement_exposure_map": {
        "required": ["aoi"],
        "function": "build_settlement_exposure_map",
    },
    "field_validation_map": {
        "required": ["aoi"],
        "function": "build_field_validation_map",
    },
    "intervention_opportunity_map": {
        "required": ["aoi"],
        "function": "build_intervention_opportunity_map",
    },
}


def run_recipe(job_like: MapJob | dict[str, Any]) -> dict[str, Any]:
    """Run a deterministic Open Earth QGIS map recipe.

    This is the function small models should call. It returns a serializable
    artifact manifest or a blocker payload; it does not silently invent inputs.
    """

    job = MapJob.from_any(job_like)
    if job.recipe not in RECIPES:
        return _block(job, [], f"Unknown map recipe: {job.recipe}", "Choose one recipe listed in qgis_map_recipes/SKILL.md.")

    root = Path(job.project_root).resolve()
    out_dir = _resolve(root, job.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    missing = _missing_required_inputs(job, root, RECIPES[job.recipe]["required"])
    if missing:
        return _block(
            job,
            missing,
            f"Missing required input(s): {', '.join(missing)}.",
            "Ask Data Steward to stage the missing AOI-clipped dataset, then rerun this recipe.",
        )

    _require_qgis()
    _set_project_crs(job.crs)
    function_name = RECIPES[job.recipe]["function"]
    artifact = globals()[function_name](job, root, out_dir)
    _write_artifact(artifact, out_dir)
    return artifact


def build_aoi_locator_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    style_vector_simple(aoi, "#005f73", width=1.1)
    layers = [_layer_record(aoi, "aoi")]
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {
        "admin_context": "#7a7a7a",
        "settlements": "#343a40",
        "roads": "#f77f00",
        "drainage": "#1769aa",
        "surface_water": "#168aad",
    }))
    exports = export_layout(job, out_dir, aoi, "aoi_locator")
    provenance = ["AOI locator map generated to verify study-area boundary and local context before analysis."]
    limitations = ["Boundary must be verified against an official/user-approved AOI before final reporting."]
    return _artifact(job, out_dir, layers, exports, provenance, limitations)


def build_dem_clip(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    dem = load_raster(root, job.inputs["dem"], "DEM raw")
    clipped_dem = clip_raster_by_mask(dem, aoi, out_dir / "dem_clipped.tif", "DEM clipped")
    style_raster_pseudocolor(clipped_dem, [0, 100, 250, 500, 800, 1200], ["#edf8fb", "#b2e2e2", "#66c2a4", "#2ca25f", "#006d2c", "#00441b"])
    layers = [_layer_record(aoi, "aoi"), _layer_record(clipped_dem, "derived_raster")]
    exports = export_layout(job, out_dir, aoi, "dem_clip")
    provenance = ["DEM clipped to AOI. Use terrain_slope_map for slope, hillshade, and contour derivatives."]
    return _artifact(job, out_dir, layers, exports, provenance, [])


def build_terrain_slope_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    dem = load_raster(root, job.inputs["dem"], "DEM raw")

    clipped_dem = clip_raster_by_mask(dem, aoi, out_dir / "dem_clipped.tif", "DEM clipped")
    slope = make_slope(clipped_dem, out_dir / "slope_degrees.tif", "Slope degrees")
    hillshade = make_hillshade(clipped_dem, out_dir / "hillshade.tif", "Hillshade")
    style_raster_pseudocolor(slope, [0, 3, 8, 15, 30, 45], ["#f7fcf0", "#ccebc5", "#7bccc4", "#2b8cbe", "#88419d", "#4d004b"])
    style_raster_gray(hillshade, opacity=0.45)

    layers = [
        _layer_record(aoi, "aoi"),
        _layer_record(clipped_dem, "derived_raster"),
        _layer_record(slope, "derived_raster"),
        _layer_record(hillshade, "derived_raster"),
    ]
    interval = int(job.parameters.get("contour_interval") or 0)
    if interval > 0:
        contour = make_contours(clipped_dem, out_dir / "contours.gpkg", interval, "Contours")
        style_vector_simple(contour, "#6f6f6f", width=0.25)
        layers.append(_layer_record(contour, "derived_vector"))

    exports = export_layout(job, out_dir, aoi, "terrain_slope")
    return _artifact(job, out_dir, layers, exports, ["DEM clipped to AOI; slope and hillshade derived in QGIS/GDAL."], [])


def build_drainage_watershed_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    drainage = load_vector(root, job.inputs["drainage"], "Drainage raw")
    clipped_drainage = clip_vector(drainage, aoi, out_dir / "drainage_clipped.gpkg", "Drainage")
    style_vector_simple(clipped_drainage, "#1769aa", width=0.7)

    layers = [_layer_record(aoi, "aoi"), _layer_record(clipped_drainage, "processed_vector")]
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {"watersheds": "#6a994e", "surface_water": "#1f9bcf"}))
    if "dem" in job.inputs:
        dem = load_raster(root, job.inputs["dem"], "DEM raw")
        hillshade = make_hillshade(clip_raster_by_mask(dem, aoi, out_dir / "dem_clipped.tif", "DEM clipped"), out_dir / "hillshade.tif", "Hillshade")
        style_raster_gray(hillshade, opacity=0.35)
        layers.append(_layer_record(hillshade, "derived_raster"))

    exports = export_layout(job, out_dir, aoi, "drainage_watershed")
    return _artifact(job, out_dir, layers, exports, ["Drainage and optional watershed/waterbody layers clipped to AOI."], [])


def build_watershed_delineation_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    layers = [_layer_record(aoi, "aoi")]
    limitations: list[str] = []
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {
        "watersheds": "#6a994e",
        "subwatersheds": "#90be6d",
        "drainage": "#1769aa",
        "surface_water": "#168aad",
        "pour_points": "#d00000",
    }))
    if "dem" in job.inputs:
        dem = load_raster(root, job.inputs["dem"], "DEM raw")
        clipped_dem = clip_raster_by_mask(dem, aoi, out_dir / "dem_clipped.tif", "DEM clipped")
        hillshade = make_hillshade(clipped_dem, out_dir / "hillshade.tif", "Hillshade")
        style_raster_gray(hillshade, opacity=0.35)
        layers.append(_layer_record(hillshade, "derived_raster"))
    else:
        limitations.append("DEM was not supplied, so this recipe cannot compute new flow direction or accumulation rasters.")
    if "watersheds" not in job.inputs and "subwatersheds" not in job.inputs:
        limitations.append("No watershed polygon layer supplied. Treat this as a watershed context map, not a new delineation.")
    exports = export_layout(job, out_dir, aoi, "watershed_delineation")
    provenance = ["Watershed context assembled from staged watershed/drainage evidence and optional DEM hillshade."]
    return _artifact(job, out_dir, layers, exports, provenance, limitations)


def build_groundwater_potential_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    layers = [_layer_record(aoi, "aoi")]
    limitations: list[str] = []

    score_keys = [
        "slope_score_raster",
        "geology_score_raster",
        "lineament_score_raster",
        "drainage_density_score_raster",
        "soil_score_raster",
        "landcover_score_raster",
    ]
    score_layers = _load_existing_rasters(job, root, score_keys)
    weights = dict(job.parameters.get("weights") or {})
    if len(score_layers) >= 2:
        potential = weighted_overlay(score_layers, weights, out_dir / "groundwater_potential_score.tif", "Groundwater potential score")
        style_raster_pseudocolor(potential, [1, 2, 3, 4, 5], ["#d7191c", "#fdae61", "#ffffbf", "#abdda4", "#2b83ba"])
        layers.append(_layer_record(potential, "derived_raster"))
    else:
        limitations.append("Weighted groundwater score was not computed because fewer than two score rasters were supplied.")

    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {
        "geology": "#a77945",
        "lineaments": "#7b2cbf",
        "drainage": "#1769aa",
        "wells": "#0b7285",
        "recharge_structures": "#2b9348",
    }))
    layers.extend(_clip_optional_rasters(job, root, out_dir, aoi, {"slope_raster": "Slope evidence"}))

    exports = export_layout(job, out_dir, aoi, "groundwater_potential")
    provenance = ["Groundwater map is a screening evidence stack unless calibrated with aquifer, well-yield, and water-level data."]
    return _artifact(job, out_dir, layers, exports, provenance, limitations)


def build_erosion_risk_screening_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    layers = [_layer_record(aoi, "aoi")]
    limitations: list[str] = []
    score_layers = _load_existing_rasters(
        job,
        root,
        ["slope_score_raster", "rainfall_score_raster", "soil_erodibility_score_raster", "landcover_risk_score_raster"],
    )
    weights = dict(job.parameters.get("weights") or {})
    if len(score_layers) >= 2:
        risk = weighted_overlay(score_layers, weights, out_dir / "erosion_risk_score.tif", "Erosion risk score")
        style_raster_pseudocolor(risk, [1, 2, 3, 4, 5], ["#1a9850", "#91cf60", "#ffffbf", "#fc8d59", "#d73027"])
        layers.append(_layer_record(risk, "derived_raster"))
    else:
        limitations.append("Erosion score was not computed because fewer than two score rasters were supplied.")

    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {"drainage": "#1769aa", "erosion_points": "#d73027", "villages": "#2f3e46"}))
    exports = export_layout(job, out_dir, aoi, "erosion_risk")
    provenance = ["Screening map only. Do not label as RUSLE unless all RUSLE factors are computed and documented."]
    return _artifact(job, out_dir, layers, exports, provenance, limitations)


def build_lulc_vegetation_water_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    layers = [_layer_record(aoi, "aoi")]
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {"lulc": "#90be6d", "surface_water": "#168aad", "forest": "#2d6a4f"}))
    raster_layers = _clip_optional_rasters(job, root, out_dir, aoi, {"ndvi": "NDVI", "ndwi": "NDWI", "lulc_raster": "LULC raster"})
    for layer in raster_layers:
        layers.append(layer)
    exports = export_layout(job, out_dir, aoi, "lulc_vegetation_water")
    provenance = ["Record imagery date, season, cloud mask, and classification source before using this map for claims."]
    return _artifact(job, out_dir, layers, exports, provenance, [])


def build_ndvi_vegetation_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    ndvi = load_raster(root, job.inputs["ndvi"], "NDVI raw")
    clipped_ndvi = clip_raster_by_mask(ndvi, aoi, out_dir / "ndvi_clipped.tif", "NDVI clipped")
    style_raster_pseudocolor(clipped_ndvi, [-0.2, 0, 0.2, 0.4, 0.6, 0.8], ["#6c757d", "#f1faee", "#d9ed92", "#76c893", "#2d6a4f", "#1b4332"])
    layers = [_layer_record(aoi, "aoi"), _layer_record(clipped_ndvi, "derived_raster")]
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {"forest": "#2d6a4f", "surface_water": "#168aad", "lulc": "#90be6d"}))
    exports = export_layout(job, out_dir, aoi, "ndvi_vegetation")
    provenance = ["NDVI clipped to AOI. Record imagery date, sensor, cloud mask, and season before interpretation."]
    return _artifact(job, out_dir, layers, exports, provenance, [])


def build_settlement_exposure_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    layers = [_layer_record(aoi, "aoi")]
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {
        "settlements": "#343a40",
        "roads": "#f77f00",
        "population": "#6c757d",
        "facilities": "#7209b7",
        "risk_zone": "#d00000",
        "drainage": "#1769aa",
    }))
    layers.extend(_clip_optional_rasters(job, root, out_dir, aoi, {"slope_raster": "Slope exposure"}))
    if len(layers) <= 1:
        return _block(
            job,
            ["settlements_or_roads_or_population_or_facilities"],
            "Settlement exposure needs at least one people or infrastructure layer.",
            "Stage settlements, roads, population, or facilities before mapping exposure.",
        )
    exports = export_layout(job, out_dir, aoi, "settlement_exposure")
    return _artifact(job, out_dir, layers, exports, ["Exposure layers clipped to AOI and overlaid with available risk context."], [])


def build_field_validation_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    layers = [_layer_record(aoi, "aoi")]
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {
        "field_sites": "#005f73",
        "risk_zone": "#d00000",
        "drainage": "#1769aa",
        "settlements": "#343a40",
        "roads": "#f77f00",
    }))
    if len(layers) <= 1:
        return _block(
            job,
            ["field_sites_or_risk_context"],
            "Field map needs at least one site, risk zone, drainage, settlement, or road layer.",
            "Create or stage a field-sites layer from the review/field task.",
        )
    exports = export_layout(job, out_dir, aoi, "field_validation")
    evidence = job.parameters.get("expected_evidence") or []
    provenance = ["Field validation map generated from review/field-task context.", f"Expected evidence: {evidence}"]
    return _artifact(job, out_dir, layers, exports, provenance, [])


def build_intervention_opportunity_map(job: MapJob, root: Path, out_dir: Path) -> dict[str, Any]:
    aoi = load_vector(root, job.inputs["aoi"], "AOI")
    layers = [_layer_record(aoi, "aoi")]
    layers.extend(_clip_optional_vectors(job, root, out_dir, aoi, {
        "intervention_zones": "#2b9348",
        "erosion_hotspots": "#d00000",
        "recharge_zones": "#168aad",
        "settlements": "#343a40",
        "roads": "#f77f00",
        "drainage": "#1769aa",
        "surface_water": "#0096c7",
        "field_sites": "#7209b7",
    }))
    layers.extend(_clip_optional_rasters(job, root, out_dir, aoi, {
        "groundwater_potential_raster": "Groundwater potential",
        "erosion_risk_raster": "Erosion risk",
        "slope_raster": "Slope",
        "lulc_raster": "LULC",
    }))
    if len(layers) <= 1:
        return _block(
            job,
            ["intervention_evidence_layers"],
            "Intervention opportunity mapping needs staged risk, resource, access, or field-validation evidence.",
            "Run groundwater, erosion, LULC, settlement, and field-validation recipes first.",
        )
    exports = export_layout(job, out_dir, aoi, "intervention_opportunity")
    provenance = ["Intervention opportunities are a synthesis overlay and must be checked by specialists and local stakeholders."]
    limitations = ["Do not treat zones as final designs without field validation, tenure/social checks, and cost feasibility review."]
    return _artifact(job, out_dir, layers, exports, provenance, limitations)


def load_vector(root: Path, path_value: str, name: str):
    path = _resolve(root, path_value)
    layer = QgsVectorLayer(str(path), name, "ogr")
    if not layer.isValid():
        raise RuntimeError(f"Could not load vector layer: {path}")
    QgsProject.instance().addMapLayer(layer)
    return layer


def load_raster(root: Path, path_value: str, name: str):
    path = _resolve(root, path_value)
    layer = QgsRasterLayer(str(path), name)
    if not layer.isValid():
        raise RuntimeError(f"Could not load raster layer: {path}")
    QgsProject.instance().addMapLayer(layer)
    return layer


def clip_vector(vector_layer, aoi_layer, output_path: Path, name: str):
    result = processing.run("native:clip", {"INPUT": vector_layer, "OVERLAY": aoi_layer, "OUTPUT": str(output_path)})
    layer = QgsVectorLayer(str(result["OUTPUT"]), name, "ogr")
    QgsProject.instance().addMapLayer(layer)
    return layer


def clip_raster_by_mask(raster_layer, aoi_layer, output_path: Path, name: str):
    result = processing.run(
        "gdal:cliprasterbymasklayer",
        {
            "INPUT": raster_layer,
            "MASK": aoi_layer,
            "SOURCE_CRS": None,
            "TARGET_CRS": None,
            "NODATA": None,
            "ALPHA_BAND": False,
            "CROP_TO_CUTLINE": True,
            "KEEP_RESOLUTION": True,
            "SET_RESOLUTION": False,
            "OUTPUT": str(output_path),
        },
    )
    layer = QgsRasterLayer(str(result["OUTPUT"]), name)
    QgsProject.instance().addMapLayer(layer)
    return layer


def make_slope(dem_layer, output_path: Path, name: str):
    result = processing.run(
        "gdal:slope",
        {"INPUT": dem_layer, "BAND": 1, "SCALE": 1.0, "AS_PERCENT": False, "COMPUTE_EDGES": True, "OUTPUT": str(output_path)},
    )
    layer = QgsRasterLayer(str(result["OUTPUT"]), name)
    QgsProject.instance().addMapLayer(layer)
    return layer


def make_hillshade(dem_layer, output_path: Path, name: str):
    result = processing.run(
        "gdal:hillshade",
        {"INPUT": dem_layer, "BAND": 1, "Z_FACTOR": 1.0, "AZIMUTH": 315.0, "ALTITUDE": 45.0, "COMPUTE_EDGES": True, "OUTPUT": str(output_path)},
    )
    layer = QgsRasterLayer(str(result["OUTPUT"]), name)
    QgsProject.instance().addMapLayer(layer)
    return layer


def make_contours(dem_layer, output_path: Path, interval: int, name: str):
    result = processing.run(
        "gdal:contour",
        {"INPUT": dem_layer, "BAND": 1, "INTERVAL": interval, "FIELD_NAME": "elev", "CREATE_3D": False, "OUTPUT": str(output_path)},
    )
    layer = QgsVectorLayer(str(result["OUTPUT"]), name, "ogr")
    QgsProject.instance().addMapLayer(layer)
    return layer


def weighted_overlay(score_layers: dict[str, Any], weights: dict[str, float], output_path: Path, name: str):
    entries = []
    expression_parts = []
    default_weight = 1.0 / max(1, len(score_layers))
    reference_layer = next(iter(score_layers.values()))
    for index, (key, layer) in enumerate(score_layers.items()):
        entry = QgsRasterCalculatorEntry()
        entry.ref = f"r{index}@1"
        entry.raster = layer
        entry.bandNumber = 1
        entries.append(entry)
        weight = float(weights.get(key, default_weight))
        expression_parts.append(f"({entry.ref} * {weight})")
    expression = " + ".join(expression_parts)
    calculator = QgsRasterCalculator(
        expression,
        str(output_path),
        "GTiff",
        reference_layer.extent(),
        reference_layer.width(),
        reference_layer.height(),
        entries,
    )
    result = calculator.processCalculation()
    if result != 0:
        raise RuntimeError(f"Raster calculator failed for {name} with code {result}")
    layer = QgsRasterLayer(str(output_path), name)
    QgsProject.instance().addMapLayer(layer)
    return layer


def export_layout(job: MapJob, out_dir: Path, extent_layer, slug_suffix: str) -> dict[str, str]:
    project = QgsProject.instance()
    project_path = out_dir / "project.qgz"
    project.write(str(project_path))

    layout_name = _safe_slug(job.title)[:40] or "open_earth_map"
    manager = project.layoutManager()
    existing = manager.layoutByName(layout_name)
    if existing:
        manager.removeLayout(existing)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName(layout_name)
    manager.addLayout(layout)

    map_item = QgsLayoutItemMap(layout)
    map_item.setExtent(extent_layer.extent())
    layout.addLayoutItem(map_item)
    map_item.attemptMove(QgsLayoutPoint(10, 24, QgsUnitTypes.LayoutMillimeters))
    map_item.attemptResize(QgsLayoutSize(190, 142, QgsUnitTypes.LayoutMillimeters))

    title = QgsLayoutItemLabel(layout)
    title.setText(job.title)
    title.setFontColor(QColor("#18211f"))
    layout.addLayoutItem(title)
    title.attemptMove(QgsLayoutPoint(10, 8, QgsUnitTypes.LayoutMillimeters))
    title.attemptResize(QgsLayoutSize(190, 12, QgsUnitTypes.LayoutMillimeters))

    note = QgsLayoutItemLabel(layout)
    note_text = f"AOI: {job.aoi_name or 'not specified'} | CRS: {job.crs} | Recipe: {job.recipe}"
    if job.notes:
        note_text += f" | Notes: {job.notes}"
    note.setText(note_text[:260])
    layout.addLayoutItem(note)
    note.attemptMove(QgsLayoutPoint(10, 171, QgsUnitTypes.LayoutMillimeters))
    note.attemptResize(QgsLayoutSize(190, 12, QgsUnitTypes.LayoutMillimeters))

    legend = QgsLayoutItemLegend(layout)
    legend.setTitle("Layers")
    legend.setLinkedMap(map_item)
    layout.addLayoutItem(legend)
    legend.attemptMove(QgsLayoutPoint(205, 24, QgsUnitTypes.LayoutMillimeters))
    legend.attemptResize(QgsLayoutSize(75, 90, QgsUnitTypes.LayoutMillimeters))

    scale = QgsLayoutItemScaleBar(layout)
    scale.setStyle("Single Box")
    scale.setLinkedMap(map_item)
    scale.applyDefaultSize()
    layout.addLayoutItem(scale)
    scale.attemptMove(QgsLayoutPoint(10, 158, QgsUnitTypes.LayoutMillimeters))

    exporter = QgsLayoutExporter(layout)
    png_path = out_dir / f"{_safe_slug(job.title)}_{slug_suffix}.png"
    pdf_path = out_dir / f"{_safe_slug(job.title)}_{slug_suffix}.pdf"
    exporter.exportToImage(str(png_path), QgsLayoutExporter.ImageExportSettings())
    exporter.exportToPdf(str(pdf_path), QgsLayoutExporter.PdfExportSettings())
    project.write(str(project_path))
    return {"png": _rel_or_abs(png_path), "pdf": _rel_or_abs(pdf_path), "qgis_project": _rel_or_abs(project_path)}


def style_raster_pseudocolor(layer, values: list[float], colors: list[str]) -> None:
    if len(values) != len(colors):
        raise ValueError("values and colors must have the same length")
    shader = QgsRasterShader()
    ramp = QgsColorRampShader()
    ramp.setColorRampType(QgsColorRampShader.Interpolated)
    ramp.setColorRampItemList([
        QgsColorRampShader.ColorRampItem(float(value), QColor(color), str(value))
        for value, color in zip(values, colors)
    ])
    shader.setRasterShaderFunction(ramp)
    layer.setRenderer(QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader))
    layer.triggerRepaint()


def style_raster_gray(layer, opacity: float = 1.0) -> None:
    layer.setOpacity(float(opacity))
    layer.triggerRepaint()


def style_vector_simple(layer, color: str, width: float = 0.5) -> None:
    renderer = layer.renderer()
    symbol = renderer.symbol() if renderer else None
    if symbol:
        symbol.setColor(QColor(color))
        for symbol_layer in symbol.symbolLayers():
            if hasattr(symbol_layer, "setWidth"):
                symbol_layer.setWidth(width)
    layer.triggerRepaint()


def style_vector_graduated(layer, field_name: str, ranges: list[tuple[float, float, str, str]]) -> None:
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    renderer_ranges = []
    for lower, upper, color, label in ranges:
        range_symbol = symbol.clone()
        range_symbol.setColor(QColor(color))
        renderer_ranges.append(QgsRendererRange(lower, upper, range_symbol, label))
    layer.setRenderer(QgsGraduatedSymbolRenderer(field_name, renderer_ranges))
    layer.triggerRepaint()


def _clip_optional_vectors(job: MapJob, root: Path, out_dir: Path, aoi_layer, color_by_key: dict[str, str]) -> list[dict[str, Any]]:
    records = []
    for key, color in color_by_key.items():
        if key not in job.inputs:
            continue
        layer = load_vector(root, job.inputs[key], f"{key} raw")
        clipped = clip_vector(layer, aoi_layer, out_dir / f"{_safe_slug(key)}_clipped.gpkg", key.replace("_", " ").title())
        style_vector_simple(clipped, color, width=0.6)
        records.append(_layer_record(clipped, "processed_vector"))
    return records


def _clip_optional_rasters(job: MapJob, root: Path, out_dir: Path, aoi_layer, name_by_key: dict[str, str]) -> list[dict[str, Any]]:
    records = []
    for key, label in name_by_key.items():
        if key not in job.inputs:
            continue
        layer = load_raster(root, job.inputs[key], f"{label} raw")
        clipped = clip_raster_by_mask(layer, aoi_layer, out_dir / f"{_safe_slug(key)}_clipped.tif", label)
        records.append(_layer_record(clipped, "processed_raster"))
    return records


def _load_existing_rasters(job: MapJob, root: Path, keys: list[str]) -> dict[str, Any]:
    layers = {}
    for key in keys:
        if key in job.inputs and _resolve(root, job.inputs[key]).exists():
            layers[key] = load_raster(root, job.inputs[key], key.replace("_", " ").title())
    return layers


def _artifact(job: MapJob, out_dir: Path, layers: list[dict[str, Any]], exports: dict[str, str], provenance: list[str], limitations: list[str]) -> dict[str, Any]:
    project_path = exports.get("qgis_project") or _rel_or_abs(out_dir / "project.qgz")
    return {
        "status": "complete",
        "recipe": job.recipe,
        "title": job.title,
        "aoi_name": job.aoi_name,
        "crs": job.crs,
        "qgis_project": project_path,
        "exports": {key: value for key, value in exports.items() if key != "qgis_project"},
        "layers": layers,
        "provenance": provenance,
        "limitations": limitations,
        "notes": job.notes,
    }


def _block(job: MapJob, missing_inputs: list[str], message: str, next_action: str) -> dict[str, Any]:
    return {
        "status": "blocked",
        "recipe": job.recipe,
        "title": job.title,
        "missing_inputs": missing_inputs,
        "message": message,
        "next_action": next_action,
    }


def _layer_record(layer, role: str) -> dict[str, str]:
    source = layer.source() if hasattr(layer, "source") else ""
    return {"name": layer.name(), "path": source, "role": role}


def _write_artifact(artifact: dict[str, Any], out_dir: Path) -> None:
    path = out_dir / "map_artifact.json"
    path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _missing_required_inputs(job: MapJob, root: Path, required: list[str]) -> list[str]:
    missing = []
    for key in required:
        value = job.inputs.get(key)
        if not value or not _resolve(root, value).exists():
            missing.append(key)
    return missing


def _resolve(root: Path, path_value: str) -> Path:
    path = Path(path_value)
    return path if path.is_absolute() else root / path


def _set_project_crs(crs: str) -> None:
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(crs))


def _require_qgis() -> None:
    if QgsProject is None or processing is None:
        raise RuntimeError("QGIS Python APIs are not available. Run this script inside QGIS Python or through QGIS MCP.")


def _safe_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "open_earth_map"


def _rel_or_abs(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


def _load_job(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("YAML job files require PyYAML in the QGIS Python environment.") from exc
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Map job file must contain a JSON/YAML object.")
    return data


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if not args:
        print("usage: openearth_qgis_recipes.py <map-job.json|yaml>")
        return 2
    artifact = run_recipe(_load_job(Path(args[0])))
    print(json.dumps(artifact, indent=2, ensure_ascii=False, default=str))
    return 0 if artifact.get("status") == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
