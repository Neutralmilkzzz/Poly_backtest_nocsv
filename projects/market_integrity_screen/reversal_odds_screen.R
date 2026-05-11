# reversal_odds_screen.R
# ------------------------------------------------------------
# 抓“BTC 方向和盘口方向相反”的轮次，并统计最后谁赢得更多。
#
# 默认逻辑：
# - 取最近 n 盘
# - 看 checkpoint_seconds 时刻之前最后一个 BTC 价格
# - 如果 BTC 从开盘到该时刻明显上涨/下跌（绝对变动 >= min_abs_btc_move）
# - 但 up_midpoint 给出的盘口方向相反（且离 0.5 至少 odds_buffer）
# - 就把这轮记为 reversal round
#
# 最后输出：
# - reversal_rounds.csv         反转赔率轮次明细
# - winner_summary.csv          这些轮次里最终 UP / DOWN 谁赢得更多
# - alignment_summary.csv       是 BTC 那边赢得更多，还是赔率那边赢得更多
# - reversal_type_summary.csv   分类型统计（btc_up__odds_down / btc_down__odds_up）
# - skipped_rounds.csv          被跳过的文件及原因
#
# 用法：
# source("C:/Users/ZHAOKAI/Poly_backtest_Final/projects/market_integrity_screen/reversal_odds_screen.R")
# main(
#   data_dir = "C:/Users/ZHAOKAI/Poly_backtest_Final/data",
#   n = 500,
#   checkpoint_seconds = 240,
#   min_abs_btc_move = 5,
#   odds_buffer = 0.02,
#   focus_direction = "up"
# )
# ------------------------------------------------------------

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

  file.path(get_script_dir(), "artifacts", "reversal_odds")
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

