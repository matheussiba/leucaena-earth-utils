#!/usr/bin/env Rscript
# Auditoria disco <-> site (ALS).
#
# Fonte da verdade no disco:
#   OUT_DIR/processed/<22|23>/<tile>/  com _INFO_summary_processing.csv e sem _ERROR.txt
#
# Uso (PowerShell / cmd, pasta geotrees/):
#   set ALS_MACHINE_TOKEN=...
#   Rscript als_audit_sync.R --out-dir E:\geotrees_als_lastools
#   Rscript als_audit_sync.R --out-dir E:\geotrees_als_lastools --tile SF-22-V-B-III-3-SE-A-I
#   Rscript als_audit_sync.R --out-dir E:\geotrees_als_lastools --report-ok
#   Rscript als_audit_sync.R --out-dir E:\geotrees_als_lastools --report-ok --dry-run
#
# --report-ok: so fecha no site tiles com produto FINAL no disco (status API -> 1).
# Nao reprocessa. Nao mexe em tile sem produto local.

args <- commandArgs(trailingOnly = TRUE)
opt <- list(
  out_dir = "",
  url = Sys.getenv("ALS_API_URL", "https://leucaena.earth"),
  token = Sys.getenv("ALS_MACHINE_TOKEN", ""),
  tile = "",
  report_ok = FALSE,
  dry_run = FALSE,
  pipeline = "lastools"
)
i <- 1L
while (i <= length(args)) {
  a <- args[[i]]
  if (a == "--out-dir" && i < length(args)) {
    i <- i + 1L
    opt$out_dir <- args[[i]]
  } else if (a == "--url" && i < length(args)) {
    i <- i + 1L
    opt$url <- args[[i]]
  } else if (a == "--token" && i < length(args)) {
    i <- i + 1L
    opt$token <- args[[i]]
  } else if (a == "--tile" && i < length(args)) {
    i <- i + 1L
    opt$tile <- toupper(trimws(args[[i]]))
  } else if (a == "--pipeline" && i < length(args)) {
    i <- i + 1L
    opt$pipeline <- tolower(trimws(args[[i]]))
  } else if (a == "--report-ok") {
    opt$report_ok <- TRUE
  } else if (a %in% c("--dry-run", "-n")) {
    opt$dry_run <- TRUE
  } else if (a %in% c("--help", "-h")) {
    cat("als_audit_sync.R — compara processed/ local com status no site\n")
    cat("  --out-dir DIR     obrigatorio\n")
    cat("  --tile ID         so um tile\n")
    cat("  --report-ok       envia status 1 para tiles com produto final no disco\n")
    cat("  --dry-run         com --report-ok: lista, nao envia\n")
    cat("  --token / ALS_MACHINE_TOKEN  para ler/escrever API\n")
    quit(save = "no", status = 0)
  }
  i <- i + 1L
}

