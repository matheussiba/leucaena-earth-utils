# Batch GeoTrees ALS (LAStools) � um tile por vez, com retomada
#
# Envolve ALS_processing_helperfunctions_v.1.0.4.R / process.dataset com
# path_lastools preenchido. Para cada .laz/.las:
#   1. pula se OUT_DIR/processed/<utm>/<tile> ja tem _INFO_summary_processing.csv
#      (utm = 22 para EPSG 32722, 23 para 32723)
#   2. senao roda o GeoTrees so naquele arquivo via LAStools (staging em tmp;
#      so promove para processed/<utm>/<tile> quando terminar ok)
#   3. grava o tile no history CSV e no GPKG de registro (rebuild a cada rodada)
#   4. problemas da rodada em geotrees_als_problems.txt
#   Report: geotrees_als_report.md | Registry: geotrees_als_registry.gpkg
#
# Limite de tempo: --max-hours (padrao 24). A checagem e entre tiles.
# Parar no meio: STOP.txt em OUT_DIR.
#
# Uso:
#   Rscript batch_geotrees_als_lastools.R
#   Rscript batch_geotrees_als_lastools.R --max-hours 12
#   Rscript batch_geotrees_als_lastools.R --path-lastools C:/LAStools/bin
#   Rscript batch_geotrees_als_lastools.R --bloco EF_19 --limit 1

VERSION <- "1.6.1-lastools"
LAYER_OUT <- "geotrees_tiles"

REG_COLS <- c(
  "tile_id", "laz_nome", "laz_path", "bloco", "layer_artic",
  "nomenc_2k", "nomenc_5k", "nomenc_10k", "periodvoo",
  "epsg_artic", "epsg_proc",
  "status", "hostname", "usuario", "pasta_saida",
  "minutos", "tamanho_mb", "erro", "iniciado_utc", "finalizado_utc",
  "rodada_id", "origem"
)

HIST_COLS <- c(
  "rodada_id", "tile_id", "laz_nome", "laz_path", "bloco", "status",
  "hostname", "usuario", "minutos", "erro", "iniciado_utc", "finalizado_utc",
  "bloco_filtro", "out_dir", "epsg_proc"
)

GPKG_COLS <- c(
  "tile_id", "laz_nome", "laz_path", "bloco", "layer_artic",
  "nomenc_2k", "nomenc_5k", "nomenc_10k", "periodvoo",
  "epsg_artic", "epsg_proc",
  "status", "hostname", "usuario", "pasta_saida",
  "minutos", "tamanho_mb", "erro", "finalizado_utc", "rodada_id", "origem"
)

HELPER_PKGS <- c(
  "Hmisc", "data.table", "terra", "sf", "igraph", "viridisLite",
  "lidR", "lasR", "future", "sp", "raster"
)
LASR_REPO <- "https://r-lidar.r-universe.dev"

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

default_opt <- function() {
  list(
    laz_dir = "D:/laz",
    bloco = "",
    out_dir = "G:/geotrees_als_lastools",
    articulacao = "",
    articulacao_layer = "articulacao-igc-merged-spatialindex-sirgas2000",
    n_cores = NA_integer_,
    resolution = 1,
    size_tile = 250,
    size_buffer = 50,
    limit = 0L,
    force = FALSE,
    dry_run = FALSE,
    report_only = FALSE,
    list_blocos = FALSE,
    help = FALSE,
    name_job = "igc",
    use_blast2dem = FALSE,
    force_utm = "from_metadata",
    metadata = "",
    path_lastools = "C:/LAStools/bin",
    # Inf = sem limite de horas (MAX_HOURS vazio no .bat)
    max_hours = Inf,
    als_api = NA,
    als_api_url = "https://leucaena.earth",
    als_api_token = "",
    als_report_every = 5L,
    als_lease_seconds = 7200L,
    als_allow_retry = TRUE,
    # Offline/API instavel: processa local e enfileira report
    als_soft_fail = TRUE,
    debug = FALSE,
    # NAS: O:/ALS_IGC/<Fehidro|Lote04|Voo22>/<BLOCO>/<tile>
    nas_dir = "",
    nas_delete_local = FALSE,
    # Apaga o .laz em LAZ_DIR quando o tile fecha com status 1
    delete_laz_on_ok = TRUE,
    # FALSE = so tiles com sp_inside=TRUE na articulacao; TRUE = SP + fora de SP
    out_sp = FALSE
  )
}

parse_cli <- function(argv) {
  opt <- default_opt()
  bools <- c(
    "--force", "--dry-run", "--report-only", "--list-blocos", "--help",
    "--no-blast", "--als-api", "--no-als-api", "--als-allow-retry",
    "--als-soft-fail", "--debug", "--no-debug",
    "--nas-delete-local", "--no-nas-delete-local",
    "--delete-laz-on-ok", "--no-delete-laz-on-ok",
    "--out-sp", "--no-out-sp"
  )
  valued <- c(
    "--laz-dir", "--bloco", "--out-dir", "--articulacao", "--articulacao-layer",
    "--n-cores", "--resolution", "--size-tile", "--size-buffer", "--limit",
    "--name-job", "--metadata", "--force-utm", "--path-lastools", "--max-hours",
    "--als-api-url", "--als-api-token", "--als-report-every", "--als-lease-seconds",
    "--nas-dir"
  )
  i <- 1L
  while (i <= length(argv)) {
    a <- argv[[i]]
    if (a %in% c("-h", "--help")) {
      opt$help <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--force") {
      opt$force <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--dry-run") {
      opt$dry_run <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--report-only") {
      opt$report_only <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--list-blocos") {
      opt$list_blocos <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--no-blast") {
      opt$use_blast2dem <- FALSE
      i <- i + 1L
      next
    }
    if (a == "--als-api") {
      opt$als_api <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--no-als-api") {
      opt$als_api <- FALSE
      i <- i + 1L
      next
    }
    if (a == "--als-allow-retry") {
      opt$als_allow_retry <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--als-soft-fail") {
      opt$als_soft_fail <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--debug") {
      opt$debug <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--no-debug") {
      opt$debug <- FALSE
      i <- i + 1L
      next
    }
    if (a == "--nas-delete-local") {
      opt$nas_delete_local <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--no-nas-delete-local") {
      opt$nas_delete_local <- FALSE
      i <- i + 1L
      next
    }
    if (a == "--delete-laz-on-ok") {
      opt$delete_laz_on_ok <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--no-delete-laz-on-ok") {
      opt$delete_laz_on_ok <- FALSE
      i <- i + 1L
      next
    }
    if (a == "--out-sp") {
      opt$out_sp <- TRUE
      i <- i + 1L
      next
    }
    if (a == "--no-out-sp") {
      opt$out_sp <- FALSE
      i <- i + 1L
      next
    }
    if (!a %in% valued) {
      stop(
        "Argumento desconhecido: ", a,
        "\nRode com --help para ver as opcoes.",
        call. = FALSE
      )
    }
    if (i == length(argv)) stop("Faltou valor para ", a, call. = FALSE)
    val <- argv[[i + 1L]]
    i <- i + 2L
    if (a == "--laz-dir") opt$laz_dir <- val
    if (a == "--bloco") opt$bloco <- val
    if (a == "--out-dir") opt$out_dir <- val
    if (a == "--articulacao") opt$articulacao <- val
    if (a == "--articulacao-layer") opt$articulacao_layer <- val
    if (a == "--n-cores") opt$n_cores <- as.integer(num_arg(val, a))
    if (a == "--resolution") opt$resolution <- num_arg(val, a)
    if (a == "--size-tile") opt$size_tile <- num_arg(val, a)
    if (a == "--size-buffer") opt$size_buffer <- num_arg(val, a)
    if (a == "--limit") opt$limit <- as.integer(num_arg(val, a))
    if (a == "--name-job") opt$name_job <- val
    if (a == "--metadata") opt$metadata <- val
    if (a == "--force-utm") opt$force_utm <- val
    if (a == "--path-lastools") opt$path_lastools <- val
    if (a == "--max-hours") opt$max_hours <- num_arg(val, a)
    if (a == "--als-api-url") opt$als_api_url <- val
    if (a == "--als-api-token") opt$als_api_token <- val
    if (a == "--als-report-every") opt$als_report_every <- as.integer(num_arg(val, a))
    if (a == "--als-lease-seconds") opt$als_lease_seconds <- as.integer(num_arg(val, a))
    if (a == "--nas-dir") opt$nas_dir <- val
  }
  opt$force_utm <- normalize_force_utm(opt$force_utm)
  if (!is.na(opt$n_cores) && opt$n_cores < 1L) {
    stop("--n-cores precisa ser >= 1.", call. = FALSE)
  }
  if (opt$resolution <= 0 || opt$size_tile <= 0 || opt$size_buffer < 0) {
    stop("resolution, size-tile e size-buffer invalidos.", call. = FALSE)
  }
  if (opt$limit < 0L) stop("--limit precisa ser >= 0.", call. = FALSE)
  # vazio / NA / <=0 / Inf = sem limite; so para se --max-hours N finito > 0
  if (is.null(opt$max_hours) || length(opt$max_hours) == 0L || is.na(opt$max_hours) ||
      !is.finite(opt$max_hours) || opt$max_hours <= 0) {
    opt$max_hours <- Inf
  }
  opt$path_lastools <- gsub("\\\\", "/", opt$path_lastools)
  opt$path_lastools <- sub("/+$", "", opt$path_lastools)
  if (!nzchar(trimws(opt$als_api_token))) {
    opt$als_api_token <- Sys.getenv("ALS_MACHINE_TOKEN", "")
  }
  if (is.na(opt$als_api)) opt$als_api <- nzchar(trimws(opt$als_api_token))
  if (isTRUE(opt$als_api) && !nzchar(trimws(opt$als_api_token))) {
    stop(
      "ALS API ligada sem token. Defina ALS_MACHINE_TOKEN ou --als-api-token.",
      call. = FALSE
    )
  }
  if (is.na(opt$als_report_every) || opt$als_report_every < 1L) opt$als_report_every <- 5L
  if (is.na(opt$als_lease_seconds) || opt$als_lease_seconds < 60L) opt$als_lease_seconds <- 7200L
  # DEBUG=true|false in bat/env overrides default unless CLI set --debug/--no-debug
  env_dbg <- Sys.getenv("GEOTREES_DEBUG", Sys.getenv("DEBUG", ""))
  if (nzchar(env_dbg) && !any(argv %in% c("--debug", "--no-debug"))) {
    opt$debug <- parse_bool_loose(env_dbg, default = FALSE)
  }
  env_out_sp <- Sys.getenv("OUT_SP", "")
  if (nzchar(env_out_sp) && !any(argv %in% c("--out-sp", "--no-out-sp"))) {
    opt$out_sp <- parse_bool_loose(env_out_sp, default = FALSE)
  }
  opt$nas_dir <- gsub("\\\\", "/", trimws(as.character(opt$nas_dir)))
  opt$nas_dir <- sub("/+$", "", opt$nas_dir)
  opt
}

parse_bool_loose <- function(val, default = FALSE) {
  key <- tolower(trimws(as.character(val[[1]])))
  if (!nzchar(key)) return(default)
  if (key %in% c("1", "true", "t", "yes", "y", "sim")) return(TRUE)
  if (key %in% c("0", "false", "f", "no", "n", "nao", "n�o")) return(FALSE)
  default
}

num_arg <- function(val, nome) {
  key <- tolower(trimws(as.character(val[[1]])))
  if (!nzchar(key) || key %in% c("inf", "infinity", "unlimited", "none", "0")) {
    return(Inf)
  }
  v <- suppressWarnings(as.numeric(val))
  if (length(v) != 1L || is.na(v)) {
    stop(nome, " precisa ser numero. Recebi: ", val, call. = FALSE)
  }
  v
}

normalize_force_utm <- function(val) {
  key <- tolower(trimws(val))
  if (key %in% c("false", "f", "0", "nao", "n�o")) return(FALSE)
  if (key %in% c("true", "t", "1", "sim")) return(TRUE)
  if (key %in% c("from_metadata", "metadata")) return("from_metadata")
  stop(
    "--force-utm aceita false, true ou from_metadata. Recebi: ", val,
    call. = FALSE
  )
}

help_text <- function() {
  c(
    "batch_geotrees_als_lastools.R � GeoTrees ALS por tile via LAStools",
    "",
    "Opcoes:",
    "  --laz-dir DIR         Pasta dos .laz/.las (padrao D:/laz). Recursivo.",
    "  --bloco COD           Campo BLOCO da articulacao. Vazio = pasta inteira.",
    "                        Varios: EF,2024",
    "  --out-dir DIR         Registro + produtos (padrao G:/geotrees_als_lastools).",
    "  --path-lastools DIR   Pasta bin do LAStools (padrao C:/LAStools/bin).",
    "  --max-hours N         Para entre tiles apos N horas (padrao 24).",
    "  --articulacao GPKG    Articulacao IGC (obrigatoria para CRS). Padrao no clone.",
    "  --articulacao-layer   Nome da camada.",
    "  --n-cores N           Padrao: nucleos fisicos - 2.",
    "  --resolution M        Padrao 1.",
    "  --size-tile M         Padrao 250 (pipeline LAStools).",
    "  --size-buffer M       Padrao 50.",
    "  --limit N             Processa so N tiles novos nesta rodada (0 = todos).",
    "  --force               Refaz mesmo quem ja tem produto pronto.",
    "  --dry-run             So mostra o plano. Nao processa e nao mexe no registro.",
    "  --list-blocos         Conta BLOCO dos arquivos da pasta e sai.",
    "  --report-only         Regera CSV, GPKG e .md a partir do que ja esta no disco.",
    "  --no-blast            Desliga use.blast2dem (ja vem desligado neste wrapper).",
    "  --force-utm VAL       false | true | from_metadata (padrao).",
    "  --metadata CSV        Opcional. Sobrescreve campos do template inline (acq_crs nao).",
    "  --name-job NOME       Padrao igc.",
    "",
    "CRS: metadata GCA fica inline. acq_crs por tile via articulacao.epsg:",
    "  31983 -> overwrite.with:EPSG:32723 | 31982 -> overwrite.with:EPSG:32722",
    "Pasta com os dois fusos: processa um grupo EPSG inteiro, depois o outro.",
    "",
    "API leucaena.earth (multi-PC):",
    "  Liga sozinha se ALS_MACHINE_TOKEN estiver no ambiente.",
    "  --als-api / --no-als-api",
    "  --als-api-url URL       Padrao https://leucaena.earth",
    "  --als-api-token TOK    Ou env ALS_MACHINE_TOKEN",
    "  --als-report-every N   Flush a cada N tiles (padrao 5)",
    "  --als-lease-seconds N  Lease do claim (padrao 7200)",
    "  --als-allow-retry      Recclaima status 2 (padrao ligado)",
    "  --als-soft-fail        Se a API cair, processa e enfileira report (padrao ligado)",
    "  --max-hours N          Para entre tiles apos N horas. Omitido/vazio = sem limite.",
    "  --nas-dir DIR          Copia produtos p/ NAS (ex. O:/ALS_IGC). Vazio = desliga.",
    "  --nas-delete-local     Apos copia OK no NAS, apaga pasta em OUT_DIR/processed (padrao off)",
    "  --no-nas-delete-local",
    "  --delete-laz-on-ok     Apaga .laz em LAZ_DIR quando status 1 (padrao on;",
    "                        no inicio faz catch-up dos ja processados)",
    "  --out-sp / --no-out-sp Inclui tiles fora de SP (sp_inside=FALSE). Padrao: so SP",
    "                        (--no-out-sp). Campo sp_inside na articulacao IGC.",
    "  --no-delete-laz-on-ok",
    "  --debug / --no-debug   Verbose do pipeline (padrao: quiet; env DEBUG/GEOTREES_DEBUG)",
    "Parar: STOP.txt em OUT_DIR (apagado ao encerrar). Offline: geotrees_als_api_queue.json.",
    "NAS: <nas>/<Fehidro|Lote04|Voo22>/<BLOCO>/<tile> (layer da articulacao);",
    "     copia em background; fila geotrees_als_nas_queue.csv."
  )
}

# ---------------------------------------------------------------------------
# Util
# ---------------------------------------------------------------------------

utc_now <- function() format(Sys.time(), "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")

maquina <- function() {
  n <- as.character(Sys.info()[["nodename"]])
  if (!nzchar(n) || is.na(n)) "desconhecida" else n
}

usuario <- function() {
  n <- as.character(Sys.info()[["user"]])
  if (!nzchar(n) || is.na(n)) "" else n
}

clip_err <- function(x, n = 400L) {
  x <- paste(as.character(x), collapse = " ")
  x <- gsub("[\r\n\t]+", " ", x)
  if (nchar(x) > n) paste0(substr(x, 1L, n), "...") else x
}

install_pkg <- function(pkg) {
  if (identical(pkg, "lasR")) {
    say("Instalando lasR. O CRAN nao tem esse pacote.")
    say("  install.packages(\"lasR\", repos = \"", LASR_REPO, "\")")
    install.packages("lasR", repos = LASR_REPO)
  } else {
    say("Instalando ", pkg)
    install.packages(pkg)
  }
}

need_pkgs <- function(pkgs, para_que = "") {
  pkgs <- unique(pkgs)
  anunciar <- nzchar(para_que)
  dbg <- isTRUE(getOption("geotrees.debug", FALSE))
  if (anunciar && dbg) say(
    "Conferindo pacotes R (", para_que, "), antes de varrer LAZ ou carregar o pipeline:"
  )
  miss <- character()
  for (pkg in pkgs) {
    if (requireNamespace(pkg, quietly = TRUE)) {
      if (anunciar && dbg) say("  ok    ", pkg)
    } else {
      say("  FALTA ", pkg)
      miss <- c(miss, pkg)
    }
  }
  if (length(miss)) {
    for (pkg in miss) {
      tryCatch(install_pkg(pkg), error = function(e) say("  falha ao instalar ", pkg, ": ", conditionMessage(e)))
    }
    ainda <- miss[!vapply(miss, requireNamespace, logical(1), quietly = TRUE)]
    if (length(ainda)) {
      dica <- character()
      if ("lasR" %in% ainda) {
        dica <- c(dica, paste0("install.packages(\"lasR\", repos = \"", LASR_REPO, "\")"))
      }
      outros <- setdiff(ainda, "lasR")
      if (length(outros)) {
        dica <- c(dica, paste0("install.packages(c(\"", paste(outros, collapse = "\", \""), "\"))"))
      }
      stop(
        "Faltam pacotes R: ", paste(ainda, collapse = ", "),
        "\nNo R:\n", paste(dica, collapse = "\n"),
        "\nNada foi processado. Instale e rode de novo.",
        call. = FALSE
      )
    }
    say("Pacotes que faltavam foram instalados.")
  }
  if (anunciar && dbg) say("Pacotes conferidos: ", length(pkgs), ".")
  else if (anunciar) say("Pacotes R ok (", length(pkgs), ").")
}

