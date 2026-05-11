# high_confidence_trade_eval.R
# ------------------------------------------------------------
# 检查“高置信度概率”到底有没有交易价值。
#
# 这个脚本会遍历 data 目录里的轮次 CSV，并在 90% / 80% / 70% / 60%
# 这些阈值上统计两类结果：
# 1. first_hit_per_round：每轮第一次达到该阈值时，若立刻买入，最终盈亏如何
# 2. all_hits：把所有达到该阈值的快照都算一次，最终命中率如何
#
# 交易解释：
# - 若 up_midpoint >= 0.90，视为“市场给出 UP 90% 概率”，买入 UP
# - 若 up_midpoint <= 0.10，视为“市场给出 DOWN 90% 概率”，买入 DOWN
# - 买入价格用对应 side 的 best ask
# - 到结算时，若买对则 payout = 1，否则 payout = 0
# - pnl = payout - entry_price
#
# 最终输出：
# - threshold_first_hits.csv
# - threshold_first_hit_summary.csv
# - threshold_all_hits.csv
# - threshold_all_hits_summary.csv
# - skipped_rounds.csv
#
# 用法：
#   Rscript projects/probability_calibration/high_confidence_trade_eval.R
#   Rscript projects/probability_calibration/high_confidence_trade_eval.R --data-dir data
#   Rscript projects/probability_calibration/high_confidence_trade_eval.R --data-dir data --n 100 --first-only
#   Rscript projects/probability_calibration/high_confidence_trade_eval.R --thresholds 0.9,0.8,0.7,0.6
#   source(".../high_confidence_trade_eval.R"); main(data_dir = ".../data")
# ------------------------------------------------------------

DEFAULT_THRESHOLDS <- c(0.9, 0.8, 0.7, 0.6)

get_script_dir <- function() {
  args_all <- commandArgs(trailingOnly = FALSE)
  file_args <- args_all[grep("^--file=", args_all)]
  if (length(file_args) > 0) {
    return(dirname(sub("^--file=", "", file_args[1])))
  }

  frame_files <- vapply(
    sys.frames(),
    function(frame) {
      if (!is.null(frame$ofile)) frame$ofile else NA_character_
    },
    character(1),
    USE.NAMES = FALSE
  )
  frame_files <- frame_files[!is.na(frame_files) & nzchar(frame_files)]
  if (length(frame_files) > 0) {
    return(dirname(normalizePath(frame_files[length(frame_files)], winslash = "/", mustWork = FALSE)))
  }

  getwd()
}

source(file.path(get_script_dir(), "performance_helpers.R"), local = TRUE)

find_repo_root <- function(start_dir) {
  current <- normalizePath(start_dir, winslash = "/", mustWork = FALSE)

  repeat {
    project_dir <- file.path(current, "projects", "probability_calibration")
    data_dir <- file.path(current, "data")

    if (dir.exists(project_dir) && dir.exists(data_dir)) {
      return(current)
    }

    parent <- dirname(current)
    if (identical(parent, current)) {
      return(NULL)
    }
    current <- parent
  }
}

parse_thresholds <- function(x) {
  if (is.null(x)) {
    return(DEFAULT_THRESHOLDS)
  }

  if (length(x) == 1L && is.character(x)) {
    x <- trimws(unlist(strsplit(x, ",", fixed = TRUE)))
  }

  vals <- suppressWarnings(as.numeric(x))
  vals <- vals[!is.na(vals) & vals > 0.5 & vals < 1]
  vals <- sort(unique(vals), decreasing = TRUE)

  if (length(vals) == 0) {
    stop("No valid thresholds provided. Use values between 0.5 and 1, e.g. 0.9,0.8,0.7,0.6")
  }

  vals
}

