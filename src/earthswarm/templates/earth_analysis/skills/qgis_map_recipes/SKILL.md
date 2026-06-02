---
name: qgis_map_recipes
description: Deterministic QGIS map-making recipes for small-model GIS agents.
---

# QGIS Map Recipes

Use this skill whenever an agent is asked to make, check, or prepare a map.
This skill is designed for small models: do not invent GIS processing. Pick a
recipe, fill the required inputs, run the matching QGIS/Python steps, and return
the artifact manifest.

## Small-Model Operating Protocol

1. Convert the user request into a `MapJob`.
2. Pick exactly one recipe from the table below.
3. Check whether every required input path exists or is available through QGIS.
4. If an input is missing, stop and return a `MapBlocker`; do not fake a layer.
5. Run the PyQGIS recipe in `scripts/openearth_qgis_recipes.py` or perform the
   same steps through QGIS MCP.
6. Export a PNG and PDF when possible.
7. Write the `MapArtifact` JSON next to the exported map.
8. Ask QA Checker to run `map_quality_check` before the map is report-ready.

## MapJob Contract

```json
{
  "recipe": "terrain_slope_map",
  "title": "Kaprada Terrain And Slope",
  "aoi_name": "Kaprada taluka, Valsad district",
  "crs": "EPSG:32643",
  "inputs": {
    "aoi": "data/processed/aoi/kaprada_boundary.gpkg",
    "dem": "data/raw/dem/copernicus_dem_kaprada.tif"
  },
  "outputs_dir": "artifacts/maps/kaprada_terrain",
  "parameters": {
    "slope_classes": [0, 3, 8, 15, 30, 45],
    "contour_interval": 20
  },
  "notes": "Use UTM Zone 43N for Gujarat distance/area work."
}
```

## MapArtifact Contract

```json
{
  "recipe": "terrain_slope_map",
  "title": "Kaprada Terrain And Slope",
  "status": "complete",
  "qgis_project": "artifacts/maps/kaprada_terrain/project.qgz",
  "exports": {
    "png": "artifacts/maps/kaprada_terrain/kaprada_terrain_slope.png",
    "pdf": "artifacts/maps/kaprada_terrain/kaprada_terrain_slope.pdf"
  },
  "layers": [
    {
      "name": "Slope degrees",
      "path": "artifacts/maps/kaprada_terrain/slope_degrees.tif",
      "role": "derived_raster"
    }
  ],
  "provenance": [
    {
      "source": "Copernicus DEM",
      "local_path": "data/raw/dem/copernicus_dem_kaprada.tif",
      "processing": "AOI clip, slope, hillshade"
    }
  ],
  "limitations": []
}
```

## Recipes

### aoi_locator_map

Use first when the study boundary or local context needs verification.

Required inputs:

- `aoi`: vector boundary

Optional inputs include `admin_context`, `settlements`, `roads`, `drainage`, and
`surface_water`. This map is a boundary/context check, not analytical proof.

### dem_clip

Use to clip a raw DEM to the AOI before making terrain derivatives.

Required inputs:

- `aoi`: vector boundary
- `dem`: DEM raster

Outputs a clipped DEM and a simple elevation context map.

### terrain_slope_map

Use for terrain, elevation, slope, hillshade, contour, and terrain constraints.

Required inputs:

- `aoi`: vector boundary
- `dem`: DEM raster

Outputs:

- clipped DEM
- slope raster
- hillshade raster
- optional contours
- exported map layout

Use this for questions such as: "Where are steep slopes?", "Which villages are
in upper slopes?", "Where are runoff pathways likely to concentrate?"

### drainage_watershed_map

Use for drainage, watershed boundaries, streams, waterbodies, and flow context.

Required inputs:

- `aoi`: vector boundary
- `drainage`: stream/drainage vector

Optional inputs:

- `watersheds`
- `surface_water`
- `dem`

Outputs:

- clipped drainage network
- clipped watershed/sub-watershed units
- clipped waterbody layer
- drainage context export

Use this before flood, runoff, recharge, or intervention zoning analysis.

### watershed_delineation_map

Use for watershed/subwatershed context, pour-point review, and catchment boundary
checking.

Required inputs:

- `aoi`: vector boundary

Recommended inputs:

- `watersheds`
- `subwatersheds`
- `drainage`
- `surface_water`
- `pour_points`
- `dem`

If watershed polygons are not supplied, the recipe must label the result as
context only, not as a new hydrological delineation.

### groundwater_potential_map

Use for a groundwater evidence stack or weighted groundwater potential index.

Required inputs:

- `aoi`: vector boundary

Recommended inputs:

- `slope_score_raster`
- `geology_score_raster`
- `lineament_score_raster`
- `drainage_density_score_raster`
- `soil_score_raster`
- `landcover_score_raster`
- `wells`

Outputs:

