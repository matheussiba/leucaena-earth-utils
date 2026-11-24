#!/usr/bin/env Rscript
# Mirror leucaena.earth ALS status_code into data/articulacao_igc_sp lidar_proc.
#
# Usage (from this folder):
#   set ALS_MACHINE_TOKEN=...
#   Rscript sync_lidar_proc_gpkg.R --dry-run
#   Rscript sync_lidar_proc_gpkg.R
#   Rscript sync_lidar_proc_gpkg.R --gpkg "D:/path/to.gpkg"

args <- commandArgs(trailingOnly = TRUE)
opt <- list(
  url = Sys.getenv("ALS_API_URL", "https://leucaena.earth"),
  token = Sys.getenv("ALS_MACHINE_TOKEN", ""),
  gpkg = "",
  dry_run = FALSE
)

i <- 1L
while (i <= length(args)) {
  a <- args[[i]]
  if (a %in% c("--dry-run", "-n")) {
    opt$dry_run <- TRUE
  } else if (a == "--url" && i < length(args)) {
    i <- i + 1L
    opt$url <- args[[i]]
  } else if (a == "--token" && i < length(args)) {
    i <- i + 1L
    opt$token <- args[[i]]
  } else if (a == "--gpkg" && i < length(args)) {
    i <- i + 1L
    opt$gpkg <- args[[i]]
  } else if (a %in% c("--help", "-h")) {
    cat(
      "Atualiza lidar_proc no GPKG de articulacao a partir do registry da API.\n",
      "  --dry-run     so mostra contagens\n",
      "  --gpkg PATH   padrao: <repo>/data/articulacao_igc_sp/....gpkg\n",
      "  --url / --token  ou env ALS_API_URL / ALS_MACHINE_TOKEN\n",
      sep = ""
    )
    quit(save = "no", status = 0)
  }
  i <- i + 1L
}

if (!nzchar(trimws(opt$token))) {
  stop("Defina ALS_MACHINE_TOKEN (ou --token).", call. = FALSE)
}

cmd_args <- commandArgs(trailingOnly = FALSE)
file_arg <- sub("^--file=", "", cmd_args[grepl("^--file=", cmd_args)])
script_dir <- if (length(file_arg) && nzchar(file_arg[[1]])) {
  dirname(normalizePath(file_arg[[1]], winslash = "/"))
} else {
  normalizePath(getwd(), winslash = "/")
}

src_api <- file.path(script_dir, "als_registry_api.R")
if (!file.exists(src_api)) stop("Nao achei als_registry_api.R em ", script_dir, call. = FALSE)
source(src_api, local = FALSE)

repo_root <- function(dir_script) {
  cur <- normalizePath(dir_script, winslash = "/", mustWork = TRUE)
  for (k in seq_len(8L)) {
    if (dir.exists(file.path(cur, "data", "articulacao_igc_sp"))) return(cur)
    parent <- dirname(cur)
    if (identical(parent, cur)) break
    cur <- parent
  }
  normalizePath(file.path(dir_script, "..", "..", ".."), winslash = "/", mustWork = FALSE)
}

default_gpkg <- file.path(
  repo_root(script_dir),
  "data", "articulacao_igc_sp",
  "articulacao-igc-merged-spatialindex-sirgas2000.gpkg"
)
gpkg <- if (nzchar(trimws(opt$gpkg))) {
  normalizePath(opt$gpkg, winslash = "/", mustWork = TRUE)
} else {
  normalizePath(default_gpkg, winslash = "/", mustWork = TRUE)
}

layer <- "articulacao-igc-merged-spatialindex-sirgas2000"

say <- function(...) {
  cat(paste0(...), "\n", sep = "")
  flush.console()
}

need <- c("DBI", "RSQLite", "data.table")
for (p in need) {
  if (!requireNamespace(p, quietly = TRUE)) {
    install.packages(p, repos = "https://cloud.r-project.org")
  }
}

cfg <- list(
  enabled = TRUE,
  url = als_normalize_url(opt$url),
  token = opt$token,
  pipeline = "sync_gpkg",
  report_every = 50L,
  lease_seconds = 7200L,
  allow_retry = TRUE,
  soft_fail = FALSE,
  track = "lidar"
)

say("API : ", cfg$url)
say("GPKG: ", gpkg)
say("Mode: ", if (opt$dry_run) "DRY-RUN" else "LIVE")

resp <- als_http_raw(cfg, "GET", "/api/v1/als/export/registry.csv?track=lidar")
if (is.null(resp$status) || resp$status < 200L || resp$status >= 300L) {
  stop("Falha ao baixar registry.csv (HTTP ", resp$status %||% "?", "): ", resp$raw, call. = FALSE)
}
reg <- data.table::fread(text = as.character(resp$raw), encoding = "UTF-8", showProgress = FALSE)
if (!nrow(reg) || !"tile_id" %in% names(reg)) {
  stop("CSV da API sem tile_id.", call. = FALSE)
}

