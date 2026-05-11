# high_confidence_reversal_utc_hour.R
# ------------------------------------------------------------
# 统计“某一边曾先达到高置信度（默认 95%），但最终却输掉”的轮次，
# 并按该高置信度首次出现的 UTC 小时汇总。
#
# 研究问题：
# “95% 以上翻盘主要出现在一天中的哪些 UTC 时间段？
#  是否和美股开盘前后（通常约 13/14 UTC）更相关？”
#
# 输出：
# - reversal_rounds.csv                  95% 翻盘轮次明细
# - all_high_confidence_hits.csv         所有首次达到 95% 的轮次
# - reversal_by_hit_utc_hour.csv         翻盘按 hit UTC 小时汇总
# - all_hits_by_hit_utc_hour.csv         所有高置信度轮次按 hit UTC 小时汇总
# - us_equity_open_window_summary.csv    美股开盘窗口 vs 其他时段
# - reversal_by_hit_utc_hour.png         翻盘数 + 翻盘率图
# ------------------------------------------------------------

DEFAULT_THRESHOLD <- 0.95

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
    project_dir <- file.path(current, "projects", "market_integrity_screen")
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
  file.path(get_script_dir(), "artifacts", "high_confidence_reversal_utc_hour")
}

parse_args <- function(args) {
  opts <- list(
    data_dir = NULL,
    n = NULL,
    threshold = DEFAULT_THRESHOLD,
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
    if (arg == "--threshold" && i < length(args)) {
      opts$threshold <- as.numeric(args[i + 1L])
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

prepare_round <- function(df, min_rows = 500L) {
  required_cols <- c("timestamp", "up_midpoint")
  if (!all(required_cols %in% names(df))) {
    return(list(df = NULL, status = "missing_columns"))
  }
  if (nrow(df) < min_rows) {
    return(list(df = NULL, status = paste0("too_few_rows:", nrow(df))))
  }

  df$timestamp <- parse_timestamp_vector(df$timestamp)
  df <- df[!is.na(df$timestamp), , drop = FALSE]
  df <- df[order(df$timestamp), , drop = FALSE]
  if (nrow(df) == 0L) {
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
  if (nrow(df) == 0L) {
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
    if (last_up > 0.5) return("up")
    if (last_up < 0.5) return("down")
  }
  NA_character_
}

first_high_confidence_hit <- function(df, threshold) {
  side <- rep(NA_character_, nrow(df))
  side[df$up_midpoint >= threshold] <- "up"
  side[df$up_midpoint <= (1 - threshold)] <- "down"
  idx <- which(!is.na(side))
  if (length(idx) == 0L) {
    return(NULL)
  }

  i <- idx[1]
  side_i <- side[i]
  implied_prob <- if (identical(side_i, "up")) df$up_midpoint[i] else 1 - df$up_midpoint[i]

  list(
    index = i,
    side = side_i,
    hit_timestamp = df$timestamp[i],
    hit_elapsed = as.numeric(df$elapsed[i]),
    implied_prob = implied_prob
  )
}

classify_us_open_window <- function(hour_utc) {
  if (is.na(hour_utc)) {
    return(NA_character_)
  }
  if (hour_utc %in% c(13L, 14L)) {
    return("us_open_window")
  }
  "other_hours"
}

analyze_one_file <- function(csv_path, threshold, min_rows) {
  raw_df <- tryCatch(
    fast_read_csv(csv_path),
    error = function(e) NULL
  )
  if (is.null(raw_df)) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "read_error", stringsAsFactors = FALSE)))
  }

  prepared <- prepare_round(raw_df, min_rows = min_rows)
  if (!identical(prepared$status, "ok")) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = prepared$status, stringsAsFactors = FALSE)))
  }

  df <- prepared$df
  final_side <- determine_final_side(df)
  if (is.na(final_side)) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "missing_label", stringsAsFactors = FALSE)))
  }

  hit <- first_high_confidence_hit(df, threshold)
  if (is.null(hit)) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "no_high_confidence_hit", stringsAsFactors = FALSE)))
  }

  hit_hour <- as.integer(format(hit$hit_timestamp, "%H", tz = "UTC"))
  start_hour <- as.integer(format(df$timestamp[1], "%H", tz = "UTC"))
  reversal_flag <- as.integer(hit$side != final_side)

  row <- data.frame(
    file = basename(csv_path),
    round_id = tools::file_path_sans_ext(basename(csv_path)),
    threshold = threshold,
    threshold_label = paste0(round(threshold * 100), "%"),
    round_start_utc = format(df$timestamp[1], tz = "UTC", usetz = TRUE),
    round_start_utc_hour = start_hour,
    hit_timestamp_utc = format(hit$hit_timestamp, tz = "UTC", usetz = TRUE),
    hit_utc_hour = hit_hour,
    us_open_window = classify_us_open_window(hit_hour),
    hit_elapsed = hit$hit_elapsed,
    hit_side = hit$side,
    hit_prob = hit$implied_prob,
    final_side = final_side,
    is_reversal = reversal_flag,
    stringsAsFactors = FALSE
  )

  list(hit = row, skipped = NULL)
}

