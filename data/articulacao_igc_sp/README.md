# IGC-SP laser articulation (merged)

`articulacao-igc-merged-spatialindex-sirgas2000.gpkg` merges the three IGC-SP
laser articulation grids into one layer:

- `Articulacao_Laser_Fehidro` (tile id in `NOMENC_2K`)
- `Articulacao_Laser_Lote4` (tile id in `NOMENC_5K`)
- `Articulacao_Laser_Voo22` (tile id in `NOMENC_5K`)

Extra fields: `layer` (source articulation), `NOMENC_10K`, `path`, `overlap`,
`msb_use`. CRS: **SIRGAS 2000** (EPSG:4674).

Used as the default `--articulacao` input for
`python/scripts/transferir_laz_rgb_ir_v3_por_aoi.py`.