atomic_write <- function(path, write_fn) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  tmp <- paste0(path, ".tmp")
  if (file.exists(tmp)) unlink(tmp)
  write_fn(tmp)
  if (file.exists(path)) unlink(path)
  ok <- file.rename(tmp, path)
  if (!ok) {
    file.copy(tmp, path, overwrite = TRUE)
    unlink(tmp)
  }
}

write_utf8 <- function(path, lines) {
  atomic_write(path, function(tmp) {
    con <- file(tmp, open = "wt", encoding = "UTF-8")
    on.exit(close(con), add = TRUE)
    writeLines(lines, con)
  })
}

script_dir <- function() {
  cmd <- commandArgs(trailingOnly = FALSE)
  hit <- grep("^--file=", cmd, value = TRUE)
  if (length(hit)) return(dirname(normalizePath(sub("^--file=", "", hit[[1]]), winslash = "/")))
  ofile <- NULL
  for (i in seq_len(sys.nframe())) {
    if (!is.null(sys.frame(i)$ofile)) ofile <- sys.frame(i)$ofile
  }
  if (!is.null(ofile)) return(dirname(normalizePath(ofile, winslash = "/")))
  getwd()
}

this_file <- function() {
  for (i in rev(seq_len(sys.nframe()))) {
    ofile <- sys.frame(i)$ofile
    if (!is.null(ofile)) return(normalizePath(ofile, winslash = "/"))
  }
  cmd <- commandArgs(trailingOnly = FALSE)
  hit <- grep("^--file=", cmd, value = TRUE)
  if (length(hit)) return(normalizePath(sub("^--file=", "", hit[[1]]), winslash = "/"))
  NA_character_
}

repo_root <- function(dir_script) {
  cur <- normalizePath(dir_script, winslash = "/", mustWork = FALSE)
  for (i in seq_len(8L)) {
    if (dir.exists(file.path(cur, "data", "articulacao_igc_sp")) ||
        file.exists(file.path(cur, ".git"))) {
      return(cur)
    }
    parent <- dirname(cur)
    if (identical(parent, cur)) break
    cur <- parent
  }
  normalizePath(file.path(dir_script, "..", "..", ".."), winslash = "/", mustWork = FALSE)
}

default_articulacao <- function(dir_script) {
  nome <- "articulacao-igc-merged-spatialindex-sirgas2000.gpkg"
  bundled <- file.path(repo_root(dir_script), "data", "articulacao_igc_sp", nome)
  local <- file.path("D:/_articulacao_igc_sp", nome)
  if (file.exists(bundled)) return(normalizePath(bundled, winslash = "/"))
  if (file.exists(local)) return(normalizePath(local, winslash = "/"))
  normalizePath(bundled, winslash = "/", mustWork = FALSE)
}

# Template inline (mesmo conteudo do _GCA_metadata.csv do laboratorio).
# acq_crs vem da articulacao: 31983 -> 32723, 31982 -> 32722.
map_epsg_proc <- function(epsg) {
  e <- suppressWarnings(as.integer(epsg))
  out <- rep(NA_integer_, length(e))
  out[e == 31983L] <- 32723L
  out[e == 31982L] <- 32722L
  out
}

inline_gca_metadata <- function(epsg_proc) {
  epsg_proc <- suppressWarnings(as.integer(epsg_proc[[1]]))
  if (is.na(epsg_proc) || epsg_proc <= 0L) {
    stop("epsg_proc invalido para metadata inline.", call. = FALSE)
  }
  data.table::data.table(
    subfolder = ".",
    dataset = "SaoPauloState",
    site = "Itu",
    source = "Rodrigo Costa Pinto / Matheus Pinheiro Ferreira",
    citation = "CMQ 2026.",
    contact = "rodrigopinto@usp.br",
    access = "open",
    license = "",
    updated = "23/10/2026",
    id_orig = "",
    acq_type = "ALS",
    acq_crs = paste0("overwrite.with:EPSG:", epsg_proc),
    acq_units = "m",
    acq_mindt = "01/12/2025",
    acq_maxdt = "10/12/2025",
    acq_system = "",
    acq_AGL = NA_real_,
    acq_swath = NA_real_,
    notes = "Matrice 350 L2; CRS da articulacao IGC mapeado para WGS84 UTM",
    issues = ""
  )
}

out_paths <- function(out_dir) {
  out_dir <- normalizePath(out_dir, winslash = "/", mustWork = FALSE)
  list(
    root = out_dir,
    processed = file.path(out_dir, "processed"),
    tmp = file.path(out_dir, "tmp"),
    inbox = file.path(out_dir, "_inbox"),
    staging = file.path(out_dir, "_staging"),
    logs = file.path(out_dir, "logs"),
    history = file.path(out_dir, "geotrees_als_history.csv"),
    problems = file.path(out_dir, "geotrees_als_problems.txt"),
    gpkg = file.path(out_dir, "geotrees_als_registry.gpkg"),
    report = file.path(out_dir, "geotrees_als_report.md"),
    plan = file.path(out_dir, "geotrees_als_plan.md"),
    lock = file.path(out_dir, ".geotrees_als_lastools.lock"),
    runlog = file.path(out_dir, "geotrees_als_run.log"),
    laz_index = file.path(out_dir, "geotrees_als_laz_index.csv"),
    laz_index_meta = file.path(out_dir, "geotrees_als_laz_index_meta.csv"),
    stop = file.path(out_dir, "STOP.txt"),
    nas_log = file.path(out_dir, "geotrees_als_nas_log.csv"),
    nas_queue = file.path(out_dir, "geotrees_als_nas_queue.csv"),
    nas_sync_lock = file.path(out_dir, ".geotrees_als_nas_sync.lock")
  )
}

# Renomeia artefatos PT -> EN se o nome novo ainda nao existir.
migrate_outdir_english_names <- function(root) {
  map <- c(
    "geotrees_als_historico.csv" = "geotrees_als_history.csv",
    "geotrees_als_problemas.txt" = "geotrees_als_problems.txt",
    "geotrees_als_registro.gpkg" = "geotrees_als_registry.gpkg",
    "geotrees_als_relatorio.md" = "geotrees_als_report.md",
    "geotrees_als_plano.md" = "geotrees_als_plan.md",
    "geotrees_als_laz_indice.csv" = "geotrees_als_laz_index.csv",
    "geotrees_als_laz_indice_meta.csv" = "geotrees_als_laz_index_meta.csv",
    "PARAR.txt" = "STOP.txt"
  )
  n <- 0L
  for (old in names(map)) {
    fo <- file.path(root, old)
    fn <- file.path(root, map[[old]])
    if (!file.exists(fo)) next
    if (!file.exists(fn)) {
      ok <- FALSE
      try(ok <- isTRUE(file.rename(fo, fn)), silent = TRUE)
      if (ok) n <- n + 1L
      # sidecars do GPKG antigo
      if (grepl("\\.gpkg$", old, ignore.case = TRUE)) {
        for (sfx in c("-wal", "-shm")) {
          so <- paste0(fo, sfx)
          sn <- paste0(fn, sfx)
          if (file.exists(so) && !file.exists(sn)) try(file.rename(so, sn), silent = TRUE)
          else if (file.exists(so)) unlink(so, force = TRUE)
        }
      }
    } else {
      unlink(fo, force = TRUE)
      if (grepl("\\.gpkg$", old, ignore.case = TRUE)) {
        unlink(paste0(fo, "-wal"), force = TRUE)
        unlink(paste0(fo, "-shm"), force = TRUE)
      }
      n <- n + 1L
    }
  }
  if (n > 0L) say("Nomes EN: migrei/limpei ", n, " artefato(s) com nome antigo (PT).")
  invisible(n)
}

# Remove artefatos antigos que nao usamos mais (CSV registro, summary, caches).
cleanup_legacy_outdir <- function(root) {
  legacy <- c(
    "geotrees_als_registry.csv",
    "geotrees_als_summary.csv",
    "articulacao_cache.gpkg",
    "geotrees_als_artic_indice.csv",
    "geotrees_als_artic_indice_meta.csv",
    # nomes PT ja migrados; se sobrar, apaga
    "geotrees_als_historico.csv",
    "geotrees_als_problemas.txt",
    "geotrees_als_registro.gpkg",
    "geotrees_als_relatorio.md",
    "geotrees_als_plano.md",
    "geotrees_als_laz_indice.csv",
    "geotrees_als_laz_indice_meta.csv",
    "PARAR.txt"
  )
  n <- 0L
  for (nm in legacy) {
    f <- file.path(root, nm)
    if (!file.exists(f)) next
    unlink(f, force = TRUE)
    unlink(paste0(f, "-wal"), force = TRUE)
    unlink(paste0(f, "-shm"), force = TRUE)
    n <- n + 1L
  }
  if (n > 0L) say("Limpeza: removi ", n, " arquivo(s) legado(s) do OUT_DIR.")
  invisible(n)
}

# No fim da rodada (fila ok ou STOP.txt): apaga _inbox / _staging / tmp se vazios.
cleanup_empty_workdir <- function(paths) {
  dirs <- c(paths$inbox, paths$staging, paths$tmp)
  n <- 0L
  for (d in dirs) {
    if (!nzchar(d) || !dir.exists(d)) next
    left <- list.files(d, all.files = TRUE, no.. = TRUE, recursive = FALSE)
    if (length(left)) next
    unlink(d, recursive = TRUE, force = TRUE)
    if (!dir.exists(d)) {
      n <- n + 1L
      say("Removi pasta vazia: ", basename(d))
    }
  }
  invisible(n)
}

utm_zone_folder <- function(epsg) {
  e <- suppressWarnings(as.integer(epsg)[[1]])
  if (length(e) != 1L || is.na(e)) return("outros")
  if (e %in% c(32722L, 31982L)) return("22")
  if (e %in% c(32723L, 31983L)) return("23")
  if (e >= 32701L && e <= 32760L) return(sprintf("%02d", e - 32700L))
  if (e >= 32601L && e <= 32660L) return(sprintf("%02d", e - 32600L))
  as.character(e)
}

is_utm_zone_dirname <- function(name) {
  grepl("^[0-9]{2}$", name) || identical(name, "outros")
}

drive_of <- function(path) {
  p <- normalizePath(path, winslash = "\\", mustWork = FALSE)
  if (grepl("^\\\\", p)) return(NA_character_)
  substr(p, 1L, 2L)
}

parse_blocos <- function(texto) {
  if (!nzchar(texto)) return(character())
  parts <- unlist(strsplit(texto, ",", fixed = TRUE))
  parts <- toupper(trimws(parts))
  parts[nzchar(parts)]
}

# ---------------------------------------------------------------------------
# Articulacao (somente leitura)
# ---------------------------------------------------------------------------

read_artic_table <- function(path, layer) {
  con <- DBI::dbConnect(
    RSQLite::SQLite(),
    dbname = normalizePath(path, winslash = "/", mustWork = TRUE),
    flags = RSQLite::SQLITE_RO
  )
  on.exit(DBI::dbDisconnect(con), add = TRUE)
  tables <- DBI::dbGetQuery(con, "SELECT name FROM sqlite_master WHERE type='table'")$name
  if (!layer %in% tables) {
    stop(
      "Camada nao encontrada no GPKG: ", layer,
      "\nCamadas: ", paste(tables, collapse = ", "),
      call. = FALSE
    )
  }
  cols <- DBI::dbListFields(con, layer)
  want <- c("NOMENC_2K", "NOMENC_5K", "NOMENC_10K", "BLOCO", "layer", "periodvoo", "epsg", "sp_inside")
  have <- want[want %in% cols]
  sql <- sprintf(
    'SELECT %s FROM "%s"',
    paste(sprintf('"%s"', have), collapse = ", "),
    layer
  )
  say_debug("Consultando a tabela da articulacao (SQLite, somente leitura)...")
  tab <- DBI::dbGetQuery(con, sql)
  say_debug("Tabela lida: ", nrow(tab), " linha(s).")
  tab
}

# sp_inside na articulacao: 1/TRUE = dentro de SP; 0/FALSE = fora.
parse_sp_inside <- function(x) {
  if (is.null(x)) return(logical())
  if (is.logical(x)) return(!is.na(x) & x)
  if (is.numeric(x)) return(!is.na(x) & as.numeric(x) != 0)
  xc <- tolower(trimws(as.character(x)))
  out <- rep(NA, length(xc))
  out[xc %in% c("1", "true", "t", "yes", "y", "sim")] <- TRUE
  out[xc %in% c("0", "false", "f", "no", "n", "nao")] <- FALSE
  out
}

build_index <- function(raw) {
  raw <- data.table::as.data.table(raw)
  for (col in c("NOMENC_2K", "NOMENC_5K", "NOMENC_10K", "BLOCO", "layer", "periodvoo")) {
    if (!col %in% names(raw)) raw[, (col) := ""]
    raw[, (col) := ifelse(is.na(get(col)), "", trimws(as.character(get(col))))]
  }
  if (!"epsg" %in% names(raw)) raw[, epsg := NA_integer_]
  raw[, epsg := suppressWarnings(as.integer(epsg))]
  raw[, epsg_proc := map_epsg_proc(epsg)]
  if (!"sp_inside" %in% names(raw)) {
    raw[, sp_inside := NA]
  } else {
    raw[, sp_inside := parse_sp_inside(sp_inside)]
  }
  pieces <- data.table::rbindlist(list(
    raw[nzchar(NOMENC_2K), .(
      tile_id = toupper(NOMENC_2K), via = "NOMENC_2K",
      bloco = BLOCO, layer_artic = layer, periodvoo = periodvoo,
      nomenc_2k = NOMENC_2K, nomenc_5k = NOMENC_5K, nomenc_10k = NOMENC_10K,
      epsg_artic = epsg, epsg_proc = epsg_proc, sp_inside = sp_inside
    )],
    raw[nzchar(NOMENC_5K), .(
      tile_id = toupper(NOMENC_5K), via = "NOMENC_5K",
      bloco = BLOCO, layer_artic = layer, periodvoo = periodvoo,
      nomenc_2k = NOMENC_2K, nomenc_5k = NOMENC_5K, nomenc_10k = NOMENC_10K,
      epsg_artic = epsg, epsg_proc = epsg_proc, sp_inside = sp_inside
    )],
    raw[nzchar(NOMENC_10K), .(
      tile_id = toupper(NOMENC_10K), via = "NOMENC_10K",
      bloco = BLOCO, layer_artic = layer, periodvoo = periodvoo,
      nomenc_2k = NOMENC_2K, nomenc_5k = NOMENC_5K, nomenc_10k = NOMENC_10K,
      epsg_artic = epsg, epsg_proc = epsg_proc, sp_inside = sp_inside
    )]
  ), use.names = TRUE)
  if (!nrow(pieces)) {
    return(data.table::data.table(
      tile_id = character(), via = character(), bloco = character(),
      layer_artic = character(), periodvoo = character(),
      nomenc_2k = character(), nomenc_5k = character(), nomenc_10k = character(),
      epsg_artic = integer(), epsg_proc = integer(), sp_inside = logical()
    ))
  }
  pieces[!duplicated(tile_id)]
}

geom_one <- function(cache, layer, tile_id) {
  vazio <- sf::st_sfc(sf::st_multipolygon(), crs = 4674)
  id <- gsub("'", "''", tile_id, fixed = TRUE)
  sql <- sprintf(
    paste0(
      'SELECT * FROM "%s" WHERE UPPER("NOMENC_2K") = \'%s\' ',
      'OR UPPER("NOMENC_5K") = \'%s\' OR UPPER("NOMENC_10K") = \'%s\' LIMIT 1'
    ),
    layer, id, id, id
  )
  hit <- tryCatch(
    sf::st_read(cache, query = sql, quiet = TRUE),
    error = function(e) NULL
  )
  if (is.null(hit) || !nrow(hit) || all(sf::st_is_empty(hit))) return(vazio)
  gm <- sf::st_geometry(hit)[1]
  if (as.character(sf::st_geometry_type(gm)) != "MULTIPOLYGON") {
    gm <- sf::st_cast(gm, "MULTIPOLYGON")
  }
  sf::st_crs(gm) <- 4674
  gm
}

# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

empty_registry <- function() {
  dt <- data.table::data.table()
  for (col in REG_COLS) dt[, (col) := character()]
  dt[, minutos := numeric()]
  dt[, tamanho_mb := numeric()]
  dt[0]
}

load_registry <- function(path) {
  if (!file.exists(path)) return(empty_registry())
  dt <- data.table::fread(path, colClasses = "character", encoding = "UTF-8", showProgress = FALSE)
  for (col in REG_COLS) if (!col %in% names(dt)) dt[, (col) := ""]
  dt <- dt[, ..REG_COLS]
  dt[, minutos := suppressWarnings(as.numeric(minutos))]
  dt[, tamanho_mb := suppressWarnings(as.numeric(tamanho_mb))]
  dt[, tile_id := toupper(trimws(tile_id))]
  dt[nzchar(tile_id)]
}

save_registry <- function(path, dt) {
  dt <- data.table::copy(dt)
  for (col in REG_COLS) if (!col %in% names(dt)) dt[, (col) := NA]
  dt <- dt[, ..REG_COLS]
  data.table::setorderv(dt, c("bloco", "tile_id"))
  atomic_write(path, function(tmp) {
    data.table::fwrite(dt, tmp, bom = TRUE, na = "")
  })
  invisible(dt)
}

sanitize_csv_text <- function(x) {
  x <- as.character(x)
  if (length(x) != 1L) x <- paste(x, collapse = " ")
  if (is.na(x)) x <- ""
  x <- gsub("[\r\n\t]+", " ", x)
  x <- gsub(",", ";", x, fixed = TRUE)
  trimws(x)
}

append_historico <- function(path, row) {
  dt <- data.table::as.data.table(row)
  for (col in HIST_COLS) if (!col %in% names(dt)) dt[, (col) := NA]
  if ("erro" %in% names(dt)) dt[, erro := sanitize_csv_text(erro)]
  dt <- dt[, ..HIST_COLS]
  novo <- !file.exists(path)
  data.table::fwrite(dt, path, append = !novo, bom = novo, na = "", quote = TRUE)
}

load_historico <- function(path) {
  if (!file.exists(path)) {
    vazio <- stats::setNames(replicate(length(HIST_COLS), character(0), simplify = FALSE), HIST_COLS)
    return(data.table::as.data.table(vazio))
  }
  data.table::fread(
    path,
    colClasses = "character",
    encoding = "UTF-8",
    showProgress = FALSE,
    fill = TRUE,
    quote = "\""
  )
}

