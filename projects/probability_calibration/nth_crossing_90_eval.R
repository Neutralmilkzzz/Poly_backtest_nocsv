# nth_crossing_90_eval.R
# ------------------------------------------------------------
# 研究“第几次突破 90%”是否影响信号质量。
#
# 定义：
# - 对 UP：前一时刻 up_midpoint < threshold，当前时刻 up_midpoint >= threshold
#   记作一次新的 90% 向上突破事件
# - 对 DOWN：前一时刻 up_midpoint > 1-threshold，当前时刻 up_midpoint <= 1-threshold
#   记作一次新的 90% 向下突破事件
#
# 这样可以避免把已经站在 90% 上方的连续抖动重复记很多次。
#
# 用法：
#   Rscript projects/probability_calibration/nth_crossing_90_eval.R --data-dir data
#   Rscript projects/probability_calibration/nth_crossing_90_eval.R --data-dir data --n 500 --threshold 0.9 --cores 24
#   Rscript projects/probability_calibration/nth_crossing_90_eval.R --data-dir data --crossing-groups 1,2,3,4,5,6,7,8,9,10+
# ------------------------------------------------------------

DEFAULT_THRESHOLD <- 0.9
DEFAULT_GROUPS <- c(as.character(1:9), "10+")

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
  file.path(get_script_dir(), "artifacts", "nth_crossing_90_eval")
}

