# leucaena-earth-utils

Small **Python** and **R** utilities used across the Leucaena / PhD workflow: grid preparation, spatial joins, Quarto reports, and QGIS Processing scripts.

**Repository language:** **English** for READMEs, script headers, comments, and user-facing messages (prints), so reviewers and collaborators share one baseline.

This repository is a **sibling** of [phd-leucaena-mapping](https://github.com/matheussiba/phd-leucaena-mapping) (project hub), [leucaena-earth-platform](https://github.com/matheussiba/leucaena-earth-platform), and [leucaena-earth-segmentation](https://github.com/matheussiba/leucaena-earth-segmentation).

Initial scripts were reorganized from a local `scripts` folder into the layout below.

## Layout

| Path | Purpose |
|:-----|:--------|
| `python/qgis/` | Lightweight QGIS tools (`.py` for Processing / PyQGIS). |
| `python/scripts/` | Standalone and PyQGIS scripts. **Naming:** `pyqgis_*` = QGIS Python console; otherwise CLI (see `python/scripts/README.md`). |
| `python/notebooks/` | Jupyter notebooks (`.ipynb`). |
| `r/scripts/` | Plain R scripts (`.R`). |
| `r/quarto/` | Quarto documents (`.qmd`). |

## Rules

- Do **not** commit large rasters, GeoPackages, or QGIS project files; keep the repo source-only.
- Prefer filenames **without spaces** (easier on the command line and in Git).

## Requirements

- **Python:** for CLI scripts such as `copy_aerial_tiles_laz_rgb_ir_from_articulation_shapefiles.py`, install from the repo root:

  ```bash
  pip install -r requirements.txt
  ```

- **R / Quarto:** use your usual R install; add `renv` later if you want a reproducible R library.

## License

[MIT](LICENSE)