append_problema <- function(path, tile_id, erro, host = maquina()) {
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  line <- sprintf(
    "%s | host=%s | tile=%s | %s\n",
    format(Sys.time(), "%Y-%m-%d %H:%M:%S"),
    host,
    as.character(tile_id),
    sanitize_csv_text(erro)
  )
  cat(line, file = path, append = TRUE)
  invisible(path)
}

list_tile_dirs <- function(dir_processed) {
  if (!dir.exists(dir_processed)) return(character())
  top <- list.dirs(dir_processed, recursive = FALSE, full.names = TRUE)
  out <- character()
  for (p in top) {
    nm <- basename(p)
    if (is_utm_zone_dirname(nm)) {
      kids <- list.dirs(p, recursive = FALSE, full.names = TRUE)
      out <- c(out, kids)
    } else {
      out <- c(out, p)
    }
  }
  out
}

prune_incomplete_processed <- function(dir_processed) {
  tiles <- list_tile_dirs(dir_processed)
  if (!length(tiles)) return(invisible(0L))
  n_rm <- 0L
  for (k in tiles) {
    info <- list.files(k, pattern = "_INFO_summary_processing\\.csv$", recursive = TRUE)
    if (length(info)) next
    say("  removendo pasta incompleta: ", normalizePath(k, winslash = "/", mustWork = FALSE))
    unlink(k, recursive = TRUE, force = TRUE)
    n_rm <- n_rm + 1L
  }
  invisible(n_rm)
}

