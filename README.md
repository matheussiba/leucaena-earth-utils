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
| `data/articulacao_igc_sp/` | Merged IGC-SP laser articulation GeoPackage (reference layer for AOI tile transfer). |

## Rules

- Do **not** commit large rasters, GeoPackages, or QGIS project files; keep the repo source-only.
- **Exception:** the IGC articulation GeoPackage under `data/articulacao_igc_sp/` (~17 MB) is tracked because it is the reference layer for `transferir_laz_rgb_ir_v3_por_aoi.py`.
- Prefer filenames **without spaces** (easier on the command line and in Git).

## Requirements

- **Python (standalone CLI):**

  ```bash
  pip install -r requirements.txt
  ```

  Currently used by `python/scripts/transferir_laz_rgb_ir_v3_por_aoi.py`
  (`geopandas`, `shapely`, `pyogrio`).

  > Older segmentation-specific scripts that used to live here
  > (`copy_aerial_tiles_*`, `build_4band_rgbir_*`, `criar-overviews-qgis.ipynb`)
  > were moved into
  > [`leucaena-earth-segmentation`](https://github.com/matheussiba/leucaena-earth-segmentation)
  > so the whole pre-processing chain lives in one repo.

- **PyQGIS:** `pyqgis_*.py` scripts run inside QGIS' own Python.

- **R / Quarto:** use your usual R install; add `renv` later if you want a reproducible R library.

## License

[MIT](LICENSE)
