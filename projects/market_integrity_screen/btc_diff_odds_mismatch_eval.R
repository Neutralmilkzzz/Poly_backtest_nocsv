# btc_diff_odds_mismatch_eval.R
# ------------------------------------------------------------
# 统计在某个时间点上：
# - btc_diff 明显为正，但盘口明显偏向 down
# - 或 btc_diff 明显为负，但盘口明显偏向 up
# 这种“BTC 方向 vs 盘口方向不一致”的轮次里，
# 最终到底是 BTC 一侧赢得更多，还是盘口更高的一侧赢得更多。
#
# 默认口径：
# - checkpoint_seconds = 240
# - odds_edge = 0.05  （至少偏离 0.5 五个点）
# - btc_diff 直接取该 checkpoint 之前最后一个非 NA 值
#
# 输出：
# - mismatch_rounds.csv
# - overall_summary.csv
# - alignment_summary.csv
# - mismatch_type_summary.csv
# - skipped_rounds.csv
# ------------------------------------------------------------

DEFAULT_CHECKPOINT_SECONDS <- 240
DEFAULT_ODDS_EDGE <- 0.05

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

source(file.path(get_script_dir(), "..", "probability_calibration", "performance_helpers.R"), local = TRUE)

find_repo_root <- function(start_dir) {
  current <- normalizePath(start_dir, winslash = "/", mustWork = FALSE)

  repeat {
    if (dir.exists(file.path(current, "projects", "market_integrity_screen"))) {
      return(current)
    }
    parent <- dirname(current)
    if (identical(parent, current)) {
      return(NULL)
    }
    current <- parent
  }
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
  file.path(get_script_dir(), "artifacts", "btc_diff_odds_mismatch")
}