scan_done <- function(dir_processed, known_ids = character()) {
  vazio <- data.table::data.table(tile_id = character(), pasta_saida = character(), finalizado_utc = character())
  say("Procurando tiles ja prontos em processed/ ...")
  say_debug("Procurando em ", dir_processed, " (pastas 22/23 e legado)")
  if (!dir.exists(dir_processed)) {
    say("  A pasta de produtos ainda nao existe. Nenhum tile pronto.")
    return(vazio)
  }
  kids <- list_tile_dirs(dir_processed)
  if (!length(kids)) {
    say("  Nenhuma pasta de tile nesta saida.")
    return(vazio)
  }
  known <- new.env(hash = TRUE, parent = emptyenv())
  if (length(known_ids)) {
    for (id in unique(known_ids)) if (nzchar(id)) known[[id]] <- TRUE
  }
  say("  ", length(kids), " pasta(s) na saida. ", length(ls(known)), " ja no registro.")
  rows <- vector("list", length(kids))
  n <- 0L
  n_fast <- 0L
  n_open <- 0L
  for (i in seq_along(kids)) {
    k <- kids[[i]]
    id <- toupper(basename(k))
    if (exists(id, envir = known, inherits = FALSE)) {
      if (!dir.exists(k)) next
      n <- n + 1L
      n_fast <- n_fast + 1L
      rows[[n]] <- data.table::data.table(
        tile_id = id,
        pasta_saida = normalizePath(k, winslash = "/"),
        finalizado_utc = ""
      )
      if (n_fast == 1L || n_fast %% 500L == 0L) {
        say_debug("  pasta ja registrada ", n_fast, ": ", id)
      }
      next
    }
    n_open <- n_open + 1L
    say_debug("  abrindo pasta nova ", id, " (", i, "/", length(kids), ")")
    info <- list.files(k, pattern = "_INFO_summary_processing\\.csv$", recursive = TRUE, full.names = TRUE)
    err <- list.files(k, pattern = "^_ERROR\\.txt$", recursive = TRUE)
    if (!length(info) || length(err)) {
      say_debug("      ainda nao conta como pronto.")
      next
    }
    mt <- file.info(info[[1]])$mtime
    n <- n + 1L
    rows[[n]] <- data.table::data.table(
      tile_id = id,
      pasta_saida = normalizePath(k, winslash = "/"),
      finalizado_utc = format(mt, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
    )
    say_debug("      pronto. Adotado do disco.")
  }
  say("Tiles prontos no disco: ", n, " | do registro: ", n_fast, " | pastas novas abertas: ", n_open, ".")
  if (!n) return(vazio)
  data.table::rbindlist(rows[seq_len(n)])
}

adopt_disk <- function(registry, done, host, user) {
  if (!nrow(done)) return(registry)
  for (i in seq_len(nrow(done))) {
    id <- done$tile_id[[i]]
    pos <- match(id, registry$tile_id)
    if (is.na(pos)) {
      registry <- data.table::rbindlist(list(registry, data.table::data.table(
        tile_id = id,
        laz_nome = "",
        laz_path = "",
        bloco = "",
        layer_artic = "",
        nomenc_2k = "",
        nomenc_5k = "",
        nomenc_10k = "",
        periodvoo = "",
        status = "processed",
        hostname = host,
        usuario = user,
        pasta_saida = done$pasta_saida[[i]],
        minutos = NA_real_,
        tamanho_mb = NA_real_,
        erro = "",
        iniciado_utc = "",
        finalizado_utc = done$finalizado_utc[[i]],
        rodada_id = "disco",
        origem = "adotado_disco"
      )), fill = TRUE)
    } else if (!identical(registry$status[[pos]], "processed") || !nzchar(registry$pasta_saida[[pos]])) {
      registry[pos, `:=`(
        status = "processed",
        pasta_saida = done$pasta_saida[[i]],
        erro = "",
        origem = "adotado_disco",
        finalizado_utc = done$finalizado_utc[[i]],
        hostname = if (nzchar(hostname)) hostname else host
      )]
    }
  }
  registry
}

fill_artic_on_registry <- function(registry, idx) {
  if (!nrow(registry) || !nrow(idx)) return(registry)
  pegar <- c(
    "bloco", "layer_artic", "periodvoo", "nomenc_2k", "nomenc_5k", "nomenc_10k",
    "epsg_artic", "epsg_proc"
  )
  tem <- pegar[pegar %in% names(idx)]
  if (!length(tem)) return(registry)
  cols <- c("tile_id", tem)
  idx2 <- data.table::copy(idx[!duplicated(tile_id), ..cols])
  data.table::setnames(idx2, tem, paste0("art_", tem))
  m <- merge(registry, idx2, by = "tile_id", all.x = TRUE, sort = FALSE)
  for (col in tem) {
    art <- m[[paste0("art_", col)]]
    if (col %in% c("epsg_artic", "epsg_proc")) {
      art_num <- suppressWarnings(as.integer(art))
      cur_num <- suppressWarnings(as.integer(m[[col]]))
      vazio <- is.na(cur_num) | cur_num <= 0L
      cur_num[vazio & !is.na(art_num)] <- art_num[vazio & !is.na(art_num)]
      m[, (col) := cur_num]
    } else {
      art[is.na(art)] <- ""
      vazio <- !nzchar(ifelse(is.na(m[[col]]), "", m[[col]])) & nzchar(art)
      atual <- ifelse(is.na(m[[col]]), "", m[[col]])
      atual[vazio] <- art[vazio]
      m[, (col) := atual]
    }
  }
  m[, paste0("art_", tem) := NULL]
  m
}

fill_laz_from_plan <- function(registry, plan) {
  if (is.null(plan) || !nrow(plan) || !nrow(registry)) return(registry)
  pos <- match(registry$tile_id, plan$tile_id)
  for (i in which(!is.na(pos))) {
    if (nzchar(registry$laz_path[[i]])) next
    j <- pos[[i]]
    registry[i, `:=`(
      laz_path = plan$laz_path[[j]],
      laz_nome = plan$laz_nome[[j]],
      tamanho_mb = plan$tamanho_mb[[j]]
    )]
  }
  registry
}

# ---------------------------------------------------------------------------
# Plano
# ---------------------------------------------------------------------------

# Contagem rapida (sem file.info) para invalidar o indice se a pasta mudou.
count_lidar_quick <- function(root, out_dir) {
  if (!nzchar(root) || !dir.exists(root)) return(0L)
  root_n <- normalizePath(root, winslash = "/", mustWork = TRUE)
  root_out <- normalizePath(out_dir, winslash = "/", mustWork = FALSE)
  skip_prefix <- tolower(paste0(root_out, "/"))
  files <- list.files(
    root_n, pattern = "\\.(laz|las)$", recursive = TRUE,
    full.names = TRUE, ignore.case = TRUE
  )
  if (!length(files)) return(0L)
  files <- suppressWarnings(normalizePath(files, winslash = "/", mustWork = FALSE))
  files <- files[
    nzchar(files) &
      tolower(files) != tolower(root_out) &
      !startsWith(tolower(files), skip_prefix)
  ]
  as.integer(length(files))
}

list_lidar <- function(root, out_dir) {
  say("Listando .laz/.las em ", root)
  say_debug("A pasta inteira e percorrida antes do filtro de BLOCO. Cada pasta aparece abaixo.")
  root_n <- normalizePath(root, winslash = "/", mustWork = TRUE)
  root_out <- normalizePath(out_dir, winslash = "/", mustWork = FALSE)
  skip_prefix <- tolower(paste0(root_out, "/"))
  found <- list()
  n_found <- 0L
  n_dirs <- 0L
  queue <- root_n
  i <- 1L
  while (i <= length(queue)) {
    d <- queue[[i]]
    i <- i + 1L
    if (tolower(d) == tolower(root_out) || startsWith(tolower(d), skip_prefix)) next
    n_dirs <- n_dirs + 1L
    say_debug("  [pasta ", n_dirs, "] lendo nomes em ", d)
    here <- list.files(
      d, pattern = "\\.(laz|las)$", recursive = FALSE,
      full.names = TRUE, ignore.case = TRUE
    )
    kids <- list.dirs(d, recursive = FALSE, full.names = TRUE)
    if (length(kids)) {
      kids <- normalizePath(kids, winslash = "/", mustWork = FALSE)
      kids <- kids[tolower(kids) != tolower(root_out) & !startsWith(tolower(kids), skip_prefix)]
      if (length(kids)) queue <- c(queue, kids)
    }
    n_here <- 0L
    if (length(here)) {
      say_debug("      normalizando ", length(here), " caminho(s)...")
      here <- normalizePath(here, winslash = "/", mustWork = TRUE)
      here <- here[!startsWith(tolower(here), skip_prefix)]
      n_here <- length(here)
      if (n_here) {
        found[[length(found) + 1L]] <- here
        n_found <- n_found + n_here
        say_debug("      ultimo desta pasta: ", basename(here[[n_here]]))
      }
    }
    say_debug(
      "      ", n_here, " arquivo(s) aqui | ", n_found, " no total | ",
      length(kids), " subpasta(s) a seguir"
    )
  }
  say("Varredura: ", n_found, " arquivo(s) .laz/.las em ", n_dirs, " pasta(s).")
  if (!n_found) return(character())
  unlist(found, use.names = FALSE)
}

dedupe_lidar <- function(files) {
  say("Inventario: ", length(files), " arquivo(s).")
  vazio <- data.table::data.table(
    laz_path = character(), laz_nome = character(),
    tile_id = character(), tamanho_mb = numeric()
  )
  if (!length(files)) return(list(dt = vazio, n_dup = 0L))
  # COPC is not readable by the open-source lasR/LASlib path used here.
  n_antes <- length(files)
  files <- files[!grepl("\\.copc\\.laz$", files, ignore.case = TRUE)]
  n_copc <- n_antes - length(files)
  if (n_copc > 0L) {
    say("Ignorando ", n_copc, " arquivo(s) .copc.laz (lasR nao abre COPC).")
  }
  if (!length(files)) return(list(dt = vazio, n_dup = 0L))
  files_dt <- data.table::data.table(laz_path = files)
  files_dt[, laz_nome := basename(laz_path)]
  files_dt[, tile_id := toupper(tools::file_path_sans_ext(laz_nome))]
  n <- nrow(files_dt)
  sizes <- numeric(n)
  say_debug("Lendo o tamanho de cada arquivo para descartar duplicata menor. Isso so ocorre na primeira vez.")
  chunk <- 200L
  for (i in seq.int(1L, n, by = chunk)) {
    j <- min(n, i + chunk - 1L)
    info <- file.info(files_dt$laz_path[i:j])
    sizes[i:j] <- ifelse(is.na(info$size), 0, info$size)
    say_debug("  tamanhos ", j, "/", n, " | ", files_dt$laz_nome[[j]])
  }
  files_dt[, tamanho_bytes := sizes]
  data.table::setorder(files_dt, tile_id, -tamanho_bytes)
  n_dup <- as.integer(nrow(files_dt) - data.table::uniqueN(files_dt$tile_id))
  files_dt <- files_dt[!duplicated(tile_id)]
  files_dt[, tamanho_mb := round(tamanho_bytes / 1024^2, 2)]
  files_dt[, tamanho_bytes := NULL]
  say("Inventario unico: ", nrow(files_dt), " | duplicados deixados de lado: ", n_dup, ".")
  list(dt = files_dt, n_dup = n_dup)
}

save_laz_index <- function(path, meta_path, inv, laz_dir, n_brutos = NA_integer_) {
  atomic_write(path, function(tmp) data.table::fwrite(inv$dt, tmp, bom = TRUE))
  n_brutos <- suppressWarnings(as.integer(n_brutos[[1]]))
  if (length(n_brutos) != 1L || is.na(n_brutos) || n_brutos < 0L) {
    n_brutos <- as.integer(nrow(inv$dt) + as.integer(inv$n_dup %||% 0L))
  }
  meta <- data.table::data.table(
    laz_dir = laz_dir,
    n_arquivos = nrow(inv$dt),
    n_dup = inv$n_dup,
    n_brutos = as.integer(n_brutos),
    gravado_utc = utc_now()
  )
  atomic_write(meta_path, function(tmp) data.table::fwrite(meta, tmp, bom = TRUE))
}

safe_int1 <- function(x, default = NA_integer_) {
  if (is.null(x) || length(x) < 1L) return(default)
  v <- suppressWarnings(as.integer(x[[1]]))
  if (length(v) != 1L || is.na(v)) default else v
}

load_laz_index <- function(path, meta_path, laz_dir, n_brutos_agora = NA_integer_) {
  if (!file.exists(path) || !file.exists(meta_path)) return(NULL)
  meta <- tryCatch(
    data.table::fread(meta_path, colClasses = "character", encoding = "UTF-8", showProgress = FALSE),
    error = function(e) NULL
  )
  if (is.null(meta) || !nrow(meta) || !all(c("laz_dir", "n_dup") %in% names(meta))) return(NULL)
  if (!identical(tolower(as.character(meta$laz_dir[[1]])), tolower(laz_dir))) {
    say("O indice de LAZ salvo e de outra pasta. Vou ler ", laz_dir, " de novo.")
    return(NULL)
  }
  # Indice antigo pode nao ter n_brutos (coluna ausente -> NULL -> if(NA/length0) quebrava).
  n_meta <- if ("n_brutos" %in% names(meta)) safe_int1(meta$n_brutos) else NA_integer_
  if (is.na(n_meta) && "n_arquivos" %in% names(meta)) {
    n_uni <- safe_int1(meta$n_arquivos)
    n_dup_m <- safe_int1(meta$n_dup, 0L)
    if (!is.na(n_uni)) n_meta <- as.integer(n_uni + n_dup_m)
  }
  n_agora <- safe_int1(n_brutos_agora)
  if (!is.na(n_agora) && !is.na(n_meta) && n_agora != n_meta) {
    say(
      "Pasta LAZ mudou desde o indice (", n_meta, " -> ", n_agora,
      " arquivo(s)). Vou ler de novo."
    )
    return(NULL)
  }
  dt <- tryCatch(
    data.table::fread(path, colClasses = "character", encoding = "UTF-8", showProgress = FALSE),
    error = function(e) NULL
  )
  precisa <- c("laz_path", "laz_nome", "tile_id", "tamanho_mb")
  if (is.null(dt) || !all(precisa %in% names(dt))) return(NULL)
  dt <- dt[, ..precisa]
  dt[, tamanho_mb := suppressWarnings(as.numeric(tamanho_mb))]
  dt[, tile_id := toupper(trimws(tile_id))]
  dt <- dt[!is.na(tile_id) & !is.na(laz_path) & nzchar(tile_id) & nzchar(laz_path)]
  n_copc <- sum(grepl("\\.copc\\.laz$", dt$laz_path, ignore.case = TRUE), na.rm = TRUE)
  if (isTRUE(n_copc > 0L)) {
    say("Indice tinha ", n_copc, " .copc.laz; removidos da fila (lasR nao abre COPC).")
    dt <- dt[!grepl("\\.copc\\.laz$", laz_path, ignore.case = TRUE)]
  }
  n_dup <- safe_int1(meta$n_dup, 0L)
  say(
    "Indice de LAZ reutilizado: ", nrow(dt), " tile(s) unicos (pasta ainda com ",
    if (is.na(n_agora)) "?" else n_agora, " arquivo(s))."
  )
  say("  ", path)
  list(dt = dt, n_dup = n_dup)
}

file_stamp <- function(path) {
  info <- file.info(path)
  paste0(sprintf("%.0f", ifelse(is.na(info$size), -1, info$size)), "|", sprintf("%.3f", as.numeric(info$mtime)))
}

save_artic_index <- function(path, meta_path, idx, artic_path) {
  atomic_write(path, function(tmp) data.table::fwrite(idx, tmp, bom = TRUE))
  meta <- data.table::data.table(
    artic_path = artic_path,
    stamp = file_stamp(artic_path),
    n_ids = nrow(idx),
    gravado_utc = utc_now()
  )
  atomic_write(meta_path, function(tmp) data.table::fwrite(meta, tmp, bom = TRUE))
}

load_artic_index <- function(path, meta_path, artic_path) {
  if (!file.exists(path) || !file.exists(meta_path) || !file.exists(artic_path)) return(NULL)
  meta <- tryCatch(
    data.table::fread(meta_path, colClasses = "character", encoding = "UTF-8", showProgress = FALSE),
    error = function(e) NULL
  )
  if (is.null(meta) || !nrow(meta) || !all(c("artic_path", "stamp") %in% names(meta))) return(NULL)
  if (!identical(tolower(meta$artic_path[[1]]), tolower(artic_path))) return(NULL)
  if (!identical(meta$stamp[[1]], file_stamp(artic_path))) {
    say("A articulacao mudou desde o indice salvo. Vou le-la de novo.")
    return(NULL)
  }
  idx <- tryCatch(
    data.table::fread(path, colClasses = "character", encoding = "UTF-8", showProgress = FALSE),
    error = function(e) NULL
  )
  precisa <- c(
    "tile_id", "via", "bloco", "layer_artic", "periodvoo",
    "nomenc_2k", "nomenc_5k", "nomenc_10k", "epsg_artic", "epsg_proc"
  )
  if (is.null(idx) || !all(precisa %in% names(idx))) return(NULL)
  say("Indice da articulacao da primeira leitura: ", nrow(idx), " id(s). O GPKG nao sera lido de novo.")
  say("  ", path)
  idx[, epsg_artic := suppressWarnings(as.integer(epsg_artic))]
  idx[, epsg_proc := suppressWarnings(as.integer(epsg_proc))]
  idx[, ..precisa]
}

make_plan <- function(files_dt, idx, blocos, done_ids, limit, force, n_dup = 0L, out_sp = FALSE) {
  if (is.null(files_dt)) {
    files_dt <- data.table::data.table(
      laz_path = character(), laz_nome = character(),
      tile_id = character(), tamanho_mb = numeric()
    )
  }
  say("Montando o plano: ", nrow(files_dt), " tile(s) unicos.")

  if (nrow(idx)) {
    plan <- merge(files_dt, idx, by = "tile_id", all.x = TRUE, sort = FALSE)
  } else {
    plan <- files_dt
    plan[, `:=`(
      via = "", bloco = "", layer_artic = "", periodvoo = "",
      nomenc_2k = "", nomenc_5k = "", nomenc_10k = "",
      epsg_artic = NA_integer_, epsg_proc = NA_integer_,
      sp_inside = NA
    )]
  }
  for (col in c("bloco", "layer_artic", "periodvoo", "nomenc_2k", "nomenc_5k", "nomenc_10k", "via")) {
    if (!col %in% names(plan)) plan[, (col) := ""]
    plan[is.na(get(col)), (col) := ""]
  }
  if (!"epsg_artic" %in% names(plan)) plan[, epsg_artic := NA_integer_]
  if (!"epsg_proc" %in% names(plan)) plan[, epsg_proc := NA_integer_]
  if (!"sp_inside" %in% names(plan)) plan[, sp_inside := NA]
  plan[, epsg_artic := suppressWarnings(as.integer(epsg_artic))]
  plan[, epsg_proc := suppressWarnings(as.integer(epsg_proc))]
  plan[, sp_inside := parse_sp_inside(sp_inside)]
  plan[is.na(epsg_proc) & !is.na(epsg_artic), epsg_proc := map_epsg_proc(epsg_artic)]
  if (length(blocos)) {
    plan[, no_filtro := toupper(bloco) %in% blocos]
  } else {
    plan[, no_filtro := TRUE]
  }
  plan[, acao := data.table::fifelse(
    !no_filtro, "fora_do_bloco",
    data.table::fifelse(tile_id %in% done_ids & !force, "pular", "processar")
  )]
  # OUT_SP=false: so sp_inside=TRUE (dentro de SP). OUT_SP=true: SP + fora.
  if (!isTRUE(out_sp)) {
    plan[
      acao == "processar" & (is.na(sp_inside) | sp_inside == FALSE),
      acao := "fora_de_sp"
    ]
  }
  # Sem CRS mapeavel nao processa: o pipeline LAStools quebra na reprojection check.
  plan[
    acao == "processar" & (is.na(epsg_proc) | epsg_proc <= 0L),
    acao := "sem_crs"
  ]
  # Mistura de fusos: processa um EPSG inteiro, depois o outro.
  data.table::setorder(plan, epsg_proc, bloco, tile_id)
  if (limit > 0L) {
    idx_go <- which(plan$acao == "processar")
    if (length(idx_go) > limit) {
      plan[idx_go[(limit + 1L):length(idx_go)], acao := "adiado_limit"]
    }
  }
  plan[, motivo := ""]
  plan[acao == "pular", motivo := "ja processado nesta maquina"]
  plan[acao == "fora_do_bloco", motivo := "fora do filtro de BLOCO"]
  plan[acao == "fora_de_sp", motivo := "fora de SP (sp_inside=FALSE; use --out-sp)"]
  plan[acao == "adiado_limit", motivo := "alem do --limit desta rodada"]
  plan[acao == "sem_crs", motivo := "sem epsg na articulacao (31982/31983)"]
  plan[acao == "processar" & !nzchar(layer_artic), motivo := "sem ficha na articulacao"]
  attr(plan, "n_dup") <- n_dup
  n_go <- sum(plan$acao == "processar")
  grupos <- if (n_go) {
    plan[acao == "processar", .N, by = epsg_proc][order(epsg_proc)]
  } else {
    data.table::data.table(epsg_proc = integer(), N = integer())
  }
  say(
    "Plano pronto. Unicos: ", nrow(plan),
    " | duplicados deixados de lado: ", n_dup, "."
  )
  say(
    "Filtro SP: ",
    if (isTRUE(out_sp)) "OUT_SP=true (dentro + fora de SP)" else "OUT_SP=false (so sp_inside=TRUE)"
  )
  if (nrow(grupos)) {
    say(
      "Fila por EPSG de processamento (articulacao 31983->32723, 31982->32722): ",
      paste(sprintf("%s=%d", grupos$epsg_proc, grupos$N), collapse = " | ")
    )
  }
  n_sem <- sum(plan$acao == "sem_crs")
  if (n_sem) {
    say(
      "AVISO: ", n_sem,
      " tile(s) sem epsg mapeavel na articulacao. Nao entram na fila."
    )
  }
  plan
}

# ---------------------------------------------------------------------------
# GPKG
# ---------------------------------------------------------------------------

row_to_sf <- function(row, geom) {
  df <- data.frame(
    tile_id = as.character(row$tile_id),
    laz_nome = as.character(row$laz_nome),
    laz_path = as.character(row$laz_path),
    bloco = as.character(row$bloco),
    layer_artic = as.character(row$layer_artic),
    nomenc_2k = as.character(row$nomenc_2k),
    nomenc_5k = as.character(row$nomenc_5k),
    nomenc_10k = as.character(row$nomenc_10k),
    periodvoo = as.character(row$periodvoo),
    epsg_artic = suppressWarnings(as.integer(row$epsg_artic)),
    epsg_proc = suppressWarnings(as.integer(row$epsg_proc)),
    status = as.character(row$status),
    hostname = as.character(row$hostname),
    usuario = as.character(row$usuario),
    pasta_saida = as.character(row$pasta_saida),
    minutos = as.numeric(row$minutos),
    tamanho_mb = as.numeric(row$tamanho_mb),
    erro = as.character(row$erro),
    finalizado_utc = as.character(row$finalizado_utc),
    rodada_id = as.character(row$rodada_id),
    origem = as.character(row$origem),
    stringsAsFactors = FALSE
  )
  df[is.na(df)] <- ""
  df$minutos <- as.numeric(row$minutos)
  df$tamanho_mb <- as.numeric(row$tamanho_mb)
  sf::st_sf(df, geometry = geom, crs = 4674)
}

upsert_gpkg <- function(gpkg, feature) {
  need_pkgs("sf")
  exists_layer <- FALSE
  if (file.exists(gpkg)) {
    con <- DBI::dbConnect(RSQLite::SQLite(), gpkg)
    tables <- DBI::dbListTables(con)
    if (LAYER_OUT %in% tables) {
      DBI::dbExecute(
        con,
        sprintf("DELETE FROM \"%s\" WHERE tile_id = ?", LAYER_OUT),
        params = list(as.character(feature$tile_id[[1]]))
      )
      exists_layer <- TRUE
    }
    DBI::dbDisconnect(con)
  }
  sf::st_write(feature, gpkg, layer = LAYER_OUT, append = exists_layer, quiet = TRUE)
  invisible(TRUE)
}

load_geom_env <- function(cache, layer) {
  g <- sf::st_read(cache, layer = layer, quiet = TRUE)
  env <- new.env(parent = emptyenv())
  geoms <- sf::st_geometry(g)
  for (col in c("NOMENC_2K", "NOMENC_5K", "NOMENC_10K")) {
    if (!col %in% names(g)) next
    ids <- toupper(trimws(as.character(g[[col]])))
    ok <- which(!is.na(ids) & nzchar(ids))
    for (i in ok) {
      if (exists(ids[[i]], envir = env, inherits = FALSE)) next
      gm <- geoms[i]
      if (as.character(sf::st_geometry_type(gm)) != "MULTIPOLYGON") {
        gm <- sf::st_cast(gm, "MULTIPOLYGON")
      }
      env[[ids[[i]]]] <- gm[[1]]
    }
  }
  env
}

rebuild_gpkg <- function(gpkg, registry, cache, layer) {
  need_pkgs("sf")
  if (!nrow(registry)) {
    if (file.exists(gpkg)) unlink(gpkg)
    return(invisible(FALSE))
  }
  env <- load_geom_env(cache, layer)
  sfc <- sf::st_sfc(lapply(registry$tile_id, function(id) {
    if (exists(id, envir = env, inherits = FALSE)) get(id, envir = env) else sf::st_multipolygon()
  }), crs = 4674)
  df <- as.data.frame(registry[, ..GPKG_COLS])
  for (col in setdiff(GPKG_COLS, c("minutos", "tamanho_mb"))) {
    df[[col]] <- as.character(df[[col]])
    df[[col]][is.na(df[[col]])] <- ""
  }
  df$minutos <- suppressWarnings(as.numeric(registry$minutos))
  df$tamanho_mb <- suppressWarnings(as.numeric(registry$tamanho_mb))
  fc <- sf::st_sf(df, geometry = sfc, crs = 4674)
  if (file.exists(gpkg)) {
    sf::st_write(fc, gpkg, layer = LAYER_OUT, delete_layer = TRUE, quiet = TRUE)
  } else {
    sf::st_write(fc, gpkg, layer = LAYER_OUT, quiet = TRUE)
  }
  invisible(TRUE)
}

# ---------------------------------------------------------------------------
# Relatorio
# ---------------------------------------------------------------------------

md_escape <- function(x) {
  x <- as.character(x)
  x[is.na(x)] <- ""
  x <- gsub("|", "/", x, fixed = TRUE)
  x <- gsub("[\r\n]+", " ", x)
  x
}

md_table <- function(dt, cols) {
  if (!nrow(dt)) return("_Nenhuma linha._")
  show <- dt[, ..cols]
  header <- paste0("| ", paste(cols, collapse = " | "), " |")
  sep <- paste0("| ", paste(rep("---", length(cols)), collapse = " | "), " |")
  body <- vapply(seq_len(nrow(show)), function(i) {
    vals <- md_escape(unlist(show[i], use.names = FALSE))
    paste0("| ", paste(vals, collapse = " | "), " |")
  }, character(1))
  c(header, sep, body)
}

fmt_num <- function(x, digits = 1) {
  x <- suppressWarnings(as.numeric(x))
  ifelse(is.na(x), "", format(round(x, digits), nsmall = digits, trim = TRUE))
}

# Sempre dias, horas e minutos, para a leitura ficar igual em todo tile.
format_dhm <- function(minutos) {
  minutos <- suppressWarnings(as.numeric(minutos))
  if (length(minutos) != 1L || is.na(minutos) || minutos < 0) {
    return("sem estimativa ainda")
  }
  total <- as.integer(round(minutos))
  dias <- total %/% (24L * 60L)
  resto <- total %% (24L * 60L)
  horas <- resto %/% 60L
  mins <- resto %% 60L
  pl <- function(n, um, varios) if (n == 1L) um else varios
  sprintf(
    "%d %s, %d %s e %d %s",
    dias, pl(dias, "dia", "dias"),
    horas, pl(horas, "hora", "horas"),
    mins, pl(mins, "minuto", "minutos")
  )
}

tempos_desta_maquina <- function(rodada, registry) {
  host <- maquina()
  desta <- numeric()
  ids <- character()
  if (!is.null(rodada) && nrow(rodada)) {
    ok <- rodada[status == "processed" & !is.na(minutos) & minutos >= 0]
    desta <- ok$minutos
    ids <- ok$tile_id
  }
  if (!is.null(registry) && nrow(registry) && "minutos" %in% names(registry)) {
    ant <- registry[
      status == "processed" &
        hostname == host &
        !is.na(minutos) &
        minutos >= 0 &
        !tile_id %in% ids
    ]
    desta <- c(desta, ant$minutos)
  }
  desta
}

eta_agora <- function(rodada, registry, n_faltam) {
  tempos <- tempos_desta_maquina(rodada, registry)
  n_faltam <- max(0L, as.integer(n_faltam))
  if (!length(tempos)) {
    return(list(
      n_base = 0L,
      media_min = NA_real_,
      restante_min = NA_real_,
      texto_media = "sem estimativa ainda",
      texto = "sem estimativa ainda (entra depois do primeiro tile ok nesta maquina)",
      previsao = ""
    ))
  }
  media <- mean(tempos)
  restante <- if (n_faltam < 1L) 0 else media * n_faltam
  previsao <- if (n_faltam < 1L) "" else format(Sys.time() + restante * 60, "%d/%m/%Y %H:%M")
  list(
    n_base = length(tempos),
    media_min = media,
    restante_min = restante,
    texto_media = format_dhm(media),
    texto = format_dhm(restante),
    previsao = previsao
  )
}

faltam_na_rodada <- function(plan, rodada) {
  n_go <- if (is.null(plan)) 0L else sum(plan$acao == "processar")
  n_feitos <- if (is.null(rodada) || !nrow(rodada)) 0L else nrow(rodada)
  max(0L, as.integer(n_go) - as.integer(n_feitos))
}

mostrar_eta <- function(rodada, registry, n_faltam, este_min = NA_real_) {
  eta <- eta_agora(rodada, registry, n_faltam)
  cat("\n")
  cat("------------------------------------------------------------\n")
  cat(" ESTIMATIVA NESTA MAQUINA (", maquina(), ")\n", sep = "")
  if (!is.na(este_min)) {
    cat(" Este arquivo: ", format_dhm(este_min), "\n", sep = "")
  }
  if (eta$n_base > 0L) {
    cat(" Media de ", eta$n_base, " tile(s) ok: ", eta$texto_media, "\n", sep = "")
    cat(" Faltam: ", n_faltam, "\n", sep = "")
    cat(" Tempo restante: ", eta$texto, "\n", sep = "")
    if (nzchar(eta$previsao)) {
      cat(" Previsao de termino: ", eta$previsao, "\n", sep = "")
    }
  } else {
    cat(" Faltam: ", n_faltam, "\n", sep = "")
    cat(" Ainda sem media. A previsao entra depois do primeiro tile que terminar ok.\n")
  }
  cat("------------------------------------------------------------\n\n")
  flush.console()
  eta
}

build_report <- function(opt, paths, registry, historico, plan, rodada, blocos, n_dup, modo, elapsed_min) {
  n_reg <- nrow(registry)
  n_ok <- if (n_reg) sum(registry$status == "processed") else 0L
  n_err <- if (n_reg) sum(registry$status == "erro") else 0L
  n_list <- if (is.null(plan)) 0L else nrow(plan)
  n_skip <- if (is.null(plan)) 0L else sum(plan$acao == "pular")
  n_go <- if (is.null(plan)) 0L else sum(plan$acao == "processar")
  n_fora <- if (is.null(plan)) 0L else sum(plan$acao == "fora_do_bloco")
  n_lim <- if (is.null(plan)) 0L else sum(plan$acao == "adiado_limit")
  n_sem <- if (is.null(plan)) 0L else sum(plan$acao != "fora_do_bloco" & !nzchar(plan$layer_artic))
  n_run_ok <- if (is.null(rodada) || !nrow(rodada)) 0L else sum(rodada$status == "processed")
  n_run_err <- if (is.null(rodada) || !nrow(rodada)) 0L else sum(rodada$status == "erro")
  filtro <- if (length(blocos)) paste(blocos, collapse = ", ") else "(pasta inteira, sem filtro de BLOCO)"
  titulo_modo <- switch(
    modo,
    dry = "PLANO (dry-run, nada foi processado)",
    lista = "LISTA DE BLOCOS (nada foi processado)",
    report = "RELATORIO (sem reprocessar)",
    parado = "PARCIAL (STOP.txt)",
    if (n_go == 0L || (n_run_ok + n_run_err) >= n_go) "RODADA ENCERRADA" else "PARCIAL"
  )

  linhas <- c(
    "# Relatorio � GeoTrees ALS por tile",
    "",
    sprintf("**Status:** %s  ", titulo_modo),
    sprintf("**Atualizado (UTC):** %s  ", utc_now()),
    sprintf("**Script:** `batch_geotrees_als_lastools.R` %s  ", VERSION),
    sprintf("**Maquina:** `%s` (%s)  ", maquina(), usuario()),
    sprintf("**Pasta LAZ:** `%s`  ", opt$laz_dir),
    sprintf("**Filtro BLOCO:** %s  ", filtro),
    sprintf("**Saida:** `%s`  ", paths$root),
    sprintf("**GPKG do que ja foi:** `%s` (camada `%s`)  ", paths$gpkg, LAYER_OUT),
    sprintf("**Produtos GeoTrees:** `%s/<utm22|23>/<tile>/`  ", paths$processed),
    ""
  )
  n_faltam <- faltam_na_rodada(plan, rodada)
  eta <- eta_agora(rodada, registry, n_faltam)
  if (!modo %in% c("lista")) {
    linhas <- c(
      linhas,
      "## Estimativa nesta maquina",
      "",
      sprintf("- Tiles ok na media: **%d**", eta$n_base),
      sprintf("- Tempo medio por tile: %s", eta$texto_media),
      sprintf("- Ainda na fila desta selecao: **%d**", n_faltam),
      sprintf("- Tempo restante: **%s**", eta$texto),
      if (nzchar(eta$previsao)) sprintf("- Previsao de termino: **%s**", eta$previsao) else NULL,
      "",
      "A media usa os tiles que esta maquina ja concluiu, nesta saida. Cada arquivo novo recalcula o restante.",
      "Um tile cortado no meio nao entra na media e, na proxima rodada, comeca de novo.",
      ""
    )
  }
  if (modo == "dry") {
    linhas <- c(linhas, "> Dry-run: nao processei LAZ e nao alterei o registro.", "")
  }
  if (modo == "report") {
    linhas <- c(linhas, "> `--report-only`: li o que ja estava no disco e regenerei CSV, GPKG e este .md.", "")
  }

  linhas <- c(
    linhas,
    "## Acumulado nesta saida",
    "",
    "Este acumulado e o registro de `OUT_DIR`. No padrao `D:\\geotrees_als` ele e desta maquina.",
    "Se varios computadores usarem a mesma `OUT_DIR` (disco compartilhado), o acumulado junta todos.",
    "",
    sprintf("- Tiles no registro: **%d**", n_reg),
    sprintf("- Processados: **%d**", n_ok),
    sprintf("- Com erro (a proxima rodada tenta de novo): **%d**", n_err),
    ""
  )

  if (n_reg) {
    por_bloco <- registry[, .(
      processados = sum(status == "processed"),
      erros = sum(status == "erro"),
      tiles = .N
    ), by = bloco]
    por_bloco[!nzchar(bloco), bloco := "(sem ficha)"]
    data.table::setorder(por_bloco, -processados, bloco)
    linhas <- c(linhas, "### Por BLOCO (registro)", "", md_table(por_bloco, c("bloco", "processados", "erros", "tiles")), "")

    por_host <- registry[, .(
      processados = sum(status == "processed"),
      erros = sum(status == "erro")
    ), by = hostname]
    data.table::setorder(por_host, -processados)
    linhas <- c(linhas, "### Por maquina (registro)", "", md_table(por_host, c("hostname", "processados", "erros")), "")
  }

  if (!is.null(historico) && nrow(historico) && "hostname" %in% names(historico)) {
    tent <- historico[, .N, by = .(hostname, status)]
    data.table::setorder(tent, hostname, status)
    linhas <- c(
      linhas,
      "### Tentativas (historico, todas as rodadas)",
      "",
      md_table(tent, c("hostname", "status", "N")),
      ""
    )
  }

  linhas <- c(linhas, "## Esta pasta de LAZ", "")
  if (is.null(plan)) {
    linhas <- c(linhas, "_Pasta nao foi varrida nesta chamada._", "")
  } else if (!nrow(plan)) {
    linhas <- c(linhas, "_Nenhum .laz/.las encontrado._", "")
  } else {
    linhas <- c(
      linhas,
      sprintf("- Arquivos unicos: **%d**", n_list),
      sprintf("- Ja processados aqui (pulados): **%d**", n_skip),
      sprintf("- Ainda nao processados: **%d** (nesta rodada: %d, alem do --limit: %d)", n_go + n_lim, n_go, n_lim),
      sprintf("- Fora do filtro de BLOCO: **%d**", n_fora),
      sprintf("- Sem ficha na articulacao (dentro do filtro): **%d**", n_sem),
      sprintf("- Segurados pelo --limit: **%d**", n_lim),
      if (n_dup > 0L) sprintf("- Nomes repetidos (ficou o arquivo maior): **%d**", n_dup) else NULL,
      ""
    )
    cobertura <- plan[, .(
      na_pasta = .N,
      ja_feitos = sum(acao == "pular"),
      na_fila = sum(acao %in% c("processar", "adiado_limit")),
      fora_do_filtro = sum(acao == "fora_do_bloco")
    ), by = bloco]
    cobertura[!nzchar(bloco), bloco := "(sem ficha)"]
    data.table::setorder(cobertura, -na_pasta, bloco)
    linhas <- c(linhas, "### Cobertura por BLOCO nesta pasta", "", md_table(cobertura, names(cobertura)), "")
  }

  linhas <- c(
    linhas,
    "## Esta rodada",
    "",
    sprintf("- Modo: `%s`", modo),
    sprintf("- Ok agora: **%d**", n_run_ok),
    sprintf("- Erro agora: **%d**", n_run_err),
    sprintf("- Duracao da rodada (min): %s", fmt_num(elapsed_min, 1)),
    sprintf("- Resolucao %s m | tile %s m | buffer %s m | cores %s | blast2dem %s",
            opt$resolution, opt$size_tile, opt$size_buffer, opt$n_cores, opt$use_blast2dem),
    ""
  )

  if (!is.null(rodada) && nrow(rodada)) {
    show <- data.table::copy(rodada)
    show[, minutos := fmt_num(minutos, 1)]
    cols <- intersect(c("tile_id", "bloco", "status", "minutos", "erro"), names(show))
    linhas <- c(linhas, "### Tiles desta rodada", "", md_table(show, cols), "")
  }

  if (n_err) {
    erros <- registry[status == "erro"]
    data.table::setorder(erros, tile_id)
    if (nrow(erros) > 40L) erros <- erros[1:40]
    erros[, minutos := fmt_num(minutos, 1)]
    linhas <- c(
      linhas,
      "## Erros no registro (a proxima rodada tenta de novo)",
      "",
      md_table(erros, c("tile_id", "bloco", "hostname", "minutos", "erro")),
      ""
    )
  }

  if (n_ok) {
    ultimos <- registry[status == "processed"]
    data.table::setorder(ultimos, -finalizado_utc)
    ultimos <- head(ultimos, 15L)
    linhas <- c(
      linhas,
      "## Ultimos processados",
      "",
      md_table(ultimos, c("tile_id", "bloco", "hostname", "finalizado_utc", "pasta_saida")),
      ""
    )
  }

  linhas <- c(
    linhas,
    "## Como retomar",
    "",
    "- Rode o mesmo .bat de novo. Tile com `_INFO_summary_processing.csv` e sem `_ERROR.txt` nao e refeito.",
    "- Erro entra de novo na fila. `--force` refaz tambem o que ja deu certo.",
    "- `--report-only` so atualiza este .md, o CSV e o GPKG.",
    "- Para parar no fim do tile atual, crie `STOP.txt` em OUT_DIR (o script apaga o arquivo ao encerrar).",
    "- A articulacao IGC nao e modificada. O poligono do tile e copiado para o GPKG de registro.",
    "",
    "Produtos do pipeline (Fischer et al. 2024, Methods in Ecology and Evolution): DTM, DSM, CHM (`chm_highest`, `chm_tin`, `chm_lspikefree`), densidade de pulso, angulo de varredura e os `_INFO_`.",
    ""
  )
  linhas
}

# ---------------------------------------------------------------------------
# Processar um tile
# ---------------------------------------------------------------------------

inbox_for <- function(laz_dir, out_dir, inbox_padrao) {
  d1 <- drive_of(laz_dir)
  d2 <- drive_of(out_dir)
  if (!is.na(d1) && !is.na(d2) && !identical(toupper(d1), toupper(d2))) {
    say(
      "LAZ em ", toupper(d1), " e saida em ", toupper(d2),
      ". O arquivo entra na saida por copia, porque hardlink nao cruza disco."
    )
  }
  inbox_padrao
}

link_into_inbox <- function(src, inbox, type_file) {
  dir.create(inbox, recursive = TRUE, showWarnings = FALSE)
  old <- list.files(inbox, full.names = TRUE, all.files = TRUE, no.. = TRUE)
  if (length(old)) unlink(old, recursive = TRUE, force = TRUE)
  stem <- tools::file_path_sans_ext(basename(src))
  dest <- file.path(inbox, paste0(stem, ".", type_file))
  ok <- suppressWarnings(file.link(src, dest))
  if (isTRUE(ok) && file.exists(dest)) {
    say_debug("Atalho do LAZ: ", dest)
    return(dest)
  }
  if (file.exists(dest)) unlink(dest, force = TRUE)
  say_debug("Copiando o LAZ para a saida: ", src)
  ok <- file.copy(src, dest, overwrite = TRUE, copy.date = TRUE)
  if (!isTRUE(ok) || !file.exists(dest)) {
    stop(
      "Nao copiei\n  ", src, "\npara\n  ", dest,
      call. = FALSE
    )
  }
  say_debug("Copia pronta: ", dest)
  dest
}

wipe_dir_contents <- function(path) {
  if (!dir.exists(path)) return(invisible())
  old <- list.files(path, full.names = TRUE, all.files = TRUE, no.. = TRUE)
  if (length(old)) unlink(old, recursive = TRUE, force = TRUE)
}

kill_stray_lastools <- function() {
  if (.Platform$OS.type != "windows") return(invisible())
  exes <- c(
    "lastile", "las2las", "lasduplicate", "lassort", "lasnoise", "lasground_new",
    "lasheight", "lasgrid", "blast2dem", "lasthin", "lasboundary", "lasclip",
    "las2txt", "lasinfo", "las2dem", "lascanopy", "lasoptimize", "lascopy",
    "lasindex", "lasmerge", "lassplit", "lasvoxel"
  )
  for (e in exes) {
    # Mute "ERROR: The process ... not found" when nothing is running.
    suppressWarnings(try(
      system2("taskkill", c("/F", "/IM", paste0(e, ".exe"), "/T"),
              stdout = FALSE, stderr = FALSE),
      silent = TRUE
    ))
  }
  invisible()
}

promote_tile_dir <- function(from_dir, to_dir) {
  if (!dir.exists(from_dir)) return(FALSE)
  parent <- dirname(to_dir)
  dir.create(parent, recursive = TRUE, showWarnings = FALSE)
  if (dir.exists(to_dir)) unlink(to_dir, recursive = TRUE, force = TRUE)
  ok <- suppressWarnings(file.rename(from_dir, to_dir))
  if (isTRUE(ok) && dir.exists(to_dir)) return(TRUE)
  dir.create(to_dir, recursive = TRUE, showWarnings = FALSE)
  copied <- FALSE
  tryCatch({
    file.copy(from_dir, parent, recursive = TRUE, overwrite = TRUE)
    staged_name <- file.path(parent, basename(from_dir))
    if (!identical(normalizePath(staged_name, winslash = "/", mustWork = FALSE),
                   normalizePath(to_dir, winslash = "/", mustWork = FALSE))) {
      if (dir.exists(to_dir)) unlink(to_dir, recursive = TRUE, force = TRUE)
      file.rename(staged_name, to_dir)
    }
    copied <- dir.exists(to_dir)
  }, error = function(e) NULL)
  unlink(from_dir, recursive = TRUE, force = TRUE)
  isTRUE(copied)
}

tile_success <- function(out_tile) {
  if (!nzchar(out_tile) || !dir.exists(out_tile)) {
    return(list(ok = FALSE, erro = "pasta de saida ausente"))
  }
  info <- list.files(out_tile, pattern = "_INFO_summary_processing\\.csv$", recursive = TRUE)
  err <- list.files(out_tile, pattern = "^_ERROR\\.txt$", recursive = TRUE, full.names = TRUE)
  if (length(err)) {
    msg <- clip_err(paste(readLines(err[[1]], warn = FALSE, n = 40), collapse = " "), 1200)
    return(list(ok = FALSE, erro = if (nzchar(msg)) msg else "pipeline gravou _ERROR.txt"))
  }
  if (!length(info)) {
    return(list(ok = FALSE, erro = "pipeline terminou sem _INFO_summary_processing.csv"))
  }
  list(ok = TRUE, erro = "")
}

issue_text <- function(logfile) {
  if (!nzchar(logfile) || !file.exists(logfile)) return("")
  dt <- tryCatch(
    data.table::fread(logfile, showProgress = FALSE, fill = TRUE),
    error = function(e) NULL
  )
  if (is.null(dt) || !nrow(dt)) return("")
  col <- if ("issue" %in% names(dt)) "issue" else names(dt)[ncol(dt)]
  vals <- as.character(dt[[col]])
  vals <- vals[!is.na(vals) & nzchar(vals)]
  if (!length(vals)) return("")
  txt <- paste(unique(vals), collapse = " | ")
  txt <- gsub("[\r\n\t]+", " ", txt)
  txt <- trimws(as.character(txt))
  if (!nzchar(txt)) return("")
  clip_err(txt, 1200)
}

# Windows: NUNCA faca `cmd /c <linha-do-LAStools>` direto.
# O helper ja coloca aspas em cada path (file.path.system), inclusive em "*.laz".
# Com varias aspas, o cmd.exe mangleia a linha (regra /C de quotes) -> LAStools
# nao acha input -> outlines vazios -> "subscript out of bounds" no clustering.
# Solucao: gravar a linha intacta num .bat temporario e rodar esse .bat (redirect
# >nul fica dentro do bat; CreateProcess sozinho ignora >nul).
quiet_system_cmd <- function(command, intern = FALSE) {
  if (isTRUE(intern)) {
    return(list(cmd = as.character(command[[1]]), bat = NULL, via = "raw"))
  }
  cmd <- as.character(command[[1]])
  if (.Platform$OS.type == "windows") {
    # Nao embutir >nul na mesma linha do LAStools: aspas + redirect no .bat
    # ainda quebram o glob "*.laz" em alguns hosts. O silenciamento fica no
    # system2(stdout/stderr=FALSE); o .bat so carrega a linha intacta.
    bat <- tempfile(pattern = "geotrees_quiet_", fileext = ".bat")
    writeLines(
      c(
        "@echo off",
        "setlocal EnableExtensions DisableDelayedExpansion",
        cmd
      ),
      bat,
      useBytes = TRUE
    )
    return(list(cmd = cmd, bat = bat, via = "bat"))
  }
  if (!grepl(">/dev/null", cmd, fixed = TRUE)) {
    cmd <- paste(cmd, ">/dev/null 2>&1")
  }
  list(cmd = cmd, bat = NULL, via = "unix")
}

install_quiet_system <- function(debug = FALSE) {
  options(geotrees.debug = isTRUE(debug))
  if (isTRUE(debug)) {
    if (exists("system", envir = globalenv(), inherits = FALSE)) {
      rm("system", envir = globalenv())
    }
    if (exists("system2", envir = globalenv(), inherits = FALSE)) {
      rm("system2", envir = globalenv())
    }
    return(invisible(FALSE))
  }
  # Override system()/system2() used by the official helper so LAStools spam
  # never hits the console. Windows: .bat com a linha intacta + system2 mudo.
  assign("system", function(command, intern = FALSE, wait = TRUE, input = NULL,
                            show.output.on.console = TRUE, minimized = FALSE,
                            invisible = TRUE, timeout = 0, ...) {
    wrapped <- quiet_system_cmd(command, intern = intern)
    on.exit({
      if (!is.null(wrapped$bat) && file.exists(wrapped$bat)) {
        try(unlink(wrapped$bat), silent = TRUE)
      }
    }, add = TRUE)
    if (.Platform$OS.type == "windows" && identical(wrapped$via, "bat") && !isTRUE(intern)) {
      # system2 evita o mangling de aspas do system("cmd /c ...").
      rc <- base::system2(
        "cmd.exe",
        c("/d", "/c", wrapped$bat),
        stdout = FALSE,
        stderr = FALSE,
        wait = wait,
        timeout = if (is.null(timeout) || !is.finite(timeout)) 0 else timeout
      )
      return(rc)
    }
    if (.Platform$OS.type == "windows") {
      return(base::system(
        wrapped$cmd,
        intern = intern,
        wait = wait,
        input = input,
        show.output.on.console = FALSE,
        minimized = minimized,
        invisible = invisible,
        timeout = timeout,
        ...
      ))
    }
    base::system(
      wrapped$cmd, intern = intern, wait = wait, input = input,
      timeout = timeout, ...
    )
  }, envir = globalenv())
  assign("system2", function(command, args = character(),
                             stdout = "", stderr = "", ...) {
    if (is.null(stdout) || identical(stdout, "")) stdout <- FALSE
    if (is.null(stderr) || identical(stderr, "")) stderr <- FALSE
    base::system2(command, args = args, stdout = stdout, stderr = stderr, ...)
  }, envir = globalenv())
  invisible(TRUE)
}

with_pipeline_output <- function(expr, log_file, debug = FALSE) {
  if (isTRUE(debug)) return(force(expr))
  dir.create(dirname(log_file), recursive = TRUE, showWarnings = FALSE)
  con <- file(log_file, open = "wt", encoding = "UTF-8")
  sink(con, type = "output")
  sink(con, type = "message")
  on.exit({
    try(sink(type = "message"), silent = TRUE)
    try(sink(type = "output"), silent = TRUE)
    try(close(con), silent = TRUE)
  }, add = TRUE)
  force(expr)
}

progress_bar <- function(i, n, label = "", width = 28L) {
  if (n <= 0) n <- 1L
  i <- max(0L, min(as.integer(i), as.integer(n)))
  pct <- i / n
  filled <- as.integer(round(pct * width))
  bar <- paste0(
    strrep("=", filled),
    if (filled < width) ">" else "",
    strrep(" ", max(0L, width - filled - 1L))
  )
  msg <- sprintf("\r[%s] %3.0f%%  %d/%d  %s", bar, 100 * pct, i, n, label)
  cat(msg)
  flush.console()
  invisible()
}

progress_done <- function() {
  cat("\n")
  flush.console()
}

source_helper <- function(path, debug = FALSE) {
  if (!file.exists(path)) {
    stop("Nao achei o pipeline oficial: ", path, call. = FALSE)
  }
  say("Carregando o pipeline oficial do GeoTrees.")
  say_debug("  ", path)
  suppressPackageStartupMessages(sys.source(path, envir = globalenv()))
  if (!exists("process.dataset", mode = "function", envir = globalenv())) {
    stop("process.dataset nao apareceu depois de carregar o helper.", call. = FALSE)
  }
  install_quiet_system(debug = debug)
  say("Pipeline GeoTrees carregado.", if (isTRUE(debug)) " (DEBUG=on)" else " (saida quieta; use --debug para verbose)")
}

# ---------------------------------------------------------------------------
# NAS: copia async OUT_DIR/processed/<utm>/<tile> -> NAS/<layer>/<BLOCO>/<tile>
# ---------------------------------------------------------------------------

nas_layer_folder <- function(layer_artic) {
  key <- tolower(trimws(as.character(layer_artic)))
  if (!nzchar(key)) return("")
  if (grepl("fehidro", key, fixed = TRUE)) return("Fehidro")
  if (grepl("lote0?4|lote4", key)) return("Lote04")
  if (grepl("voo22", key, fixed = TRUE)) return("Voo22")
  ""
}

nas_products_ok <- function(pasta) {
  pasta <- as.character(pasta)
  if (!nzchar(pasta) || !dir.exists(pasta)) return(FALSE)
  info <- list.files(pasta, pattern = "_INFO_summary_processing\\.csv$", recursive = TRUE)
  err <- list.files(pasta, pattern = "^_ERROR\\.txt$", recursive = TRUE)
  length(info) > 0L && length(err) == 0L
}

nas_dest_path <- function(nas_root, layer_artic, bloco, tile_id) {
  layer_dir <- nas_layer_folder(layer_artic)
  if (!nzchar(layer_dir) || !nzchar(nas_root)) return("")
  bl <- trimws(as.character(bloco))
  if (!nzchar(bl) || is.na(bl)) bl <- "_SEM_BLOCO"
  file.path(nas_root, layer_dir, bl, toupper(as.character(tile_id)))
}

nas_copy_tree <- function(src, dest) {
  src <- normalizePath(src, winslash = "/", mustWork = TRUE)
  dest <- gsub("\\\\", "/", dest)
  dir.create(dest, recursive = TRUE, showWarnings = FALSE)
  if (.Platform$OS.type == "windows") {
    src_w <- normalizePath(src, winslash = "\\")
    dest_w <- chartr("/", "\\", dest)
    rc <- suppressWarnings(system2(
      "robocopy",
      c(src_w, dest_w, "/E", "/R:2", "/W:5", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"),
      stdout = FALSE,
      stderr = FALSE
    ))
    # robocopy: 0-7 = sucesso parcial/total; >=8 = falha
    ok <- !is.na(rc) && rc < 8L
  } else {
    files <- list.files(src, all.files = TRUE, full.names = TRUE, no.. = TRUE)
    ok <- TRUE
    for (f in files) {
      if (!file.copy(f, dest, recursive = TRUE, overwrite = TRUE)) ok <- FALSE
    }
  }
  isTRUE(ok) && nas_products_ok(dest)
}

append_nas_log <- function(path, row) {
  dt <- data.table::as.data.table(row)
  if (file.exists(path)) {
    data.table::fwrite(dt, path, append = TRUE, col.names = FALSE)
  } else {
    data.table::fwrite(dt, path)
  }
}

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1L && is.na(a))) b else a

