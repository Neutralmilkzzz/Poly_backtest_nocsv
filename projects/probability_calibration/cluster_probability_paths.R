# cluster_probability_paths.R
# ------------------------------------------------------------
# 用 k-means 对 5 分钟市场的概率路径做聚类，并画出 3 类平均路径。
#
# 思路：
# 1. 每轮市场按固定时间采样（默认每 5 秒一次）
# 2. 每轮变成一个统一长度的向量
# 3. 先按最终方向分开（UP / DOWN）
# 4. 在组内用 k-means 分成 3 类
# 5. 画出每一类的平均概率路径
#
# 用法：
# source("C:/Users/ZHAOKAI/Poly_backtest_Final/projects/probability_calibration/cluster_probability_paths.R")
# run_interactive()
# main(
#   data_dir = "C:/Users/ZHAOKAI/Poly_backtest_Final/data",
#   n = 1000,
#   side = "up",
#   plot_path = "C:/Users/ZHAOKAI/Poly_backtest_Final/projects/probability_calibration/cluster_paths_1000.png"
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

determine_label <- function(df) {
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

load_round_vector <- function(csv_path, sample_seconds = DEFAULT_SECONDS) {
  df <- tryCatch(
    fast_read_csv(csv_path),
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

  label <- determine_label(df)
  if (is.na(label)) {
    return(NULL)
  }

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

  # 用最近可用值做前向填充，尽量保留路径形状。
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

  list(
    file = basename(csv_path),
    vector = values,
    label = label
  )
}

build_matrix <- function(csv_files, sample_seconds = DEFAULT_SECONDS, side = c("up", "down"), cores = NULL) {
  side <- match.arg(side)
  rows <- parallel_map(csv_files, load_round_vector, sample_seconds = sample_seconds, cores = cores)
  rows <- rows[!vapply(rows, is.null, logical(1))]
  rows <- rows[vapply(rows, function(item) identical(item$label, side), logical(1))]
  if (length(rows) == 0) {
    return(NULL)
  }

  mat <- do.call(rbind, lapply(rows, function(item) item$vector))
  rownames(mat) <- vapply(rows, function(item) item$file, character(1))
  colnames(mat) <- paste0("t_", sample_seconds)
  list(
    matrix = mat,
    labels = data.frame(
      file = vapply(rows, function(item) item$file, character(1)),
      label = vapply(rows, function(item) item$label, character(1)),
      stringsAsFactors = FALSE
    )
  )
}

summarize_clusters <- function(path_matrix, cluster_ids, sample_seconds = DEFAULT_SECONDS) {
  cluster_levels <- sort(unique(cluster_ids))
  rows <- lapply(cluster_levels, function(cluster_id) {
    members <- path_matrix[cluster_ids == cluster_id, , drop = FALSE]
    data.frame(
      cluster = cluster_id,
      elapsed_s = sample_seconds,
      mean_prob_up = colMeans(members),
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, rows)
}

plot_cluster_paths <- function(cluster_summary, plot_path = NULL) {
  if (nrow(cluster_summary) == 0) {
    message("No cluster summary to plot.")
    return(invisible(NULL))
  }

  if (!is.null(plot_path)) {
    dir.create(dirname(plot_path), recursive = TRUE, showWarnings = FALSE)
    png(plot_path, width = 1100, height = 700)
    on.exit(dev.off(), add = TRUE)
  }

  colors <- c("firebrick", "steelblue4", "darkgreen")
  plot(
    NA,
    xlim = range(cluster_summary$elapsed_s),
    ylim = c(0, 1),
    xlab = "Elapsed seconds",
    ylab = "Mean up_midpoint",
    main = "Cluster Mean Probability Paths"
  )

  for (cluster_id in sort(unique(cluster_summary$cluster))) {
    part <- cluster_summary[cluster_summary$cluster == cluster_id, , drop = FALSE]
    lines(part$elapsed_s, part$mean_prob_up, lwd = 3, col = colors[cluster_id])
  }

  abline(h = 0.5, col = "gray60", lty = 2)
  legend(
    "topleft",
    legend = paste("Cluster", sort(unique(cluster_summary$cluster))),
    col = colors[sort(unique(cluster_summary$cluster))],
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

main <- function(data_dir = NULL, n = 1000, k = 3, side = c("up", "down"), plot_path = NULL, sample_seconds = DEFAULT_SECONDS, cores = NULL) {
  side <- match.arg(side)
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
  built <- build_matrix(recent_files, sample_seconds = sample_seconds, side = side, cores = use_cores)
  if (is.null(built) || nrow(built$matrix) < k) {
    stop(sprintf("Not enough valid '%s' rounds to run k-means clustering.", side))
  }
  path_matrix <- built$matrix

  fit <- kmeans(path_matrix, centers = k, nstart = 20)
  cluster_summary <- summarize_clusters(path_matrix, fit$cluster, sample_seconds = sample_seconds)
  assignment_df <- data.frame(
    file = rownames(path_matrix),
    side = side,
    cluster = fit$cluster,
    stringsAsFactors = FALSE
  )

  cat("Data dir:", data_dir, "\n")
  cat("Files requested:", n_use, "\n")
  cat("Worker processes:", use_cores, "\n")
  cat("Side clustered:", side, "\n")
  cat("Valid rounds clustered:", nrow(path_matrix), "\n\n")
  print(as.data.frame(table(assignment_df$cluster), stringsAsFactors = FALSE), row.names = FALSE)

  plot_cluster_paths(cluster_summary, plot_path = plot_path)
  if (!is.null(plot_path)) {
    cat("\nPlot saved to:", plot_path, "\n")
  }

  invisible(
    list(
      path_matrix = path_matrix,
      cluster_fit = fit,
      assignments = assignment_df,
      cluster_summary = cluster_summary,
      labels = built$labels
    )
  )
}

run_cluster_probability_paths_interactive <- function() {
  data_dir <- prompt_path_value("数据目录", default = resolve_data_dir())
  n <- prompt_integer_value("最近要分析多少个轮次 n", default = 1000L, min_value = 1L)
  k <- prompt_integer_value("聚成几类 k", default = 3L, min_value = 2L)
  side <- prompt_choice_value("要聚类哪一边的轮次", choices = c("up", "down"), default = "up")
  plot_path <- prompt_optional_path(
    "聚类路径图保存路径",
    default = file.path(default_artifacts_dir(), sprintf("cluster_paths_%s_%d.png", side, n))
  )

  main(
    data_dir = data_dir,
    n = n,
    k = k,
    side = side,
    plot_path = plot_path
  )
}

run_interactive <- run_cluster_probability_paths_interactive

if (sys.nframe() == 0) {
  main()
} else {
  announce_interactive_ready("cluster_probability_paths.R", "run_interactive", parent.frame())
}
