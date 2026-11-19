# Batch GeoTrees ALS — um tile por vez, com retomada
#
# Envolve o pipeline oficial (ALS_processing_helperfunctions.R / process.dataset)
# sem alterar esses arquivos. Para cada .laz/.las:
#   1. pula se OUT_DIR/processed/<utm>/<tile> ja tem _INFO_summary_processing.csv
#      (utm = 22 para EPSG 32722, 23 para 32723)
#   2. senao roda o GeoTrees so naquele arquivo (staging em tmp;
#      so promove para processed/<utm>/<tile> quando terminar ok)
#   3. grava o tile no CSV, no historico e no GPKG de registro
#   4. problemas da rodada em geotrees_als_problemas.txt
#
# O GPKG de articulacao e somente leitura. O que "ja foi" vai para
# OUT_DIR/geotrees_als_registro.gpkg (camada geotrees_tiles).
#
# Registro e por OUT_DIR (por maquina, no padrao D:\geotrees_als).
# A mesma OUT_DIR em outro PC, ou um disco compartilhado, junta o relatorio
# e evita refazer tile. Produto pronto no disco tambem conta: se 10% da pasta
# ja foi processado, esses arquivos nao rodam de novo.
#
# Uso:
#   Rscript batch_geotrees_als.R
#   Rscript batch_geotrees_als.R --bloco EF
#   Rscript batch_geotrees_als.R --laz-dir D:/laz --bloco ES_23,2024
#   Rscript batch_geotrees_als.R --dry-run
#   Rscript batch_geotrees_als.R --list-blocos
#   Rscript batch_geotrees_als.R --limit 1
#   Rscript batch_geotrees_als.R --report-only
#   Rscript batch_geotrees_als.R --force
#
# Para parar entre tiles, crie o arquivo PARAR.txt dentro de OUT_DIR.

VERSION <- "1.3.1"
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
    out_dir = "G:/geotrees_als",
    articulacao = "",
    articulacao_layer = "articulacao-igc-merged-spatialindex-sirgas2000",
    n_cores = NA_integer_,
    resolution = 1,
    size_tile = 100,
    size_buffer = 50,
    limit = 0L,
    force = FALSE,
    dry_run = FALSE,
    report_only = FALSE,
    list_blocos = FALSE,
    help = FALSE,
    name_job = "igc",
    use_blast2dem = TRUE,
    force_utm = "from_metadata",
    metadata = "",
    als_api = NA,
    als_api_url = "https://leucaena.earth",
    als_api_token = "",
    als_report_every = 5L,
    als_lease_seconds = 7200L,
    als_allow_retry = TRUE,
    als_soft_fail = FALSE
  )
}

parse_cli <- function(argv) {
  opt <- default_opt()
  bools <- c(
    "--force", "--dry-run", "--report-only", "--list-blocos", "--help",
    "--no-blast", "--als-api", "--no-als-api", "--als-allow-retry",
    "--als-soft-fail"
  )
  valued <- c(
    "--laz-dir", "--bloco", "--out-dir", "--articulacao", "--articulacao-layer",
    "--n-cores", "--resolution", "--size-tile", "--size-buffer", "--limit",
    "--name-job", "--metadata", "--force-utm",
    "--als-api-url", "--als-api-token", "--als-report-every", "--als-lease-seconds"
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
    if (a == "--als-api-url") opt$als_api_url <- val
    if (a == "--als-api-token") opt$als_api_token <- val
    if (a == "--als-report-every") opt$als_report_every <- as.integer(num_arg(val, a))
    if (a == "--als-lease-seconds") opt$als_lease_seconds <- as.integer(num_arg(val, a))
  }
  opt$force_utm <- normalize_force_utm(opt$force_utm)
  if (!is.na(opt$n_cores) && opt$n_cores < 1L) {
    stop("--n-cores precisa ser >= 1.", call. = FALSE)
  }
  if (opt$resolution <= 0 || opt$size_tile <= 0 || opt$size_buffer < 0) {
    stop("resolution, size-tile e size-buffer invalidos.", call. = FALSE)
  }
  if (opt$limit < 0L) stop("--limit precisa ser >= 0.", call. = FALSE)
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
  opt
}