parse_args <- function(args) {
  opts <- list(
    data_dir = NULL,
    n = NULL,
    thresholds = DEFAULT_THRESHOLDS,
    output_dir = NULL,
    include_all_hits = TRUE,
    cores = NULL
  )

  i <- 1L
  while (i <= length(args)) {
    arg <- args[i]

    if (arg == "--data-dir" && i < length(args)) {
      opts$data_dir <- args[i + 1L]
      i <- i + 2L
      next
    }

    if (arg == "--n" && i < length(args)) {
      opts$n <- as.integer(args[i + 1L])
      i <- i + 2L
      next
    }

    if (arg == "--thresholds" && i < length(args)) {
      opts$thresholds <- parse_thresholds(args[i + 1L])
      i <- i + 2L
      next
    }

    if (arg == "--output-dir" && i < length(args)) {
      opts$output_dir <- args[i + 1L]
      i <- i + 2L
      next
    }

    if (arg == "--first-only") {
      opts$include_all_hits <- FALSE
      i <- i + 1L
      next
    }

    if (arg == "--cores" && i < length(args)) {
      opts$cores <- as.integer(args[i + 1L])
      i <- i + 2L
      next
    }

    i <- i + 1L
  }

  opts
}

resolve_data_dir <- function(explicit_dir = NULL) {
  if (!is.null(explicit_dir)) {
    return(normalizePath(explicit_dir, winslash = "/", mustWork = FALSE))
  }

  candidate_roots <- unique(c(
    find_repo_root(getwd()),
    find_repo_root(get_script_dir())
  ))
  candidate_roots <- candidate_roots[!is.na(candidate_roots) & nzchar(candidate_roots)]

  for (repo_root in candidate_roots) {
    primary <- file.path(repo_root, "data")
    raw_dir <- file.path(primary, "raw")

    if (dir.exists(primary) && length(list.files(primary, pattern = "\\.csv$", full.names = TRUE)) > 0) {
      return(primary)
    }
    if (dir.exists(raw_dir) && length(list.files(raw_dir, pattern = "\\.csv$", full.names = TRUE)) > 0) {
      return(raw_dir)
    }
  }

  stop("Could not locate data directory automatically. Please pass data_dir explicitly.")
}

resolve_output_dir <- function(explicit_dir = NULL) {
  if (!is.null(explicit_dir)) {
    return(normalizePath(explicit_dir, winslash = "/", mustWork = FALSE))
  }

  file.path(get_script_dir(), "artifacts", "high_confidence_trade_eval")
}

parse_timestamp_vector <- function(x) {
  if (inherits(x, "POSIXct")) {
    return(x)
  }

  x <- as.character(x)
  x <- trimws(x)
  result <- as.POSIXct(rep(NA_real_, length(x)), origin = "1970-01-01", tz = "UTC")
  missing_idx <- is.na(x) | !nzchar(x)
  if (all(missing_idx)) {
    return(result)
  }

  normalized_x <- x
  normalized_x[!missing_idx] <- sub("([+-][0-9]{2}):([0-9]{2})$", "\\1\\2", normalized_x[!missing_idx])
  values <- normalized_x[!missing_idx]
  parsed <- as.POSIXct(rep(NA_real_, length(values)), origin = "1970-01-01", tz = "UTC")
  formats <- c(
    "%Y-%m-%dT%H:%M:%OS%z",
    "%Y-%m-%dT%H:%M:%OSZ",
    "%Y-%m-%d %H:%M:%OS",
    "%Y-%m-%dT%H:%M:%OS",
    "%Y/%m/%d %H:%M:%OS",
    "%m/%d/%Y %H:%M:%OS"
  )

  for (fmt in formats) {
    trial <- suppressWarnings(tryCatch(
      as.POSIXct(values, format = fmt, tz = "UTC"),
      error = function(e) as.POSIXct(rep(NA_real_, length(values)), origin = "1970-01-01", tz = "UTC")
    ))
    fill <- is.na(parsed) & !is.na(trial)
    parsed[fill] <- trial[fill]
  }

  if (any(is.na(parsed))) {
    loose <- suppressWarnings(tryCatch(
      as.POSIXct(values[is.na(parsed)], tz = "UTC"),
      error = function(e) as.POSIXct(rep(NA_real_, sum(is.na(parsed))), origin = "1970-01-01", tz = "UTC")
    ))
    parsed[is.na(parsed)] <- loose
  }

  result[!missing_idx] <- parsed
  result
}

