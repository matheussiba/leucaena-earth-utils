# Python scripts in this folder

**Language:** documentation and comments in this repository are **English**.

**Runtime:** almost every script here is **PyQGIS**. Run them **inside QGIS**
(*Python Console* or *Run Script*), **not** with `python script.py` from the
system shell, because they depend on `qgis.core`, `processing`, etc.

| File | Summary |
|:-----|:--------|
| `align-grids.py` | Translate a world grid using origin vs target reference polygons. |
| `assign_states_to_grid.py` | Fill `states` (multi-UF) by intersection with state boundaries. |
| `assign_statescentroids_to_grid.py` | Fill `state_centroid` (single UF) from cell centroid containment. |
| `create-world-grid-row-col-value.py` | Build global EPSG:4326 grid with `row_value` / `col_value` labels. |
| `create-world-grid.py` | Build a grid using Processing `native:creategrid`. |
| `hierarchical_subgrid_indexing_preparing_to_export_leucaenaearth.py` | Assign `sub_2dd` for SP groups of four sub-cells (Leucaena.Earth prep). |
| `ordering_cell_number.py` | Fill `state_seq` by sorting cells per state (N→S, W→E). |
| `spatial_join_global_index.py` | Copy `row_value` / `col_value` with a slight negative buffer join. |
| `subdivide-grid_gt_6_pts.py` | Adaptive subdivision by point counts; refresh `NUMPOINTS` / `grid_status`. |

Each `.py` starts with a module docstring: required **layer names**, **fields**
created, and **how to run**.