resolve_laz_path <- function(tile_id, laz_dir, hint = "") {
  hint <- as.character(hint %||% "")
  if (nzchar(hint)) {
    hp <- suppressWarnings(normalizePath(hint, winslash = "/", mustWork = FALSE))
    if (nzchar(hp) && file.exists(hp)) return(hp)
  }
  if (!nzchar(laz_dir) || !dir.exists(laz_dir)) return("")
  tid <- toupper(trimws(as.character(tile_id %||% "")))
  if (!nzchar(tid)) return("")
  for (ext in c(".laz", ".las")) {
    p <- file.path(laz_dir, paste0(tid, ext))
    if (file.exists(p)) {
      return(suppressWarnings(normalizePath(p, winslash = "/", mustWork = FALSE)))
    }
  }
  # LAZ_DIR pode ter subpastas (ex. D:/2024/23/...).
  esc <- gsub("([.|()\\^{}+$*?]|\\[|\\])", "\\\\\\1", tid)
  hits <- list.files(
    laz_dir,
    pattern = paste0("^", esc, "\\.(laz|las)$"),
    recursive = TRUE,
    full.names = TRUE,
    ignore.case = TRUE
  )
  if (!length(hits)) return("")
  suppressWarnings(normalizePath(hits[[1]], winslash = "/", mustWork = FALSE))
}

