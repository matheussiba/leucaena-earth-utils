# Python scripts in this folder

**Language:** English for documentation, comments, and console output.

## Naming convention

| Prefix | Meaning |
|:-------|:--------|
| `pyqgis_` | Run **inside QGIS** (*Python Console* or *Run Script*). Depends on `qgis.core` / `processing`. |
| *(no prefix)* | Standalone **CLI** Python (`python script.py`). |

---

## Standalone CLI scripts

| File | Purpose |
|:-----|:--------|
| `transferir_laz_rgb_ir_v3_por_aoi.py` | Copy IGC-SP LAZ/RGB/IR tiles by intersecting any AOI (shp/geojson/gpkg/kml/kmz) with the merged articulation in `data/articulacao_igc_sp/`. Optional coverage GeoPackage output. |
| `build_rgbnir_from_rgb_ir.py` | Fuse RGB bands 1–3 with IR band 1 into a 4-band RGBNIR GeoTIFF. IR is nearest-neighbor resampled onto the RGB grid. Tiles whose header bounds do not intersect the matching layer of `G:\sp-bounds.gpkg` (`epsg31982` or `epsg31983`, no reprojection) are skipped. Output names end in `_resampled.tif`. Default folders on the lab machine: `E:\RGB`, `F:\IR`, `G:\RGBNIR`. Resume-safe (skips outputs already on disk). Writes `rgbnir_summary.csv`, `rgbnir_registry.csv` and a column dictionary into the output folder. Double-click `build_rgbnir_from_rgb_ir.bat`. |

```powershell
pip install -r ../../requirements.txt

python transferir_laz_rgb_ir_v3_por_aoi.py `
  --aoi "C:\Users\Public\Desktop\deleteme.shp" `
  --source-laz "D:\laz" `
  --source-rgb "D:\rgb" `
  --source-ir  "D:\ir" `
  --dest "C:\deleteme" `
  --cobertura-out "C:\deleteme\cobertura_baixados.gpkg"
```

Lab data-center fusion (double-click the `.bat`, or):

```powershell
python build_rgbnir_from_rgb_ir.py
python build_rgbnir_from_rgb_ir.py --dry-run
```

Defaults are `E:\RGB`, `F:\IR` and `G:\RGBNIR`. A second run skips GeoTIFFs already in the output folder. Open `G:\RGBNIR\rgbnir_summary.csv` for how many tiles are done and how many are still missing.

Older segmentation-pipeline scripts that used to live here were **moved** into the
sibling repository so that the whole pre-processing chain lives in one place:

| Old location (here) | New location |
|:--------------------|:-------------|
| `copy_aerial_tiles_laz_rgb_ir_from_articulation_shapefiles.py` | [`leucaena-earth-segmentation/prep-copy-tiles-from-aoi.py`](https://github.com/matheussiba/leucaena-earth-segmentation/blob/main/prep-copy-tiles-from-aoi.py) |
| `build_4band_rgbir_geotiff_from_rgb_and_ir_false_color_per_aoi_tile.py` | [`leucaena-earth-segmentation/prep-rgbnir-from-rgb-ir.py`](https://github.com/matheussiba/leucaena-earth-segmentation/blob/main/prep-rgbnir-from-rgb-ir.py) |

(The companion `criar-overviews-qgis.ipynb` notebook in `python/notebooks/`
was also moved — only the PowerShell cell — to
[`leucaena-earth-segmentation/scripts/build-overviews-qgis.ps1`](https://github.com/matheussiba/leucaena-earth-segmentation/blob/main/scripts/build-overviews-qgis.ps1).)

---

## PyQGIS scripts (`pyqgis_*`)

Run **inside QGIS**, not from the system shell.

| File | Purpose |
|:-----|:--------|
| `pyqgis_align_grid_translate_from_reference_polygons.py` | Translate a world grid using two reference polygons (origin vs target centroids). |
| `pyqgis_fill_grid_states_by_state_polygon_intersection.py` | Fill `states` (multi-UF) by intersection with state boundaries. |
| `pyqgis_fill_grid_state_from_centroid_within_state.py` | Fill `state_centroid` (single UF) from cell centroid containment. |
| `pyqgis_build_global_wgs84_grid_labeled_row_col.py` | Build global EPSG:4326 grid with `row_value` / `col_value` labels. |
| `pyqgis_build_world_grid_native_creategrid.py` | Build a grid using Processing `native:creategrid`. |
| `pyqgis_leucaenaearth_brazil_grid_subgrid_index_sp.py` | Assign `sub_2dd` for SP groups of four sub-cells (Leucaena.Earth prep). |
| `pyqgis_number_grid_cells_sequential_per_state.py` | Fill `state_seq` by sorting cells per state (N→S, W→E). |
| `pyqgis_join_grid_rowcol_from_index_layer_negative_buffer.py` | Transfer `row_value` / `col_value` with a slight negative-buffer spatial join. |
| `pyqgis_subdivide_grid_cells_by_point_count_adaptive.py` | Adaptive subdivision by point counts; refresh `NUMPOINTS` / `grid_status`. |

Each `pyqgis_*.py` file starts with a module docstring: required **layer names**, **fields** created, and **how to run**.
