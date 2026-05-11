# build_first30_regime_dataset.R
# ------------------------------------------------------------
# 构建“本轮前 30 秒 -> 最终 regime 标签”的监督学习数据集。
# 标签来自整轮 winner-aligned K-means 聚类。
# 特征只允许使用前 30 秒可观测数据，避免未来信息泄露。
# 用法：
# source("C:/Users/ZHAOKAI/Poly_backtest_Final/projects/probability_calibration/entry_regime_supervised/build_first30_regime_dataset.R")
# run_interactive()
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

project_dir <- normalizePath(file.path(get_script_dir(), ".."), winslash = "/", mustWork = FALSE)
source(file.path(project_dir, "interactive_helpers.R"), local = TRUE)

regime_env <- new.env(parent = globalenv())
source(file.path(project_dir, "cluster_regime_features.R"), local = regime_env)

FIRST30_SECONDS <- seq(0, 30, by = 5)

load_first30_window <- function(csv_path, window_seconds = 30) {
  df <- tryCatch(read.csv(csv_path, stringsAsFactors = FALSE), error = function(e) NULL)
  if (is.null(df) || !all(c("timestamp", "up_midpoint") %in% names(df))) {
    return(NULL)
  }

  df$timestamp <- regime_env$parse_timestamp_vector(df$timestamp)
  df$up_midpoint <- suppressWarnings(as.numeric(df$up_midpoint))
  df <- df[!is.na(df$timestamp) & !is.na(df$up_midpoint), , drop = FALSE]
  df <- df[order(df$timestamp), , drop = FALSE]
  if (nrow(df) < 2) {
    return(NULL)
  }

  df$elapsed <- as.numeric(difftime(df$timestamp, df$timestamp[1], units = "secs"))
  df <- df[df$elapsed >= 0 & df$elapsed <= window_seconds, c("elapsed", "up_midpoint"), drop = FALSE]
  if (nrow(df) < 2) {
    return(NULL)
  }

  df
}

compute_first30_features <- function(window_df) {
  series <- window_df$up_midpoint
  elapsed <- window_df$elapsed
  idx_30 <- max(which(elapsed <= 30))
  current_prob_30 <- series[idx_30]
  opening_prob <- series[1]
  move_0_30 <- current_prob_30 - opening_prob
  abs_move_0_30 <- abs(move_0_30)
  path_length_0_30 <- sum(abs(diff(series)))
  range_width_0_30 <- diff(range(series))
  crossing_count_0_30 <- regime_env$count_sign_crossings(series, center = 0.5)
  max_run_ratio_0_30 <- regime_env$max_directional_run(series)
  er_0_30 <- regime_env$calc_efficiency_ratio(series)
  hurst_0_30 <- regime_env$compute_hurst(series)
  returns <- diff(series)
  realized_vol_0_30 <- if (length(returns) > 1) stats::sd(returns) else 0
  mean_prob_0_30 <- mean(series)
  slope_per_sec_0_30 <- move_0_30 / 30
  distance_from_half_30 <- abs(current_prob_30 - 0.5)

  data.frame(
    opening_up_prob = opening_prob,
    current_up_prob_30 = current_prob_30,
    move_0_30 = move_0_30,
    abs_move_0_30 = abs_move_0_30,
    slope_per_sec_0_30 = slope_per_sec_0_30,
    mean_prob_0_30 = mean_prob_0_30,
    distance_from_half_30 = distance_from_half_30,
    er_0_30 = er_0_30,
    hurst_0_30 = hurst_0_30,
    path_length_0_30 = path_length_0_30,
    range_width_0_30 = range_width_0_30,
    crossing_count_0_30 = crossing_count_0_30,
    max_run_ratio_0_30 = max_run_ratio_0_30,
    realized_vol_0_30 = realized_vol_0_30,
    n_points_0_30 = length(series),
    stringsAsFactors = FALSE
  )
}

build_first30_feature_df <- function(csv_files) {
  rows <- lapply(csv_files, function(csv_path) {
    window_df <- load_first30_window(csv_path, window_seconds = 30)
    if (is.null(window_df)) {
      return(NULL)
    }

    features <- compute_first30_features(window_df)
    finite_mask <- vapply(features[1, ], is.finite, logical(1))
    finite_mask[is.na(finite_mask)] <- FALSE
    allow_na <- names(features) == "hurst_0_30"
    if (any(!(finite_mask | allow_na))) {
      return(NULL)
    }

    data.frame(
      file = basename(csv_path),
      features,
      stringsAsFactors = FALSE,
      check.names = FALSE
    )
  })

  rows <- rows[!vapply(rows, is.null, logical(1))]
  if (length(rows) == 0) {
    return(NULL)
  }

  do.call(rbind, rows)
}