maybe_delete_laz <- function(laz_path, laz_dir, enabled) {
  if (!isTRUE(enabled)) return(invisible(FALSE))
  lp <- suppressWarnings(normalizePath(laz_path, winslash = "/", mustWork = FALSE))
  ld <- suppressWarnings(normalizePath(laz_dir, winslash = "/", mustWork = FALSE))
  if (!nzchar(lp) || !file.exists(lp)) return(invisible(FALSE))
  under <- startsWith(tolower(lp), tolower(paste0(ld, "/"))) ||
    identical(tolower(dirname(lp)), tolower(ld))
  if (!under) {
    say("AVISO: LAZ fora de LAZ_DIR; nao apaguei: ", lp)
    return(invisible(FALSE))
  }
  ok <- FALSE
  try(ok <- isTRUE(file.remove(lp)) && !file.exists(lp), silent = TRUE)
  if (!ok) {
    try(unlink(lp, force = TRUE), silent = TRUE)
    ok <- !file.exists(lp)
  }
  # Sidecar index LAStools, se existir
  lax <- sub("\\.(laz|las)$", ".lax", lp, ignore.case = TRUE)
  if (nzchar(lax) && file.exists(lax)) try(unlink(lax, force = TRUE), silent = TRUE)
  if (ok) say("LAZ removido (tile ok / status 1): ", basename(lp))
  else say("AVISO: falha ao apagar LAZ: ", lp)
  invisible(ok)
}

# Apaga .laz de tiles ja processados (produtos OK em processed/) que ainda
# estao em LAZ_DIR � catch-up de rodadas anteriores com DELETE_LAZ_ON_OK.
laz_catchup <- function(opt, paths, registry, plan = NULL) {
  if (!isTRUE(opt$delete_laz_on_ok)) return(invisible(0L))
  if (!dir.exists(opt$laz_dir)) return(invisible(0L))

  map <- new.env(parent = emptyenv())
  add_map <- function(tid, lp) {
    tid <- toupper(trimws(as.character(tid %||% "")))
    lp <- as.character(lp %||% "")
    if (!nzchar(tid) || !nzchar(lp)) return(invisible(NULL))
    if (!exists(tid, envir = map, inherits = FALSE)) assign(tid, lp, envir = map)
    invisible(NULL)
  }

  if (!is.null(registry) && nrow(registry)) {
    for (i in seq_len(nrow(registry))) {
      if (!identical(as.character(registry$status[[i]]), "processed")) next
      add_map(registry$tile_id[[i]], registry$laz_path[[i]])
    }
  }
  if (!is.null(plan) && nrow(plan)) {
    skip <- plan[acao == "pular"]
    if (nrow(skip)) {
      for (i in seq_len(nrow(skip))) add_map(skip$tile_id[[i]], skip$laz_path[[i]])
    }
  }

  tiles <- list_tile_dirs(paths$processed)
  if (!length(tiles)) {
    say("LAZ catch-up: nenhuma pasta processada ainda.")
    return(invisible(0L))
  }
  say("LAZ catch-up: conferindo ", length(tiles), " pasta(s) em processed/...")
  n_del <- 0L
  n_miss <- 0L
  for (k in tiles) {
    if (!nas_products_ok(k)) next
    tid <- toupper(basename(k))
    hint <- if (exists(tid, envir = map, inherits = FALSE)) {
      get(tid, envir = map, inherits = FALSE)
    } else {
      ""
    }
    lp <- resolve_laz_path(tid, opt$laz_dir, hint)
    if (!nzchar(lp) || !file.exists(lp)) {
      n_miss <- n_miss + 1L
      next
    }
    if (maybe_delete_laz(lp, opt$laz_dir, TRUE)) n_del <- n_del + 1L
  }
  say(
    "LAZ catch-up: ", n_del, " arquivo(s) removido(s)",
    if (n_miss > 0L) paste0("; ", n_miss, " tile(s) ok sem .laz restante em LAZ_DIR") else "",
    "."
  )
  invisible(n_del)
}

nas_init <- function(opt) {
  env <- new.env(parent = emptyenv())
  env$enabled <- nzchar(opt$nas_dir)
  env$root <- opt$nas_dir
  env$delete_local <- isTRUE(opt$nas_delete_local)
  env$jobs <- list()
  env$done <- FALSE
  # Copia em background via robocopy wait=FALSE (nao usa future::plan �
  # multisession global competia com o pipeline / lidR).
  if (env$enabled) {
    if (!dir.exists(env$root)) {
      say("AVISO NAS: pasta nao acessivel agora: ", env$root, " (copias ficam para quando montar)")
    } else {
      say("NAS: ", env$root, " | apagar local apos copia: ", env$delete_local)
    }
  }
  env
}

nas_finish_one <- function(res, paths, say_fn = say) {
  if (is.null(res) || !is.list(res)) return(invisible(FALSE))
  ok <- isTRUE(res$ok)
  append_nas_log(paths$nas_log, list(
    tile_id = res$tile_id,
    status = if (ok) "ok" else "erro",
    src = res$src,
    dest = res$dest,
    utc = utc_now(),
    erro = if (ok) "" else as.character(res$erro %||% "copia falhou")
  ))
  if (ok) {
    say_fn("NAS ok: ", res$tile_id, " -> ", res$dest)
    if (isTRUE(res$delete_local) && nzchar(res$src) && dir.exists(res$src)) {
      unlink(res$src, recursive = TRUE, force = TRUE)
      say_fn("NAS: pasta local removida apos copia: ", res$src)
    }
  } else {
    say_fn("AVISO NAS falhou ", res$tile_id, ": ", res$erro %||% "erro")
  }
  invisible(ok)
}

nas_queue_append <- function(path, row) {
  dt <- data.table::as.data.table(row)
  if (file.exists(path)) {
    data.table::fwrite(dt, path, append = TRUE, col.names = FALSE)
  } else {
    data.table::fwrite(dt, path)
  }
}

