# average_probability_path.R
# ------------------------------------------------------------
# 画 Polymarket 5 分钟市场的“平均概率路径图”。
#
# 直观理解：
# - 每个 CSV 是一轮市场
# - 每轮都有一条 up_midpoint 随时间变化的路径
# - 我们把很多轮对齐到“开盘后第几秒”，然后取平均
# - 最后画出一条 mean probability path
#
# 这张图能帮助你回答：
# “市场概率在 5 分钟里是如何逐步收敛的？”
#
# 用法：
# source("C:/Users/ZHAOKAI/Poly_backtest_Final/projects/probability_calibration/average_probability_path.R")
# run_interactive()
# main(
#   data_dir = "C:/Users/ZHAOKAI/Poly_backtest_Final/data",
#   n = 500,
#   plot_path = "C:/Users/ZHAOKAI/Poly_backtest_Final/projects/probability_calibration/average_probability_path_500.png"
# )
# ------------------------------------------------------------

DEFAULT_SECONDS <- seq(0, 300, by = 5)

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

source(file.path(get_script_dir(), "interactive_helpers.R"), local = TRUE)

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

parse_timestamp_vector <- function(x) {
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

load_round_path <- function(csv_path, sample_seconds = DEFAULT_SECONDS) {
  df <- tryCatch(
    read.csv(csv_path, stringsAsFactors = FALSE),
    error = function(e) NULL
  )
  if (is.null(df) || !all(c("timestamp", "up_midpoint") %in% names(df))) {
    return(NULL)
  }

  df$timestamp <- parse_timestamp_vector(df$timestamp)
  df$up_midpoint <- suppressWarnings(as.numeric(df$up_midpoint))
  df <- df[!is.na(df$timestamp), , drop = FALSE]
  df <- df[order(df$timestamp), , drop = FALSE]
  if (nrow(df) == 0) {
    return(NULL)
  }

  df$elapsed <- as.numeric(difftime(df$timestamp, df$timestamp[1], units = "secs"))
  df <- df[!is.na(df$up_midpoint), , drop = FALSE]
  if (nrow(df) == 0) {
    return(NULL)
  }

  rows <- lapply(sample_seconds, function(sec) {
    window <- df[df$elapsed <= sec, , drop = FALSE]
    if (nrow(window) == 0) {
      return(data.frame(
        file = basename(csv_path),
        elapsed_s = sec,
        implied_prob_up = NA_real_,
        stringsAsFactors = FALSE
      ))
    }

    data.frame(
      file = basename(csv_path),
      elapsed_s = sec,
      implied_prob_up = tail(window$up_midpoint, 1),
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, rows)
}

summarize_average_path <- function(path_rows) {
  ok <- path_rows[!is.na(path_rows$implied_prob_up), , drop = FALSE]
  if (nrow(ok) == 0) {
    return(data.frame())
  }

  summary <- aggregate(
    implied_prob_up ~ elapsed_s,
    data = ok,
    FUN = function(x) c(mean = mean(x), sd = sd(x), count = length(x))
  )

  data.frame(
    elapsed_s = summary$elapsed_s,
    mean_prob_up = summary$implied_prob_up[, "mean"],
    sd_prob_up = summary$implied_prob_up[, "sd"],
    rounds_used = summary$implied_prob_up[, "count"],
    stringsAsFactors = FALSE
  )
}

plot_average_path <- function(summary_df, plot_path = NULL) {
  if (nrow(summary_df) == 0) {
    message("No valid path summary to plot.")
    return(invisible(NULL))
  }

  if (!is.null(plot_path)) {
    dir.create(dirname(plot_path), recursive = TRUE, showWarnings = FALSE)
    png(plot_path, width = 950, height = 600)
    on.exit(dev.off(), add = TRUE)
  }

  upper <- pmin(1, summary_df$mean_prob_up + summary_df$sd_prob_up)
  lower <- pmax(0, summary_df$mean_prob_up - summary_df$sd_prob_up)

  plot(
    summary_df$elapsed_s,
    summary_df$mean_prob_up,
    type = "n",
    ylim = c(0, 1),
    xlab = "Elapsed seconds",
    ylab = "Mean up_midpoint",
    main = "Average Probability Path"
  )

  polygon(
    c(summary_df$elapsed_s, rev(summary_df$elapsed_s)),
    c(upper, rev(lower)),
    col = rgb(70, 130, 180, 80, maxColorValue = 255),
    border = NA
  )

  lines(summary_df$elapsed_s, summary_df$mean_prob_up, col = "steelblue4", lwd = 3)
  abline(h = 0.5, col = "gray60", lty = 2)

  legend(
    "topleft",
    legend = c("Mean path", "Mean +/- 1 SD", "0.5 reference"),
    col = c("steelblue4", rgb(70, 130, 180, 80, maxColorValue = 255), "gray60"),
    lty = c(1, NA, 2),
    lwd = c(3, NA, 1),
    pch = c(NA, 15, NA),
    pt.cex = c(NA, 2, NA),
    bty = "n"
  )
}

default_artifacts_dir <- function() {
  repo_root <- find_repo_root(getwd())
  if (is.null(repo_root)) {
    repo_root <- find_repo_root(get_script_dir())
  }

  if (is.null(repo_root)) {
    return(file.path(getwd(), "projects", "probability_calibration", "artifacts"))
  }

  file.path(repo_root, "projects", "probability_calibration", "artifacts")
}

main <- function(data_dir = NULL, n = 200, plot_path = NULL, sample_seconds = DEFAULT_SECONDS) {
  data_dir <- resolve_data_dir(data_dir)
  csv_files <- list.files(data_dir, pattern = "\\.csv$", full.names = TRUE)
  if (length(csv_files) == 0) {
    stop(sprintf("No CSV files found in %s", data_dir))
  }

  info <- file.info(csv_files)
  info$file <- rownames(info)
  info <- info[order(info$mtime, decreasing = TRUE), , drop = FALSE]
  n_use <- min(as.integer(n), nrow(info))
  recent_files <- info$file[seq_len(n_use)]

  path_rows <- do.call(rbind, lapply(recent_files, load_round_path, sample_seconds = sample_seconds))
  summary_df <- summarize_average_path(path_rows)

  cat("Data dir:", data_dir, "\n")
  cat("Files used:", n_use, "\n\n")
  print(summary_df, row.names = FALSE)

  plot_average_path(summary_df, plot_path = plot_path)
  if (!is.null(plot_path)) {
    cat("\nPlot saved to:", plot_path, "\n")
  }

  invisible(list(path_rows = path_rows, summary = summary_df))
}

run_average_probability_path_interactive <- function() {
  data_dir <- prompt_path_value("数据目录", default = resolve_data_dir())
  n <- prompt_integer_value("最近要分析多少个轮次 n", default = 200L, min_value = 1L)
  plot_path <- prompt_optional_path(
    "平均概率路径图保存路径",
    default = file.path(default_artifacts_dir(), sprintf("average_probability_path_%d.png", n))
  )

  main(
    data_dir = data_dir,
    n = n,
    plot_path = plot_path
  )
}

run_interactive <- run_average_probability_path_interactive

if (sys.nframe() == 0) {
  main()
} else {
  announce_interactive_ready("average_probability_path.R", "run_interactive", parent.frame())
}
