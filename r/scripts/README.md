# R scripts

**Naming:** `r_` prefix + short action description + data source / year where relevant.

| File | Summary |
|:-----|:--------|
| `r_download_brazil_states_geobr_ibge2020.R` | Download Brazil state polygons (IBGE 2020) with `{geobr}` and save to GeoPackage. Set `output_path` before running. |

```bash
Rscript r/scripts/r_download_brazil_states_geobr_ibge2020.R
```

Requires `install.packages(c("geobr", "sf"))`.