forward_fill <- function(x) {
  if (length(x) == 0 || all(is.na(x))) {
    return(x)
  }

  for (i in seq_along(x)) {
    if (is.na(x[i]) && i > 1L) {
      x[i] <- x[i - 1L]
    }
  }
  x
}

calc_midpoint <- function(bid, ask) {
  ifelse(!is.na(bid) & !is.na(ask), (bid + ask) / 2, NA_real_)
}

empty_hits_df <- function() {
  data.frame(
    file = character(0),
    threshold = numeric(0),
    threshold_label = character(0),
    round_start_utc = as.POSIXct(character(0), tz = "UTC"),
    hit_timestamp = as.POSIXct(character(0), tz = "UTC"),
    hit_elapsed = numeric(0),
    side = character(0),
    implied_prob_selected_side = numeric(0),
    up_midpoint = numeric(0),
    entry_price = numeric(0),
    final_side = character(0),
    win = integer(0),
    payout = numeric(0),
    pnl = numeric(0),
    stringsAsFactors = FALSE
  )
}

empty_skipped_df <- function() {
  data.frame(
    file = character(0),
    status = character(0),
    stringsAsFactors = FALSE
  )
}

bind_rows_safe <- function(dfs, empty_df) {
  dfs <- dfs[!vapply(dfs, is.null, logical(1))]
  dfs <- dfs[vapply(dfs, nrow, integer(1)) > 0L]
  if (length(dfs) == 0L) {
    return(empty_df)
  }
  do.call(rbind, dfs)
}

scalar_or_na <- function(x, default = NA) {
  if (length(x) == 0L || all(is.na(x))) {
    return(default)
  }
  unname(x[[1L]])
}

prepare_round <- function(df) {
  required_cols <- c("timestamp", "up_midpoint")
  if (!all(required_cols %in% names(df))) {
    return(list(df = NULL, status = "missing_columns"))
  }

  df$timestamp <- parse_timestamp_vector(df$timestamp)
  df <- df[!is.na(df$timestamp), , drop = FALSE]
  df <- df[order(df$timestamp), , drop = FALSE]
  if (nrow(df) == 0) {
    return(list(df = NULL, status = "no_valid_rows"))
  }

  numeric_cols <- intersect(
    c("up_best_bid", "up_best_ask", "up_midpoint", "down_best_bid", "down_best_ask", "down_midpoint"),
    names(df)
  )
  for (col in numeric_cols) {
    df[[col]] <- suppressWarnings(as.numeric(df[[col]]))
  }

  fill_cols <- intersect(
    c("up_best_bid", "up_best_ask", "up_midpoint", "down_best_bid", "down_best_ask", "down_midpoint"),
    names(df)
  )
  for (col in fill_cols) {
    df[[col]] <- forward_fill(df[[col]])
  }

  if (all(c("up_best_bid", "up_best_ask") %in% names(df))) {
    na_mid <- is.na(df$up_midpoint)
    df$up_midpoint[na_mid] <- calc_midpoint(df$up_best_bid[na_mid], df$up_best_ask[na_mid])
  }

  if (!("down_midpoint" %in% names(df))) {
    df$down_midpoint <- NA_real_
  }
  if (all(c("down_best_bid", "down_best_ask") %in% names(df))) {
    na_mid <- is.na(df$down_midpoint)
    df$down_midpoint[na_mid] <- calc_midpoint(df$down_best_bid[na_mid], df$down_best_ask[na_mid])
  }

  df <- df[!is.na(df$up_midpoint), , drop = FALSE]
  if (nrow(df) == 0) {
    return(list(df = NULL, status = "no_probability"))
  }

  df$elapsed <- as.numeric(difftime(df$timestamp, df$timestamp[1], units = "secs"))
  list(df = df, status = "ok")
}