num_arg <- function(val, nome) {
  v <- suppressWarnings(as.numeric(val))
  if (length(v) != 1L || is.na(v)) {
    stop(nome, " precisa ser numero. Recebi: ", val, call. = FALSE)
  }
  v
}

normalize_force_utm <- function(val) {
  key <- tolower(trimws(val))
  if (key %in% c("false", "f", "0", "nao", "não")) return(FALSE)
  if (key %in% c("true", "t", "1", "sim")) return(TRUE)
  if (key %in% c("from_metadata", "metadata")) return("from_metadata")
  stop(
    "--force-utm aceita false, true ou from_metadata. Recebi: ", val,
    call. = FALSE
  )
}

help_text <- function() {
  c(
    "batch_geotrees_als.R — GeoTrees ALS por tile, com registro e retomada",
    "",
    "Opcoes:",
    "  --laz-dir DIR         Pasta dos .laz/.las (padrao D:/laz). Recursivo.",
    "  --bloco COD           Campo BLOCO da articulacao. Vazio = pasta inteira.",
    "                        Varios: EF,2024",
    "  --out-dir DIR         Registro + produtos (padrao G:/geotrees_als).",
    "  --articulacao GPKG    Articulacao IGC (obrigatoria para CRS). Padrao no clone.",
    "  --articulacao-layer   Nome da camada.",
    "  --n-cores N           Padrao: nucleos fisicos - 2.",
    "  --resolution M        Padrao 1.",
    "  --size-tile M         Padrao 100.",
    "  --size-buffer M       Padrao 50.",
    "  --limit N             Processa so N tiles novos nesta rodada (0 = todos).",
    "  --force               Refaz mesmo quem ja tem produto pronto.",
    "  --dry-run             So mostra o plano. Nao processa e nao mexe no registro.",
    "  --list-blocos         Conta BLOCO dos arquivos da pasta e sai.",
    "  --report-only         Regera CSV, GPKG e .md a partir do que ja esta no disco.",
    "  --no-blast            Desliga use.blast2dem (padrao: ligado).",
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
    "  --als-soft-fail        Se a API cair, processa sem claim",
    "Parar no meio: crie PARAR.txt em OUT_DIR (a checagem e entre tiles).",
    "A primeira rodada grava o indice dos .laz e o da articulacao em OUT_DIR.",
    "As seguintes reutilizam esses arquivos. Para reler a pasta, apague geotrees_als_laz_indice.csv.",
    "O GPKG da articulacao nao e alterado."
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
  if (anunciar) say(
    "Conferindo pacotes R (", para_que, "), antes de varrer LAZ ou carregar o pipeline:"
  )
  miss <- character()
  for (pkg in pkgs) {
    if (requireNamespace(pkg, quietly = TRUE)) {
      if (anunciar) say("  ok    ", pkg)
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
  if (anunciar) say("Pacotes conferidos: ", length(pkgs), ".")
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
    logs = file.path(out_dir, "logs"),
    registry = file.path(out_dir, "geotrees_als_registry.csv"),
    historico = file.path(out_dir, "geotrees_als_historico.csv"),
    problemas = file.path(out_dir, "geotrees_als_problemas.txt"),
    gpkg = file.path(out_dir, "geotrees_als_registro.gpkg"),
    md = file.path(out_dir, "geotrees_als_relatorio.md"),
    plano = file.path(out_dir, "geotrees_als_plano.md"),
    summary = file.path(out_dir, "geotrees_als_summary.csv"),
    lock = file.path(out_dir, ".geotrees_als.lock"),
    runlog = file.path(out_dir, "geotrees_als_run.log"),
    artic_cache = file.path(out_dir, "articulacao_cache.gpkg"),
    laz_index = file.path(out_dir, "geotrees_als_laz_indice.csv"),
    laz_index_meta = file.path(out_dir, "geotrees_als_laz_indice_meta.csv"),
    artic_index = file.path(out_dir, "geotrees_als_artic_indice.csv"),
    artic_index_meta = file.path(out_dir, "geotrees_als_artic_indice_meta.csv"),
    parar = file.path(out_dir, "PARAR.txt")
  )
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
  sql <- sprintf(
    paste0(
      'SELECT "NOMENC_2K", "NOMENC_5K", "NOMENC_10K", "BLOCO", "layer", ',
      '"periodvoo", "epsg" FROM "%s"'
    ),
    layer
  )
  say("Consultando a tabela da articulacao (SQLite, somente leitura)...")
  tab <- DBI::dbGetQuery(con, sql)
  say("Tabela lida: ", nrow(tab), " linha(s).")
  tab
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
  pieces <- data.table::rbindlist(list(
    raw[nzchar(NOMENC_2K), .(
      tile_id = toupper(NOMENC_2K), via = "NOMENC_2K",
      bloco = BLOCO, layer_artic = layer, periodvoo = periodvoo,
      nomenc_2k = NOMENC_2K, nomenc_5k = NOMENC_5K, nomenc_10k = NOMENC_10K,
      epsg_artic = epsg, epsg_proc = epsg_proc
    )],
    raw[nzchar(NOMENC_5K), .(
      tile_id = toupper(NOMENC_5K), via = "NOMENC_5K",
      bloco = BLOCO, layer_artic = layer, periodvoo = periodvoo,
      nomenc_2k = NOMENC_2K, nomenc_5k = NOMENC_5K, nomenc_10k = NOMENC_10K,
      epsg_artic = epsg, epsg_proc = epsg_proc
    )],
    raw[nzchar(NOMENC_10K), .(
      tile_id = toupper(NOMENC_10K), via = "NOMENC_10K",
      bloco = BLOCO, layer_artic = layer, periodvoo = periodvoo,
      nomenc_2k = NOMENC_2K, nomenc_5k = NOMENC_5K, nomenc_10k = NOMENC_10K,
      epsg_artic = epsg, epsg_proc = epsg_proc
    )]
  ), use.names = TRUE)
  if (!nrow(pieces)) {
    return(data.table::data.table(
      tile_id = character(), via = character(), bloco = character(),
      layer_artic = character(), periodvoo = character(),
      nomenc_2k = character(), nomenc_5k = character(), nomenc_10k = character(),
      epsg_artic = integer(), epsg_proc = integer()
    ))
  }
  pieces[!duplicated(tile_id)]
}

ensure_artic_cache <- function(src, dest) {
  copy <- !file.exists(dest)
  if (!copy) {
    a <- file.info(src)
    b <- file.info(dest)
    copy <- is.na(b$size) || a$size != b$size || a$mtime > b$mtime + 1
  }
  if (copy) {
    say("Copiando a articulacao para o cache da saida:")
    say("  ", dest)
    ok <- file.copy(src, dest, overwrite = TRUE)
    if (!ok) stop("Nao copiei a articulacao para ", dest, call. = FALSE)
    unlink(paste0(dest, "-wal"))
    unlink(paste0(dest, "-shm"))
    say("Cache da articulacao gravado.")
  } else {
    say("Cache da articulacao ja esta em dia: ", dest)
  }
  dest
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
  say("Procurando tiles ja prontos em ", dir_processed, " (pastas 22/23 e legado)")
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
  say("  ", length(kids), " pasta(s) na saida. ", length(ls(known)), " ja estao no registro e nao serao abertas de novo.")
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
        say("  pasta ja registrada ", n_fast, ": ", id)
      }
      next
    }
    n_open <- n_open + 1L
    say("  abrindo pasta nova ", id, " (", i, "/", length(kids), ")")
    info <- list.files(k, pattern = "_INFO_summary_processing\\.csv$", recursive = TRUE, full.names = TRUE)
    err <- list.files(k, pattern = "^_ERROR\\.txt$", recursive = TRUE)
    if (!length(info) || length(err)) {
      say("      ainda nao conta como pronto.")
      next
    }
    mt <- file.info(info[[1]])$mtime
    n <- n + 1L
    rows[[n]] <- data.table::data.table(
      tile_id = id,
      pasta_saida = normalizePath(k, winslash = "/"),
      finalizado_utc = format(mt, "%Y-%m-%dT%H:%M:%SZ", tz = "UTC")
    )
    say("      pronto. Adotado do disco.")
  }
  say("Tiles prontos no disco: ", n, " | reaproveitados do registro: ", n_fast, " | pastas abertas agora: ", n_open, ".")
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

