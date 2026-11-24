# Python scripts in this folder

**Language:** English for documentation, comments, and console output.

## Naming convention

Standalone **CLI** Python (`python script.py`). PyQGIS scripts live in [`python/qgis/`](../qgis/).

---

## Standalone CLI scripts

| File | Purpose |
|:-----|:--------|
| `transferir_laz_rgb_ir_v3_por_aoi.py` | Copy IGC-SP LAZ/RGB/IR tiles by intersecting any AOI (shp/geojson/gpkg/kml/kmz) with the merged articulation in `data/articulacao_igc_sp/`. Optional coverage GeoPackage output. |
| `build_rgbnir_from_rgb_ir.py` | Fuse RGB bands 1–3 with IR band 1 into a 4-band RGBNIR GeoTIFF. IR is nearest-neighbor resampled onto the RGB grid. Tiles whose header bounds do not intersect the matching layer of `data/sp-bounds.gpkg` (`epsg31982` or `epsg31983`, no reprojection) are skipped. That check is saved in `rgbnir_sp_vistoria.csv` and reused; set `FORCE_SP_VISTORIA = True` or pass `--force-sp` to run it again. Output keeps the original name when the grids match, and adds `-resampled` only when the IR grid has to be aligned. Default folders on the lab machine: `E:\RGB`, `F:\IR`, `G:\RGBNIR`. Resume-safe (skips outputs already on disk). Writes `rgbnir_summary.csv`, `rgbnir_registry.csv` and a column dictionary into the output folder. Double-click `build_rgbnir_from_rgb_ir.bat`. |
| `stage_external_laz_tif.py` | Copy a quota of `.laz` / `.tif` tiles from an external disk into a local folder. `--max-files` and `--max-gb` are independent: set one, the other, or neither (neither copies every file that is still pending). Names already in the destination do not count toward the quota. Tiles with `lidar_proc=1` on [leucaena.earth](https://leucaena.earth) are skipped, using `ALS_MACHINE_TOKEN` and the registry CSV export (same host and `X-ALS-Token` header as the GeoTrees R client). Standard library only. Double-click `stage_external_laz_tif.bat` (copy it from the sample; the real `.bat` is gitignored). |
| `sync_lidar_proc_from_api.py` | Set `lidar_proc=1` on matching rows in `data/articulacao_igc_sp/*.gpkg` from the live ALS registry (`status_code=1`). Needs `ALS_MACHINE_TOKEN`. `--out-sp false` (default) updates only `sp_inside=1`; `--out-sp true` includes outside SP. Sample bat: `sync_lidar_proc_from_api-sample.bat`. |

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

Stage a slice of the external LAZ/optical archive (token comes from `ALS_MACHINE_TOKEN`, not from the command line):

```powershell
python stage_external_laz_tif.py --source E:\tiles --dest C:\OUTDIR --max-files 100
python stage_external_laz_tif.py --source E:\tiles --dest C:\OUTDIR --max-gb 40
python stage_external_laz_tif.py --source E:\tiles --dest C:\OUTDIR --dry-run
```

Leave both limits unset to copy every file that is not already in `C:\OUTDIR` and not marked `lidar_proc=1`. Asking for 100 files when 50 names are already in the destination copies 100 other files.

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

## PyQGIS

Those scripts were moved to `python/qgis/`. Run them inside QGIS (Python Console or Run Script), not from the system shell.

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
| `pyqgis_mosaic_rgbnir.py` | Mosaic 4-band RGBNIR tiles listed by `NOMENC_10K` in a GeoPackage. |
| `build-overviews-qgis.py` | Build external GeoTIFF overviews (`.tif.ovr`) for a folder. |
