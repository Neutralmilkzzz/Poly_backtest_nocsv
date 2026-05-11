# cluster_regime_features.R
# ------------------------------------------------------------
# 用 ER / Hurst 等路径特征做 K-means，把市场分成：
# 1. trend_dangerous      更接近大单边、对均值回归危险
# 2. mean_reversion_ok    更接近正常震荡、对均值回归友好
#
# 关键点：
# - 不是直接拿原始路径做聚类，而是先做特征工程
# - 所有路径都会先对齐到“最终赢家那条线怎么赢”
# - K-means 对量纲很敏感，所以先做归一化
# - 默认使用 z-score：(x - mean(x)) / sd(x)
# - 想给 ER / Hurst 更大权重，就在归一化后乘权重
#
# 用法：
# source("C:/Users/ZHAOKAI/Poly_backtest_Final/projects/probability_calibration/cluster_regime_features.R")
# run_interactive()
# main(
#   data_dir = "C:/Users/ZHAOKAI/Poly_backtest_Final/data",
#   n = 1000,
#   plot_path = "C:/Users/ZHAOKAI/Poly_backtest_Final/projects/probability_calibration/cluster_regime_paths.png"
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
source(file.path(get_script_dir(), "performance_helpers.R"), local = TRUE)

find_repo_root <- function(start_dir) {
  current <- normalizePath(start_dir, winslash = "/", mustWork = FALSE)

  repeat {
    if (dir.exists(file.path(current, "projects", "probability_calibration")) &&
        dir.exists(file.path(current, "data"))) {
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

determine_winner_side <- function(values) {
  values <- values[!is.na(values)]
  if (length(values) == 0) {
    return(NA_character_)
  }

  last_up <- tail(values, 1)
  if (last_up > 0.5) {
    return("up")
  }
  if (last_up < 0.5) {
    return("down")
  }
  NA_character_
}

sample_round_series <- function(csv_path, sample_seconds = DEFAULT_SECONDS) {
  df <- tryCatch(fast_read_csv(csv_path), error = function(e) NULL)
  if (is.null(df) || !all(c("timestamp", "up_midpoint") %in% names(df))) {
    return(NULL)
  }

  df$timestamp <- parse_timestamp_vector(df$timestamp)
  df$up_midpoint <- suppressWarnings(as.numeric(df$up_midpoint))
  df <- df[!is.na(df$timestamp) & !is.na(df$up_midpoint), , drop = FALSE]
  df <- df[order(df$timestamp), , drop = FALSE]
  if (nrow(df) < 8) {
    return(NULL)
  }

  df$elapsed <- as.numeric(difftime(df$timestamp, df$timestamp[1], units = "secs"))

  values <- vapply(sample_seconds, function(sec) {
    window <- df[df$elapsed <= sec, , drop = FALSE]
    if (nrow(window) == 0) {
      return(NA_real_)
    }
    tail(window$up_midpoint, 1)
  }, numeric(1))

  if (all(is.na(values))) {
    return(NULL)
  }

  first_valid <- which(!is.na(values))[1]
  if (!is.na(first_valid) && first_valid > 1) {
    values[1:(first_valid - 1)] <- values[first_valid]
  }
  for (i in seq_along(values)) {
    if (i > 1 && is.na(values[i])) {
      values[i] <- values[i - 1]
    }
  }
  if (any(is.na(values))) {
    return(NULL)
  }

  winner_side <- determine_winner_side(values)
  if (is.na(winner_side)) {
    return(NULL)
  }

  winner_prob <- if (identical(winner_side, "up")) values else 1 - values

  data.frame(
    elapsed = sample_seconds,
    up_midpoint = values,
    winner_prob = winner_prob,
    winner_side = winner_side,
    stringsAsFactors = FALSE
  )
}

calc_efficiency_ratio <- function(series) {
  series <- series[!is.na(series)]
  if (length(series) < 2) return(NA_real_)
  path_length <- sum(abs(diff(series)))
  if (is.na(path_length) || path_length <= 0) return(NA_real_)
  abs(tail(series, 1) - series[1]) / path_length
}

compute_hurst <- function(series) {
  if (!requireNamespace("pracma", quietly = TRUE)) {
    stop("需要安装 pracma 包: install.packages('pracma')")
  }

  series <- series[!is.na(series)]
  if (length(series) < 8) return(NA_real_)

  out <- tryCatch(pracma::hurstexp(series, display = FALSE), error = function(e) NULL)
  if (is.null(out)) return(NA_real_)

  candidates <- c(out$Hs, out$Hal, out$He, out$Ht)
  candidates <- candidates[is.finite(candidates)]
  if (length(candidates) == 0) return(NA_real_)
  candidates[1]
}

count_sign_crossings <- function(series, center = 0.5) {
  shifted <- series - center
  shifted[abs(shifted) < 1e-8] <- 0
  signs <- sign(shifted)
  signs <- signs[signs != 0]
  if (length(signs) < 2) {
    return(0)
  }
  sum(diff(signs) != 0)
}

max_directional_run <- function(series) {
  deltas <- diff(series)
  deltas <- deltas[abs(deltas) > 1e-8]
  if (length(deltas) == 0) {
    return(0)
  }
  directions <- sign(deltas)
  runs <- rle(directions)
  max(runs$lengths) / length(directions)
}

compute_round_features <- function(sampled_df) {
  series <- sampled_df$winner_prob
  elapsed <- sampled_df$elapsed
  late_anchor_idx <- max(which(elapsed <= 240))

  path_length <- sum(abs(diff(series)))
  net_move_abs <- abs(tail(series, 1) - series[1])
  late_move_abs <- abs(tail(series, 1) - series[late_anchor_idx])
  range_width <- diff(range(series))
  crossing_count <- count_sign_crossings(series, center = 0.5)
  max_run_ratio <- max_directional_run(series)

  data.frame(
    er = calc_efficiency_ratio(series),
    hurst = compute_hurst(series),
    net_move_abs = net_move_abs,
    late_move_abs = late_move_abs,
    path_length = path_length,
    crossing_count = crossing_count,
    max_run_ratio = max_run_ratio,
    range_width = range_width,
    stringsAsFactors = FALSE
  )
}

zscore_normalize <- function(x) {
  x <- as.numeric(x)
  mu <- mean(x, na.rm = TRUE)
  sigma <- stats::sd(x, na.rm = TRUE)
  if (!is.finite(sigma) || sigma <= 0) {
    return(rep(0, length(x)))
  }
  (x - mu) / sigma
}

minmax_normalize <- function(x) {
  x <- as.numeric(x)
  lo <- min(x, na.rm = TRUE)
  hi <- max(x, na.rm = TRUE)
  span <- hi - lo
  if (!is.finite(span) || span <= 0) {
    return(rep(0, length(x)))
  }
  (x - lo) / span
}

normalize_feature_df <- function(feature_df, method = c("zscore", "minmax", "none")) {
  method <- match.arg(method)
  out <- feature_df

  for (nm in names(feature_df)) {
    if (method == "zscore") {
      out[[nm]] <- zscore_normalize(feature_df[[nm]])
    } else if (method == "minmax") {
      out[[nm]] <- minmax_normalize(feature_df[[nm]])
    } else {
      out[[nm]] <- as.numeric(feature_df[[nm]])
    }
  }

  out
}

build_feature_dataset <- function(csv_files, sample_seconds = DEFAULT_SECONDS, cores = NULL) {
  rows <- parallel_map(csv_files, function(csv_path) {
    sampled_df <- sample_round_series(csv_path, sample_seconds = sample_seconds)
    if (is.null(sampled_df)) {
      return(NULL)
    }

    features <- compute_round_features(sampled_df)
    if (any(!is.finite(as.numeric(features[1, ])))) {
      return(NULL)
    }

    data.frame(
      file = basename(csv_path),
      features,
      stringsAsFactors = FALSE,
      check.names = FALSE
    )
  }, cores = cores)

  rows <- rows[!vapply(rows, is.null, logical(1))]
  if (length(rows) == 0) {
    return(NULL)
  }

  feature_df <- do.call(rbind, rows)
  sampled_info <- lapply(feature_df$file, function(file_name) {
    sample_round_series(csv_files[basename(csv_files) == file_name][1], sample_seconds = sample_seconds)
  })
  path_matrix <- do.call(rbind, lapply(sampled_info, function(item) item$winner_prob))
  rownames(path_matrix) <- feature_df$file
  colnames(path_matrix) <- paste0("t_", sample_seconds)
  winner_side <- vapply(sampled_info, function(item) item$winner_side[1], character(1))
  feature_df$winner_side <- winner_side

  list(
    feature_df = feature_df,
    path_matrix = path_matrix
  )
}

summarize_clusters <- function(path_matrix, cluster_ids, sample_seconds = DEFAULT_SECONDS) {
  cluster_levels <- sort(unique(cluster_ids))
  rows <- lapply(cluster_levels, function(cluster_id) {
    members <- path_matrix[cluster_ids == cluster_id, , drop = FALSE]
    data.frame(
      cluster = cluster_id,
      elapsed_s = sample_seconds,
      mean_winner_prob = colMeans(members),
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, rows)
}

plot_cluster_paths <- function(cluster_summary, plot_path = NULL) {
  if (nrow(cluster_summary) == 0) {
    return(invisible(NULL))
  }

  if (!is.null(plot_path)) {
    dir.create(dirname(plot_path), recursive = TRUE, showWarnings = FALSE)
    png(plot_path, width = 1100, height = 700)
    on.exit(dev.off(), add = TRUE)
  }

  colors <- c("firebrick", "steelblue4")
  plot(
    NA,
    xlim = range(cluster_summary$elapsed_s),
    ylim = c(0, 1),
    xlab = "Elapsed seconds",
    ylab = "Mean winner-side probability",
    main = "Regime Cluster Winner-Aligned Paths"
  )

  for (cluster_id in sort(unique(cluster_summary$cluster))) {
    part <- cluster_summary[cluster_summary$cluster == cluster_id, , drop = FALSE]
    lines(part$elapsed_s, part$mean_winner_prob, lwd = 3, col = colors[cluster_id])
  }

  abline(h = 0.5, col = "gray60", lty = 2)
  legend(
    "topleft",
    legend = c("trend_dangerous", "mean_reversion_ok"),
    col = colors,
    lwd = 3,
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

main <- function(data_dir = NULL,
                 n = 1000,
                 k = 2,
                 sample_seconds = DEFAULT_SECONDS,
                 normalize_method = c("zscore", "minmax", "none"),
                 weights = c(
                   er = 3.0,
                   hurst = 3.0,
                   net_move_abs = 2.0,
                   late_move_abs = 1.5,
                   max_run_ratio = 1.5,
                   crossing_count = 1.2,
                   path_length = 1.0,
                   range_width = 1.0
                 ),
                 out_dir = NULL,
                 plot_path = NULL,
                 seed = 42,
                 cores = NULL) {
  if (k != 2) {
    stop("这个脚本当前固定做二分类聚类，请把 k 设为 2。")
  }

  normalize_method <- match.arg(normalize_method)
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

  use_cores <- resolve_cores(cores, n_tasks = length(recent_files))
  built <- build_feature_dataset(recent_files, sample_seconds = sample_seconds, cores = use_cores)
  if (is.null(built) || nrow(built$feature_df) < k) {
    stop("Not enough valid rounds to run feature clustering.")
  }

  raw_features <- built$feature_df
  feature_cols <- setdiff(names(raw_features), c("file", "winner_side"))
  normalized_features <- normalize_feature_df(raw_features[, feature_cols, drop = FALSE], method = normalize_method)

  if (!setequal(names(weights), feature_cols)) {
    missing_weights <- setdiff(feature_cols, names(weights))
    extra_weights <- setdiff(names(weights), feature_cols)
    stop(sprintf(
      "weights 与特征列不匹配。missing: %s ; extra: %s",
      paste(missing_weights, collapse = ", "),
      paste(extra_weights, collapse = ", ")
    ))
  }

  weighted_features <- normalized_features
  for (nm in feature_cols) {
    direction <- if (nm == "crossing_count") -1 else 1
    weighted_features[[nm]] <- normalized_features[[nm]] * weights[[nm]] * direction
  }

  set.seed(seed)
  fit <- kmeans(weighted_features, centers = k, nstart = 50)

  trend_score <- rowSums(weighted_features)
  cluster_mean_score <- tapply(trend_score, fit$cluster, mean)
  dangerous_cluster <- as.integer(names(cluster_mean_score)[which.max(cluster_mean_score)])

  regime_label <- ifelse(fit$cluster == dangerous_cluster, "trend_dangerous", "mean_reversion_ok")
  cluster_numeric <- ifelse(regime_label == "trend_dangerous", 1L, 2L)

  assignment_df <- data.frame(
    file = raw_features$file,
    winner_side = raw_features$winner_side,
    raw_features[, feature_cols, drop = FALSE],
    normalized_features,
    weighted_trend_score = trend_score,
    cluster = cluster_numeric,
    regime = regime_label,
    stringsAsFactors = FALSE,
    check.names = FALSE
  )

  colnames(assignment_df)[3:(2 + length(feature_cols))] <- paste0("raw_", feature_cols)
  colnames(assignment_df)[(3 + length(feature_cols)):(2 + 2 * length(feature_cols))] <- paste0("norm_", feature_cols)

  cluster_summary <- summarize_clusters(built$path_matrix, cluster_numeric, sample_seconds = sample_seconds)

  if (is.null(out_dir)) {
    repo_root <- find_repo_root(get_script_dir())
    if (is.null(repo_root)) {
      out_dir <- file.path(getwd(), "projects", "probability_calibration", "artifacts")
    } else {
      out_dir <- file.path(repo_root, "projects", "probability_calibration", "artifacts")
    }
  }
  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  assignments_path <- file.path(out_dir, "regime_cluster_assignments.csv")
  centers_path <- file.path(out_dir, "regime_cluster_feature_centers.csv")
  summary_path <- file.path(out_dir, "regime_cluster_summary.csv")

  center_df <- aggregate(
    assignment_df[, c(paste0("raw_", feature_cols), "weighted_trend_score"), drop = FALSE],
    by = list(regime = assignment_df$regime),
    FUN = mean
  )
  cluster_counts <- as.data.frame(table(assignment_df$regime), stringsAsFactors = FALSE)
  names(cluster_counts) <- c("regime", "n_rounds")
  summary_df <- merge(cluster_counts, center_df, by = "regime", all.x = TRUE, sort = FALSE)

  write.csv(assignment_df, assignments_path, row.names = FALSE)
  write.csv(center_df, centers_path, row.names = FALSE)
  write.csv(summary_df, summary_path, row.names = FALSE)

  if (is.null(plot_path)) {
    plot_path <- file.path(out_dir, "regime_cluster_paths.png")
  }
  plot_cluster_paths(cluster_summary, plot_path = plot_path)

  cat("Data dir:", data_dir, "\n")
  cat("Files requested:", n_use, "\n")
  cat("Worker processes:", use_cores, "\n")
  cat("Valid rounds clustered:", nrow(raw_features), "\n")
  cat("Normalization:", normalize_method, "\n")
  cat("Assignments saved to:", assignments_path, "\n")
  cat("Centers saved to:", centers_path, "\n")
  cat("Summary saved to:", summary_path, "\n")
  cat("Plot saved to:", plot_path, "\n\n")
  print(summary_df, row.names = FALSE)

  invisible(list(
    assignments = assignment_df,
    summary = summary_df,
    centers = center_df,
    cluster_fit = fit,
    cluster_summary = cluster_summary,
    weighted_features = weighted_features
  ))
}

run_cluster_regime_features_interactive <- function() {
  data_dir <- prompt_path_value("数据目录", default = resolve_data_dir())
  n <- prompt_integer_value("最近要分析多少个轮次 n", default = 1000L, min_value = 1L)
  normalize_method <- prompt_choice_value(
    "归一化方法",
    choices = c("zscore", "minmax", "none"),
    default = "zscore"
  )
  out_dir <- prompt_path_value("输出目录", default = default_artifacts_dir())
  plot_path <- prompt_path_value(
    "聚类路径图保存路径",
    default = file.path(out_dir, "regime_cluster_paths.png")
  )

  main(
    data_dir = data_dir,
    n = n,
    normalize_method = normalize_method,
    out_dir = out_dir,
    plot_path = plot_path
  )
}

run_interactive <- run_cluster_regime_features_interactive

if (sys.nframe() == 0) {
  main()
} else {
  announce_interactive_ready("cluster_regime_features.R", "run_interactive", parent.frame())
}