parse_args <- function(args) {
  opts <- list(
    data_dir = NULL,
    n = NULL,
    checkpoint_seconds = DEFAULT_CHECKPOINT_SECONDS,
    odds_edge = DEFAULT_ODDS_EDGE,
    output_dir = NULL,
    cores = NULL,
    min_rows = 500L
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
    if (arg == "--checkpoint-seconds" && i < length(args)) {
      opts$checkpoint_seconds <- as.numeric(args[i + 1L])
      i <- i + 2L
      next
    }
    if (arg == "--odds-edge" && i < length(args)) {
      opts$odds_edge <- as.numeric(args[i + 1L])
      i <- i + 2L
      next
    }
    if (arg == "--output-dir" && i < length(args)) {
      opts$output_dir <- args[i + 1L]
      i <- i + 2L
      next
    }
    if (arg == "--cores" && i < length(args)) {
      opts$cores <- as.integer(args[i + 1L])
      i <- i + 2L
      next
    }
    if (arg == "--min-rows" && i < length(args)) {
      opts$min_rows <- as.integer(args[i + 1L])
      i <- i + 2L
      next
    }

    i <- i + 1L
  }

  opts
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
    "%Y-%m-%dT%H:%M:%OS"
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

last_value_before <- function(df, column, checkpoint_seconds) {
  if (!(column %in% names(df))) {
    return(NA_real_)
  }
  idx <- which(df$elapsed <= checkpoint_seconds & !is.na(df[[column]]))
  if (length(idx) == 0L) {
    return(NA_real_)
  }
  tail(df[[column]][idx], 1)
}

determine_settlement_side <- function(df) {
  windows <- list(c(285, 298), c(240, 285))
  for (w in windows) {
    idx <- which(df$elapsed >= w[1] & df$elapsed <= w[2] & !is.na(df$up_midpoint))
    if (length(idx) == 0L) {
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

classify_btc_direction <- function(btc_diff_value) {
  if (is.na(btc_diff_value) || btc_diff_value == 0) {
    return(NA_character_)
  }
  if (btc_diff_value > 0) {
    return("up")
  }
  "down"
}

classify_odds_direction <- function(up_midpoint_value, odds_edge) {
  if (is.na(up_midpoint_value)) {
    return(NA_character_)
  }
  if (up_midpoint_value >= 0.5 + odds_edge) {
    return("up")
  }
  if (up_midpoint_value <= 0.5 - odds_edge) {
    return("down")
  }
  NA_character_
}

analyze_one_file <- function(csv_path, checkpoint_seconds, odds_edge, min_rows) {
  raw_df <- tryCatch(
    fast_read_csv(csv_path),
    error = function(e) NULL
  )
  if (is.null(raw_df)) {
    return(list(row = NULL, skipped = data.frame(round_file = basename(csv_path), reason = "read_error", stringsAsFactors = FALSE)))
  }

  required_cols <- c("timestamp", "up_midpoint", "btc_diff")
  if (!all(required_cols %in% names(raw_df))) {
    return(list(row = NULL, skipped = data.frame(round_file = basename(csv_path), reason = "missing_required_columns", stringsAsFactors = FALSE)))
  }
  if (nrow(raw_df) < min_rows) {
    return(list(row = NULL, skipped = data.frame(round_file = basename(csv_path), reason = paste0("too_few_rows:", nrow(raw_df)), stringsAsFactors = FALSE)))
  }

  raw_df$timestamp <- parse_timestamp_vector(raw_df$timestamp)
  raw_df <- raw_df[!is.na(raw_df$timestamp), , drop = FALSE]
  raw_df <- raw_df[order(raw_df$timestamp), , drop = FALSE]
  if (nrow(raw_df) == 0L) {
    return(list(row = NULL, skipped = data.frame(round_file = basename(csv_path), reason = "invalid_timestamps", stringsAsFactors = FALSE)))
  }

  for (col in intersect(c("up_midpoint", "down_midpoint", "btc_diff"), names(raw_df))) {
    raw_df[[col]] <- suppressWarnings(as.numeric(raw_df[[col]]))
  }

  raw_df$elapsed <- as.numeric(difftime(raw_df$timestamp, raw_df$timestamp[1], units = "secs"))
  if (max(raw_df$elapsed, na.rm = TRUE) < checkpoint_seconds) {
    return(list(row = NULL, skipped = data.frame(round_file = basename(csv_path), reason = "round_too_short", stringsAsFactors = FALSE)))
  }

  settlement_side <- determine_settlement_side(raw_df)
  if (is.na(settlement_side)) {
    return(list(row = NULL, skipped = data.frame(round_file = basename(csv_path), reason = "missing_settlement_label", stringsAsFactors = FALSE)))
  }

  btc_diff_at_checkpoint <- last_value_before(raw_df, "btc_diff", checkpoint_seconds)
  up_mid_at_checkpoint <- last_value_before(raw_df, "up_midpoint", checkpoint_seconds)
  down_mid_at_checkpoint <- if ("down_midpoint" %in% names(raw_df)) last_value_before(raw_df, "down_midpoint", checkpoint_seconds) else 1 - up_mid_at_checkpoint

  btc_direction <- classify_btc_direction(btc_diff_at_checkpoint)
  odds_direction <- classify_odds_direction(up_mid_at_checkpoint, odds_edge)

  if (is.na(btc_direction) || is.na(odds_direction) || identical(btc_direction, odds_direction)) {
    return(list(
      row = NULL,
      skipped = data.frame(round_file = basename(csv_path), reason = "not_mismatch_or_not_strong_enough", stringsAsFactors = FALSE)
    ))
  }

  mismatch_type <- paste0("btc_", btc_direction, "__odds_", odds_direction)
  row <- data.frame(
    round_id = tools::file_path_sans_ext(basename(csv_path)),
    round_file = basename(csv_path),
    checkpoint_seconds = checkpoint_seconds,
    odds_edge = odds_edge,
    btc_diff_at_checkpoint = btc_diff_at_checkpoint,
    btc_direction = btc_direction,
    up_mid_at_checkpoint = up_mid_at_checkpoint,
    down_mid_at_checkpoint = down_mid_at_checkpoint,
    odds_direction = odds_direction,
    mismatch_type = mismatch_type,
    final_winner = settlement_side,
    btc_side_won = as.integer(settlement_side == btc_direction),
    odds_side_won = as.integer(settlement_side == odds_direction),
    stringsAsFactors = FALSE
  )

  list(row = row, skipped = NULL)
}

bind_rows_safe <- function(dfs) {
  dfs <- dfs[!vapply(dfs, is.null, logical(1))]
  dfs <- dfs[vapply(dfs, nrow, integer(1)) > 0L]
  if (length(dfs) == 0L) {
    return(NULL)
  }
  do.call(rbind, dfs)
}

summarize_alignment <- function(df) {
  if (nrow(df) == 0L) {
    return(data.frame(side = character(0), wins = integer(0), share = numeric(0), stringsAsFactors = FALSE))
  }
  wins <- c(
    btc_side = sum(df$btc_side_won, na.rm = TRUE),
    odds_side = sum(df$odds_side_won, na.rm = TRUE)
  )
  data.frame(
    side = names(wins),
    wins = as.integer(wins),
    share = as.numeric(wins) / nrow(df),
    stringsAsFactors = FALSE
  )
}

summarize_mismatch_types <- function(df) {
  if (nrow(df) == 0L) {
    return(data.frame(
      mismatch_type = character(0),
      rounds = integer(0),
      btc_side_wins = integer(0),
      odds_side_wins = integer(0),
      btc_side_win_rate = numeric(0),
      odds_side_win_rate = numeric(0),
      stringsAsFactors = FALSE
    ))
  }

  split_rows <- split(df, df$mismatch_type)
  pieces <- lapply(names(split_rows), function(type_name) {
    chunk <- split_rows[[type_name]]
    data.frame(
      mismatch_type = type_name,
      rounds = nrow(chunk),
      btc_side_wins = sum(chunk$btc_side_won, na.rm = TRUE),
      odds_side_wins = sum(chunk$odds_side_won, na.rm = TRUE),
      btc_side_win_rate = mean(chunk$btc_side_won, na.rm = TRUE),
      odds_side_win_rate = mean(chunk$odds_side_won, na.rm = TRUE),
      stringsAsFactors = FALSE
    )
  })
  out <- do.call(rbind, pieces)
  out[order(out$rounds, decreasing = TRUE), , drop = FALSE]
}

main <- function(
  data_dir = NULL,
  n = NULL,
  checkpoint_seconds = DEFAULT_CHECKPOINT_SECONDS,
  odds_edge = DEFAULT_ODDS_EDGE,
  output_dir = NULL,
  cores = NULL,
  min_rows = 500L
) {
  opts <- parse_args(commandArgs(trailingOnly = TRUE))
  if (is.null(data_dir)) data_dir <- opts$data_dir
  if (is.null(n)) n <- opts$n
  if (missing(checkpoint_seconds) || is.null(checkpoint_seconds)) checkpoint_seconds <- opts$checkpoint_seconds
  if (missing(odds_edge) || is.null(odds_edge)) odds_edge <- opts$odds_edge
  if (is.null(output_dir)) output_dir <- opts$output_dir
  if (is.null(cores)) cores <- opts$cores
  if (missing(min_rows) || is.null(min_rows)) min_rows <- opts$min_rows

  data_dir <- resolve_data_dir(data_dir)
  output_dir <- resolve_output_dir(output_dir)
  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

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
  results <- parallel_map(
    selected_files,
    analyze_one_file,
    checkpoint_seconds = checkpoint_seconds,
    odds_edge = odds_edge,
    min_rows = min_rows,
    cores = use_cores
  )

  mismatch_rounds <- bind_rows_safe(lapply(results, `[[`, "row"))
  skipped_df <- bind_rows_safe(lapply(results, `[[`, "skipped"))
  if (is.null(mismatch_rounds) || nrow(mismatch_rounds) == 0L) {
    stop("No mismatch rounds found under the current settings.")
  }

  overall_summary <- data.frame(
    rounds = nrow(mismatch_rounds),
    btc_side_wins = sum(mismatch_rounds$btc_side_won, na.rm = TRUE),
    odds_side_wins = sum(mismatch_rounds$odds_side_won, na.rm = TRUE),
    btc_side_win_rate = mean(mismatch_rounds$btc_side_won, na.rm = TRUE),
    odds_side_win_rate = mean(mismatch_rounds$odds_side_won, na.rm = TRUE),
    stringsAsFactors = FALSE
  )
  alignment_summary <- summarize_alignment(mismatch_rounds)
  mismatch_type_summary <- summarize_mismatch_types(mismatch_rounds)

  mismatch_path <- file.path(output_dir, "mismatch_rounds.csv")
  overall_path <- file.path(output_dir, "overall_summary.csv")
  alignment_path <- file.path(output_dir, "alignment_summary.csv")
  mismatch_type_path <- file.path(output_dir, "mismatch_type_summary.csv")
  skipped_path <- file.path(output_dir, "skipped_rounds.csv")

  write.csv(mismatch_rounds, mismatch_path, row.names = FALSE, na = "")
  write.csv(overall_summary, overall_path, row.names = FALSE, na = "")
  write.csv(alignment_summary, alignment_path, row.names = FALSE, na = "")
  write.csv(mismatch_type_summary, mismatch_type_path, row.names = FALSE, na = "")
  write.csv(if (is.null(skipped_df)) data.frame() else skipped_df, skipped_path, row.names = FALSE, na = "")

  cat("Data dir:", data_dir, "\n")
  cat("Files used:", length(selected_files), "\n")
  cat("Worker processes:", use_cores, "\n")
  cat("Checkpoint seconds:", checkpoint_seconds, "\n")
  cat("Odds edge:", odds_edge, "\n\n")

  cat("Overall summary:\n")
  print(overall_summary, row.names = FALSE)
  cat("\nAlignment summary:\n")
  print(alignment_summary, row.names = FALSE)
  cat("\nMismatch-type summary:\n")
  print(mismatch_type_summary, row.names = FALSE)

  cat("\nWrote:", mismatch_path, "\n")
  cat("Wrote:", overall_path, "\n")
  cat("Wrote:", alignment_path, "\n")
  cat("Wrote:", mismatch_type_path, "\n")
  cat("Wrote:", skipped_path, "\n")

  invisible(list(
    mismatch_rounds = mismatch_rounds,
    overall_summary = overall_summary,
    alignment_summary = alignment_summary,
    mismatch_type_summary = mismatch_type_summary,
    skipped = skipped_df
  ))
}

if (sys.nframe() == 0) {
  main()
}
