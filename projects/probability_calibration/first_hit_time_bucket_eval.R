# first_hit_time_bucket_eval.R
# ------------------------------------------------------------
# 对每个轮次只抓“第一次达到阈值”的时刻，
# 然后按首次触发时间分桶，比较不同时间桶里的：
# 1. 命中率
# 2. 买入价格
# 3. 回测盈亏
#
# 默认研究问题：
# “同样都是 90% 高置信度信号，早期第一次出现 vs 尾盘第一次出现，
#  哪一类更准？哪一类更有交易价值？”
#
# 用法：
#   Rscript projects/probability_calibration/first_hit_time_bucket_eval.R --data-dir data
#   Rscript projects/probability_calibration/first_hit_time_bucket_eval.R --data-dir data --n 500 --threshold 0.9 --cores 24
#   Rscript projects/probability_calibration/first_hit_time_bucket_eval.R --data-dir data --bucket-breaks 0,120,240,300
# ------------------------------------------------------------

DEFAULT_THRESHOLD <- 0.9
DEFAULT_BUCKET_BREAKS <- c(0, 120, 240, 300)

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

  file.path(get_script_dir(), "artifacts", "first_hit_time_bucket_eval")
}

parse_args <- function(args) {
  opts <- list(
    data_dir = NULL,
    n = NULL,
    threshold = DEFAULT_THRESHOLD,
    bucket_breaks = DEFAULT_BUCKET_BREAKS,
    output_dir = NULL,
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

    if (arg == "--threshold" && i < length(args)) {
      opts$threshold <- as.numeric(args[i + 1L])
      i <- i + 2L
      next
    }

    if (arg == "--bucket-breaks" && i < length(args)) {
      parts <- trimws(unlist(strsplit(args[i + 1L], ",", fixed = TRUE)))
      opts$bucket_breaks <- as.numeric(parts)
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

bucket_labels_from_breaks <- function(breaks) {
  labels <- character(0)
  for (i in seq_len(length(breaks) - 1L)) {
    left <- breaks[i]
    right <- breaks[i + 1L]
    if (i < length(breaks) - 1L) {
      labels <- c(labels, sprintf("[%ss, %ss)", left, right))
    } else {
      labels <- c(labels, sprintf("[%ss, %ss]", left, right))
    }
  }
  labels
}

assign_time_bucket <- function(hit_elapsed, breaks) {
  cut(
    hit_elapsed,
    breaks = breaks,
    include.lowest = TRUE,
    right = FALSE,
    labels = bucket_labels_from_breaks(breaks)
  )
}

analyze_one_file <- function(csv_path, threshold, bucket_breaks) {
  raw_df <- tryCatch(
    fast_read_csv(csv_path),
    error = function(e) NULL
  )

  if (is.null(raw_df)) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "read_error", stringsAsFactors = FALSE)))
  }

  prepared <- prepare_round(raw_df)
  if (!identical(prepared$status, "ok")) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = prepared$status, stringsAsFactors = FALSE)))
  }

  df <- prepared$df
  final_side <- determine_final_side(df)
  if (is.na(final_side)) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "missing_label", stringsAsFactors = FALSE)))
  }

  side <- rep(NA_character_, nrow(df))
  side[df$up_midpoint >= threshold] <- "up"
  side[df$up_midpoint <= (1 - threshold)] <- "down"
  eligible_idx <- which(!is.na(side))

  if (length(eligible_idx) == 0L) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "no_threshold_hit", stringsAsFactors = FALSE)))
  }

  i <- eligible_idx[1]
  hit_side <- side[i]
  ask_col <- if (identical(hit_side, "up")) "up_best_ask" else "down_best_ask"
  if (!(ask_col %in% names(df))) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "missing_ask_column", stringsAsFactors = FALSE)))
  }

  entry_price <- suppressWarnings(as.numeric(df[[ask_col]][i]))
  if (is.na(entry_price) || entry_price <= 0 || entry_price > 1) {
    return(list(hit = NULL, skipped = data.frame(file = basename(csv_path), status = "invalid_entry_price", stringsAsFactors = FALSE)))
  }

  implied_prob <- if (identical(hit_side, "up")) df$up_midpoint[i] else 1 - df$up_midpoint[i]
  payout <- if (identical(hit_side, final_side)) 1 else 0
  hit_elapsed <- as.numeric(df$elapsed[i])
  time_bucket <- assign_time_bucket(hit_elapsed, bucket_breaks)

  hit <- data.frame(
    file = basename(csv_path),
    threshold = threshold,
    threshold_label = paste0(round(threshold * 100), "%"),
    round_start_utc = df$timestamp[1],
    hit_timestamp = df$timestamp[i],
    hit_elapsed = hit_elapsed,
    time_bucket = as.character(time_bucket),
    side = hit_side,
    implied_prob_selected_side = implied_prob,
    entry_price = entry_price,
    final_side = final_side,
    win = as.integer(identical(hit_side, final_side)),
    payout = payout,
    pnl = payout - entry_price,
    stringsAsFactors = FALSE
  )

  list(hit = hit, skipped = NULL)
}

