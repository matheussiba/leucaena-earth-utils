# ALS registry client for leucaena.earth
# Docs: leucaena-earth-platform/docs/als-registry.md
#
# Status: 0 pending | 1 ok | 2 fail | 3 claim | 4 skipped
# Flush: claim on start, report every N tiles, report at end of run.

als_ensure_http_pkgs <- function() {
  need <- c("jsonlite", "curl")
  miss <- need[!vapply(need, requireNamespace, logical(1), quietly = TRUE)]
  if (!length(miss)) return(invisible(TRUE))
  say_api <- function(...) cat(paste0(...), "\n", sep = "")
  say_api("Instalando pacotes HTTP para a API ALS: ", paste(miss, collapse = ", "))
  ok <- TRUE
  for (p in miss) {
    tryCatch(
      utils::install.packages(p, repos = "https://cloud.r-project.org", quiet = TRUE),
      error = function(e) {
        ok <<- FALSE
        say_api("Falha ao instalar ", p, ": ", conditionMessage(e))
      }
    )
  }
  still <- need[!vapply(need, requireNamespace, logical(1), quietly = TRUE)]
  if (length(still)) {
    stop(
      "Pacotes HTTP ausentes para a API ALS: ", paste(still, collapse = ", "),
      call. = FALSE
    )
  }
  invisible(ok)
}

als_normalize_url <- function(url) {
  u <- trimws(as.character(url[[1]]))
  u <- sub("/+$", "", u)
  if (!nzchar(u)) u <- "https://leucaena.earth"
  u
}

als_cfg <- function(opt, pipeline = c("lastools", "opensource")) {
  pipeline <- match.arg(pipeline)
  token <- trimws(as.character(opt$als_api_token %||% ""))
  if (!nzchar(token)) token <- trimws(Sys.getenv("ALS_MACHINE_TOKEN", ""))
  url <- als_normalize_url(opt$als_api_url %||% Sys.getenv("ALS_API_URL", "https://leucaena.earth"))
  enabled <- opt$als_api
  if (is.null(enabled) || (length(enabled) == 1L && is.na(enabled))) {
    enabled <- nzchar(token)
  }
  enabled <- isTRUE(enabled)
  every <- suppressWarnings(as.integer(opt$als_report_every %||% 5L))
  if (is.na(every) || every < 1L) every <- 5L
  lease <- suppressWarnings(as.integer(opt$als_lease_seconds %||% 7200L))
  if (is.na(lease) || lease < 60L) lease <- 7200L
  list(
    enabled = enabled,
    url = url,
    token = token,
    pipeline = pipeline,
    report_every = every,
    lease_seconds = lease,
    allow_retry = isTRUE(opt$als_allow_retry),
    soft_fail = isTRUE(opt$als_soft_fail),
    track = "lidar"
  )
}

`%||%` <- function(a, b) if (is.null(a) || (length(a) == 1L && is.na(a))) b else a

als_http_json <- function(cfg, method, path, body = NULL) {
  als_ensure_http_pkgs()
  url <- paste0(cfg$url, path)
  headers <- list(
    "X-ALS-Token" = cfg$token,
    "Content-Type" = "application/json",
    "Accept" = "application/json"
  )
  h <- curl::new_handle()
  curl::handle_setopt(h, customrequest = toupper(method))
  do.call(curl::handle_setheaders, c(list(h), headers))
  if (!is.null(body)) {
    payload <- jsonlite::toJSON(body, auto_unbox = TRUE, null = "null", digits = NA)
    curl::handle_setopt(h, postfields = payload)
  }
  res <- curl::curl_fetch_memory(url, handle = h)
  txt <- rawToChar(res$content)
  parsed <- tryCatch(
    if (nzchar(txt)) jsonlite::fromJSON(txt, simplifyVector = FALSE) else list(),
    error = function(e) list(parse_error = conditionMessage(e), raw_text = txt)
  )
  list(
    status = as.integer(res$status_code),
    body = parsed,
    raw = txt
  )
}

als_claim_one <- function(cfg, tile_id, hostname, usuario) {
  if (!isTRUE(cfg$enabled)) {
    return(list(ok = TRUE, claimed = TRUE, reason = "api_off"))
  }
  if (!nzchar(cfg$token)) {
    stop("ALS API ligada sem token. Defina ALS_MACHINE_TOKEN ou --als-api-token.", call. = FALSE)
  }
  tile_id <- toupper(trimws(tile_id))
  resp <- tryCatch(
    als_http_json(
      cfg, "POST", "/api/v1/als/tiles/claim",
      body = list(
        tile_ids = list(tile_id),
        hostname = hostname,
        usuario = usuario,
        pipeline = cfg$pipeline,
        lease_seconds = cfg$lease_seconds,
        allow_retry = isTRUE(cfg$allow_retry),
        track = cfg$track
      )
    ),
    error = function(e) list(status = 0L, body = list(error = conditionMessage(e)), raw = "")
  )
  if (resp$status < 200L || resp$status >= 300L) {
    msg <- if (!is.null(resp$body$error)) as.character(resp$body$error) else resp$raw
    if (isTRUE(cfg$soft_fail)) {
      return(list(ok = FALSE, claimed = TRUE, reason = paste0("http_", resp$status, ": ", msg), soft = TRUE))
    }
    return(list(ok = FALSE, claimed = FALSE, reason = paste0("http_", resp$status, ": ", msg)))
  }
  claimed_ids <- character()
  if (!is.null(resp$body$claimed) && length(resp$body$claimed)) {
    claimed_ids <- toupper(vapply(resp$body$claimed, function(x) as.character(x$tile_id %||% ""), character(1)))
  }
  if (tile_id %in% claimed_ids) {
    return(list(ok = TRUE, claimed = TRUE, reason = "claimed"))
  }
  reason <- "skipped"
  if (!is.null(resp$body$skipped) && length(resp$body$skipped)) {
    for (s in resp$body$skipped) {
      if (identical(toupper(as.character(s$tile_id %||% "")), tile_id)) {
        reason <- as.character(s$reason %||% "skipped")
        break
      }
    }
  }
  list(ok = TRUE, claimed = FALSE, reason = reason)
}