if (!nzchar(opt$out_dir)) {
  stop("Passe --out-dir (ex.: E:\\geotrees_als_lastools).", call. = FALSE)
}
out_dir <- normalizePath(opt$out_dir, winslash = "/", mustWork = FALSE)
if (!dir.exists(out_dir)) {
  stop("OUT_DIR nao existe: ", out_dir, call. = FALSE)
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

say <- function(...) {
  cat(paste0(...), "\n", sep = "")
  flush.console()
}

stamp <- format(Sys.time(), "%Y%m%d_%H%M%S")
host <- toupper(Sys.info()[["nodename"]])
user <- Sys.info()[["user"]]

# --- scan processed/ ---
dir_processed <- file.path(out_dir, "processed")
list_tile_dirs <- function(root) {
  if (!dir.exists(root)) return(character())
  top <- list.dirs(root, recursive = FALSE, full.names = TRUE)
  out <- character()
  for (d in top) {
    bn <- basename(d)
    if (grepl("^[0-9]{2}$", bn)) {
      out <- c(out, list.dirs(d, recursive = FALSE, full.names = TRUE))
    } else {
      out <- c(out, d)
    }
  }
  out
}

tiles_disk <- list()
for (k in list_tile_dirs(dir_processed)) {
  tid <- toupper(basename(k))
  if (nzchar(opt$tile) && !identical(tid, opt$tile)) next
  pasta <- normalizePath(k, winslash = "/", mustWork = FALSE)
  ok <- als_products_final_ok(pasta)
  info <- list.files(pasta, pattern = "_INFO_summary_processing\\.csv$", recursive = TRUE)
  err <- list.files(pasta, pattern = "^_ERROR\\.txt$", recursive = TRUE)
  tiles_disk[[tid]] <- list(
    tile_id = tid,
    pasta_saida = pasta,
    disco_ok = isTRUE(ok),
    tem_info = length(info) > 0L,
    tem_error = length(err) > 0L
  )
}

# --- local registry ---
reg_path <- file.path(out_dir, "geotrees_als_registry.csv")
reg <- NULL
if (file.exists(reg_path)) {
  reg <- tryCatch(
    utils::read.csv(reg_path, stringsAsFactors = FALSE, check.names = FALSE),
    error = function(e) NULL
  )
}

reg_status <- function(tid) {
  if (is.null(reg) || !nrow(reg) || !"tile_id" %in% names(reg)) return("")
  hit <- which(toupper(as.character(reg$tile_id)) == tid)
  if (!length(hit)) return("")
  as.character(reg$status[[hit[[1]]]] %||% "")
}

# --- API (optional) ---
api_map <- list()
cfg <- NULL
has_token <- nzchar(trimws(opt$token))
if (has_token) {
  cfg <- list(
    enabled = TRUE,
    url = als_normalize_url(opt$url),
    token = trimws(opt$token),
    pipeline = opt$pipeline,
    report_every = 50L,
    lease_seconds = 7200L,
    allow_retry = TRUE,
    soft_fail = FALSE,
    track = "lidar"
  )
  for (code in 0:4) {
    path <- sprintf("/api/v1/als/tiles?status=%d&limit=2000", code)
    resp <- tryCatch(
      als_http_json(cfg, "GET", path),
      error = function(e) list(status = 0L, body = list(), raw = conditionMessage(e))
    )
    if (resp$status < 200L || resp$status >= 300L) {
      say("AVISO: falha ao listar status=", code, " HTTP ", resp$status)
      next
    }
    tiles <- resp$body$tiles
    if (is.null(tiles) || !length(tiles)) next
    for (t in tiles) {
      id <- toupper(as.character(t$tile_id %||% ""))
      if (!nzchar(id)) next
      if (nzchar(opt$tile) && !identical(id, opt$tile)) next
      api_map[[id]] <- list(
        status_code = suppressWarnings(as.integer(t$status_code %||% code)),
        hostname = as.character(t$hostname %||% ""),
        pasta_api = as.character(t$pasta_saida %||% ""),
        erro = as.character(t$erro %||% "")
      )
    }
  }
} else {
  say("Sem ALS_MACHINE_TOKEN: so auditoria local (sem status do site).")
}

# union of ids
ids <- unique(c(names(tiles_disk), names(api_map)))
if (nzchar(opt$tile)) ids <- intersect(ids, opt$tile)
if (!length(ids) && nzchar(opt$tile)) ids <- opt$tile

rows <- list()
for (tid in sort(ids)) {
  d <- tiles_disk[[tid]]
  a <- api_map[[tid]]
  disco_ok <- isTRUE(d$disco_ok)
  api_code <- if (is.null(a)) NA_integer_ else a$status_code
  api_host <- if (is.null(a)) "" else a$hostname
  local_reg <- reg_status(tid)
  acao <- "ok_consistente"
  if (disco_ok && (is.na(api_code) || !identical(api_code, 1L))) {
    acao <- "FECHAR_SITE_PARA_1"
  } else if (!disco_ok && identical(api_code, 1L)) {
    acao <- "SITE_DIZ_1_MAS_DISCO_SEM_PRODUTO"
  } else if (!disco_ok && identical(api_code, 3L)) {
    acao <- "CLAIM_ORFAO_OU_AINDA_RODANDO"
  } else if (!disco_ok && is.na(api_code) && !nzchar(local_reg)) {
    acao <- "ausente"
  } else if (!disco_ok && identical(local_reg, "erro")) {
    acao <- "erro_local"
  } else if (disco_ok && identical(api_code, 1L)) {
    acao <- "ok_consistente"
  }

  rows[[length(rows) + 1L]] <- data.frame(
    tile_id = tid,
    disco_ok = disco_ok,
    pasta_saida = if (is.null(d)) "" else d$pasta_saida,
    registro_local = local_reg,
    api_status = if (is.na(api_code)) NA_integer_ else api_code,
    api_hostname = api_host,
    acao_sugerida = acao,
    stringsAsFactors = FALSE
  )
}

tab <- if (length(rows)) {
  do.call(rbind, rows)
} else {
  data.frame(
    tile_id = character(), disco_ok = logical(), pasta_saida = character(),
    registro_local = character(), api_status = integer(), api_hostname = character(),
    acao_sugerida = character(), stringsAsFactors = FALSE
  )
}

csv_out <- file.path(out_dir, paste0("geotrees_als_auditoria_", stamp, ".csv"))
md_out <- file.path(out_dir, paste0("geotrees_als_auditoria_", stamp, ".md"))
latest_csv <- file.path(out_dir, "geotrees_als_auditoria_latest.csv")
latest_md <- file.path(out_dir, "geotrees_als_auditoria_latest.md")

utils::write.csv(tab, csv_out, row.names = FALSE, fileEncoding = "UTF-8")
file.copy(csv_out, latest_csv, overwrite = TRUE)

code_lbl <- c(
  "0=pending", "1=ok", "2=fail", "3=claim", "4=skipped"
)
md <- c(
  paste0("# Auditoria ALS ", stamp),
  "",
  paste0("- Maquina: `", host, "` / usuario `", user, "`"),
  paste0("- OUT_DIR: `", out_dir, "`"),
  paste0("- API: ", if (has_token) opt$url else "(sem token)"),
  "",
  "## Regra",
  "",
  "1. Disco com `_INFO_summary_processing.csv` e sem `_ERROR.txt` em `processed/<utm>/<tile>` = **terminou**.",
  "2. Site so confia em status **1** depois do report com produto final.",
  "3. Status **3** com disco ok = claim orfao (Ctrl+C / report falhou) → use `--report-ok`.",
  "",
  "## Status API",
  paste0("- ", paste(code_lbl, collapse = " | ")),
  "",
  "## Resultado",
  "",
  paste0("| tile | disco_ok | reg_local | api | host_api | acao |"),
  paste0("|---|---|---|---|---|---|")
)
if (nrow(tab)) {
  for (i in seq_len(nrow(tab))) {
    r <- tab[i, ]
    md <- c(md, sprintf(
      "| %s | %s | %s | %s | %s | %s |",
      r$tile_id,
      as.character(r$disco_ok),
      if (nzchar(r$registro_local)) r$registro_local else "-",
      if (is.na(r$api_status)) "-" else as.character(r$api_status),
      if (nzchar(r$api_hostname)) r$api_hostname else "-",
      r$acao_sugerida
    ))
  }
} else {
  md <- c(md, "| (nenhum tile) | | | | | |")
}
md <- c(
  md, "",
  "## Arquivos desta maquina (sempre)",
  "",
  "- `geotrees_als_registry.csv` — status local por tile",
  "- `geotrees_als_run.log` — log append da rodada",
  "- `geotrees_als_historico.csv` — tentativas",
  "- `geotrees_als_relatorio.md` — resumo",
  "- este arquivo + CSV de auditoria",
  ""
)
writeLines(md, md_out, useBytes = TRUE)
file.copy(md_out, latest_md, overwrite = TRUE)

say("Auditoria gravada:")
say("  ", csv_out)
say("  ", md_out)
say("  (copias latest: geotrees_als_auditoria_latest.*)")
if (nrow(tab)) {
  print(tab[, c("tile_id", "disco_ok", "registro_local", "api_status", "acao_sugerida")], row.names = FALSE)
}

# --- optional close orphans to 1 ---
if (isTRUE(opt$report_ok)) {
  if (!has_token || is.null(cfg)) {
    stop("--report-ok precisa de ALS_MACHINE_TOKEN / --token.", call. = FALSE)
  }
  to_fix <- tab[isTRUE(tab$disco_ok) & (is.na(tab$api_status) | tab$api_status != 1L), , drop = FALSE]
  say("Tiles com disco ok para fechar no site (status 1): ", nrow(to_fix))
  if (!nrow(to_fix)) {
    say("Nada a reportar.")
    quit(save = "no", status = 0)
  }
  payloads <- lapply(seq_len(nrow(to_fix)), function(i) {
    r <- to_fix[i, ]
    list(
      tile_id = r$tile_id,
      status_code = 1L,
      minutos = NULL,
      tamanho_mb = NULL,
      erro = NULL,
      hostname = host,
      usuario = user,
      pasta_saida = r$pasta_saida,
      out_dir = out_dir,
      rodada_id = paste0("audit_", stamp),
      pipeline = opt$pipeline,
      iniciado_utc = "",
      finalizado_utc = format(as.POSIXlt(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ"),
      bloco = "",
      laz_nome = paste0(r$tile_id, ".laz"),
      laz_path = ""
    )
  })
  for (p in payloads) {
    say("  -> ", p$tile_id, " pasta=", p$pasta_saida)
  }
  if (isTRUE(opt$dry_run)) {
    say("Dry-run: nenhum report enviado.")
    quit(save = "no", status = 0)
  }
  out <- als_report_flush(cfg, payloads, say_fn = say)
  if (!isTRUE(out$ok)) {
    say("AVISO: alguns reports falharam. Rode de novo a auditoria.")
    quit(save = "no", status = 1)
  }
  say("Pronto. Rode a auditoria de novo para confirmar api_status=1.")
}

quit(save = "no", status = 0)