determine_final_side <- function(df) {
  windows <- list(c(285, 298), c(240, 285))

  for (w in windows) {
    idx <- which(df$elapsed >= w[1] & df$elapsed <= w[2] & !is.na(df$up_midpoint))
    if (length(idx) == 0) {
      next
    }

    last_up <- tail(df$up_midpoint[idx], 1)
    if (last_up > 0.5) {
      return("up")
    }
    if (last_up < 0.5) {
      return("down")
    }
  }

  NA_character_
}

extract_threshold_hits <- function(df, csv_path, threshold, first_only = FALSE, final_side = NULL) {
  if (is.null(final_side)) {
    final_side <- determine_final_side(df)
  }
  if (is.na(final_side)) {
    return(empty_hits_df())
  }

  side <- rep(NA_character_, nrow(df))
  side[df$up_midpoint >= threshold] <- "up"
  side[df$up_midpoint <= (1 - threshold)] <- "down"

  eligible_idx <- which(!is.na(side))
  if (length(eligible_idx) == 0L) {
    return(empty_hits_df())
  }

  hit_rows <- lapply(eligible_idx, function(i) {
    hit_side <- side[i]
    ask_col <- if (identical(hit_side, "up")) "up_best_ask" else "down_best_ask"
    if (!(ask_col %in% names(df))) {
      return(NULL)
    }

    entry_price <- suppressWarnings(as.numeric(df[[ask_col]][i]))
    if (is.na(entry_price) || entry_price <= 0 || entry_price > 1) {
      return(NULL)
    }

    implied_prob <- if (identical(hit_side, "up")) {
      df$up_midpoint[i]
    } else {
      1 - df$up_midpoint[i]
    }
    payout <- if (identical(hit_side, final_side)) 1 else 0
    row <- list(
      file = scalar_or_na(basename(csv_path), NA_character_),
      threshold = scalar_or_na(threshold, NA_real_),
      threshold_label = scalar_or_na(paste0(round(threshold * 100), "%"), NA_character_),
      round_start_utc = scalar_or_na(df$timestamp[1], as.POSIXct(NA, tz = "UTC")),
      hit_timestamp = scalar_or_na(df$timestamp[i], as.POSIXct(NA, tz = "UTC")),
      hit_elapsed = scalar_or_na(df$elapsed[i], NA_real_),
      side = scalar_or_na(hit_side, NA_character_),
      implied_prob_selected_side = scalar_or_na(implied_prob, NA_real_),
      up_midpoint = scalar_or_na(df$up_midpoint[i], NA_real_),
      entry_price = scalar_or_na(entry_price, NA_real_),
      final_side = scalar_or_na(final_side, NA_character_),
      win = scalar_or_na(as.integer(identical(hit_side, final_side)), NA_integer_),
      payout = scalar_or_na(payout, NA_real_),
      pnl = scalar_or_na(payout - entry_price, NA_real_)
    )
    as.data.frame(row, stringsAsFactors = FALSE)
  })

  out <- bind_rows_safe(hit_rows, empty_hits_df())
  if (first_only && nrow(out) > 1L) {
    out <- out[1, , drop = FALSE]
  }
  out
}