bind_rows_safe <- function(dfs) {
  dfs <- dfs[!vapply(dfs, is.null, logical(1))]
  dfs <- dfs[vapply(dfs, nrow, integer(1)) > 0L]
  if (length(dfs) == 0L) {
    return(NULL)
  }
  do.call(rbind, dfs)
}

summarize_buckets <- function(hit_df, bucket_breaks) {
  labels <- bucket_labels_from_breaks(bucket_breaks)
  rows <- lapply(labels, function(label) {
    part <- hit_df[hit_df$time_bucket == label, , drop = FALSE]
    if (nrow(part) == 0L) {
      return(data.frame(
        time_bucket = label,
        opportunities = 0L,
        up_trades = 0L,
        down_trades = 0L,
        wins = 0L,
        losses = 0L,
        win_rate = NA_real_,
        mean_hit_elapsed = NA_real_,
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
      time_bucket = label,
      opportunities = nrow(part),
      up_trades = sum(part$side == "up"),
      down_trades = sum(part$side == "down"),
      wins = sum(part$win, na.rm = TRUE),
      losses = sum(1L - part$win, na.rm = TRUE),
      win_rate = mean(part$win, na.rm = TRUE),
      mean_hit_elapsed = mean(part$hit_elapsed, na.rm = TRUE),
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

plot_first_hit_bucket_roi <- function(bucket_summary, threshold, output_path) {
  roi_values <- bucket_summary$roi_on_cost
  labels <- bucket_summary$time_bucket
  x <- seq_len(nrow(bucket_summary))
  y_min <- min(c(roi_values, 0), na.rm = TRUE)
  y_max <- max(c(roi_values, 0), na.rm = TRUE)
  if (!is.finite(y_min) || !is.finite(y_max) || y_min == y_max) {
    y_min <- -0.01
    y_max <- 0.01
  }

  png(output_path, width = 1400, height = 800, res = 150)
  on.exit(dev.off(), add = TRUE)
  par(mar = c(8, 5, 4, 2) + 0.1)

  plot(
    x,
    roi_values,
    type = "b",
    pch = 19,
    lwd = 2,
    col = "#1f77b4",
    xaxt = "n",
    xlab = "First-hit time bucket",
    ylab = "ROI on cost",
    main = sprintf("First-hit %s ROI by time bucket", paste0(round(threshold * 100), "%")),
    ylim = c(y_min, y_max)
  )
  axis(1, at = x, labels = labels, las = 2)
  abline(h = 0, lty = 2, col = "gray50")
  grid(nx = NA, ny = NULL, col = "gray90", lty = "dotted")
}

main <- function(
  data_dir = NULL,
  n = NULL,
  threshold = DEFAULT_THRESHOLD,
  bucket_breaks = DEFAULT_BUCKET_BREAKS,
  output_dir = NULL,
  cores = NULL
) {
  opts <- parse_args(commandArgs(trailingOnly = TRUE))
  if (is.null(data_dir)) data_dir <- opts$data_dir
  if (is.null(n)) n <- opts$n
  if (missing(threshold) || is.null(threshold)) threshold <- opts$threshold
  if (missing(bucket_breaks) || is.null(bucket_breaks)) bucket_breaks <- opts$bucket_breaks
  if (is.null(output_dir)) output_dir <- opts$output_dir
  if (is.null(cores)) cores <- opts$cores

  data_dir <- resolve_data_dir(data_dir)
  output_dir <- resolve_output_dir(output_dir)

  threshold <- as.numeric(threshold)
  if (is.na(threshold) || threshold <= 0.5 || threshold >= 1) {
    stop("threshold must be between 0.5 and 1.")
  }

  bucket_breaks <- sort(unique(as.numeric(bucket_breaks)))
  if (length(bucket_breaks) < 2 || any(is.na(bucket_breaks))) {
    stop("bucket_breaks must contain at least two valid numeric breakpoints.")
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
    bucket_breaks = bucket_breaks,
    cores = use_cores
  )

  first_hits <- bind_rows_safe(lapply(analyzed, `[[`, "hit"))
  skipped <- bind_rows_safe(lapply(analyzed, `[[`, "skipped"))

  if (is.null(first_hits) || nrow(first_hits) == 0L) {
    stop("No usable first-hit events were found.")
  }

  bucket_summary <- summarize_buckets(first_hits, bucket_breaks = bucket_breaks)
  overall_summary <- data.frame(
    threshold = threshold,
    threshold_label = paste0(round(threshold * 100), "%"),
    opportunities = nrow(first_hits),
    wins = sum(first_hits$win, na.rm = TRUE),
    losses = sum(1L - first_hits$win, na.rm = TRUE),
    win_rate = mean(first_hits$win, na.rm = TRUE),
    avg_entry_price = mean(first_hits$entry_price, na.rm = TRUE),
    total_pnl = sum(first_hits$pnl, na.rm = TRUE),
    avg_pnl_per_trade = mean(first_hits$pnl, na.rm = TRUE),
    roi_on_cost = sum(first_hits$pnl, na.rm = TRUE) / sum(first_hits$entry_price, na.rm = TRUE),
    stringsAsFactors = FALSE
  )

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  first_hits_path <- file.path(output_dir, "first_hit_events.csv")
  bucket_summary_path <- file.path(output_dir, "first_hit_bucket_summary.csv")
  overall_summary_path <- file.path(output_dir, "overall_summary.csv")
  skipped_path <- file.path(output_dir, "skipped_rounds.csv")
  roi_plot_path <- file.path(output_dir, "first_hit_roi_by_time_bucket.png")

  write.csv(first_hits, first_hits_path, row.names = FALSE, na = "")
  write.csv(bucket_summary, bucket_summary_path, row.names = FALSE, na = "")
  write.csv(overall_summary, overall_summary_path, row.names = FALSE, na = "")
  write.csv(if (is.null(skipped)) data.frame() else skipped, skipped_path, row.names = FALSE, na = "")
  plot_first_hit_bucket_roi(bucket_summary, threshold = threshold, output_path = roi_plot_path)

  cat("Data dir:", data_dir, "\n")
  cat("Files used:", length(selected_files), "\n")
  cat("Worker processes:", use_cores, "\n")
  cat("Threshold:", paste0(round(threshold * 100), "%"), "\n")
  cat("Bucket breaks:", paste(bucket_breaks, collapse = ", "), "\n\n")

  cat("Overall summary:\n")
  print(overall_summary, row.names = FALSE)
  cat("\nFirst-hit time bucket summary:\n")
  print(bucket_summary, row.names = FALSE)

  cat("\nWrote:", first_hits_path, "\n")
  cat("Wrote:", bucket_summary_path, "\n")
  cat("Wrote:", overall_summary_path, "\n")
  cat("Wrote:", skipped_path, "\n")
  cat("Wrote:", roi_plot_path, "\n")

  invisible(list(
    first_hits = first_hits,
    bucket_summary = bucket_summary,
    overall_summary = overall_summary,
    skipped = skipped
  ))
}

if (sys.nframe() == 0) {
  main()
}