reg[, tile_id := toupper(trimws(as.character(tile_id)))]
if ("status_code" %in% names(reg)) {
  reg[, status_code := suppressWarnings(as.integer(status_code))]
} else if ("status" %in% names(reg)) {
  st <- tolower(trimws(as.character(reg$status)))
  reg[, status_code := data.table::fcase(
    st %in% c("1", "processed", "ok", "done"), 1L,
    st %in% c("2", "erro", "error", "failed"), 2L,
    st %in% c("3", "em_andamento", "claimed"), 3L,
    st %in% c("4", "pulado", "skipped"), 4L,
    default = 0L
  )]
} else {
  stop("CSV sem status_code/status.", call. = FALSE)
}
reg <- reg[nzchar(tile_id) & !is.na(status_code)]
reg <- reg[!duplicated(tile_id)]
say("API tiles: ", nrow(reg))
say("API by status: ", paste(sprintf("%s=%d", names(table(reg$status_code)), as.integer(table(reg$status_code))), collapse = ", "))

api <- data.table::data.table(
  tile_id = reg$tile_id,
  status_code = as.integer(reg$status_code)
)

con <- DBI::dbConnect(RSQLite::SQLite(), dbname = gpkg)
on.exit(try(DBI::dbDisconnect(con), silent = TRUE), add = TRUE)

cols <- DBI::dbListFields(con, layer)
if (!"lidar_proc" %in% cols) {
  DBI::dbExecute(con, sprintf('ALTER TABLE "%s" ADD COLUMN lidar_proc INTEGER DEFAULT 0', layer))
}

before <- DBI::dbGetQuery(
  con,
  sprintf(
    'SELECT COALESCE(lidar_proc, 0) AS code, COUNT(*) AS n FROM "%s" GROUP BY COALESCE(lidar_proc, 0)',
    layer
  )
)
say("GPKG before: ", paste(sprintf("%s=%d", before$code, before$n), collapse = ", "))

rows <- DBI::dbGetQuery(
  con,
  sprintf(
    paste0(
      'SELECT fid, ',
      'UPPER(TRIM(COALESCE("NOMENC_2K", ""))) AS n2, ',
      'UPPER(TRIM(COALESCE("NOMENC_5K", ""))) AS n5, ',
      'UPPER(TRIM(COALESCE("NOMENC_10K", ""))) AS n10, ',
      'COALESCE(lidar_proc, 0) AS cur ',
      'FROM "%s"'
    ),
    layer
  )
)
data.table::setDT(rows)

# Prefer NOMENC_2K, then 5K, then 10K (same order as the Python sync).
m2 <- api[rows, on = c(tile_id = "n2"), nomatch = NA]
m5 <- api[rows, on = c(tile_id = "n5"), nomatch = NA]
m10 <- api[rows, on = c(tile_id = "n10"), nomatch = NA]
rows[, new_code := data.table::fcoalesce(m2$status_code, m5$status_code, m10$status_code)]

hit <- rows[!is.na(new_code)]
chg <- hit[as.integer(cur) != as.integer(new_code)]
matched_ids <- unique(na.omit(c(
  hit$n2[hit$n2 %in% api$tile_id],
  hit$n5[hit$n5 %in% api$tile_id],
  hit$n10[hit$n10 %in% api$tile_id]
)))
matched_ids <- matched_ids[nzchar(matched_ids)]

say("GPKG rows with API match: ", nrow(hit))
say("Unique API ids matched: ", length(matched_ids))
say("Rows to update: ", nrow(chg))
say("API ids with no GPKG match: ", length(setdiff(api$tile_id, matched_ids)))

if (opt$dry_run) {
  say("Dry-run — nenhuma escrita.")
  quit(save = "no", status = 0)
}

# Plain RSQLite cannot run GPKG rtree triggers that call ST_IsEmpty.
trigs <- DBI::dbGetQuery(
  con,
  "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
  params = list(layer)
)
drop_names <- trigs$name[grepl("_geom_update4$|_geom_update5$", trigs$name)]
for (nm in drop_names) {
  DBI::dbExecute(con, sprintf('DROP TRIGGER IF EXISTS "%s"', nm))
  say("temporarily dropped trigger ", nm)
}

if (nrow(chg)) {
  DBI::dbExecute(con, "BEGIN")
  ok <- FALSE
  tryCatch({
    stmt <- DBI::dbSendStatement(
      con,
      sprintf('UPDATE "%s" SET lidar_proc = ? WHERE fid = ?', layer)
    )
    tryCatch({
      DBI::dbBind(stmt, params = list(as.integer(chg$new_code), as.integer(chg$fid)))
      invisible(DBI::dbGetRowsAffected(stmt))
    }, finally = {
      DBI::dbClearResult(stmt)
    })
    DBI::dbExecute(con, "COMMIT")
    ok <- TRUE
  }, error = function(e) {
    try(DBI::dbExecute(con, "ROLLBACK"), silent = TRUE)
    stop(e)
  })
  if (!ok) stop("Falha ao gravar lidar_proc.", call. = FALSE)
}

for (i in seq_along(drop_names)) {
  sql <- trigs$sql[trigs$name == drop_names[[i]]]
  if (length(sql) && nzchar(sql[[1]])) {
    DBI::dbExecute(con, sql[[1]])
    say("restored trigger ", drop_names[[i]])
  }
}

after <- DBI::dbGetQuery(
  con,
  sprintf(
    'SELECT COALESCE(lidar_proc, 0) AS code, COUNT(*) AS n FROM "%s" GROUP BY COALESCE(lidar_proc, 0)',
    layer
  )
)
say("GPKG after: ", paste(sprintf("%s=%d", after$code, after$n), collapse = ", "))
say("Done. Updated ", nrow(chg), " row(s).")
