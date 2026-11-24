# IGC-SP laser articulation (merged)

`articulacao-igc-merged-spatialindex-sirgas2000.gpkg` merges the three IGC-SP
laser articulation grids into one layer:

- `Articulacao_Laser_Fehidro` (tile id in `NOMENC_2K`)
- `Articulacao_Laser_Lote4` (tile id in `NOMENC_5K`)
- `Articulacao_Laser_Voo22` (tile id in `NOMENC_5K`)

Extra fields: `layer` (source articulation), `NOMENC_10K`, `path`, `overlap`,
`msb_use`, **`epsg`** (31982 or 31983 for each tile — used to assign the correct
projected CRS when a source TIFF is tagged with another EPSG, e.g. 32723),
**`lidar_proc`** / **`rgbnir_proc`** (0–4; mirror from leucaena.earth via
`python/scripts/sync_lidar_proc_from_api.py`), **`sp_inside`** (`1` inside SP,
`0` outside).
CRS of the layer geometry: **SIRGAS 2000** (EPSG:4674).

Used as the default `--articulacao` input for
`python/scripts/transferir_laz_rgb_ir_v3_por_aoi.py`,
`python/scripts/build_rgbnir_from_rgb_ir.py` (EPSG assignment /
`-projetion_assigned` outputs), and `r/scripts/geotrees/batch_geotrees_als.R`.
