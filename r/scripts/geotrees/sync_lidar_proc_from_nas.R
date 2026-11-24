#!/usr/bin/env Rscript
# NAS (O:/ALS_IGC) -> data/articulacao_igc_sp lidar_proc=1 -> leucaena.earth API
#
# GPKG is the source of truth for this sync. The API is only the online command.
#
# Usage (from this folder):
#   set ALS_MACHINE_TOKEN=...
#   Rscript sync_lidar_proc_from_nas.R --dry-run
#   Rscript sync_lidar_proc_from_nas.R
#   Rscript sync_lidar_proc_from_nas.R --nas-dir "O:/ALS_IGC" --campaigns Fehidro,Lote04,Voo22

args <- commandArgs(trailingOnly = TRUE)
opt <- list(
  url = Sys.getenv("ALS_API_URL", "https://leucaena.earth"),
  token = Sys.getenv("ALS_MACHINE_TOKEN", ""),
  nas_dir = "O:/ALS_IGC",
  campaigns = c("Fehidro", "Lote04", "Voo22"),
  gpkg = "",
  dry_run = FALSE,
  skip_api = FALSE
)

i <- 1L
while (i <= length(args)) {
  a <- args[[i]]
  if (a %in% c("--dry-run", "-n")) {
    opt$dry_run <- TRUE
  } else if (a == "--skip-api") {
    opt$skip_api <- TRUE
  } else if (a == "--url" && i < length(args)) {
    i <- i + 1L; opt$url <- args[[i]]
  } else if (a == "--token" && i < length(args)) {
    i <- i + 1L; opt$token <- args[[i]]
  } else if (a == "--nas-dir" && i < length(args)) {
    i <- i + 1L; opt$nas_dir <- args[[i]]
  } else if (a == "--campaigns" && i < length(args)) {
    i <- i + 1L
    opt$campaigns <- trimws(unlist(strsplit(args[[i]], ",", fixed = TRUE)))
  } else if (a == "--gpkg" && i < length(args)) {
    i <- i + 1L; opt$gpkg <- args[[i]]
  } else if (a %in% c("--help", "-h")) {
    cat(
      "NAS -> GPKG lidar_proc=1 -> API report status 1.\n",
      "  --nas-dir O:/ALS_IGC\n",
      "  --campaigns Fehidro,Lote04,Voo22\n",
      "  --dry-run / --skip-api\n",
      sep = ""
    )
    quit(save = "no", status = 0)
  }
  i <- i + 1L
}