bind_rows_safe <- function(dfs) {
  dfs <- dfs[!vapply(dfs, is.null, logical(1))]
  dfs <- dfs[vapply(dfs, nrow, integer(1)) > 0L]
  if (length(dfs) == 0L) {
    return(NULL)
  }
  do.call(rbind, dfs)
}

summarize_by_hour <- function(all_hits) {
  rows <- lapply(0:23, function(hour) {
    hour_hits <- all_hits[all_hits$hit_utc_hour == hour, , drop = FALSE]
    hour_reversals <- hour_hits[hour_hits$is_reversal == 1L, , drop = FALSE]
    data.frame(
      hit_utc_hour = hour,
      all_high_conf_hits = nrow(hour_hits),
      reversal_rounds = nrow(hour_reversals),
      reversal_share_of_all_reversals = if (sum(all_hits$is_reversal) > 0) nrow(hour_reversals) / sum(all_hits$is_reversal) else NA_real_,
      reversal_rate_within_hour = if (nrow(hour_hits) > 0) nrow(hour_reversals) / nrow(hour_hits) else NA_real_,
      stringsAsFactors = FALSE
    )
  })
  do.call(rbind, rows)
}

summarize_open_window <- function(all_hits) {
  groups <- c("us_open_window", "other_hours")
  rows <- lapply(groups, function(group_name) {
    part <- all_hits[all_hits$us_open_window == group_name, , drop = FALSE]
    reversals <- part[part$is_reversal == 1L, , drop = FALSE]
    data.frame(
      window = group_name,
      all_high_conf_hits = nrow(part),
      reversal_rounds = nrow(reversals),
      reversal_rate = if (nrow(part) > 0) nrow(reversals) / nrow(part) else NA_real_,
      stringsAsFactors = FALSE
    )
  })
  do.call(rbind, rows)
}

plot_hour_summary <- function(hour_summary, threshold, output_path) {
  png(output_path, width = 1500, height = 900, res = 150)
  on.exit(dev.off(), add = TRUE)
  par(mar = c(6, 5, 4, 5) + 0.1)

  x <- seq_len(nrow(hour_summary))
  counts <- hour_summary$reversal_rounds
  rates <- hour_summary$reversal_rate_within_hour
  max_count <- max(c(counts, 1), na.rm = TRUE)
  max_rate <- max(c(rates, 0.01), na.rm = TRUE)

  barplot(
    counts,
    names.arg = hour_summary$hit_utc_hour,
    col = "#9ecae1",
    border = NA,
    xlab = "UTC hour of first 95% hit",
    ylab = "Reversal count",
    main = sprintf("%s reversals by UTC hour", paste0(round(threshold * 100), "%")),
    ylim = c(0, max_count * 1.15)
  )

  par(new = TRUE)
  plot(
    x,
    rates,
    type = "b",
    pch = 19,
    lwd = 2,
    col = "#d62728",
    axes = FALSE,
    xlab = "",
    ylab = "",
    ylim = c(0, max_rate * 1.2)
  )
  axis(4, col.axis = "#d62728", col = "#d62728")
  mtext("Reversal rate within hour", side = 4, line = 3, col = "#d62728")
  abline(v = c(14, 15), lty = 2, col = "gray50")
  legend(
    "topright",
    legend = c("Reversal count", "Reversal rate", "US open window marker"),
    fill = c("#9ecae1", NA, NA),
    border = c(NA, NA, NA),
    lty = c(NA, 1, 2),
    pch = c(NA, 19, NA),
    col = c(NA, "#d62728", "gray50"),
    bty = "n"
  )
}