list_lidar <- function(root, out_dir) {
  say("Listando .laz/.las em ", root)
  say("A pasta inteira e percorrida antes do filtro de BLOCO. Cada pasta aparece abaixo.")
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
    say("  [pasta ", n_dirs, "] lendo nomes em ", d)
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
      say("      normalizando ", length(here), " caminho(s)...")
      here <- normalizePath(here, winslash = "/", mustWork = TRUE)
      here <- here[!startsWith(tolower(here), skip_prefix)]
      n_here <- length(here)
      if (n_here) {
        found[[length(found) + 1L]] <- here
        n_found <- n_found + n_here
        say("      ultimo desta pasta: ", basename(here[[n_here]]))
      }
    }
    say(
      "      ", n_here, " arquivo(s) aqui | ", n_found, " no total | ",
      length(kids), " subpasta(s) a seguir"
    )
  }
  say("Varredura terminou: ", n_found, " arquivo(s) .laz/.las em ", n_dirs, " pasta(s).")
  if (!n_found) return(character())
  unlist(found, use.names = FALSE)
}

dedupe_lidar <- function(files) {
  say("Montando o inventario: ", length(files), " arquivo(s).")
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
  say("Lendo o tamanho de cada arquivo para descartar duplicata menor. Isso so ocorre na primeira vez.")
  chunk <- 200L
  for (i in seq.int(1L, n, by = chunk)) {
    j <- min(n, i + chunk - 1L)
    info <- file.info(files_dt$laz_path[i:j])
    sizes[i:j] <- ifelse(is.na(info$size), 0, info$size)
    say("  tamanhos ", j, "/", n, " | ", files_dt$laz_nome[[j]])
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

save_laz_index <- function(path, meta_path, inv, laz_dir) {
  atomic_write(path, function(tmp) data.table::fwrite(inv$dt, tmp, bom = TRUE))
  meta <- data.table::data.table(
    laz_dir = laz_dir,
    n_arquivos = nrow(inv$dt),
    n_dup = inv$n_dup,
    gravado_utc = utc_now()
  )
  atomic_write(meta_path, function(tmp) data.table::fwrite(meta, tmp, bom = TRUE))
}

load_laz_index <- function(path, meta_path, laz_dir) {
  if (!file.exists(path) || !file.exists(meta_path)) return(NULL)
  meta <- tryCatch(
    data.table::fread(meta_path, colClasses = "character", encoding = "UTF-8", showProgress = FALSE),
    error = function(e) NULL
  )
  if (is.null(meta) || !nrow(meta) || !all(c("laz_dir", "n_dup") %in% names(meta))) return(NULL)
  if (!identical(tolower(meta$laz_dir[[1]]), tolower(laz_dir))) {
    say("O indice de LAZ salvo e de outra pasta. Vou ler ", laz_dir, " de novo.")
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
  dt <- dt[nzchar(tile_id) & nzchar(laz_path)]
  n_copc <- sum(grepl("\\.copc\\.laz$", dt$laz_path, ignore.case = TRUE))
  if (n_copc > 0L) {
    say("Indice tinha ", n_copc, " .copc.laz; removidos da fila (lasR nao abre COPC).")
    dt <- dt[!grepl("\\.copc\\.laz$", laz_path, ignore.case = TRUE)]
  }
  n_dup <- suppressWarnings(as.integer(meta$n_dup[[1]]))
  if (is.na(n_dup)) n_dup <- 0L
  say("Indice de LAZ da primeira leitura: ", nrow(dt), " tile(s). O tamanho nao sera lido de novo.")
  say("  ", path)
  say("Para forcar outra leitura da pasta, apague esse arquivo.")
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

make_plan <- function(files_dt, idx, blocos, done_ids, limit, force, n_dup = 0L) {
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
      epsg_artic = NA_integer_, epsg_proc = NA_integer_
    )]
  }
  for (col in c("bloco", "layer_artic", "periodvoo", "nomenc_2k", "nomenc_5k", "nomenc_10k", "via")) {
    if (!col %in% names(plan)) plan[, (col) := ""]
    plan[is.na(get(col)), (col) := ""]
  }
  if (!"epsg_artic" %in% names(plan)) plan[, epsg_artic := NA_integer_]
  if (!"epsg_proc" %in% names(plan)) plan[, epsg_proc := NA_integer_]
  plan[, epsg_artic := suppressWarnings(as.integer(epsg_artic))]
  plan[, epsg_proc := suppressWarnings(as.integer(epsg_proc))]
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
  # Sem CRS mapeavel nao processa.
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
    parado = "PARCIAL (PARAR.txt)",
    if (n_go == 0L || (n_run_ok + n_run_err) >= n_go) "RODADA ENCERRADA" else "PARCIAL"
  )

  linhas <- c(
    "# Relatorio — GeoTrees ALS por tile",
    "",
    sprintf("**Status:** %s  ", titulo_modo),
    sprintf("**Atualizado (UTC):** %s  ", utc_now()),
    sprintf("**Script:** `batch_geotrees_als.R` %s  ", VERSION),
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
    "- Para parar no fim do tile atual, crie `PARAR.txt` em OUT_DIR e apague o arquivo antes da proxima rodada.",
    "- A articulacao IGC nao e modificada. O poligono do tile e copiado para o GPKG de registro.",
    "",
    "Produtos do pipeline (Fischer et al. 2024, Methods in Ecology and Evolution): DTM, DSM, CHM (`chm_highest`, `chm_tin`, `chm_lspikefree`), densidade de pulso, angulo de varredura e os `_INFO_`.",
    ""
  )
  linhas
}