parse_args <- function(args) {
  opts <- list(
    data_dir = NULL,
    n = NULL,
    threshold = DEFAULT_THRESHOLD,
    output_dir = NULL,
    cores = NULL,
    crossing_groups = DEFAULT_GROUPS
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
    if (arg == "--crossing-groups" && i < length(args)) {
      opts$crossing_groups <- trimws(unlist(strsplit(args[i + 1L], ",", fixed = TRUE)))
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
    if (last_up > 0.5) return("up")
    if (last_up < 0.5) return("down")
  }
  NA_character_
}

find_crossing_events <- function(df, threshold) {
  up_now <- !is.na(df$up_midpoint) & df$up_midpoint >= threshold
  up_prev <- c(FALSE, head(up_now, -1))
  up_cross <- which(up_now & !up_prev)

  down_now <- !is.na(df$up_midpoint) & df$up_midpoint <= (1 - threshold)
  down_prev <- c(FALSE, head(down_now, -1))
  down_cross <- which(down_now & !down_prev)

  events <- list()

  if (length(up_cross) > 0L) {
    events <- c(events, lapply(up_cross, function(i) {
      list(index = i, side = "up")
    }))
  }
  if (length(down_cross) > 0L) {
    events <- c(events, lapply(down_cross, function(i) {
      list(index = i, side = "down")
    }))
  }

  if (length(events) == 0L) {
    return(NULL)
  }

  ord <- order(vapply(events, function(e) e$index, integer(1)))
  events[ord]
}

crossing_group_label <- function(crossing_number, configured_groups) {
  if (crossing_number <= 0) {
    return(NA_character_)
  }

  numeric_groups <- suppressWarnings(as.integer(configured_groups[!grepl("\\+$", configured_groups)]))
  numeric_groups <- numeric_groups[!is.na(numeric_groups)]
  plus_groups <- configured_groups[grepl("\\+$", configured_groups)]

  if (crossing_number %in% numeric_groups) {
    return(as.character(crossing_number))
  }

  if (length(plus_groups) > 0L) {
    thresholds <- suppressWarnings(as.integer(sub("\\+$", "", plus_groups)))
    thresholds <- thresholds[!is.na(thresholds)]
    if (length(thresholds) > 0L) {
      best_idx <- which.max(thresholds)
      if (crossing_number >= thresholds[best_idx]) {
        return(plus_groups[best_idx])
      }
    }
  }

  NA_character_
}

analyze_one_file <- function(csv_path, threshold, configured_groups) {
  raw_df <- tryCatch(
    fast_read_csv(csv_path),
    error = function(e) NULL
  )

  if (is.null(raw_df)) {
    return(list(events = NULL, skipped = data.frame(file = basename(csv_path), status = "read_error", stringsAsFactors = FALSE)))
  }

  prepared <- prepare_round(raw_df)
  if (!identical(prepared$status, "ok")) {
    return(list(events = NULL, skipped = data.frame(file = basename(csv_path), status = prepared$status, stringsAsFactors = FALSE)))
  }

  df <- prepared$df
  final_side <- determine_final_side(df)
  if (is.na(final_side)) {
    return(list(events = NULL, skipped = data.frame(file = basename(csv_path), status = "missing_label", stringsAsFactors = FALSE)))
  }

  events <- find_crossing_events(df, threshold)
  if (is.null(events)) {
    return(list(events = NULL, skipped = data.frame(file = basename(csv_path), status = "no_crossing", stringsAsFactors = FALSE)))
  }

  out_rows <- lapply(seq_along(events), function(k) {
    ev <- events[[k]]
    i <- ev$index
    side <- ev$side
    ask_col <- if (identical(side, "up")) "up_best_ask" else "down_best_ask"
    if (!(ask_col %in% names(df))) {
      return(NULL)
    }

    entry_price <- suppressWarnings(as.numeric(df[[ask_col]][i]))
    if (is.na(entry_price) || entry_price <= 0 || entry_price > 1) {
      return(NULL)
    }

    implied_prob <- if (identical(side, "up")) df$up_midpoint[i] else 1 - df$up_midpoint[i]
    payout <- if (identical(side, final_side)) 1 else 0
    group_label <- crossing_group_label(k, configured_groups)

    data.frame(
      file = basename(csv_path),
      threshold = threshold,
      threshold_label = paste0(round(threshold * 100), "%"),
      crossing_number = k,
      crossing_group = group_label,
      round_start_utc = df$timestamp[1],
      hit_timestamp = df$timestamp[i],
      hit_elapsed = as.numeric(df$elapsed[i]),
      side = side,
      implied_prob_selected_side = implied_prob,
      entry_price = entry_price,
      final_side = final_side,
      win = as.integer(identical(side, final_side)),
      payout = payout,
      pnl = payout - entry_price,
      stringsAsFactors = FALSE
    )
  })

  out_rows <- out_rows[!vapply(out_rows, is.null, logical(1))]
  if (length(out_rows) == 0L) {
    return(list(events = NULL, skipped = data.frame(file = basename(csv_path), status = "no_usable_crossing", stringsAsFactors = FALSE)))
  }

  list(events = do.call(rbind, out_rows), skipped = NULL)
}

bind_rows_safe <- function(dfs) {
  dfs <- dfs[!vapply(dfs, is.null, logical(1))]
  dfs <- dfs[vapply(dfs, nrow, integer(1)) > 0L]
  if (length(dfs) == 0L) {
    return(NULL)
  }
  do.call(rbind, dfs)
}

summarize_groups <- function(event_df, configured_groups) {
  rows <- lapply(configured_groups, function(group_label) {
    part <- event_df[event_df$crossing_group == group_label, , drop = FALSE]
    if (nrow(part) == 0L) {
      return(data.frame(
        crossing_group = group_label,
        opportunities = 0L,
        unique_rounds = 0L,
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
      crossing_group = group_label,
      opportunities = nrow(part),
      unique_rounds = length(unique(part$file)),
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

plot_crossing_group_summary <- function(grouped_summary, threshold, output_path) {
  plot_df <- grouped_summary[grouped_summary$opportunities > 0, , drop = FALSE]
  if (nrow(plot_df) == 0L) {
    return(invisible(NULL))
  }

  labels <- plot_df$crossing_group
  x <- seq_len(nrow(plot_df))
  win_rate <- plot_df$win_rate
  entry_price <- plot_df$avg_entry_price
  y_min <- min(c(win_rate, entry_price), na.rm = TRUE)
  y_max <- max(c(win_rate, entry_price), na.rm = TRUE)
  if (!is.finite(y_min) || !is.finite(y_max) || y_min == y_max) {
    y_min <- 0.85
    y_max <- 0.95
  }

  png(output_path, width = 1400, height = 800, res = 150)
  on.exit(dev.off(), add = TRUE)
  par(mar = c(8, 5, 4, 2) + 0.1)

  plot(
    x,
    win_rate,
    type = "b",
    pch = 19,
    lwd = 2,
    col = "#1f77b4",
    xaxt = "n",
    xlab = "Nth-crossing bucket",
    ylab = "Rate / price",
    main = sprintf("Nth-crossing %s: win rate vs entry price", paste0(round(threshold * 100), "%")),
    ylim = c(y_min, y_max)
  )
  lines(x, entry_price, type = "b", pch = 17, lwd = 2, col = "#d62728")
  axis(1, at = x, labels = labels, las = 2)
  legend(
    "topright",
    legend = c("Win rate", "Avg entry price"),
    col = c("#1f77b4", "#d62728"),
    lty = 1,
    pch = c(19, 17),
    bty = "n"
  )
  grid(nx = NA, ny = NULL, col = "gray90", lty = "dotted")
}

main <- function(
  data_dir = NULL,
  n = NULL,
  threshold = DEFAULT_THRESHOLD,
  output_dir = NULL,
  cores = NULL,
  crossing_groups = DEFAULT_GROUPS
) {
  opts <- parse_args(commandArgs(trailingOnly = TRUE))
  if (is.null(data_dir)) data_dir <- opts$data_dir
  if (is.null(n)) n <- opts$n
  if (missing(threshold) || is.null(threshold)) threshold <- opts$threshold
  if (is.null(output_dir)) output_dir <- opts$output_dir
  if (is.null(cores)) cores <- opts$cores
  if (missing(crossing_groups) || is.null(crossing_groups)) crossing_groups <- opts$crossing_groups

  data_dir <- resolve_data_dir(data_dir)
  output_dir <- resolve_output_dir(output_dir)

  threshold <- as.numeric(threshold)
  if (is.na(threshold) || threshold <= 0.5 || threshold >= 1) {
    stop("threshold must be between 0.5 and 1.")
  }

  crossing_groups <- crossing_groups[nzchar(crossing_groups)]
  if (length(crossing_groups) == 0L) {
    stop("crossing_groups must not be empty.")
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
    configured_groups = crossing_groups,
    cores = use_cores
  )

  event_df <- bind_rows_safe(lapply(analyzed, `[[`, "events"))
  skipped <- bind_rows_safe(lapply(analyzed, `[[`, "skipped"))

  if (is.null(event_df) || nrow(event_df) == 0L) {
    stop("No usable threshold-crossing events were found.")
  }

  grouped_summary <- summarize_groups(event_df, crossing_groups)
  overall_summary <- data.frame(
    threshold = threshold,
    threshold_label = paste0(round(threshold * 100), "%"),
    opportunities = nrow(event_df),
    unique_rounds = length(unique(event_df$file)),
    wins = sum(event_df$win, na.rm = TRUE),
    losses = sum(1L - event_df$win, na.rm = TRUE),
    win_rate = mean(event_df$win, na.rm = TRUE),
    avg_entry_price = mean(event_df$entry_price, na.rm = TRUE),
    total_pnl = sum(event_df$pnl, na.rm = TRUE),
    avg_pnl_per_trade = mean(event_df$pnl, na.rm = TRUE),
    roi_on_cost = sum(event_df$pnl, na.rm = TRUE) / sum(event_df$entry_price, na.rm = TRUE),
    stringsAsFactors = FALSE
  )

  dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
  events_path <- file.path(output_dir, "crossing_events.csv")
  grouped_path <- file.path(output_dir, "crossing_group_summary.csv")
  overall_path <- file.path(output_dir, "overall_summary.csv")
  skipped_path <- file.path(output_dir, "skipped_rounds.csv")
  plot_path <- file.path(output_dir, "nth_crossing_winrate_vs_entryprice.png")

  write.csv(event_df, events_path, row.names = FALSE, na = "")
  write.csv(grouped_summary, grouped_path, row.names = FALSE, na = "")
  write.csv(overall_summary, overall_path, row.names = FALSE, na = "")
  write.csv(if (is.null(skipped)) data.frame() else skipped, skipped_path, row.names = FALSE, na = "")
  plot_crossing_group_summary(grouped_summary, threshold = threshold, output_path = plot_path)

  cat("Data dir:", data_dir, "\n")
  cat("Files used:", length(selected_files), "\n")
  cat("Worker processes:", use_cores, "\n")
  cat("Threshold:", paste0(round(threshold * 100), "%"), "\n")
  cat("Crossing groups:", paste(crossing_groups, collapse = ", "), "\n\n")

  cat("Overall summary:\n")
  print(overall_summary, row.names = FALSE)
  cat("\nCrossing-group summary:\n")
  print(grouped_summary, row.names = FALSE)

  cat("\nWrote:", events_path, "\n")
  cat("Wrote:", grouped_path, "\n")
  cat("Wrote:", overall_path, "\n")
  cat("Wrote:", skipped_path, "\n")
  cat("Wrote:", plot_path, "\n")

  invisible(list(
    events = event_df,
    grouped_summary = grouped_summary,
    overall_summary = overall_summary,
    skipped = skipped
  ))
}

if (sys.nframe() == 0) {
  main()
}