analyze_one_file <- function(csv_path, thresholds = DEFAULT_THRESHOLDS, include_all_hits = TRUE) {
  raw_df <- tryCatch(
    fast_read_csv(csv_path),
    error = function(e) NULL
  )

  if (is.null(raw_df)) {
    return(list(
      first_hits = empty_hits_df(),
      all_hits = empty_hits_df(),
      skipped = data.frame(file = basename(csv_path), status = "read_error", stringsAsFactors = FALSE)
    ))
  }

  prepared <- prepare_round(raw_df)
  if (!identical(prepared$status, "ok")) {
    return(list(
      first_hits = empty_hits_df(),
      all_hits = empty_hits_df(),
      skipped = data.frame(file = basename(csv_path), status = prepared$status, stringsAsFactors = FALSE)
    ))
  }

  df <- prepared$df
  final_side <- determine_final_side(df)
  if (is.na(final_side)) {
    return(list(
      first_hits = empty_hits_df(),
      all_hits = empty_hits_df(),
      skipped = data.frame(file = basename(csv_path), status = "missing_label", stringsAsFactors = FALSE)
    ))
  }

  first_hits <- lapply(thresholds, function(th) {
    extract_threshold_hits(df, csv_path, threshold = th, first_only = TRUE, final_side = final_side)
  })
  all_hits <- if (isTRUE(include_all_hits)) {
    lapply(thresholds, function(th) {
      extract_threshold_hits(df, csv_path, threshold = th, first_only = FALSE, final_side = final_side)
    })
  } else {
    list(empty_hits_df())
  }

  list(
    first_hits = bind_rows_safe(first_hits, empty_hits_df()),
    all_hits = bind_rows_safe(all_hits, empty_hits_df()),
    skipped = empty_skipped_df()
  )
}