save_summary <- function(path, opt, registry, plan, rodada, elapsed_min, modo) {
  n_reg <- nrow(registry)
  stats <- list(
    c("modo", modo, ""),
    c("maquina", maquina(), ""),
    c("laz_dir", opt$laz_dir, ""),
    c("bloco_filtro", if (nzchar(opt$bloco)) opt$bloco else "*", ""),
    c("out_dir", opt$out_dir, ""),
    c("arquivos_na_pasta", if (is.null(plan)) 0L else nrow(plan), "unicos por nome"),
    c("pulados", if (is.null(plan)) 0L else sum(plan$acao == "pular"), "ja tinham produto"),
    c("fila", if (is.null(plan)) 0L else sum(plan$acao == "processar"), "iriam ou foram processar"),
    c("ok_nesta_rodada", if (is.null(rodada) || !nrow(rodada)) 0L else sum(rodada$status == "processed"), ""),
    c("erro_nesta_rodada", if (is.null(rodada) || !nrow(rodada)) 0L else sum(rodada$status == "erro"), ""),
    c("processados_acumulado", if (n_reg) sum(registry$status == "processed") else 0L, "registro"),
    c("erros_acumulado", if (n_reg) sum(registry$status == "erro") else 0L, "serao tentados de novo"),
    c("minutos_rodada", fmt_num(elapsed_min, 2), ""),
    c("atualizado_utc", utc_now(), "")
  )
  eta_s <- eta_agora(rodada, registry, faltam_na_rodada(plan, rodada))
  stats <- c(stats, list(
    c("faltam", faltam_na_rodada(plan, rodada), "ainda na fila desta selecao"),
    c("media_min", if (is.na(eta_s$media_min)) "" else fmt_num(eta_s$media_min, 2), "tiles ok nesta maquina"),
    c("restante", eta_s$texto, "dias, horas e minutos"),
    c("previsao_termino", eta_s$previsao, "horario local")
  ))
  atomic_write(path, function(tmp) {
    data.table::fwrite(
      data.table::rbindlist(lapply(stats, function(x) data.table::data.table(metrica = x[[1]], valor = x[[2]], nota = x[[3]]))),
      tmp, bom = TRUE
    )
  })
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
    say("Atalho do LAZ: ", dest)
    return(dest)
  }
  if (file.exists(dest)) unlink(dest, force = TRUE)
  say("Copiando o LAZ para a saida: ", src)
  ok <- file.copy(src, dest, overwrite = TRUE, copy.date = TRUE)
  if (!isTRUE(ok) || !file.exists(dest)) {
    stop(
      "Nao copiei\n  ", src, "\npara\n  ", dest,
      call. = FALSE
    )
  }
  say("Copia pronta: ", dest)
  dest
}