nas_launch_robocopy <- function(src, dest) {
  src_w <- normalizePath(src, winslash = "\\", mustWork = TRUE)
  dest_w <- chartr("/", "\\", gsub("\\\\", "/", dest))
  dir.create(dest, recursive = TRUE, showWarnings = FALSE)
  # Start-Process retorna na hora; robocopy segue em background (mais confiavel
  # que system2(wait=FALSE) em alguns R/Windows).
  arglist <- paste(
    sapply(
      c(src_w, dest_w, "/E", "/R:2", "/W:5", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"),
      function(a) paste0("'", gsub("'", "''", a), "'")
    ),
    collapse = ","
  )
  ps <- paste0(
    "Start-Process -FilePath robocopy -ArgumentList @(", arglist,
    ") -WindowStyle Hidden"
  )
  rc <- tryCatch(
    base::system2(
      "powershell",
      c("-NoProfile", "-Command", ps),
      stdout = FALSE,
      stderr = FALSE,
      wait = TRUE
    ),
    error = function(e) 1L
  )
  isTRUE(is.null(rc) || (!is.na(rc) && rc == 0L))
}

nas_reap <- function(nas, paths, wait = FALSE, timeout_min = 120) {
  if (!isTRUE(nas$enabled) || !length(nas$jobs)) return(invisible(NULL))
  keep <- list()
  for (job in nas$jobs) {
    ok_now <- nas_products_ok(job$dest)
    elapsed <- as.numeric(difftime(Sys.time(), job$t0, units = "mins"))
    if (is.na(elapsed)) elapsed <- 0
    if (!ok_now && isTRUE(wait)) {
      while (!nas_products_ok(job$dest) &&
             as.numeric(difftime(Sys.time(), job$t0, units = "mins")) < timeout_min) {
        Sys.sleep(2)
      }
      ok_now <- nas_products_ok(job$dest)
      elapsed <- as.numeric(difftime(Sys.time(), job$t0, units = "mins"))
    }
    timed_out <- !ok_now && elapsed >= timeout_min
    if (!ok_now && !timed_out) {
      keep[[length(keep) + 1L]] <- job
      next
    }
    nas_finish_one(
      list(
        ok = ok_now, tile_id = job$tile_id, src = job$src, dest = job$dest,
        delete_local = job$delete_local,
        erro = if (ok_now) "" else paste0(
          "copia NAS incompleta apos ", round(elapsed, 1), " min"
        )
      ),
      paths
    )
  }
  nas$jobs <- keep
  invisible(NULL)
}

nas_enqueue <- function(nas, paths, src, layer_artic, bloco, tile_id) {
  if (!isTRUE(nas$enabled)) return(invisible(FALSE))
  if (!nzchar(src) || !dir.exists(src) || !nas_products_ok(src)) {
    say("AVISO NAS: origem sem produto final, pulando ", tile_id)
    return(invisible(FALSE))
  }
  dest <- nas_dest_path(nas$root, layer_artic, bloco, tile_id)
  if (!nzchar(dest)) {
    say("AVISO NAS: layer desconhecido (", layer_artic, ") para ", tile_id, " � nao copiado.")
    return(invisible(FALSE))
  }
  if (nas_products_ok(dest)) {
    say("NAS ja tem produto: ", dest)
    if (isTRUE(nas$delete_local)) {
      unlink(src, recursive = TRUE, force = TRUE)
      say("NAS: pasta local removida (ja estava no NAS): ", src)
    }
    return(invisible(TRUE))
  }
  if (!dir.exists(nas$root)) {
    say("AVISO NAS offline; fica para o proximo run: ", tile_id)
    return(invisible(FALSE))
  }
  say("NAS copiando em paralelo: ", tile_id, " -> ", dest)
  delete_local <- isTRUE(nas$delete_local)
  src_n <- src
  dest_n <- dest
  tid <- toupper(as.character(tile_id))
  nas_queue_append(paths$nas_queue, list(
    tile_id = tid,
    src = src_n,
    dest = dest_n,
    layer = as.character(layer_artic %||% ""),
    bloco = as.character(bloco %||% ""),
    delete_local = as.character(delete_local),
    status = "pending",
    enqueued_utc = utc_now(),
    erro = ""
  ))
  launched <- FALSE
  if (.Platform$OS.type == "windows") {
    launched <- isTRUE(tryCatch(
      nas_launch_robocopy(src_n, dest_n),
      error = function(e) {
        say("AVISO NAS: falha ao iniciar robocopy: ", conditionMessage(e))
        FALSE
      }
    ))
  }
  if (isTRUE(launched)) {
    nas$jobs[[length(nas$jobs) + 1L]] <- list(
      tile_id = tid, src = src_n, dest = dest_n,
      delete_local = delete_local, t0 = Sys.time()
    )
  } else {
    say("NAS: fallback copia sincrona para ", tid)
    ok <- tryCatch(nas_copy_tree(src_n, dest_n), error = function(e) FALSE)
    nas_finish_one(
      list(
        ok = isTRUE(ok), tile_id = tid, src = src_n, dest = dest_n,
        delete_local = delete_local,
        erro = if (isTRUE(ok)) "" else "copia sincrona falhou"
      ),
      paths
    )
  }
  invisible(TRUE)
}

nas_shutdown <- function(nas, paths, wait = FALSE) {
  if (!isTRUE(nas$enabled) || isTRUE(nas$done)) return(invisible(NULL))
  n_job <- length(nas$jobs)
  if (n_job && isTRUE(wait)) {
    say("NAS: aguardando ", n_job, " copia(s) em andamento...")
    nas_reap(nas, paths, wait = TRUE)
  } else if (n_job) {
    # Nao bloqueia o fim do batch: copias seguem em background; proximo run faz catch-up.
    say(
      "NAS: ", n_job, " copia(s) ainda em andamento (nao vou esperar).",
      " Proximo run faz catch-up se faltar."
    )
    nas_reap(nas, paths, wait = FALSE)
  }
  nas$done <- TRUE
  invisible(NULL)
}

nas_catchup <- function(nas, paths, registry, artic_dt = NULL) {
  if (!isTRUE(nas$enabled)) return(invisible(0L))
  if (!dir.exists(nas$root)) {
    say("NAS catch-up adiado: ", nas$root, " inacessivel.")
    return(invisible(0L))
  }
  tiles <- list_tile_dirs(paths$processed)
  if (!length(tiles)) return(invisible(0L))
  say("NAS catch-up: conferindo ", length(tiles), " pasta(s) em processed/...")
  n_q <- 0L
  for (k in tiles) {
    if (!nas_products_ok(k)) next
    tid <- toupper(basename(k))
    bloco <- ""
    layer <- ""
    if (!is.null(registry) && nrow(registry)) {
      hit <- which(toupper(as.character(registry$tile_id)) == tid)
      if (length(hit)) {
        bloco <- as.character(registry$bloco[[hit[[1]]]] %||% "")
        layer <- as.character(registry$layer_artic[[hit[[1]]]] %||% "")
      }
    }
    if ((!nzchar(bloco) || !nzchar(layer)) && !is.null(artic_dt) && nrow(artic_dt)) {
      ah <- which(toupper(as.character(artic_dt$tile_id)) == tid)
      if (length(ah)) {
        if (!nzchar(bloco)) bloco <- as.character(artic_dt$bloco[[ah[[1]]]] %||% "")
        if (!nzchar(layer)) layer <- as.character(artic_dt$layer_artic[[ah[[1]]]] %||% "")
      }
    }
    dest <- nas_dest_path(nas$root, layer, bloco, tid)
    if (!nzchar(dest)) next
    if (nas_products_ok(dest)) next
    nas_enqueue(nas, paths, k, layer, bloco, tid)
    n_q <- n_q + 1L
  }
  say("NAS catch-up: ", n_q, " pasta(s) enfileirada(s) para copia.")
  invisible(n_q)
}

run_one <- function(src, opt, paths, inbox, metadata, epsg_proc = NA_integer_) {
  ext <- tolower(tools::file_ext(src))
  type_file <- if (ext %in% c("laz", "las")) ext else "laz"
  say("  [1/4] Preparando LAZ no inbox...")
  dest <- link_into_inbox(src, inbox, type_file)
  stem <- tools::file_path_sans_ext(basename(dest))
  zone <- utm_zone_folder(epsg_proc)
  staging_root <- paths$staging
  out_staging <- file.path(staging_root, stem)
  out_final <- file.path(paths$processed, zone, stem)
  dir.create(paths$processed, recursive = TRUE, showWarnings = FALSE)
  dir.create(paths$tmp, recursive = TRUE, showWarnings = FALSE)
  dir.create(paths$logs, recursive = TRUE, showWarnings = FALSE)
  kill_stray_lastools()
  wipe_dir_contents(paths$tmp)
  wipe_dir_contents(staging_root)
  dir.create(staging_root, recursive = TRUE, showWarnings = FALSE)
  logfile <- file.path(paths$logs, paste0(toupper(stem), "_issues.csv"))
  pipe_log <- file.path(paths$logs, paste0(toupper(stem), "_pipeline.log"))
  if (file.exists(logfile)) unlink(logfile)
  if (file.exists(pipe_log)) unlink(pipe_log)
  # Limit BLAS threads so n_cores LAStools/R workers do not OOM on DSM steps.
  Sys.setenv(
    OPENBLAS_NUM_THREADS = "1",
    OMP_NUM_THREADS = "1",
    MKL_NUM_THREADS = "1",
    VECLIB_MAXIMUM_THREADS = "1",
    NUMEXPR_NUM_THREADS = "1"
  )
  say(
    "  [2/4] Pipeline GeoTrees/LAStools em andamento",
    if (isTRUE(opt$debug)) "..." else paste0(" (log: ", pipe_log, ")...")
  )
  t0 <- Sys.time()
  status <- tryCatch(
    with_pipeline_output(
      process.dataset(
        name_job = opt$name_job,
        type_file = type_file,
        dir_dataset = inbox,
        dir_processed = staging_root,
        tmpdir_processing = paths$tmp,
        path_lastools = opt$path_lastools,
        metadata = metadata,
        resolution = opt$resolution,
        n_cores = opt$n_cores,
        size_tile = opt$size_tile,
        size_buffer = opt$size_buffer,
        force.utm = opt$force_utm,
        remove.vlr = TRUE,
        remove.evlr = TRUE,
        type_architecture = "64",
        use.blast2dem = opt$use_blast2dem,
        patterns_skip = character(),
        cleanup = TRUE,
        retile = "include.adjacents",
        by_file = TRUE,
        force.recompute = isTRUE(opt$force),
        print.summary_job = FALSE,
        logfile = logfile
      ),
      log_file = pipe_log,
      debug = isTRUE(opt$debug)
    ),
    error = function(e) e
  )
  minutos <- as.numeric(difftime(Sys.time(), t0, units = "mins"))
  say(sprintf("  [3/4] Pipeline terminou em %s", format_dhm(minutos)))
  kill_stray_lastools()
  unlink(dest)
  detalhe <- issue_text(logfile)
  finish_fail <- function(erro) {
    if (dir.exists(out_staging)) unlink(out_staging, recursive = TRUE, force = TRUE)
    if (nzchar(detalhe)) erro <- paste0(erro, " | ", detalhe)
    if (nzchar(logfile)) erro <- paste0(erro, " | log: ", logfile)
    if (!isTRUE(opt$debug) && file.exists(pipe_log)) {
      erro <- paste0(erro, " | pipeline_log: ", pipe_log)
    }
    list(ok = FALSE, erro = clip_err(erro, 1200), minutos = minutos, pasta_saida = "")
  }
  if (inherits(status, "error")) {
    erro <- clip_err(conditionMessage(status), 1200)
    check <- tile_success(out_staging)
    if (!check$ok && nzchar(check$erro)) erro <- paste0(erro, " | ", check$erro)
    return(finish_fail(erro))
  }
  check <- tile_success(out_staging)
  if (!check$ok) {
    return(finish_fail(check$erro))
  }
  say("  [4/4] Promovendo para processed/", zone, "/", stem, "...")
  if (!promote_tile_dir(out_staging, out_final)) {
    return(finish_fail(paste0("tile ok no staging mas falhou ao promover para processed/", zone)))
  }
  pasta <- normalizePath(out_final, winslash = "/")
  list(ok = TRUE, erro = "", minutos = minutos, pasta_saida = pasta)
}

blank_meta <- function(plan_row) {
  x <- as.list(plan_row)
  for (nm in c("bloco", "layer_artic", "nomenc_2k", "nomenc_5k", "nomenc_10k", "periodvoo", "laz_nome", "laz_path")) {
    if (is.null(x[[nm]]) || length(x[[nm]]) == 0L || is.na(x[[nm]])) x[[nm]] <- ""
    x[[nm]] <- as.character(x[[nm]])
  }
  x$tamanho_mb <- suppressWarnings(as.numeric(plan_row$tamanho_mb))
  x$epsg_artic <- suppressWarnings(as.integer(plan_row$epsg_artic))
  x$epsg_proc <- suppressWarnings(as.integer(plan_row$epsg_proc))
  if (length(x$epsg_artic) != 1L) x$epsg_artic <- NA_integer_
  if (length(x$epsg_proc) != 1L) x$epsg_proc <- NA_integer_
  x
}

put_registry <- function(registry, row) {
  registry <- registry[tile_id != row$tile_id]
  data.table::rbindlist(list(registry, data.table::as.data.table(row)), fill = TRUE)
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

say <- function(...) {
  cat(paste0(...), "\n", sep = "")
  flush.console()
}

say_debug <- function(...) {
  if (!isTRUE(getOption("geotrees.debug", FALSE))) return(invisible())
  say(...)
}

log_line <- function(path, ...) {
  msg <- paste0(format(Sys.time(), "%Y-%m-%d %H:%M:%S"), " | ", paste0(..., collapse = ""))
  # Always keep disk log; console only in debug (progress_bar / say cover the UI).
  if (isTRUE(getOption("geotrees.debug", FALSE))) {
    cat(msg, "\n")
    flush.console()
  }
  dir.create(dirname(path), recursive = TRUE, showWarnings = FALSE)
  cat(msg, "\n", file = path, append = TRUE)
}

read_lock_pid <- function(path) {
  lines <- tryCatch(readLines(path, warn = FALSE), error = function(e) character())
  hit <- grep("^pid=", lines, value = TRUE)
  if (!length(hit)) return(NA_integer_)
  pid <- suppressWarnings(as.integer(sub("^pid=", "", hit[[1]])))
  if (length(pid) != 1L || is.na(pid) || pid <= 0L) NA_integer_ else pid
}

pid_is_running <- function(pid) {
  pid <- suppressWarnings(as.integer(pid))
  if (length(pid) != 1L || is.na(pid) || pid <= 0L) return(FALSE)
  if (.Platform$OS.type != "windows") {
    return(system2("kill", c("-0", as.character(pid)), stdout = FALSE, stderr = FALSE) == 0L)
  }
  # PowerShell: empty output = PID not found. More reliable than tasklist exit codes.
  ps <- tryCatch(
    suppressWarnings(system2(
      "powershell",
      c(
        "-NoProfile", "-Command",
        sprintf(
          "if (Get-Process -Id %d -ErrorAction SilentlyContinue) { '%d' } else { '' }",
          pid, pid
        )
      ),
      stdout = TRUE,
      stderr = TRUE
    )),
    error = function(e) character()
  )
  if (any(grepl(sprintf("^\\s*%d\\s*$", pid), ps))) return(TRUE)

  tl <- tryCatch(
    suppressWarnings(system2(
      "cmd",
      c("/c", sprintf('tasklist /FI "PID eq %d" /FO CSV /NH', pid)),
      stdout = TRUE,
      stderr = TRUE
    )),
    error = function(e) character()
  )
  if (any(grepl(sprintf("\"%d\"", pid), tl, fixed = TRUE))) return(TRUE)
  txt <- paste(c(ps, tl), collapse = " ")
  if (grepl(
    "No tasks are running|nenhuma tarefa|Cannot find a process|nao encontrado|n�o encontrado",
    txt,
    ignore.case = TRUE
  )) {
    return(FALSE)
  }
  # Empty PowerShell result already means not running.
  if (!length(ps) || all(!nzchar(trimws(ps)))) return(FALSE)
  FALSE
}

stop_lock_busy <- function(...) {
  cond <- simpleError(paste0(...))
  class(cond) <- c("geotrees_lock_busy", class(cond))
  stop(cond)
}

release_lock <- function(path, say_fn = NULL) {
  path <- as.character(path %||% "")
  if (!nzchar(path)) return(invisible(TRUE))
  if (!file.exists(path)) return(invisible(TRUE))
  try(unlink(path, force = TRUE), silent = TRUE)
  if (file.exists(path)) {
    Sys.sleep(0.25)
    try(unlink(path, force = TRUE), silent = TRUE)
  }
  ok <- !file.exists(path)
  if (!is.null(say_fn)) {
    if (ok) say_fn("Trava removida: ", path)
    else say_fn("AVISO: nao consegui remover a trava: ", path)
  }
  invisible(ok)
}

acquire_lock <- function(path) {
  if (file.exists(path)) {
    pid <- read_lock_pid(path)
    vivo <- pid_is_running(pid)
    if (isTRUE(vivo)) {
      msg <- paste(readLines(path, warn = FALSE), collapse = " | ")
      stop_lock_busy(
        "Ja existe uma copia deste script rodando (PID ", pid, ").\n",
        "Trava: ", path, "\n",
        msg, "\n",
        "A outra janela continua. Esta sera fechada."
      )
    }
    cat(
      "Trava antiga encontrada (PID ", if (is.na(pid)) "?" else pid,
      " nao esta mais rodando). Vou remove-la e continuar.\n",
      sep = ""
    )
    release_lock(path)
  }
  writeLines(c(maquina(), usuario(), utc_now(), paste0("pid=", Sys.getpid())), path)
  invisible(TRUE)
}


resolve_cores <- function(n) {
  if (!is.na(n)) return(as.integer(n))
  n_phys <- parallel::detectCores(logical = FALSE)
  if (is.na(n_phys) || n_phys < 1L) n_phys <- parallel::detectCores(logical = TRUE)
  if (is.na(n_phys) || n_phys < 1L) n_phys <- 1L
  max(1L, as.integer(n_phys) - 2L)
}

main <- function() {
  opt <- parse_cli(commandArgs(trailingOnly = TRUE))
  options(geotrees.debug = isTRUE(opt$debug))
  if (opt$help) {
    cat(paste(help_text(), collapse = "\n"), "\n")
    return(0L)
  }
  if (opt$list_blocos) opt$dry_run <- TRUE

  base_pkgs <- c("data.table", "DBI", "RSQLite")
  if (!opt$dry_run && !opt$report_only) {
    need_pkgs(c(base_pkgs, HELPER_PKGS), "pipeline GeoTrees")
  } else if (opt$report_only) {
    need_pkgs(c(base_pkgs, "sf"), "relatorio e GPKG")
  } else {
    need_pkgs(base_pkgs, "plano, sem processar")
  }
  suppressPackageStartupMessages(library(data.table))
  options(warn = 1, sf_use_s2 = FALSE)
  if (.Platform$OS.type == "windows") {
    try(suppressWarnings(Sys.setlocale("LC_CTYPE", ".UTF-8")), silent = TRUE)
  }

  dir_script <- script_dir()
  if (!nzchar(opt$articulacao)) opt$articulacao <- default_articulacao(dir_script)
  opt$n_cores <- resolve_cores(opt$n_cores)
  blocos <- parse_blocos(opt$bloco)

  if (!opt$report_only && !dir.exists(opt$laz_dir)) {
    stop("Pasta de LAZ nao existe: ", opt$laz_dir, call. = FALSE)
  }
  if (nzchar(opt$metadata) && !file.exists(opt$metadata)) {
    stop("Metadata nao encontrado: ", opt$metadata, call. = FALSE)
  }

  paths <- out_paths(opt$out_dir)
  dir.create(paths$root, recursive = TRUE, showWarnings = FALSE)
  lock_held <- FALSE
  acquire_lock(paths$lock)
  lock_held <- TRUE
  on.exit({
    if (isTRUE(lock_held)) release_lock(paths$lock)
  }, add = TRUE)

  if (grepl("OneDrive|Dropbox|Google Drive", paths$root, ignore.case = TRUE)) {
    cat("AVISO: OUT_DIR esta numa pasta sincronizada. O tmp do GeoTrees fica lento. Use um disco local, por exemplo D:/geotrees_als\n")
  }

  artic_ok <- file.exists(opt$articulacao)
  idx <- build_index(data.frame())
  if (!artic_ok) {
    if (length(blocos)) {
      stop(
        "Articulacao nao encontrada e BLOCO foi pedido:\n  ", opt$articulacao,
        "\nO arquivo fica em data/articulacao_igc_sp/ dentro do clone do repositorio.",
        call. = FALSE
      )
    }
    cat("AVISO: articulacao nao encontrada em:\n  ", opt$articulacao, "\n", sep = "")
    cat("Sem articulacao nao ha campo epsg: nenhum tile recebe CRS (31982->32722 / 31983->32723).\n")
    cat("O GPKG precisa estar em data/articulacao_igc_sp/ dentro do clone:\n")
    cat("  articulacao-igc-merged-spatialindex-sirgas2000.gpkg\n")
    cat("Ou passe --articulacao com o caminho do arquivo.\n")
  } else {
    say("Lendo articulacao (atributos): ", opt$articulacao)
    artic_tab <- read_artic_table(opt$articulacao, opt$articulacao_layer)
    say_debug("Montando o indice NOMENC_2K / NOMENC_5K / NOMENC_10K...")
    idx <- build_index(artic_tab)
    say("Indice da articulacao em memoria: ", nrow(idx), " id(s).")
  }

  # Registro so em GPKG; a cada rodada comeca zerado a partir de processed/.
  migrate_outdir_english_names(paths$root)
  cleanup_legacy_outdir(paths$root)
  if (!opt$dry_run && !opt$report_only) {
    n_log <- 0L
    if (dir.exists(paths$logs)) {
      old_logs <- list.files(paths$logs, full.names = TRUE, all.files = TRUE, no.. = TRUE)
      n_log <- length(old_logs)
      if (n_log) unlink(old_logs, recursive = TRUE, force = TRUE)
    }
    dir.create(paths$logs, recursive = TRUE, showWarnings = FALSE)
    if (n_log > 0L) say("Logs: limpei ", n_log, " arquivo(s) antigo(s) em logs/ (so desta rodada).")
  }
  say("Registro: zerado nesta rodada; fonte = pastas OK em processed/ (+ GPKG no fim).")
  registry <- empty_registry()
  if (!opt$dry_run && !opt$report_only) {
    say_debug("Limpando pastas incompletas em processed/ (sem _INFO_summary_processing.csv)...")
    n_prune <- prune_incomplete_processed(paths$processed)
    if (n_prune > 0L) say("  pastas incompletas removidas: ", n_prune)
  }
  done <- scan_done(paths$processed, character())
  if (!opt$dry_run) {
    if (file.exists(paths$gpkg)) {
      unlink(paths$gpkg, force = TRUE)
      unlink(paste0(paths$gpkg, "-wal"), force = TRUE)
      unlink(paste0(paths$gpkg, "-shm"), force = TRUE)
      say("GPKG de registro anterior removido (rebuild nesta rodada).")
    }
    say_debug("Montando registro a partir de processed/ + articulacao...")
    registry <- adopt_disk(registry, done, maquina(), usuario())
    registry <- fill_artic_on_registry(registry, idx)
    say("Registro em memoria: ", nrow(registry), " tile(s) ja prontos no disco.")
  }
  done_ids <- if (opt$force) character() else done$tile_id

  plan <- NULL
  n_dup <- 0L
  if (opt$report_only) {
    if (!dir.exists(opt$laz_dir)) {
      cat("AVISO: pasta LAZ ausente. Relatorio so do registro.\n")
    }
  }
  if (!opt$report_only || dir.exists(opt$laz_dir)) {
    if (dir.exists(opt$laz_dir)) {
      laz_dir_n <- normalizePath(opt$laz_dir, winslash = "/", mustWork = TRUE)
      n_laz_agora <- count_lidar_quick(laz_dir_n, paths$root)
      say("Arquivos .laz/.las na pasta agora: ", n_laz_agora)
      inv <- load_laz_index(
        paths$laz_index, paths$laz_index_meta, laz_dir_n,
        n_brutos_agora = n_laz_agora
      )
      if (is.null(inv)) {
        say("Lendo pasta de LAZ (indice ausente, de outra pasta, ou pasta mudou)...")
        files <- list_lidar(laz_dir_n, paths$root)
        inv <- dedupe_lidar(files)
        save_laz_index(
          paths$laz_index, paths$laz_index_meta, inv, laz_dir_n,
          n_brutos = length(files)
        )
        say("Indice de LAZ gravado: ", nrow(inv$dt), " unicos / ", length(files), " brutos.")
        say("  ", paths$laz_index)
      }
      plan <- make_plan(
        inv$dt, idx, blocos, done_ids,
        if (opt$report_only) 0L else opt$limit, opt$force, inv$n_dup,
        out_sp = isTRUE(opt$out_sp)
      )
      n_dup <- attr(plan, "n_dup")
      if (is.null(n_dup)) n_dup <- 0L
    }
  }
  if (!opt$dry_run) {
    say("Completando caminhos de LAZ no registro...")
    registry <- fill_laz_from_plan(registry, plan)
    if (artic_ok && nrow(registry)) {
      say("Gravando registro GPKG (tiles ja prontos): ", paths$gpkg)
      rebuild_gpkg(paths$gpkg, registry, opt$articulacao, opt$articulacao_layer)
      say("Registro GPKG gravado.")
    } else if (!nrow(registry)) {
      say("Registro ainda vazio. Nada para gravar agora.")
    }
  }

  cat("\n")
  cat("Maquina: ", maquina(), "\n", sep = "")
  cat("LAZ:     ", opt$laz_dir, "\n", sep = "")
  cat("BLOCO:   ", if (length(blocos)) paste(blocos, collapse = ", ") else "(todos na pasta)", "\n", sep = "")
  cat(
    "OUT_SP:  ", if (isTRUE(opt$out_sp)) "true (SP + fora)" else "false (so sp_inside=TRUE)",
    "\n", sep = ""
  )
  cat("Saida:   ", paths$root, "\n", sep = "")
  if (!is.null(plan)) {
    cat("Arquivos:", nrow(plan), "\n")
    cat("  processar:    ", sum(plan$acao == "processar"), "\n", sep = "")
    cat("  pular:        ", sum(plan$acao == "pular"), "\n", sep = "")
    cat("  fora do bloco:", sum(plan$acao == "fora_do_bloco"), "\n", sep = "")
    cat("  fora de SP:   ", sum(plan$acao == "fora_de_sp"), "\n", sep = "")
    cat("  sem CRS:      ", sum(plan$acao == "sem_crs"), "\n", sep = "")
    cat("  alem do limit:", sum(plan$acao == "adiado_limit"), "\n", sep = "")
    go <- plan[acao == "processar"]
    if (nrow(go)) {
      grp <- go[, .N, by = .(epsg_artic, epsg_proc)][order(epsg_proc)]
      cat("  grupos EPSG (artic -> proc):\n")
      for (gi in seq_len(nrow(grp))) {
        cat(
          sprintf(
            "    %s -> %s : %d tile(s)\n",
            as.character(grp$epsg_artic[[gi]]),
            as.character(grp$epsg_proc[[gi]]),
            grp$N[[gi]]
          )
        )
      }
    }
  }
  cat("\n")

  t_run <- Sys.time()
  rodada <- data.table::data.table(
    tile_id = character(), bloco = character(), status = character(),
    minutos = numeric(), erro = character()
  )

  write_md <- function(modo) {
    elapsed <- as.numeric(difftime(Sys.time(), t_run, units = "mins"))
    hist <- load_historico(paths$history)
    linhas <- build_report(opt, paths, registry, hist, plan, rodada, blocos, n_dup, modo, elapsed)
    dest <- if (modo %in% c("dry", "lista")) paths$plan else paths$report
    write_utf8(dest, linhas)
    dest
  }

  if (opt$dry_run) {
    modo <- if (opt$list_blocos) "lista" else "dry"
    say("Gravando o plano, sem processar tile...")
    dest <- write_md(modo)
    if (!is.null(plan) && any(plan$acao == "processar")) {
      prev <- head(plan[acao == "processar"], 20L)
      cat("Primeiros a processar (ja agrupados por epsg_proc):\n")
      print(prev[, .(tile_id, bloco, epsg_artic, epsg_proc, laz_nome)])
    }
    if (opt$list_blocos && !is.null(plan) && nrow(plan)) {
      cat("\nBLOCO nesta pasta:\n")
      print(plan[, .N, by = bloco][order(-N)])
    }
    cat("\nPlano: ", dest, "\n", sep = "")
    return(0L)
  }

  if (opt$report_only) {
    if (artic_ok && nrow(registry)) {
      say("Atualizando GPKG do registro...")
      rebuild_gpkg(paths$gpkg, registry, opt$articulacao, opt$articulacao_layer)
      say("GPKG atualizado.")
    }
    dest <- write_md("report")
    cleanup_empty_workdir(paths)
    cat("Report:   ", dest, "\n", sep = "")
    cat("Registry: ", paths$gpkg, "\n", sep = "")
    return(0L)
  }

  fila <- if (is.null(plan)) plan else plan[acao == "processar"]
  if (is.null(fila) || !nrow(fila)) {
    # Mesmo sem tile novo: catch-up NAS + limpeza LAZ (rodadas anteriores).
    nas <- nas_init(opt)
    on.exit(nas_shutdown(nas, paths, wait = FALSE), add = TRUE)
    if (isTRUE(nas$enabled)) nas_catchup(nas, paths, registry, idx)
    laz_catchup(opt, paths, registry, plan)
    nas_shutdown(nas, paths, wait = FALSE)
    if (artic_ok && nrow(registry)) {
      rebuild_gpkg(paths$gpkg, registry, opt$articulacao, opt$articulacao_layer)
    }
    release_lock(paths$lock, say)
    lock_held <- FALSE
    dest <- write_md("rodada")
    cleanup_empty_workdir(paths)
    cat("Nada novo para processar.\nReport:   ", dest, "\n", sep = "")
    cat("Registry: ", paths$gpkg, "\n", sep = "")
    return(0L)
  }

  if (!dir.exists(opt$path_lastools)) {
    stop(
      "Pasta do LAStools nao existe: ", opt$path_lastools,
      "\nPasse --path-lastools ou edite PATH_LASTOOLS no .bat.",
      call. = FALSE
    )
  }
  say("LAStools: ", opt$path_lastools)
  if (is.finite(opt$max_hours)) {
    say("Limite desta rodada: ", opt$max_hours, " hora(s). A checagem e entre tiles.")
  } else {
    say("Limite desta rodada: indefinido (sem --max-hours). Pare com STOP.txt ou Ctrl+C.")
  }
  say("Metadata GCA: inline (acq_crs por tile via articulacao).")
  source(file.path(dirname(dir_script), "als_registry_api.R"), local = FALSE)
  als <- als_cfg(opt, pipeline = "lastools")
  pending_als <- list()
  if (als$enabled) {
    say(
      "ALS API: ", als$url,
      " | claim por tile | report a cada ", als$report_every,
      " | lease ", als$lease_seconds, "s",
      " | soft-fail=", als$soft_fail,
      " | fila local se offline"
    )
    tryCatch(als_upsert_plan(als, plan, say_fn = say), error = function(e) {
      say("AVISO ALS upsert (seguindo offline): ", conditionMessage(e))
    })
    pending_als <- als_flush_with_queue(als, paths$root, list(), say_fn = say)
  } else {
    say("ALS API: desligada (sem ALS_MACHINE_TOKEN / --no-als-api).")
  }
  source_helper(
    file.path(dir_script, "ALS_processing_helperfunctions_v.1.0.4.R"),
    debug = isTRUE(opt$debug)
  )
  nas <- nas_init(opt)
  on.exit(nas_shutdown(nas, paths, wait = FALSE), add = TRUE)
  say("Apagar LAZ apos tile ok (status 1): ", isTRUE(opt$delete_laz_on_ok))
  if (isTRUE(nas$enabled)) {
    nas_catchup(nas, paths, registry, idx)
  }
  laz_catchup(opt, paths, registry, plan)
  meta_base <- NULL
  if (nzchar(opt$metadata)) {
    say("Sobrescrevendo campos do template com --metadata: ", opt$metadata)
    meta_base <- data.table::fread(opt$metadata)
  }
  inbox <- inbox_for(opt$laz_dir, paths$root, paths$inbox)
  filtro_txt <- if (length(blocos)) paste(blocos, collapse = ",") else "*"
  n_fila <- nrow(fila)
  parou <- FALSE
  n_erro_run <- 0L
  n_pulados <- if (is.null(plan)) 0L else sum(plan$acao == "pular")
  n_done_disk <- if (exists("done", inherits = FALSE) && is.data.frame(done)) nrow(done) else 0L
  cat(
    "Retomada: ", n_pulados, " tile(s) da fila pulados (LAZ ainda na pasta e ja em processed/).\n",
    sep = ""
  )
  if (n_done_disk > n_pulados) {
    cat(
      "  Em processed/ ha ", n_done_disk,
      " tile(s) OK; os que nao entram em 'pular' geralmente ja nao tem .laz (DELETE_LAZ_ON_OK).\n",
      sep = ""
    )
  }
  cat("Na fila agora: ", n_fila, ".\n", sep = "")
  cat("Se o script parou no meio de um .laz, esse arquivo comeca de novo. Os que ja terminaram ficam pulados.\n")
  cat("Ordem: um grupo EPSG por vez (32722 depois 32723, conforme a articulacao).\n")
  mostrar_eta(rodada, registry, n_fila)

  epsg_atual <- NA_integer_
  for (i in seq_len(n_fila)) {
    nas_reap(nas, paths, wait = FALSE)
    horas_rodada <- as.numeric(difftime(Sys.time(), t_run, units = "hours"))
    if (is.finite(opt$max_hours) && !is.na(horas_rodada) && horas_rodada >= opt$max_hours) {
      parou <- TRUE
      log_line(
        paths$runlog,
        sprintf(
          "Limite de %.1f h atingido (rodou %.2f h). Parando antes do tile %d/%d.",
          opt$max_hours, horas_rodada, i, n_fila
        )
      )
      say(
        "Limite de ", opt$max_hours, " h atingido. Pare agora; rode de novo depois para continuar."
      )
      break
    }
    stop_legacy <- file.path(paths$root, "PARAR.txt")
    if (file.exists(paths$stop) || file.exists(stop_legacy)) {
      parou <- TRUE
      log_line(paths$runlog, "STOP.txt encontrado. Parando antes do tile ", i, "/", n_fila, ".")
      say("STOP.txt encontrado. Encerrando apos o tile anterior; apagando STOP.txt.")
      try(unlink(paths$stop), silent = TRUE)
      try(unlink(stop_legacy), silent = TRUE)
      break
    }
    item <- fila[i]
    meta <- blank_meta(item)
    if (is.na(meta$epsg_proc) || meta$epsg_proc <= 0L) {
      n_erro_run <- n_erro_run + 1L
      say("FALHOU ", meta$tile_id, ": sem epsg_proc na articulacao.")
      result <- list(
        ok = FALSE,
        erro = "sem epsg_proc na articulacao (esperado 31982->32722 ou 31983->32723)",
        minutos = NA_real_,
        pasta_saida = ""
      )
      append_problema(paths$problems, meta$tile_id, result$erro)
      status <- "erro"
      iniciado <- utc_now()
      row <- list(
        tile_id = meta$tile_id,
        laz_nome = meta$laz_nome,
        laz_path = meta$laz_path,
        bloco = meta$bloco,
        layer_artic = meta$layer_artic,
        nomenc_2k = meta$nomenc_2k,
        nomenc_5k = meta$nomenc_5k,
        nomenc_10k = meta$nomenc_10k,
        periodvoo = meta$periodvoo,
        epsg_artic = meta$epsg_artic,
        epsg_proc = meta$epsg_proc,
        status = status,
        hostname = maquina(),
        usuario = usuario(),
        pasta_saida = "",
        minutos = NA_real_,
        tamanho_mb = meta$tamanho_mb,
        erro = result$erro,
        iniciado_utc = iniciado,
        finalizado_utc = utc_now(),
        rodada_id = format(t_run, "%Y%m%dT%H%M%S"),
        origem = "pipeline"
      )
      registry <- put_registry(registry, row)
      if (artic_ok) {
        tryCatch({
          feature <- row_to_sf(row, geom_one(opt$articulacao, opt$articulacao_layer, row$tile_id))
          upsert_gpkg(paths$gpkg, feature)
        }, error = function(e) {
          log_line(paths$runlog, "AVISO GPKG ", row$tile_id, ": ", conditionMessage(e))
        })
      }
      append_historico(paths$history, list(
        rodada_id = row$rodada_id,
        tile_id = row$tile_id,
        laz_nome = row$laz_nome,
        laz_path = row$laz_path,
        bloco = row$bloco,
        status = row$status,
        hostname = row$hostname,
        usuario = row$usuario,
        minutos = row$minutos,
        erro = row$erro,
        iniciado_utc = row$iniciado_utc,
        finalizado_utc = row$finalizado_utc,
        bloco_filtro = filtro_txt,
        epsg_proc = meta$epsg_proc,
        out_dir = paths$root
      ))
      if (als$enabled) {
        pending_als <- als_buffer_push(
          als, pending_als,
          als_result_payload(row, 4L, als$pipeline, paths$root),
          say_fn = say,
          out_dir = paths$root
        )
      }
      next
    }
    if (is.na(epsg_atual) || epsg_atual != meta$epsg_proc) {
      epsg_atual <- meta$epsg_proc
      n_grupo <- sum(fila$epsg_proc == epsg_atual, na.rm = TRUE)
      say(
        "=== Grupo EPSG ", epsg_atual,
        " (articulacao ", meta$epsg_artic, " -> ", epsg_atual, ", ",
        n_grupo, " tile(s) neste grupo) ==="
      )
      log_line(
        paths$runlog,
        sprintf(
          "Grupo EPSG %s (artic %s): %d tile(s)",
          epsg_atual, meta$epsg_artic, n_grupo
        )
      )
    }
    metadata <- inline_gca_metadata(meta$epsg_proc)
    if (!is.null(meta_base) && nrow(meta_base)) {
      keep_crs <- metadata$acq_crs
      metadata <- data.table::rbindlist(list(meta_base[1], metadata), use.names = TRUE, fill = TRUE)
      metadata <- metadata[.N]
      metadata[, acq_crs := keep_crs]
    }
    faltam_agora <- n_fila - i + 1L
    eta_antes <- eta_agora(rodada, registry, faltam_agora)
    if (eta_antes$n_base > 0L) {
      log_line(
        paths$runlog,
        sprintf(
          "[%d/%d] Antes deste tile: faltam %d | restante %s | termino %s",
          i, n_fila, faltam_agora, eta_antes$texto,
          if (nzchar(eta_antes$previsao)) eta_antes$previsao else "-"
        )
      )
    }
    if (als$enabled) {
      # tenta esvaziar fila local se a rede voltou
      if (length(pending_als) || file.exists(als_queue_path(paths$root))) {
        pending_als <- als_flush_with_queue(als, paths$root, pending_als, say_fn = say)
      }
      claim <- als_claim_one(als, meta$tile_id, maquina(), usuario())
      if (!isTRUE(claim$claimed)) {
        say("ALS claim pulou ", meta$tile_id, ": ", claim$reason)
        log_line(paths$runlog, sprintf("[%d/%d] PULADO (API) %s | %s", i, n_fila, meta$tile_id, claim$reason))
        # Ja feito na API (outra maquina / rodada): apaga .laz local se ainda existir.
        if (isTRUE(opt$delete_laz_on_ok) &&
            grepl("already_done|already done|status\\s*1|done", claim$reason, ignore.case = TRUE)) {
          lp_del <- resolve_laz_path(meta$tile_id, opt$laz_dir, meta$laz_path)
          maybe_delete_laz(lp_del, opt$laz_dir, TRUE)
        }
        next
      }
      if (!isTRUE(claim$ok) && isTRUE(claim$soft)) {
        if (isTRUE(claim$offline)) {
          say("ALS offline/instavel; processando local e enfileirando report: ", claim$reason)
        } else {
          say("AVISO ALS claim soft-fail; processando mesmo assim: ", claim$reason)
        }
      } else if (!isTRUE(claim$ok)) {
        n_erro_run <- n_erro_run + 1L
        say("FALHOU claim ", meta$tile_id, ": ", claim$reason)
        next
      } else {
        say("ALS claim ok: ", meta$tile_id)
      }
    }
    progress_bar(i - 1L, n_fila, paste0("iniciando ", meta$tile_id))
    progress_done()
    say(sprintf(
      "[%d/%d] PROCESSANDO %s | bloco %s | EPSG %s -> %s",
      i, n_fila, meta$tile_id,
      if (nzchar(meta$bloco)) meta$bloco else "-",
      as.character(meta$epsg_artic), as.character(meta$epsg_proc)
    ))
    log_line(
      paths$runlog,
      sprintf(
        "[%d/%d] PROCESSANDO %s | bloco %s | EPSG %s -> %s",
        i, n_fila, meta$tile_id,
        if (nzchar(meta$bloco)) meta$bloco else "-",
        as.character(meta$epsg_artic), as.character(meta$epsg_proc)
      )
    )
    iniciado <- utc_now()
    result <- tryCatch(
      run_one(meta$laz_path, opt, paths, inbox, metadata, epsg_proc = meta$epsg_proc),
      error = function(e) list(ok = FALSE, erro = clip_err(conditionMessage(e), 1200), minutos = NA_real_, pasta_saida = "")
    )
    status <- if (isTRUE(result$ok)) "processed" else "erro"
    als_code <- als_status_from_local(status, result = result)
    # Rigid: never keep local "processed" / API 1 without verified products.
    if (identical(status, "processed") && !identical(als_code, 1L)) {
      status <- "erro"
      als_code <- 2L
      if (!nzchar(as.character(result$erro %||% ""))) {
        result$erro <- "ALS gate: sem produtos finais em processed/<utm>/<tile>"
      }
      result$pasta_saida <- ""
    }
    if (status == "erro") {
      n_erro_run <- n_erro_run + 1L
      say("FALHOU ", meta$tile_id, ": ", result$erro)
      append_problema(paths$problems, meta$tile_id, result$erro)
    }
    if (identical(als_code, 1L)) {
      lp_del <- resolve_laz_path(meta$tile_id, opt$laz_dir, meta$laz_path)
      maybe_delete_laz(lp_del, opt$laz_dir, opt$delete_laz_on_ok)
      if (isTRUE(nas$enabled) && nzchar(as.character(result$pasta_saida %||% ""))) {
        nas_enqueue(
          nas, paths, result$pasta_saida,
          meta$layer_artic, meta$bloco, meta$tile_id
        )
      }
    }
    row <- list(
      tile_id = meta$tile_id,
      laz_nome = meta$laz_nome,
      laz_path = meta$laz_path,
      bloco = meta$bloco,
      layer_artic = meta$layer_artic,
      nomenc_2k = meta$nomenc_2k,
      nomenc_5k = meta$nomenc_5k,
      nomenc_10k = meta$nomenc_10k,
      periodvoo = meta$periodvoo,
      epsg_artic = meta$epsg_artic,
      epsg_proc = meta$epsg_proc,
      status = status,
      hostname = maquina(),
      usuario = usuario(),
      pasta_saida = if (identical(als_code, 1L)) as.character(result$pasta_saida) else "",
      minutos = result$minutos,
      tamanho_mb = meta$tamanho_mb,
      erro = if (status == "erro") result$erro else "",
      iniciado_utc = iniciado,
      finalizado_utc = utc_now(),
      rodada_id = format(t_run, "%Y%m%dT%H%M%S"),
      origem = "pipeline"
    )
    registry <- put_registry(registry, row)
    append_historico(paths$history, list(
      rodada_id = row$rodada_id,
      tile_id = row$tile_id,
      laz_nome = row$laz_nome,
      laz_path = row$laz_path,
      bloco = row$bloco,
      status = row$status,
      hostname = row$hostname,
      usuario = row$usuario,
      minutos = row$minutos,
      erro = row$erro,
      iniciado_utc = row$iniciado_utc,
      finalizado_utc = row$finalizado_utc,
      bloco_filtro = filtro_txt,
      epsg_proc = meta$epsg_proc,
      out_dir = paths$root
    ))
    rodada <- data.table::rbindlist(list(rodada, data.table::data.table(
      tile_id = row$tile_id, bloco = row$bloco, status = row$status,
      minutos = row$minutos, erro = row$erro
    )), fill = TRUE)
    if (artic_ok) {
      tryCatch({
        feature <- row_to_sf(row, geom_one(opt$articulacao, opt$articulacao_layer, row$tile_id))
        upsert_gpkg(paths$gpkg, feature)
      }, error = function(e) {
        log_line(paths$runlog, "AVISO GPKG ", row$tile_id, ": ", conditionMessage(e))
      })
    }
    write_md(if (parou) "parado" else "rodada")
    if (als$enabled) {
      pending_als <- als_buffer_push(
        als, pending_als,
        als_result_payload(row, als_code, als$pipeline, paths$root),
        say_fn = say,
        out_dir = paths$root
      )
    }
    faltam_depois <- n_fila - i
    progress_bar(i, n_fila, if (status == "erro") paste0("ERRO ", row$tile_id) else paste0("OK ", row$tile_id))
    progress_done()
    mostrar_eta(rodada, registry, faltam_depois, este_min = result$minutos)
    log_line(
      paths$runlog,
      sprintf(
        "[%d/%d] %s %s | %s | faltam %d | restante %s",
        i, n_fila, toupper(status), row$tile_id,
        if (status == "erro") paste0("motivo: ", row$erro) else paste0("este ", format_dhm(result$minutos)),
        faltam_depois,
        eta_agora(rodada, registry, faltam_depois)$texto
      )
    )
    gc(verbose = FALSE)
  }
  progress_bar(n_fila, n_fila, "fila desta rodada concluida")
  progress_done()

  # Libera a trava antes de qualquer espera residual do NAS.
  release_lock(paths$lock, say)
  lock_held <- FALSE

  nas_shutdown(nas, paths, wait = FALSE)

  if (als$enabled) {
    say("ALS flush final...")
    pending_als <- als_flush_with_queue(als, paths$root, pending_als, say_fn = say)
    if (length(pending_als)) {
      say("Ainda ha ", length(pending_als), " resultado(s) na fila local (rede/API). Proxima rodada tenta de novo.")
    }
  }

  if (artic_ok && nrow(registry)) {
    cat("Fechando GPKG do registro...\n")
    tryCatch(
      rebuild_gpkg(paths$gpkg, registry, opt$articulacao, opt$articulacao_layer),
      error = function(e) log_line(paths$runlog, "AVISO rebuild GPKG: ", conditionMessage(e))
    )
  }
  modo_fim <- if (parou) "parado" else "rodada"
  dest <- write_md(modo_fim)
  cleanup_empty_workdir(paths)
  cat("\nReport:   ", dest, "\n", sep = "")
  cat("Registry: ", paths$gpkg, "\n", sep = "")
  if (n_erro_run > 0L) 1L else 0L
}

is_entry <- function() {
  f <- this_file()
  if (is.na(f)) return(FALSE)
  cmd <- commandArgs(trailingOnly = FALSE)
  hit <- grep("^--file=", cmd, value = TRUE)
  if (!length(hit)) return(FALSE)
  entry <- normalizePath(sub("^--file=", "", hit[[1]]), winslash = "/", mustWork = FALSE)
  identical(tolower(entry), tolower(f))
}

if (is_entry()) {
  code <- tryCatch(
    main(),
    geotrees_lock_busy = function(e) {
      cat("\n[ERRO] ", conditionMessage(e), "\n", sep = "")
      3L
    },
    error = function(e) {
      cat("\n[ERRO] ", conditionMessage(e), "\n", sep = "")
      2L
    }
  )
  quit(status = as.integer(code), save = "no")
}