summarize_threshold_hits <- function(hits_df, thresholds = DEFAULT_THRESHOLDS) {
  rows <- lapply(thresholds, function(th) {
    part <- hits_df[hits_df$threshold == th, , drop = FALSE]
    if (nrow(part) == 0L) {
      return(data.frame(
        threshold = th,
        threshold_label = paste0(round(th * 100), "%"),
        opportunities = 0L,
        unique_rounds = 0L,
        up_trades = 0L,
        down_trades = 0L,
        wins = 0L,
        losses = 0L,
        win_rate = NA_real_,
        avg_entry_price = NA_real_,
        breakeven_win_rate = NA_real_,
        total_cost = 0,
        total_payout = 0,
        total_pnl = 0,
        avg_pnl_per_trade = NA_real_,
        roi_on_cost = NA_real_,
        stringsAsFactors = FALSE
      ))
    }

    total_cost <- sum(part$entry_price, na.rm = TRUE)
    total_payout <- sum(part$payout, na.rm = TRUE)
    total_pnl <- sum(part$pnl, na.rm = TRUE)

    data.frame(
      threshold = th,
      threshold_label = paste0(round(th * 100), "%"),
      opportunities = nrow(part),
      unique_rounds = length(unique(part$file)),
      up_trades = sum(part$side == "up"),
      down_trades = sum(part$side == "down"),
      wins = sum(part$win, na.rm = TRUE),
      losses = sum(1L - part$win, na.rm = TRUE),
      win_rate = mean(part$win, na.rm = TRUE),
      avg_entry_price = mean(part$entry_price, na.rm = TRUE),
      breakeven_win_rate = mean(part$entry_price, na.rm = TRUE),
      total_cost = total_cost,
      total_payout = total_payout,
      total_pnl = total_pnl,
      avg_pnl_per_trade = mean(part$pnl, na.rm = TRUE),
      roi_on_cost = if (total_cost > 0) total_pnl / total_cost else NA_real_,
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, rows)
}

main <- function(
  data_dir = NULL,
  n = NULL,
  thresholds = DEFAULT_THRESHOLDS,
  output_dir = NULL,
  include_all_hits = TRUE,
  cores = NULL
) {
  if (is.null(data_dir) || is.null(n) || missing(thresholds) || is.null(output_dir) || missing(include_all_hits) || is.null(cores)) {
    opts <- parse_args(commandArgs(trailingOnly = TRUE))
    if (is.null(data_dir)) {
      data_dir <- opts$data_dir
    }
    if (is.null(n)) {
      n <- opts$n
    }
    if (missing(thresholds)) {
      thresholds <- opts$thresholds
    }
    if (is.null(output_dir)) {
      output_dir <- opts$output_dir
    }
    if (missing(include_all_hits)) {
      include_all_hits <- opts$include_all_hits
    }
    if (is.null(cores)) {
      cores <- opts$cores
    }
  }

  data_dir <- resolve_data_dir(data_dir)
  output_dir <- resolve_output_dir(output_dir)
  thresholds <- parse_thresholds(thresholds)

  csv_files <- list.files(data_dir, pattern = "\\.csv$", full.names = TRUE)
  if (length(csv_files) == 0L) {
    stop(sprintf("No CSV files found in %s", data_dir))
  }

  info <- file.info(csv_files)
  info$file <- rownames(info)
  info <- info[order(info$mtime, decreasing = TRUE), , drop = FALSE]
  if (!is.null(n) && !is.na(n) && n > 0) {
    info <- info[seq_len(min(as.integer(n), nrow(info))), , drop = FALSE]
  }
  selected_files <- info$file

  use_cores <- resolve_cores(cores, n_tasks = length(selected_files))
  analyzed <- parallel_map(
    selected_files,
    analyze_one_file,
    thresholds = thresholds,
    include_all_hits = include_all_hits,
    cores = use_cores
  )
  first_hits <- bind_rows_safe(lapply(analyzed, `[[`, "first_hits"), empty_hits_df())
  all_hits <- bind_rows_safe(lapply(analyzed, `[[`, "all_hits"), empty_hits_df())
  skipped <- bind_rows_safe(lapply(analyzed, `[[`, "skipped"), empty_skipped_df())

  first_summary <- summarize_threshold_hits(first_hits, thresholds = thresholds)
  all_summary <- summarize_threshold_hits(all_hits, thresholds = thresholds)

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  first_hits_path <- file.path(output_dir, "threshold_first_hits.csv")
  first_summary_path <- file.path(output_dir, "threshold_first_hit_summary.csv")
  all_hits_path <- file.path(output_dir, "threshold_all_hits.csv")
  all_summary_path <- file.path(output_dir, "threshold_all_hits_summary.csv")
  skipped_path <- file.path(output_dir, "skipped_rounds.csv")

  write.csv(first_hits, first_hits_path, row.names = FALSE, na = "")
  write.csv(first_summary, first_summary_path, row.names = FALSE, na = "")
  write.csv(all_hits, all_hits_path, row.names = FALSE, na = "")
  write.csv(all_summary, all_summary_path, row.names = FALSE, na = "")
  write.csv(skipped, skipped_path, row.names = FALSE, na = "")

  cat("Data dir:", data_dir, "\n")
  cat("Files used:", length(selected_files), "\n")
  cat("Worker processes:", use_cores, "\n")
  cat("Thresholds:", paste0(round(thresholds * 100), "%", collapse = ", "), "\n\n")

  cat("First hit per round summary (closest to '每轮一到阈值就买一次'):\n")
  print(first_summary, row.names = FALSE)

  if (isTRUE(include_all_hits)) {
    cat("\nAll threshold-hit snapshots summary:\n")
    print(all_summary, row.names = FALSE)
  }

  if (nrow(skipped) > 0) {
    cat("\nSkipped rounds:\n")
    print(skipped, row.names = FALSE)
  }

  cat("\nWrote:", first_hits_path, "\n")
  cat("Wrote:", first_summary_path, "\n")
  cat("Wrote:", all_hits_path, "\n")
  cat("Wrote:", all_summary_path, "\n")
  cat("Wrote:", skipped_path, "\n")

  invisible(list(
    first_hits = first_hits,
    first_summary = first_summary,
    all_hits = all_hits,
    all_summary = all_summary,
    skipped = skipped
  ))
}

if (sys.nframe() == 0) {
  main()
}