wipe_dir_contents <- function(path) {
  if (!dir.exists(path)) return(invisible())
  old <- list.files(path, full.names = TRUE, all.files = TRUE, no.. = TRUE)
  if (length(old)) unlink(old, recursive = TRUE, force = TRUE)
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
  txt <- paste(unique(as.character(dt[[col]])), collapse = " | ")
  txt <- gsub("[\r\n\t]+", " ", txt)
  txt <- trimws(txt)
  if (!nzchar(txt)) return("")
  clip_err(txt, 1200)
}

source_helper <- function(path) {
  if (!file.exists(path)) {
    stop("Nao achei o pipeline oficial: ", path, call. = FALSE)
  }
  say("Carregando o pipeline oficial do GeoTrees. Os pacotes ja foram conferidos no inicio.")
  say("  ", path)
  suppressPackageStartupMessages(sys.source(path, envir = globalenv()))
  if (!exists("process.dataset", mode = "function", envir = globalenv())) {
    stop("process.dataset nao apareceu depois de carregar o helper.", call. = FALSE)
  }
  say("Pipeline GeoTrees carregado.")
}

run_one <- function(src, opt, paths, inbox, metadata, epsg_proc = NA_integer_) {
  ext <- tolower(tools::file_ext(src))
  type_file <- if (ext %in% c("laz", "las")) ext else "laz"
  dest <- link_into_inbox(src, inbox, type_file)
  stem <- tools::file_path_sans_ext(basename(dest))
  zone <- utm_zone_folder(epsg_proc)
  staging_root <- file.path(paths$root, "_staging")
  out_staging <- file.path(staging_root, stem)
  out_final <- file.path(paths$processed, zone, stem)
  dir.create(paths$processed, recursive = TRUE, showWarnings = FALSE)
  dir.create(paths$tmp, recursive = TRUE, showWarnings = FALSE)
  dir.create(paths$logs, recursive = TRUE, showWarnings = FALSE)
  wipe_dir_contents(paths$tmp)
  wipe_dir_contents(staging_root)
  dir.create(staging_root, recursive = TRUE, showWarnings = FALSE)
  logfile <- file.path(paths$logs, paste0(toupper(stem), "_issues.csv"))
  if (file.exists(logfile)) unlink(logfile)
  Sys.setenv(
    OPENBLAS_NUM_THREADS = "1",
    OMP_NUM_THREADS = "1",
    MKL_NUM_THREADS = "1",
    VECLIB_MAXIMUM_THREADS = "1",
    NUMEXPR_NUM_THREADS = "1"
  )
  t0 <- Sys.time()
  status <- tryCatch(
    process.dataset(
      name_job = opt$name_job,
      type_file = type_file,
      dir_dataset = inbox,
      dir_processed = staging_root,
      tmpdir_processing = paths$tmp,
      path_lastools = "",
      metadata = metadata,
      resolution = opt$resolution,
      n_cores = opt$n_cores,
      size_tile = opt$size_tile,
      size_buffer = opt$size_buffer,
      force.utm = opt$force_utm,
      remove.vlr = FALSE,
      remove.evlr = FALSE,
      type_architecture = "64",
      use.blast2dem = opt$use_blast2dem,
      patterns_skip = character(),
      cleanup = TRUE,
      retile = FALSE,
      by_file = TRUE,
      force.recompute = isTRUE(opt$force),
      print.summary_job = FALSE,
      logfile = logfile
    ),
    error = function(e) e
  )
  minutos <- as.numeric(difftime(Sys.time(), t0, units = "mins"))
  unlink(dest)
  detalhe <- issue_text(logfile)
  finish_fail <- function(erro) {
    if (dir.exists(out_staging)) unlink(out_staging, recursive = TRUE, force = TRUE)
    if (nzchar(detalhe)) erro <- paste0(erro, " | ", detalhe)
    if (nzchar(logfile)) erro <- paste0(erro, " | log: ", logfile)
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

log_line <- function(path, ...) {
  msg <- paste0(format(Sys.time(), "%Y-%m-%d %H:%M:%S"), " | ", paste0(..., collapse = ""))
  cat(msg, "\n")
  flush.console()
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
    "No tasks are running|nenhuma tarefa|Cannot find a process|nao encontrado|não encontrado",
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
    unlink(path)
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
  acquire_lock(paths$lock)
  on.exit(unlink(paths$lock), add = TRUE)

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
    cached_idx <- load_artic_index(paths$artic_index, paths$artic_index_meta, opt$articulacao)
    if (!is.null(cached_idx)) {
      idx <- cached_idx
    } else {
      say("Lendo articulacao (atributos): ", opt$articulacao)
      artic_tab <- read_artic_table(opt$articulacao, opt$articulacao_layer)
      say("Montando o indice NOMENC_2K / NOMENC_5K / NOMENC_10K...")
      idx <- build_index(artic_tab)
      save_artic_index(paths$artic_index, paths$artic_index_meta, idx, opt$articulacao)
      say("Indice da articulacao gravado. Na proxima rodada o GPKG nao e lido de novo.")
      say("  ", paths$artic_index)
    }
    say("IDs indexados: ", nrow(idx), ".")
  }

  say("Lendo registro: ", paths$registry)
  registry <- load_registry(paths$registry)
  say("Registro: ", nrow(registry), " linha(s).")
  known_ids <- if (nrow(registry)) as.character(registry[status == "processed", tile_id]) else character()
  known_ids <- known_ids[!is.na(known_ids) & nzchar(known_ids)]
  if (!opt$dry_run && !opt$report_only) {
    say("Limpando pastas incompletas em processed/ (sem _INFO_summary_processing.csv)...")
    n_prune <- prune_incomplete_processed(paths$processed)
    if (n_prune > 0L) say("  removidas: ", n_prune)
  }
  done <- scan_done(paths$processed, known_ids)
  if (!opt$dry_run) {
    say("Cruzando o registro com os tiles prontos e com a articulacao...")
    registry <- adopt_disk(registry, done, maquina(), usuario())
    registry <- fill_artic_on_registry(registry, idx)
    say("Registro cruzado.")
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
      inv <- load_laz_index(paths$laz_index, paths$laz_index_meta, laz_dir_n)
      if (is.null(inv)) {
        say("Primeira leitura desta pasta de LAZ. As proximas rodadas usam o indice gravado.")
        files <- list_lidar(laz_dir_n, paths$root)
        inv <- dedupe_lidar(files)
        save_laz_index(paths$laz_index, paths$laz_index_meta, inv, laz_dir_n)
        say("Indice de LAZ gravado. Na proxima rodada o tamanho dos arquivos nao e lido de novo.")
        say("  ", paths$laz_index)
      }
      plan <- make_plan(
        inv$dt, idx, blocos, done_ids,
        if (opt$report_only) 0L else opt$limit, opt$force, inv$n_dup
      )
      n_dup <- attr(plan, "n_dup")
      if (is.null(n_dup)) n_dup <- 0L
    }
  }
  if (!opt$dry_run) {
    say("Completando caminhos de LAZ no registro...")
    registry <- fill_laz_from_plan(registry, plan)
    if (nrow(registry)) {
      say("Gravando registro: ", paths$registry)
      save_registry(paths$registry, registry)
      say("Registro gravado.")
    } else {
      say("Registro ainda vazio. Nada para gravar agora.")
    }
  }

  cat("\n")
  cat("Maquina: ", maquina(), "\n", sep = "")
  cat("LAZ:     ", opt$laz_dir, "\n", sep = "")
  cat("BLOCO:   ", if (length(blocos)) paste(blocos, collapse = ", ") else "(todos na pasta)", "\n", sep = "")
  cat("Saida:   ", paths$root, "\n", sep = "")
  if (!is.null(plan)) {
    cat("Arquivos:", nrow(plan), "\n")
    cat("  processar:    ", sum(plan$acao == "processar"), "\n", sep = "")
    cat("  pular:        ", sum(plan$acao == "pular"), "\n", sep = "")
    cat("  fora do bloco:", sum(plan$acao == "fora_do_bloco"), "\n", sep = "")
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
    hist <- load_historico(paths$historico)
    linhas <- build_report(opt, paths, registry, hist, plan, rodada, blocos, n_dup, modo, elapsed)
    dest <- if (modo %in% c("dry", "lista")) paths$plano else paths$md
    write_utf8(dest, linhas)
    if (!modo %in% c("dry", "lista")) {
      save_summary(paths$summary, opt, registry, plan, rodada, elapsed, modo)
    }
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

  if (artic_ok) ensure_artic_cache(opt$articulacao, paths$artic_cache)

  if (opt$report_only) {
    if (artic_ok && nrow(registry)) {
      say("Atualizando GPKG do registro...")
      rebuild_gpkg(paths$gpkg, registry, paths$artic_cache, opt$articulacao_layer)
      say("GPKG atualizado.")
    }
    dest <- write_md("report")
    cat("Relatorio: ", dest, "\n", sep = "")
    cat("GPKG:      ", paths$gpkg, "\n", sep = "")
    return(0L)
  }

  fila <- if (is.null(plan)) plan else plan[acao == "processar"]
  if (is.null(fila) || !nrow(fila)) {
    if (artic_ok && nrow(registry)) {
      rebuild_gpkg(paths$gpkg, registry, paths$artic_cache, opt$articulacao_layer)
    }
    dest <- write_md("rodada")
    cat("Nada novo para processar.\nRelatorio: ", dest, "\n", sep = "")
    return(0L)
  }

  source(file.path(dir_script, "als_registry_api.R"), local = FALSE)
  als <- als_cfg(opt, pipeline = "opensource")
  pending_als <- list()
  if (als$enabled) {
    say(
      "ALS API: ", als$url,
      " | claim por tile | report a cada ", als$report_every,
      " | lease ", als$lease_seconds, "s"
    )
    als_upsert_plan(als, plan, say_fn = say)
  } else {
    say("ALS API: desligada (sem ALS_MACHINE_TOKEN / --no-als-api).")
  }
  source_helper(file.path(dir_script, "ALS_processing_helperfunctions.R"))
  say("Metadata GCA: inline (acq_crs por tile via articulacao).")
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
  cat("Retomada: ", n_pulados, " tile(s) ja prontos nesta saida e nao serao refeitos.\n", sep = "")
  cat("Na fila agora: ", n_fila, ".\n", sep = "")
  cat("Se o script parou no meio de um .laz, esse arquivo comeca de novo. Os que ja terminaram ficam pulados.\n")
  cat("Ordem: um grupo EPSG por vez (32722 depois 32723, conforme a articulacao).\n")
  mostrar_eta(rodada, registry, n_fila)

  epsg_atual <- NA_integer_
  for (i in seq_len(n_fila)) {
    if (file.exists(paths$parar)) {
      parou <- TRUE
      log_line(paths$runlog, "PARAR.txt encontrado. Parando antes do tile ", i, "/", n_fila, ".")
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
      append_problema(paths$problemas, meta$tile_id, result$erro)
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
      save_registry(paths$registry, registry)
      append_historico(paths$historico, list(
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
          say_fn = say
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
      claim <- als_claim_one(als, meta$tile_id, maquina(), usuario())
      if (!isTRUE(claim$claimed)) {
        say("ALS claim pulou ", meta$tile_id, ": ", claim$reason)
        log_line(paths$runlog, sprintf("[%d/%d] PULADO (API) %s | %s", i, n_fila, meta$tile_id, claim$reason))
        next
      }
      if (!isTRUE(claim$ok) && isTRUE(claim$soft)) {
        say("AVISO ALS claim soft-fail; processando mesmo assim: ", claim$reason)
      } else if (!isTRUE(claim$ok)) {
        n_erro_run <- n_erro_run + 1L
        say("FALHOU claim ", meta$tile_id, ": ", claim$reason)
        next
      } else {
        say("ALS claim ok: ", meta$tile_id)
      }
    }
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
      append_problema(paths$problemas, meta$tile_id, result$erro)
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
    save_registry(paths$registry, registry)
    append_historico(paths$historico, list(
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
        feature <- row_to_sf(row, geom_one(paths$artic_cache, opt$articulacao_layer, row$tile_id))
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
        say_fn = say
      )
    }
    faltam_depois <- n_fila - i
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

  if (als$enabled && length(pending_als)) {
    say("ALS flush final: ", length(pending_als), " tile(s)...")
    als_report_flush(als, pending_als, say_fn = say)
  }

  if (artic_ok && nrow(registry)) {
    cat("Fechando GPKG do registro...\n")
    tryCatch(
      rebuild_gpkg(paths$gpkg, registry, paths$artic_cache, opt$articulacao_layer),
      error = function(e) log_line(paths$runlog, "AVISO rebuild GPKG: ", conditionMessage(e))
    )
  }
  modo_fim <- if (parou) "parado" else "rodada"
  dest <- write_md(modo_fim)
  cat("\nRelatorio: ", dest, "\n", sep = "")
  cat("Registro:  ", paths$registry, "\n", sep = "")
  cat("GPKG:      ", paths$gpkg, "\n", sep = "")
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
