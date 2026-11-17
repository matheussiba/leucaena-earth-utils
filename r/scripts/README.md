# R scripts

**Naming:** `r_` prefix + short action description + data source / year where relevant.

| File | Summary |
|:-----|:--------|
| `r_download_brazil_states_geobr_ibge2020.R` | Download Brazil state polygons (IBGE 2020) with `{geobr}` and save to GeoPackage. Set `output_path` before running. |

```bash
Rscript r/scripts/r_download_brazil_states_geobr_ibge2020.R
```

Requires `install.packages(c("geobr", "sf"))`.

## GeoTrees ALS

`geotrees/batch_geotrees_als.bat` and `geotrees/batch_geotrees_als.R` walk a LAZ folder tile by tile. How to run them: [`geotrees/README.md`](geotrees/README.md).

The official `ALS_processing.R` and `ALS_processing_helperfunctions.R` are not stored in this repository. Copy `ALS_processing_helperfunctions.R` into `geotrees/` before running.