if (!opt$skip_api && !nzchar(trimws(opt$token))) {
  stop("Defina ALS_MACHINE_TOKEN (ou --token), ou use --skip-api.", call. = FALSE)
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

need <- c("DBI", "RSQLite", "data.table")
for (p in need) {
  if (!requireNamespace(p, quietly = TRUE)) {
    install.packages(p, repos = "https://cloud.r-project.org")
  }
}

say <- function(...) {
  cat(paste0(...), "\n", sep = "")
  flush.console()
}

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
nas_dir <- gsub("\\\\", "/", normalizePath(opt$nas_dir, winslash = "/", mustWork = TRUE))

looks_like_tile <- function(name) {
  grepl("^[A-Za-z]{2}-[0-9]{2}-[A-Za-z]-[A-Za-z]-", name)
}

scan_nas <- function(nas_dir, campaigns) {
  rows <- list()
  n <- 0L
  for (camp in campaigns) {
    base <- file.path(nas_dir, camp)
    if (!dir.exists(base)) {
      say("AVISO: campanha ausente: ", base)
      next
    }
    tops <- list.dirs(base, full.names = TRUE, recursive = FALSE)
    for (top in tops) {
      bn <- basename(top)
      if (looks_like_tile(bn)) {
        n <- n + 1L
        rows[[n]] <- data.table::data.table(
          campaign = camp, bloco = "(root)",
          tile_id = toupper(bn), pasta_saida = gsub("\\\\", "/", top)
        )
        next
      }
      kids <- list.dirs(top, full.names = TRUE, recursive = FALSE)
      tile_kids <- kids[looks_like_tile(basename(kids))]
      if (length(tile_kids)) {
        for (k in tile_kids) {
          n <- n + 1L
          rows[[n]] <- data.table::data.table(
            campaign = camp, bloco = bn,
            tile_id = toupper(basename(k)),
            pasta_saida = gsub("\\\\", "/", k)
          )
        }
      } else {
        say("  note: ", camp, "/", bn, " sem pastas de tile")
      }
    }
  }
  if (!length(rows)) {
    return(data.table::data.table(
      campaign = character(), bloco = character(),
      tile_id = character(), pasta_saida = character()
    ))
  }
  dt <- data.table::rbindlist(rows, use.names = TRUE)
  dt[!duplicated(tile_id)]
}

say("NAS : ", nas_dir)
say("GPKG: ", gpkg)
say("Campaigns: ", paste(opt$campaigns, collapse = ", "))
say("Mode: ", if (opt$dry_run) "DRY-RUN" else "LIVE")

disk <- scan_nas(nas_dir, opt$campaigns)
say("NAS unique tiles: ", nrow(disk))
if (!nrow(disk)) stop("Nenhum tile encontrado no NAS.", call. = FALSE)

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

ids <- disk$tile_id
on_nas <- function(n2, n5, n10) {
  n2 %in% ids || n5 %in% ids || n10 %in% ids
}
rows[, on_nas := mapply(on_nas, n2, n5, n10)]
hit <- rows[on_nas == TRUE]
chg <- hit[as.integer(cur) != 1L]
matched_ids <- unique(c(
  hit$n2[hit$n2 %in% ids],
  hit$n5[hit$n5 %in% ids],
  hit$n10[hit$n10 %in% ids]
))
matched_ids <- matched_ids[nzchar(matched_ids)]

say("GPKG rows matching NAS: ", nrow(hit))
say("Unique NAS ids matched in GPKG: ", length(matched_ids))
say("GPKG rows to set lidar_proc=1: ", nrow(chg))
say("NAS ids with no GPKG match: ", length(setdiff(ids, matched_ids)))

if (opt$dry_run) {
  say("Dry-run — sem escrita no GPKG nem API.")
  quit(save = "no", status = 0)
}

# 1) Update GPKG first (source of truth)
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
  tryCatch({
    stmt <- DBI::dbSendStatement(
      con,
      sprintf('UPDATE "%s" SET lidar_proc = 1 WHERE fid = ?', layer)
    )
    tryCatch({
      DBI::dbBind(stmt, params = list(as.integer(chg$fid)))
      invisible(DBI::dbGetRowsAffected(stmt))
    }, finally = {
      DBI::dbClearResult(stmt)
    })
    DBI::dbExecute(con, "COMMIT")
  }, error = function(e) {
    try(DBI::dbExecute(con, "ROLLBACK"), silent = TRUE)
    stop(e)
  })
}

for (nm in drop_names) {
  sql <- trigs$sql[trigs$name == nm]
  if (length(sql) && nzchar(sql[[1]])) {
    DBI::dbExecute(con, sql[[1]])
    say("restored trigger ", nm)
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
say("GPKG updated ", nrow(chg), " row(s) to lidar_proc=1.")

if (isTRUE(opt$skip_api)) {
  say("skip-api: done.")
  quit(save = "no", status = 0)
}

# 2) Push to API from NAS paths (GPKG already updated)
cfg <- list(
  enabled = TRUE,
  url = als_normalize_url(opt$url),
  token = opt$token,
  pipeline = "adopt_nas",
  report_every = 100L,
  lease_seconds = 7200L,
  allow_retry = TRUE,
  soft_fail = FALSE,
  track = "lidar"
)

host <- Sys.info()[["nodename"]]
if (is.null(host) || !nzchar(host)) host <- "nas-sync"

results <- lapply(seq_len(nrow(disk)), function(i) {
  list(
    tile_id = disk$tile_id[[i]],
    status_code = 1L,
    hostname = host,
    pipeline = "adopt_nas",
    bloco = disk$bloco[[i]],
    pasta_saida = disk$pasta_saida[[i]],
    out_dir = nas_dir,
    rodada_id = paste0("nas-sync-", format(Sys.time(), "%Y%m%d"))
  )
})

say("API report of ", length(results), " NAS tile(s) as status 1...")
out <- als_report_flush(cfg, results, say_fn = say)
if (!isTRUE(out$ok)) {
  say("AVISO: alguns reports ficaram pendentes (", length(out$pending), ").")
  quit(save = "no", status = 2)
}
say("Done. GPKG + API synced from NAS.")
