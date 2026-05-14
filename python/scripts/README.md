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
| `copy_aerial_tiles_laz_rgb_ir_from_articulation_shapefiles.py` | Copy LAZ + RGB/IR GeoTIFF tiles listed in IGC-style articulation `*_selecao.shp` files into `laz/`, `rgb/`, `ir/` under a destination root. |

**Dependency:** `geopandas` (see root `requirements.txt`). Example:

```bash
pip install -r requirements.txt
python copy_aerial_tiles_laz_rgb_ir_from_articulation_shapefiles.py
```

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