determine_settlement_side <- function(df) {
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

last_value_before <- function(df, column, checkpoint_seconds) {
  if (!(column %in% names(df))) {
    return(NA_real_)
  }

  idx <- which(df$elapsed <= checkpoint_seconds & !is.na(df[[column]]))
  if (length(idx) == 0) {
    return(NA_real_)
  }

  tail(df[[column]][idx], 1)
}

first_non_na <- function(x) {
  idx <- which(!is.na(x))
  if (length(idx) == 0) {
    return(NA_real_)
  }
  x[idx[1]]
}

classify_btc_direction <- function(move_value, min_abs_btc_move) {
  if (is.na(move_value) || abs(move_value) < min_abs_btc_move) {
    return(NA_character_)
  }
  if (move_value > 0) {
    return("up")
  }
  if (move_value < 0) {
    return("down")
  }
  NA_character_
}

classify_odds_direction <- function(up_midpoint_value, odds_buffer) {
  if (is.na(up_midpoint_value)) {
    return(NA_character_)
  }
  if (up_midpoint_value >= 0.5 + odds_buffer) {
    return("up")
  }
  if (up_midpoint_value <= 0.5 - odds_buffer) {
    return("down")
  }
  NA_character_
}

load_round <- function(csv_path, checkpoint_seconds = 240, min_rows = 500) {
  df <- tryCatch(
    read.csv(csv_path, stringsAsFactors = FALSE),
    error = function(e) NULL
  )
  if (is.null(df)) {
    return(list(data = NULL, reason = "read_error"))
  }

  required_cols <- c("timestamp", "up_midpoint")
  if (!all(required_cols %in% names(df))) {
    return(list(data = NULL, reason = "missing_required_columns"))
  }

  if (!("btc_price" %in% names(df)) && !("btc_diff" %in% names(df))) {
    return(list(data = NULL, reason = "missing_btc_columns"))
  }

  if (nrow(df) < min_rows) {
    return(list(data = NULL, reason = paste0("too_few_rows:", nrow(df))))
  }

  df$timestamp <- parse_timestamp_vector(df$timestamp)
  df <- df[!is.na(df$timestamp), , drop = FALSE]
  df <- df[order(df$timestamp), , drop = FALSE]
  if (nrow(df) == 0) {
    return(list(data = NULL, reason = "invalid_timestamps"))
  }

  numeric_cols <- intersect(c("up_midpoint", "down_midpoint", "btc_price", "btc_diff"), names(df))
  for (col in numeric_cols) {
    df[[col]] <- suppressWarnings(as.numeric(df[[col]]))
  }

  df$elapsed <- as.numeric(difftime(df$timestamp, df$timestamp[1], units = "secs"))
  if (max(df$elapsed, na.rm = TRUE) < checkpoint_seconds) {
    return(list(data = NULL, reason = "round_too_short"))
  }

  df <- df[order(df$elapsed), , drop = FALSE]
  settlement_side <- determine_settlement_side(df)
  if (is.na(settlement_side)) {
    return(list(data = NULL, reason = "missing_settlement_label"))
  }

  list(
    data = df,
    reason = NULL,
    round_id = tools::file_path_sans_ext(basename(csv_path)),
    settlement_side = settlement_side
  )
}

build_round_row <- function(csv_path, checkpoint_seconds = 240, min_abs_btc_move = 5, odds_buffer = 0.02, min_rows = 500) {
  loaded <- load_round(csv_path, checkpoint_seconds = checkpoint_seconds, min_rows = min_rows)
  if (is.null(loaded$data)) {
    return(list(
      row = NULL,
      skipped = data.frame(
        round_file = basename(csv_path),
        reason = loaded$reason,
        stringsAsFactors = FALSE
      )
    ))
  }

  df <- loaded$data

  btc_source <- if ("btc_price" %in% names(df) && any(!is.na(df$btc_price))) {
    "btc_price"
  } else if ("btc_diff" %in% names(df) && any(!is.na(df$btc_diff))) {
    "btc_diff"
  } else {
    NA_character_
  }

  if (is.na(btc_source)) {
    return(list(
      row = NULL,
      skipped = data.frame(
        round_file = basename(csv_path),
        reason = "no_usable_btc_series",
        stringsAsFactors = FALSE
      )
    ))
  }

  btc_start <- if (identical(btc_source, "btc_price")) first_non_na(df$btc_price) else 0
  btc_at_checkpoint <- last_value_before(df, btc_source, checkpoint_seconds)
  if (is.na(btc_start) || is.na(btc_at_checkpoint)) {
    return(list(
      row = NULL,
      skipped = data.frame(
        round_file = basename(csv_path),
        reason = "missing_btc_snapshot",
        stringsAsFactors = FALSE
      )
    ))
  }

  btc_move_abs <- btc_at_checkpoint - btc_start
  btc_move_pct <- if (identical(btc_source, "btc_price") && !is.na(btc_start) && btc_start != 0) {
    100 * btc_move_abs / btc_start
  } else {
    NA_real_
  }
  btc_direction <- classify_btc_direction(btc_move_abs, min_abs_btc_move)

  up_mid_at_checkpoint <- last_value_before(df, "up_midpoint", checkpoint_seconds)
  down_mid_at_checkpoint <- last_value_before(df, "down_midpoint", checkpoint_seconds)
  odds_direction <- classify_odds_direction(up_mid_at_checkpoint, odds_buffer)

  if (is.na(btc_direction) || is.na(odds_direction)) {
    return(list(
      row = data.frame(
        round_id = loaded$round_id,
        round_file = basename(csv_path),
        checkpoint_seconds = checkpoint_seconds,
        btc_source = btc_source,
        btc_start = btc_start,
        btc_at_checkpoint = btc_at_checkpoint,
        btc_move_abs = btc_move_abs,
        btc_move_pct = btc_move_pct,
        btc_direction = btc_direction,
        up_mid_at_checkpoint = up_mid_at_checkpoint,
        down_mid_at_checkpoint = down_mid_at_checkpoint,
        odds_direction = odds_direction,
        is_reversal = 0L,
        reversal_type = NA_character_,
        final_winner = loaded$settlement_side,
        btc_side_won = NA_integer_,
        odds_side_won = NA_integer_,
        stringsAsFactors = FALSE
      ),
      skipped = NULL
    ))
  }

  is_reversal <- as.integer(btc_direction != odds_direction)
  reversal_type <- if (is_reversal == 1L) {
    paste0("btc_", btc_direction, "__odds_", odds_direction)
  } else {
    NA_character_
  }

  list(
    row = data.frame(
      round_id = loaded$round_id,
      round_file = basename(csv_path),
      checkpoint_seconds = checkpoint_seconds,
      btc_source = btc_source,
      btc_start = btc_start,
      btc_at_checkpoint = btc_at_checkpoint,
      btc_move_abs = btc_move_abs,
      btc_move_pct = btc_move_pct,
      btc_direction = btc_direction,
      up_mid_at_checkpoint = up_mid_at_checkpoint,
      down_mid_at_checkpoint = down_mid_at_checkpoint,
      odds_direction = odds_direction,
      is_reversal = is_reversal,
      reversal_type = reversal_type,
      final_winner = loaded$settlement_side,
      btc_side_won = as.integer(loaded$settlement_side == btc_direction),
      odds_side_won = as.integer(loaded$settlement_side == odds_direction),
      stringsAsFactors = FALSE
    ),
    skipped = NULL
  )
}

summarize_final_winners <- function(df) {
  if (nrow(df) == 0) {
    return(data.frame(
      final_winner = character(0),
      rounds = integer(0),
      share = numeric(0),
      stringsAsFactors = FALSE
    ))
  }

  counts <- sort(table(df$final_winner), decreasing = TRUE)
  data.frame(
    final_winner = names(counts),
    rounds = as.integer(counts),
    share = as.numeric(counts) / nrow(df),
    stringsAsFactors = FALSE
  )
}

summarize_alignment <- function(df) {
  if (nrow(df) == 0) {
    return(data.frame(
      side = character(0),
      wins = integer(0),
      share = numeric(0),
      stringsAsFactors = FALSE
    ))
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

summarize_reversal_types <- function(df) {
  if (nrow(df) == 0) {
    return(data.frame(
      reversal_type = character(0),
      rounds = integer(0),
      up_wins = integer(0),
      down_wins = integer(0),
      btc_side_wins = integer(0),
      odds_side_wins = integer(0),
      stringsAsFactors = FALSE
    ))
  }

  split_rows <- split(df, df$reversal_type)
  pieces <- lapply(names(split_rows), function(type_name) {
    chunk <- split_rows[[type_name]]
    data.frame(
      reversal_type = type_name,
      rounds = nrow(chunk),
      up_wins = sum(chunk$final_winner == "up", na.rm = TRUE),
      down_wins = sum(chunk$final_winner == "down", na.rm = TRUE),
      btc_side_wins = sum(chunk$btc_side_won, na.rm = TRUE),
      odds_side_wins = sum(chunk$odds_side_won, na.rm = TRUE),
      stringsAsFactors = FALSE
    )
  })

  out <- do.call(rbind, pieces)
  out[order(out$rounds, decreasing = TRUE), , drop = FALSE]
}

main <- function(
  data_dir = NULL,
  n = 500,
  checkpoint_seconds = 240,
  min_abs_btc_move = 5,
  odds_buffer = 0.02,
  focus_direction = c("up", "down", "both"),
  output_dir = NULL,
  min_rows = 500
) {
  focus_direction <- match.arg(focus_direction)
  data_dir <- resolve_data_dir(data_dir)
  output_dir <- resolve_output_dir(output_dir)
  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

  csv_files <- list.files(data_dir, pattern = "\\.csv$", full.names = TRUE)
  if (length(csv_files) == 0) {
    stop(sprintf("No CSV files found in %s", data_dir))
  }

  info <- file.info(csv_files)
  info$file <- rownames(info)
  info <- info[order(info$mtime, decreasing = TRUE), , drop = FALSE]
  n_use <- min(as.integer(n), nrow(info))
  recent_files <- info$file[seq_len(n_use)]

  results <- lapply(
    recent_files,
    build_round_row,
    checkpoint_seconds = checkpoint_seconds,
    min_abs_btc_move = min_abs_btc_move,
    odds_buffer = odds_buffer,
    min_rows = min_rows
  )

  row_list <- lapply(results, function(x) x$row)
  row_list <- row_list[!vapply(row_list, is.null, logical(1))]
  skipped_list <- lapply(results, function(x) x$skipped)
  skipped_list <- skipped_list[!vapply(skipped_list, is.null, logical(1))]

  all_rounds <- if (length(row_list) > 0) {
    do.call(rbind, row_list)
  } else {
    data.frame(stringsAsFactors = FALSE)
  }
  skipped_df <- if (length(skipped_list) > 0) {
    do.call(rbind, skipped_list)
  } else {
    data.frame(round_file = character(0), reason = character(0), stringsAsFactors = FALSE)
  }

  eligible <- if (nrow(all_rounds) > 0) {
    all_rounds[
      !is.na(all_rounds$btc_direction) &
        !is.na(all_rounds$odds_direction),
      ,
      drop = FALSE
    ]
  } else {
    data.frame(stringsAsFactors = FALSE)
  }

  reversal_rounds <- eligible[eligible$is_reversal == 1L, , drop = FALSE]
  if (!identical(focus_direction, "both")) {
    reversal_rounds <- reversal_rounds[reversal_rounds$btc_direction == focus_direction, , drop = FALSE]
  }

  winner_summary <- summarize_final_winners(reversal_rounds)
  alignment_summary <- summarize_alignment(reversal_rounds)
  reversal_type_summary <- summarize_reversal_types(reversal_rounds)

  reversal_path <- file.path(output_dir, "reversal_rounds.csv")
  winner_path <- file.path(output_dir, "winner_summary.csv")
  alignment_path <- file.path(output_dir, "alignment_summary.csv")
  reversal_type_path <- file.path(output_dir, "reversal_type_summary.csv")
  skipped_path <- file.path(output_dir, "skipped_rounds.csv")

  write.csv(reversal_rounds, reversal_path, row.names = FALSE, na = "")
  write.csv(winner_summary, winner_path, row.names = FALSE, na = "")
  write.csv(alignment_summary, alignment_path, row.names = FALSE, na = "")
  write.csv(reversal_type_summary, reversal_type_path, row.names = FALSE, na = "")
  write.csv(skipped_df, skipped_path, row.names = FALSE, na = "")

  cat("Data dir:", data_dir, "\n")
  cat("Files checked:", n_use, "\n")
  cat("Checkpoint seconds:", checkpoint_seconds, "\n")
  cat("Focus direction:", focus_direction, "\n")
  cat("Min BTC move:", min_abs_btc_move, "\n")
  cat("Odds buffer:", odds_buffer, "\n")
  cat("Eligible rounds:", nrow(eligible), "\n")
  cat("Reversal rounds:", nrow(reversal_rounds), "\n\n")

  if (nrow(winner_summary) > 0) {
    cat("Final winner summary:\n")
    print(winner_summary, row.names = FALSE)
    cat("\n")
  } else {
    cat("No reversal rounds found under current filters.\n\n")
  }

  if (nrow(alignment_summary) > 0) {
    cat("BTC side vs odds side:\n")
    print(alignment_summary, row.names = FALSE)
    cat("\n")
  }

  cat("Wrote:", reversal_path, "\n")
  cat("Wrote:", winner_path, "\n")
  cat("Wrote:", alignment_path, "\n")
  cat("Wrote:", reversal_type_path, "\n")
  cat("Wrote:", skipped_path, "\n")

  invisible(list(
    all_rounds = all_rounds,
    eligible = eligible,
    reversal_rounds = reversal_rounds,
    winner_summary = winner_summary,
    alignment_summary = alignment_summary,
    reversal_type_summary = reversal_type_summary,
    skipped_rounds = skipped_df
  ))
}

if (sys.nframe() == 0) {
  main()
}