main <- function(data_dir = NULL,
                 n = 1000,
                 out_dir = NULL,
                 normalize_method = "zscore",
                 seed = 42) {
  cluster_result <- regime_env$main(
    data_dir = data_dir,
    n = n,
    out_dir = tempfile(pattern = "regime_cluster_labels_"),
    normalize_method = normalize_method,
    seed = seed
  )

  data_dir <- regime_env$resolve_data_dir(data_dir)
  csv_files <- list.files(data_dir, pattern = "\\.csv$", full.names = TRUE)
  info <- file.info(csv_files)
  info$file <- rownames(info)
  info <- info[order(info$mtime, decreasing = TRUE), , drop = FALSE]
  n_use <- min(as.integer(n), nrow(info))
  recent_files <- info$file[seq_len(n_use)]

  first30_df <- build_first30_feature_df(recent_files)
  if (is.null(first30_df) || nrow(first30_df) == 0) {
    stop("无法构建前 30 秒特征数据集。")
  }

  label_df <- cluster_result$assignments[, c("file", "winner_side", "regime", "cluster"), drop = FALSE]
  dataset <- merge(first30_df, label_df, by = "file", all = FALSE, sort = FALSE)
  dataset$target_trend_dangerous <- as.integer(dataset$regime == "trend_dangerous")

  if (nrow(dataset) == 0) {
    stop("前 30 秒特征与 regime 标签没有成功对齐。")
  }

  if (is.null(out_dir)) {
    repo_root <- regime_env$find_repo_root(getwd())
    if (is.null(repo_root)) {
      repo_root <- regime_env$find_repo_root(get_script_dir())
    }
    if (is.null(repo_root)) {
      repo_root <- getwd()
    }
    out_dir <- file.path(
      repo_root,
      "projects",
      "probability_calibration",
      "entry_regime_supervised",
      "artifacts"
    )
  }
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  dataset_path <- file.path(out_dir, "first30_regime_dataset.csv")
  summary_path <- file.path(out_dir, "first30_regime_summary.csv")

  summary_df <- aggregate(
    dataset[, setdiff(names(dataset), c("file", "winner_side", "regime")), drop = FALSE],
    by = list(regime = dataset$regime),
    FUN = function(x) mean(x, na.rm = TRUE)
  )

  write.csv(dataset, dataset_path, row.names = FALSE)
  write.csv(summary_df, summary_path, row.names = FALSE)

  cat("Data dir:", data_dir, "\n")
  cat("Files requested:", n_use, "\n")
  cat("Rows in dataset:", nrow(dataset), "\n")
  cat("Dataset saved to:", dataset_path, "\n")
  cat("Summary saved to:", summary_path, "\n\n")
  print(summary_df, row.names = FALSE)

  invisible(list(
    dataset = dataset,
    summary = summary_df,
    dataset_path = dataset_path,
    summary_path = summary_path
  ))
}

default_entry_artifacts_dir <- function() {
  repo_root <- regime_env$find_repo_root(getwd())
  if (is.null(repo_root)) {
    repo_root <- regime_env$find_repo_root(get_script_dir())
  }

  if (is.null(repo_root)) {
    return(file.path(getwd(), "projects", "probability_calibration", "entry_regime_supervised", "artifacts"))
  }

  file.path(repo_root, "projects", "probability_calibration", "entry_regime_supervised", "artifacts")
}

run_build_first30_regime_dataset_interactive <- function() {
  data_dir <- prompt_path_value("数据目录", default = regime_env$resolve_data_dir())
  n <- prompt_integer_value("最近要分析多少个轮次 n", default = 1000L, min_value = 1L)
  normalize_method <- prompt_choice_value(
    "归一化方法",
    choices = c("zscore", "minmax", "none"),
    default = "zscore"
  )
  out_dir <- prompt_path_value("输出目录", default = default_entry_artifacts_dir())

  main(
    data_dir = data_dir,
    n = n,
    out_dir = out_dir,
    normalize_method = normalize_method
  )
}

run_interactive <- run_build_first30_regime_dataset_interactive

if (sys.nframe() == 0) {
  main()
} else {
  announce_interactive_ready("build_first30_regime_dataset.R", "run_interactive", parent.frame())
}
