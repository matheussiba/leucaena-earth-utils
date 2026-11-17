# GeoTrees ALS batch

Runs the official GeoTrees ALS pipeline one `.laz` or `.las` tile at a time, writes a registry, and skips tiles that already finished.

`ALS_processing.R` and `ALS_processing_helperfunctions.R` are not in this repository. Before the first run, copy `ALS_processing_helperfunctions.R` into this folder, next to `batch_geotrees_als.R`. The batch script loads that file. `ALS_processing.R` is not used.

The articulation layer is read-only:

`data/articulacao_igc_sp/articulacao-igc-merged-spatialindex-sirgas2000.gpkg`

## Run the batch file

This is the lab launcher. Edit the variables at the top of `batch_geotrees_als.bat`:

| Variable | Default | Meaning |
|:---------|:--------|:--------|
| `LAZ_DIR` | `D:\laz` | Folder of `.laz` / `.las` files. Subfolders are included. |
| `OUT_DIR` | `G:\geotrees_als` | Products and registry. The script creates the folder. Another disk is fine: each LAZ is copied into the output inbox. |
| `BLOCO` | empty | Empty = every file. One block: `EF`. Several: `EF,2024`. |
| `N_CORES` | `20` | Cores passed to the pipeline. Empty = physical cores minus 2. |
| `LIMIT` | empty | Empty = no limit. `1` = only the first new tile. |
| `ARTICULACAO` | empty | Empty = the GeoPackage in `data/articulacao_igc_sp/`. |

From this folder:

```bat
batch_geotrees_als.bat
```

Extra arguments on the command line override the variables above, because the R script keeps the last value:

```bat
batch_geotrees_als.bat --bloco EF_19 --limit 1
```

The window stays open when a tile or the setup fails. If another live copy already holds the lock, this window closes without waiting.

## Run the R script

Same job, without the `.bat` defaults. From this folder:

```bat
"C:\Program Files\R\R-4.6.2\bin\Rscript.exe" batch_geotrees_als.R --laz-dir "D:\laz" --out-dir "G:\geotrees_als" --bloco EF_19 --n-cores 20
```

Flags:

| Flag | Effect |
|:-----|:-------|
| `--dry-run` | Print the plan. Do not process. |
| `--list-blocos` | Count `BLOCO` values in the LAZ folder and exit. |
| `--report-only` | Rebuild the CSV, GeoPackage, and Markdown from the registry. |
| `--limit N` | Process only N new tiles this run. |
| `--force` | Redo tiles that already succeeded. |
| `--n-cores N` | Worker count. Omit it to use physical cores minus 2. |
| `--no-blast` | Turn off `use.blast2dem`. |

All options:

```bat
Rscript batch_geotrees_als.R --help
```

Missing R packages are installed on the first real run. `lasR` is not on CRAN; the script installs it from `https://r-lidar.r-universe.dev`.

## Resume

A tile that already has `_INFO_summary_processing.csv` and no `_ERROR.txt` under `OUT_DIR\processed\<tile>` is skipped. An interrupted tile starts over. A tile that failed is tried again on the next run. `--force` redoes successes.

To stop between tiles, create `OUT_DIR\PARAR.txt`.

The first run scans the LAZ folder and the articulation table, then saves indexes under `OUT_DIR`. Later runs reuse them. Delete `OUT_DIR\geotrees_als_laz_indice.csv` to scan the LAZ folder again.

## Outputs

| Path | Contents |
|:-----|:---------|
| `OUT_DIR\processed\<tile>\` | GeoTrees products for that tile. |
| `OUT_DIR\geotrees_als_registry.csv` | One row per tile. |
| `OUT_DIR\geotrees_als_historico.csv` | One row per attempt. |
| `OUT_DIR\geotrees_als_registro.gpkg` | Same registry, layer `geotrees_tiles`. |
| `OUT_DIR\geotrees_als_relatorio.md` | Markdown report, including the time estimate for this machine. |

The time estimate is printed after each finished tile. It is the mean of successful tiles on this computer times the tiles still queued.