main <- function(
  data_dir = NULL,
  n = NULL,
  threshold = DEFAULT_THRESHOLD,
  output_dir = NULL,
  cores = NULL,
  min_rows = 500L
) {
  opts <- parse_args(commandArgs(trailingOnly = TRUE))
  if (is.null(data_dir)) data_dir <- opts$data_dir
  if (is.null(n)) n <- opts$n
  if (missing(threshold) || is.null(threshold)) threshold <- opts$threshold
  if (is.null(output_dir)) output_dir <- opts$output_dir
  if (is.null(cores)) cores <- opts$cores
  if (missing(min_rows) || is.null(min_rows)) min_rows <- opts$min_rows

  data_dir <- resolve_data_dir(data_dir)
  output_dir <- resolve_output_dir(output_dir)

  threshold <- as.numeric(threshold)
  if (is.na(threshold) || threshold <= 0.5 || threshold >= 1) {
    stop("threshold must be between 0.5 and 1.")
  }

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
    threshold = threshold,
    min_rows = min_rows,
    cores = use_cores
  )

  all_hits <- bind_rows_safe(lapply(analyzed, `[[`, "hit"))
  skipped <- bind_rows_safe(lapply(analyzed, `[[`, "skipped"))
  if (is.null(all_hits) || nrow(all_hits) == 0L) {
    stop("No usable high-confidence hits were found.")
  }

  reversal_rounds <- all_hits[all_hits$is_reversal == 1L, , drop = FALSE]
  hour_summary <- summarize_by_hour(all_hits)
  open_window_summary <- summarize_open_window(all_hits)

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  reversal_path <- file.path(output_dir, "reversal_rounds.csv")
  all_hits_path <- file.path(output_dir, "all_high_confidence_hits.csv")
  hour_summary_path <- file.path(output_dir, "reversal_by_hit_utc_hour.csv")
  all_hits_hour_path <- file.path(output_dir, "all_hits_by_hit_utc_hour.csv")
  open_window_path <- file.path(output_dir, "us_equity_open_window_summary.csv")
  skipped_path <- file.path(output_dir, "skipped_rounds.csv")
  plot_path <- file.path(output_dir, "reversal_by_hit_utc_hour.png")

  write.csv(reversal_rounds, reversal_path, row.names = FALSE, na = "")
  write.csv(all_hits, all_hits_path, row.names = FALSE, na = "")
  write.csv(hour_summary, hour_summary_path, row.names = FALSE, na = "")
  write.csv(data.frame(hit_utc_hour = 0:23, all_high_conf_hits = hour_summary$all_high_conf_hits, stringsAsFactors = FALSE), all_hits_hour_path, row.names = FALSE, na = "")
  write.csv(open_window_summary, open_window_path, row.names = FALSE, na = "")
  write.csv(if (is.null(skipped)) data.frame() else skipped, skipped_path, row.names = FALSE, na = "")
  plot_hour_summary(hour_summary, threshold = threshold, output_path = plot_path)

  cat("Data dir:", data_dir, "\n")
  cat("Files used:", length(selected_files), "\n")
  cat("Worker processes:", use_cores, "\n")
  cat("Threshold:", paste0(round(threshold * 100), "%"), "\n\n")

  cat("Overall summary:\n")
  print(data.frame(
    total_high_conf_hits = nrow(all_hits),
    reversal_rounds = nrow(reversal_rounds),
    reversal_rate = nrow(reversal_rounds) / nrow(all_hits),
    stringsAsFactors = FALSE
  ), row.names = FALSE)
  cat("\nUTC hour summary:\n")
  print(hour_summary, row.names = FALSE)
  cat("\nUS equity open window summary:\n")
  print(open_window_summary, row.names = FALSE)

  cat("\nWrote:", reversal_path, "\n")
  cat("Wrote:", all_hits_path, "\n")
  cat("Wrote:", hour_summary_path, "\n")
  cat("Wrote:", all_hits_hour_path, "\n")
  cat("Wrote:", open_window_path, "\n")
  cat("Wrote:", skipped_path, "\n")
  cat("Wrote:", plot_path, "\n")

  invisible(list(
    all_hits = all_hits,
    reversal_rounds = reversal_rounds,
    hour_summary = hour_summary,
    open_window_summary = open_window_summary,
    skipped = skipped
  ))
}

if (sys.nframe() == 0) {
  main()
}