als_result_payload <- function(row, status_code, pipeline, out_dir) {
  list(
    tile_id = toupper(as.character(row$tile_id)),
    status_code = as.integer(status_code),
    minutos = if (is.null(row$minutos) || length(row$minutos) == 0L || is.na(row$minutos)) NULL else as.numeric(row$minutos),
    tamanho_mb = if (is.null(row$tamanho_mb) || length(row$tamanho_mb) == 0L || is.na(row$tamanho_mb)) NULL else as.numeric(row$tamanho_mb),
    erro = if (is.null(row$erro) || !nzchar(as.character(row$erro))) NULL else as.character(row$erro),
    hostname = as.character(row$hostname %||% ""),
    usuario = as.character(row$usuario %||% ""),
    pasta_saida = as.character(row$pasta_saida %||% ""),
    out_dir = as.character(out_dir %||% ""),
    rodada_id = as.character(row$rodada_id %||% ""),
    pipeline = pipeline,
    iniciado_utc = as.character(row$iniciado_utc %||% ""),
    finalizado_utc = as.character(row$finalizado_utc %||% ""),
    bloco = as.character(row$bloco %||% ""),
    laz_nome = as.character(row$laz_nome %||% ""),
    laz_path = as.character(row$laz_path %||% "")
  )
}

als_status_from_local <- function(status, skipped_na = FALSE) {
  if (isTRUE(skipped_na)) return(4L)
  if (identical(status, "processed")) return(1L)
  if (identical(status, "erro")) return(2L)
  if (identical(status, "pular") || identical(status, "skipped")) return(4L)
  2L
}

als_report_flush <- function(cfg, pending, say_fn = message) {
  if (!isTRUE(cfg$enabled) || !length(pending)) {
    return(list(pending = pending, ok = TRUE))
  }
  if (!nzchar(cfg$token)) {
    return(list(pending = pending, ok = FALSE, error = "sem token"))
  }
  # API max 200; we flush in chunks of report_every or all remaining
  chunks <- split(pending, ceiling(seq_along(pending) / 100))
  left <- list()
  for (ch in chunks) {
    resp <- tryCatch(
      als_http_json(
        cfg, "POST", "/api/v1/als/tiles/report",
        body = list(track = cfg$track, results = ch)
      ),
      error = function(e) list(status = 0L, body = list(error = conditionMessage(e)), raw = "")
    )
    if (resp$status < 200L || resp$status >= 300L) {
      msg <- if (!is.null(resp$body$error)) as.character(resp$body$error) else resp$raw
      say_fn("AVISO ALS report falhou (HTTP ", resp$status, "): ", msg)
      left <- c(left, ch)
    } else {
      say_fn("ALS report: ", length(ch), " tile(s) enviados.")
    }
  }
  list(pending = left, ok = !length(left))
}

als_buffer_push <- function(cfg, pending, payload, say_fn = message) {
  pending <- c(pending, list(payload))
  if (length(pending) >= cfg$report_every) {
    out <- als_report_flush(cfg, pending, say_fn = say_fn)
    return(out$pending)
  }
  pending
}

als_upsert_plan <- function(cfg, plan_dt, say_fn = message) {
  if (!isTRUE(cfg$enabled) || is.null(plan_dt) || !nrow(plan_dt)) return(invisible(FALSE))
  if (!nzchar(cfg$token)) return(invisible(FALSE))
  go <- plan_dt
  if ("acao" %in% names(go)) go <- go[acao %in% c("processar", "pular", "sem_crs")]
  if (!nrow(go)) return(invisible(FALSE))
  tiles <- lapply(seq_len(nrow(go)), function(i) {
    r <- go[i]
    list(
      tile_id = toupper(as.character(r$tile_id)),
      bloco = as.character(r$bloco %||% ""),
      laz_nome = as.character(r$laz_nome %||% ""),
      laz_path = as.character(r$laz_path %||% ""),
      nomenc_2k = as.character(r$nomenc_2k %||% ""),
      nomenc_5k = as.character(r$nomenc_5k %||% ""),
      nomenc_10k = as.character(r$nomenc_10k %||% ""),
      layer_artic = as.character(r$layer_artic %||% ""),
      periodvoo = as.character(r$periodvoo %||% ""),
      epsg_artic = if (is.na(r$epsg_artic)) NULL else as.integer(r$epsg_artic),
      epsg_proc = if (is.na(r$epsg_proc)) NULL else as.integer(r$epsg_proc)
    )
  })
  # chunk 500
  n <- length(tiles)
  for (start in seq(1L, n, by = 500L)) {
    end <- min(start + 499L, n)
    resp <- tryCatch(
      als_http_json(
        cfg, "POST", "/api/v1/als/tiles/upsert",
        body = list(tiles = tiles[start:end])
      ),
      error = function(e) list(status = 0L, body = list(error = conditionMessage(e)))
    )
    if (resp$status < 200L || resp$status >= 300L) {
      msg <- if (!is.null(resp$body$error)) as.character(resp$body$error) else "erro"
      say_fn("AVISO ALS upsert falhou: ", msg)
    } else {
      say_fn("ALS upsert: tiles ", start, "-", end, " / ", n)
    }
  }
  invisible(TRUE)
}