- evidence stack map
- optional weighted potential raster when score rasters are supplied
- uncertainty notes for missing factors

Do not claim aquifer potential from model reasoning alone. Use mapped evidence
and label the output as screening-level unless verified by hydrogeological data.

### erosion_risk_screening_map

Use for first-pass erosion and runoff-risk screening.

Required inputs:

- `aoi`: vector boundary

Recommended inputs:

- `slope_score_raster`
- `rainfall_score_raster`
- `soil_erodibility_score_raster`
- `landcover_risk_score_raster`
- `drainage`

Outputs:

- erosion evidence stack
- optional weighted erosion-risk raster
- map of high-risk patches and notes for field validation

Do not call this a formal RUSLE result unless all RUSLE factors are properly
computed and documented.

### lulc_vegetation_water_map

Use for land cover, vegetation, surface water, NDVI, NDWI, forest/agriculture,
and seasonal change context.

Required inputs:

- `aoi`: vector boundary

Recommended inputs:

- `lulc`
- `ndvi`
- `ndwi`
- `surface_water`
- `forest`

Outputs:

- clipped LULC/vegetation/water layers
- map export
- cloud/date/season caveats in provenance

### ndvi_vegetation_map

Use when the map specifically needs NDVI or vegetation greenness.

Required inputs:

- `aoi`: vector boundary
- `ndvi`: NDVI raster

Recommended inputs:

- `forest`
- `surface_water`
- `lulc`

Record imagery date, sensor, season, and cloud mask before interpreting the map.

### settlement_exposure_map

Use for people, roads, villages, public facilities, flood/slope/erosion exposure,
and intervention access.

Required inputs:

- `aoi`: vector boundary
- at least one of `settlements`, `roads`, `population`, `facilities`

Recommended inputs:

- `risk_zone`
- `drainage`
- `slope_raster`

Outputs:

- clipped exposure layers
- settlement/access context map
- list of exposed features if an overlay layer is available

### field_validation_map

Use to create a field team map from review or field-validation tasks.

Required inputs:

- `aoi`: vector boundary
- one of `field_sites`, `risk_zone`, `drainage`, or `settlements`

Outputs:

- printable field map
- site/transect labels
- evidence checklist copied from the field task

### intervention_opportunity_map

Use after the diagnostic layers exist to synthesize candidate treatment or
intervention zones.

Required inputs:

- `aoi`: vector boundary

Recommended inputs:

- `intervention_zones`
- `erosion_hotspots`
- `recharge_zones`
- `settlements`
- `roads`
- `drainage`
- `groundwater_potential_raster`
- `erosion_risk_raster`
- `slope_raster`
- `lulc_raster`

This is a discussion map. Do not convert it into final designs without field,
tenure, social, and cost feasibility checks.

## Runnable PyQGIS Entry Point

Inside the QGIS Python console or through QGIS MCP, run:

```python
from pathlib import Path
import json

skill_dir = Path(r"skills/qgis_map_recipes").resolve()
exec((skill_dir / "scripts" / "openearth_qgis_recipes.py").read_text(encoding="utf-8"))

job = {
    "recipe": "terrain_slope_map",
    "title": "Kaprada Terrain And Slope",
    "aoi_name": "Kaprada taluka",
    "crs": "EPSG:32643",
    "inputs": {
        "aoi": "data/processed/aoi/kaprada_boundary.gpkg",
        "dem": "data/raw/dem/copernicus_dem_kaprada.tif"
    },
    "outputs_dir": "artifacts/maps/kaprada_terrain",
    "parameters": {"contour_interval": 20}
}

artifact = run_recipe(job)
print(json.dumps(artifact, indent=2))
```

## QGIS MCP Tool Sequence

When using QGIS MCP instead of direct Python:

1. Open or create a QGIS project.
2. Set project CRS from `MapJob.crs`.
3. Load AOI first and zoom to it.
4. Load raw layers.
5. Clip layers to AOI.
6. Create derived layers named with plain analytical roles, not raw filenames.
7. Apply recipe symbology.
8. Create a layout with title, legend, scale bar, source note, and north arrow
   when useful.
9. Export PNG and PDF into `outputs_dir`.
10. Write `MapArtifact` JSON with provenance and limitations.

## Blocker Format

Return this when a small model cannot proceed:

```json
{
  "status": "blocked",
  "recipe": "terrain_slope_map",
  "missing_inputs": ["dem"],
  "message": "DEM raster is required. Search data_sources/environmental_data_catalog.yaml or request data acquisition.",
  "next_action": "Ask Data Steward to stage AOI-clipped DEM."
}
```

## Safety Rules

- Never invent layer paths.
- Never change raw data in `data/raw`.
- Never mix CRS without reprojecting or explicitly documenting why not.
- Never call a screening map a final risk map.
- Always preserve source names, dates, processing steps, and limitations.
- Always store generated map layers under `data/processed` or `artifacts/maps`.
