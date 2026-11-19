#!/usr/bin/env Rscript
# Reset ALS tiles with lidar_proc in {2,3,4} back to 0 for reprocessing.
# Never touches status 1 (server rejects already_ok) — sync O:\ALS_IGC stays safe.
#
# Usage (from this folder):
#   set ALS_MACHINE_TOKEN=...
#   Rscript als_reset_non_ok.R --hostname RSPC-001 --dry-run
#   Rscript als_reset_non_ok.R --hostname RSPC-001

args <- commandArgs(trailingOnly = TRUE)
opt <- list(
  url = Sys.getenv("ALS_API_URL", "https://leucaena.earth"),
  token = Sys.getenv("ALS_MACHINE_TOKEN", ""),
  hostname = "",
  dry_run = FALSE,
  reason = "reset_old_script_for_reprocess"
)
i <- 1L
while (i <= length(args)) {
  a <- args[[i]]
  if (a %in% c("--dry-run", "-n")) {
    opt$dry_run <- TRUE
  } else if (a == "--hostname" && i < length(args)) {
    i <- i + 1L
    opt$hostname <- toupper(trimws(args[[i]]))
  } else if (a == "--url" && i < length(args)) {
    i <- i + 1L
    opt$url <- args[[i]]
  } else if (a == "--token" && i < length(args)) {
    i <- i + 1L
    opt$token <- args[[i]]
  } else if (a == "--reason" && i < length(args)) {
    i <- i + 1L
    opt$reason <- args[[i]]
  } else if (a %in% c("--help", "-h")) {
    cat("Reset ALS status 2/3/4 -> 0. Status 1 is never cleared.\n")
    cat("  --hostname RSPC-001   only that host (recommended)\n")
    cat("  --dry-run             list only\n")
    quit(save = "no", status = 0)
  }
  i <- i + 1L
}

if (!nzchar(opt$token)) {
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

cfg <- list(
  enabled = TRUE,
  url = als_normalize_url(opt$url),
  token = opt$token,
  pipeline = "lastools",
  report_every = 50L,
  lease_seconds = 7200L,
  allow_retry = TRUE,
  soft_fail = FALSE,
  track = "lidar"
)

say <- function(...) {
  cat(paste0(...), "\n", sep = "")
  flush.console()
}

fetch_status <- function(status_code) {
  path <- sprintf("/api/v1/als/tiles?status=%d&limit=2000", as.integer(status_code))
  resp <- als_http_json(cfg, "GET", path)
  if (resp$status < 200L || resp$status >= 300L) {
    stop("Falha ao listar status=", status_code, ": ", resp$raw, call. = FALSE)
  }
  tiles <- resp$body$tiles
  if (is.null(tiles)) list() else tiles
}

tiles <- c(fetch_status(2L), fetch_status(3L), fetch_status(4L))
if (!length(tiles)) {
  say("Nenhum tile com status 2/3/4.")
  quit(save = "no", status = 0)
}

ids <- character()
for (t in tiles) {
  id <- toupper(as.character(t$tile_id %||% ""))
  host <- toupper(as.character(t$hostname %||% ""))
  code <- suppressWarnings(as.integer(t$status_code %||% NA_integer_))
  if (!nzchar(id)) next
  if (identical(code, 1L)) next
  if (nzchar(opt$hostname) && !identical(host, opt$hostname)) next
  if (grepl("^SYNC-", host) || grepl("ALS_IGC", host, fixed = TRUE)) next
  ids <- c(ids, id)
  say(sprintf(
    "  candidate %s | status=%s | host=%s | bloco=%s",
    id, as.character(code), host, as.character(t$bloco %||% "")
  ))
}
ids <- unique(ids)
say("Total a resetar: ", length(ids))
if (!length(ids)) quit(save = "no", status = 0)
if (isTRUE(opt$dry_run)) {
  say("Dry-run: nenhum report enviado.")
  quit(save = "no", status = 0)
}

als_reset_to_pending(cfg, ids, reason = opt$reason, say_fn = say)
say("Pronto. Confira com GET /api/v1/als/summary")
