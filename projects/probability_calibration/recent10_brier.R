# recent10_brier.R
# ------------------------------------------------------------
# 这个脚本是一个“checkpoint 版”的 calibration 例子：
# 它会在固定时间点提取概率，然后比较最近 n 个轮次
# 在不同 checkpoint 的平均 Brier Score，并画柱状图。
#
# 你可以把它理解成：
# 1. 从 data 目录里挑出最近的 10 个市场轮次文件
# 2. 在 30/60/90/120/180/240/270 秒提取概率预测 p
# 3. 给每个轮次构造真实标签 y（UP=1, DOWN=0）
# 4. 计算每个 checkpoint 的 Brier Score = (p - y)^2
# 5. 对最近 n 轮按 checkpoint 求平均，并画柱状图
#
# 用法:
#   Rscript projects/probability_calibration/recent10_brier.R
#   Rscript projects/probability_calibration/recent10_brier.R --data-dir data --n 10
#   source(".../recent10_brier.R"); run_interactive()
#   source(".../recent10_brier.R"); main(data_dir = ".../data", n = 10)
# ------------------------------------------------------------

DEFAULT_CHECKPOINTS <- c(30, 60, 90, 120, 180, 240, 270)

# 找到当前脚本所在目录。
# 依次尝试：
# 1. Rscript 提供的 --file= 参数
# 2. source() 调用时可用的 sys.frames()$ofile
# 3. 最后才退回当前工作目录
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

# 从某个起点目录一路向上找，直到找到仓库根目录。
# 这里把“同时存在 projects/probability_calibration 和 data”当作项目根的标记。
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

# 解析命令行参数。
# 这里只保留两个最基础的参数：
# --data-dir: 手动指定数据目录
# --n:       指定要看最近多少个 CSV
parse_args <- function(args) {
  opts <- list(
    data_dir = NULL,
    n = 10L,
    plot_path = NULL,
    hour_plot_path = NULL,
    reliability_plot_path = NULL,
    bins_csv_path = NULL
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

    if (arg == "--plot-path" && i < length(args)) {
      opts$plot_path <- args[i + 1L]
      i <- i + 2L
      next
    }

    if (arg == "--hour-plot-path" && i < length(args)) {
      opts$hour_plot_path <- args[i + 1L]
      i <- i + 2L
      next
    }

    if (arg == "--reliability-plot-path" && i < length(args)) {
      opts$reliability_plot_path <- args[i + 1L]
      i <- i + 2L
      next
    }

    if (arg == "--bins-csv-path" && i < length(args)) {
      opts$bins_csv_path <- args[i + 1L]
      i <- i + 2L
      next
    }

    i <- i + 1L
  }

  opts
}

# 自动推断数据目录。
# 不再假设“脚本目录往上两层一定是仓库根目录”，
# 而是分别从当前工作目录和脚本目录出发，向上搜索真正的 repo root。
# 找到后：
# - 若 data 根目录直接有 CSV，就用 data
# - 否则如果 data/raw 有 CSV，就用 data/raw
resolve_data_dir <- function(explicit_dir = NULL) {
  if (!is.null(explicit_dir)) {
    return(normalizePath(explicit_dir, winslash = "/", mustWork = FALSE))
  }

  candidate_roots <- unique(c(
    find_repo_root(getwd()),
    find_repo_root(get_script_dir())
  ))
  candidate_roots <- candidate_roots[!is.na(candidate_roots) & nzchar(candidate_roots)]

  if (length(candidate_roots) == 0) {
    stop(
      paste(
        "Could not locate repo root automatically.",
        "Please run from the repository or pass --data-dir explicitly."
      )
    )
  }

  for (repo_root in candidate_roots) {
    primary <- file.path(repo_root, "data")
    raw_dir <- file.path(primary, "raw")

    if (dir.exists(primary) &&
        length(list.files(primary, pattern = "\\.csv$", full.names = TRUE)) > 0) {
      return(primary)
    }

    if (dir.exists(raw_dir) &&
        length(list.files(raw_dir, pattern = "\\.csv$", full.names = TRUE)) > 0) {
      return(raw_dir)
    }
  }

  stop(
    sprintf(
      "No CSV files found in repo data directories checked from: %s",
      paste(candidate_roots, collapse = ", ")
    )
  )
}

# 给一个轮次构造“真实结果”标签。
# 这里沿用你项目里的规则：
# - 优先看 285-298 秒窗口
# - 如果没有，再看 240-285 秒窗口
# - 取窗口里最后一个 up_midpoint
# - up_midpoint > 0.5 记为 UP 赢（1）
# - up_midpoint < 0.5 记为 DOWN 赢（0）
#
# 返回值:
#   1  -> UP 赢
#   0  -> DOWN 赢
#   NA -> 无法确定标签
determine_label <- function(df) {
  if (!("elapsed" %in% names(df))) {
    df$elapsed <- as.numeric(difftime(df$timestamp, df$timestamp[1], units = "secs"))
  }

  windows <- list(c(285, 298), c(240, 285))

  for (w in windows) {
    idx <- which(df$elapsed >= w[1] & df$elapsed <= w[2] & !is.na(df$up_midpoint))
    if (length(idx) == 0) {
      next
    }

    last_up <- tail(df$up_midpoint[idx], 1)
    if (last_up > 0.5) {
      return(1)
    }
    if (last_up < 0.5) {
      return(0)
    }
  }

  NA_integer_
}

# 确保数据里有 elapsed 列，表示“距离本轮开始过去了多少秒”。
# checkpoint 分析就是靠这列来判断：
# 第 30 秒时最近的一次概率是多少，
# 第 60 秒时最近的一次概率是多少，等等。
ensure_elapsed <- function(df) {
  if (!("elapsed" %in% names(df))) {
    df$elapsed <- as.numeric(difftime(df$timestamp, df$timestamp[1], units = "secs"))
  }
  df
}

# 把 timestamp 列尽量稳健地解析成 POSIXct。
# 有些 CSV 的时间字符串格式不统一，直接 as.POSIXct(x) 可能报错。
# 这里改成多种格式逐个尝试；解析失败的值记为 NA。
parse_timestamp_vector <- function(x) {
  if (inherits(x, "POSIXct")) {
    return(x)
  }

  x <- as.character(x)
  x <- trimws(x)
  result <- as.POSIXct(rep(NA_real_, length(x)), origin = "1970-01-01", tz = "UTC")
  missing_idx <- is.na(x) | !nzchar(trimws(x))
  if (all(missing_idx)) {
    return(result)
  }

  # 把 ISO8601 时区从 +00:00 转成 +0000，方便 R 用 %z 解析。
  normalized_x <- x
  normalized_x[!missing_idx] <- sub("([+-][0-9]{2}):([0-9]{2})$", "\\1\\2", normalized_x[!missing_idx])

  parse_one <- function(values, fmt) {
    suppressWarnings(
      tryCatch(
        as.POSIXct(values, format = fmt, tz = "UTC"),
        error = function(e) as.POSIXct(rep(NA_real_, length(values)), origin = "1970-01-01", tz = "UTC")
      )
    )
  }

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
    trial <- parse_one(values, fmt)
    fill <- is.na(parsed) & !is.na(trial)
    parsed[fill] <- trial[fill]
  }

  # 最后再给一次宽松尝试，兼容少数不规则但 R 能识别的字符串。
  if (any(is.na(parsed))) {
    loose <- suppressWarnings(
      tryCatch(
        as.POSIXct(values[is.na(parsed)], tz = "UTC"),
        error = function(e) as.POSIXct(rep(NA_real_, sum(is.na(parsed))), origin = "1970-01-01", tz = "UTC")
      )
    )
    parsed[is.na(parsed)] <- loose
  }

  result[!missing_idx] <- parsed
  result
}

# 在固定 checkpoint 上提取每轮的概率预测。
# 这里不是取整轮最后一个概率，而是回答：
# “如果我在第 30 秒 / 60 秒 / ... / 270 秒观察市场，
#  当时的概率预测效果怎么样？”
build_checkpoint_rows <- function(df, csv_path, checkpoints = DEFAULT_CHECKPOINTS) {
  df <- ensure_elapsed(df)
  label <- determine_label(df)
  round_start_hour_utc <- as.integer(format(df$timestamp[1], "%H", tz = "UTC"))
  if (is.na(label)) {
    return(data.frame(
      file = basename(csv_path),
      checkpoint_s = checkpoints,
      round_start_hour_utc = round_start_hour_utc,
      implied_prob_up = NA_real_,
      label = NA_integer_,
      brier_score = NA_real_,
      status = "missing_label",
      stringsAsFactors = FALSE
    ))
  }

  rows <- lapply(checkpoints, function(cp) {
    window <- df[df$elapsed <= cp & !is.na(df$up_midpoint), , drop = FALSE]
    if (nrow(window) == 0) {
      return(data.frame(
        file = basename(csv_path),
        checkpoint_s = cp,
        round_start_hour_utc = round_start_hour_utc,
        implied_prob_up = NA_real_,
        label = label,
        brier_score = NA_real_,
        status = "no_snapshot",
        stringsAsFactors = FALSE
      ))
    }

    p <- tail(window$up_midpoint, 1)
    data.frame(
      file = basename(csv_path),
      checkpoint_s = cp,
      round_start_hour_utc = round_start_hour_utc,
      implied_prob_up = p,
      label = label,
      brier_score = (p - label) ^ 2,
      status = "ok",
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, rows)
}

# 计算单个 CSV 文件在多个 checkpoint 上的 Brier Score。
# 这个函数会尽量“稳健”一些：
# - 文件读不出来，返回 read_error
# - 缺列，返回 missing_columns
# - 没有有效时间戳，返回 no_valid_rows
# - 没有 up_midpoint，返回 no_probability
# - 无法构造标签，返回 missing_label
#
# 为什么要这样写？
# 因为做数据科学时，原始数据常常并不干净。
# 比起脚本直接报错退出，先把每个文件的状态保留下来，
# 更利于你理解到底是哪一步出了问题。
score_one_file <- function(csv_path, checkpoints = DEFAULT_CHECKPOINTS) {
  df <- tryCatch(
    read.csv(csv_path, stringsAsFactors = FALSE),
    error = function(e) NULL
  )

  if (is.null(df)) {
    return(data.frame(
      file = basename(csv_path),
      checkpoint_s = checkpoints,
      round_start_hour_utc = NA_integer_,
      implied_prob_up = NA_real_,
      label = NA_integer_,
      brier_score = NA_real_,
      status = "read_error",
      stringsAsFactors = FALSE
    ))
  }

  required_cols <- c("timestamp", "up_midpoint")
  if (!all(required_cols %in% names(df))) {
    return(data.frame(
      file = basename(csv_path),
      checkpoint_s = checkpoints,
      round_start_hour_utc = NA_integer_,
      implied_prob_up = NA_real_,
      label = NA_integer_,
      brier_score = NA_real_,
      status = "missing_columns",
      stringsAsFactors = FALSE
    ))
  }

  df$timestamp <- parse_timestamp_vector(df$timestamp)
  df <- df[!is.na(df$timestamp), , drop = FALSE]
  df <- df[order(df$timestamp), , drop = FALSE]

  if (nrow(df) == 0) {
    return(data.frame(
      file = basename(csv_path),
      checkpoint_s = checkpoints,
      round_start_hour_utc = NA_integer_,
      implied_prob_up = NA_real_,
      label = NA_integer_,
      brier_score = NA_real_,
      status = "no_valid_rows",
      stringsAsFactors = FALSE
    ))
  }

  df$up_midpoint <- suppressWarnings(as.numeric(df$up_midpoint))
  df <- ensure_elapsed(df)
  if (!any(!is.na(df$up_midpoint))) {
    return(data.frame(
      file = basename(csv_path),
      checkpoint_s = checkpoints,
      implied_prob_up = NA_real_,
      label = NA_integer_,
      brier_score = NA_real_,
      status = "no_probability",
      stringsAsFactors = FALSE
    ))
  }

  build_checkpoint_rows(df, csv_path, checkpoints = checkpoints)
}

summarize_checkpoints <- function(results) {
  ok <- results[results$status == "ok" & !is.na(results$brier_score), , drop = FALSE]
  if (nrow(ok) == 0) {
    return(data.frame())
  }

  summary <- aggregate(
    brier_score ~ checkpoint_s,
    data = ok,
    FUN = function(x) c(mean = mean(x), count = length(x))
  )

  data.frame(
    checkpoint_s = summary$checkpoint_s,
    mean_brier_score = summary$brier_score[, "mean"],
    rounds_used = summary$brier_score[, "count"],
    stringsAsFactors = FALSE
  )
}

summarize_hours <- function(results) {
  ok <- results[results$status == "ok" & !is.na(results$brier_score) & !is.na(results$round_start_hour_utc), , drop = FALSE]
  if (nrow(ok) == 0) {
    return(data.frame())
  }

  summary <- aggregate(
    brier_score ~ round_start_hour_utc,
    data = ok,
    FUN = function(x) c(mean = mean(x), count = length(x))
  )

  out <- data.frame(
    utc_hour = summary$round_start_hour_utc,
    mean_brier_score = summary$brier_score[, "mean"],
    checkpoint_rows_used = summary$brier_score[, "count"],
    stringsAsFactors = FALSE
  )

  out[order(out$utc_hour), , drop = FALSE]
}

make_calibration_bins <- function(results, bin_size = 0.1) {
  ok <- results[
    results$status == "ok" &
      !is.na(results$implied_prob_up) &
      !is.na(results$label),
    ,
    drop = FALSE
  ]
  if (nrow(ok) == 0) {
    return(data.frame())
  }

  breaks <- seq(0, 1, by = bin_size)
  if (tail(breaks, 1) < 1) {
    breaks <- c(breaks, 1)
  }

  ok$probability_bin <- cut(
    ok$implied_prob_up,
    breaks = breaks,
    include.lowest = TRUE,
    right = TRUE
  )

  grouped <- split(ok, ok$probability_bin, drop = TRUE)
  rows <- lapply(names(grouped), function(bin_name) {
    part <- grouped[[bin_name]]
    data.frame(
      probability_bin = bin_name,
      count = nrow(part),
      mean_predicted_prob = mean(part$implied_prob_up),
      actual_up_rate = mean(part$label),
      calibration_gap = mean(part$implied_prob_up) - mean(part$label),
      mean_brier_score = mean(part$brier_score),
      stringsAsFactors = FALSE
    )
  })

  out <- do.call(rbind, rows)
  out$bin_midpoint <- seq_len(nrow(out))
  out
}

plot_checkpoint_brier <- function(summary_df, plot_path = NULL) {
  if (nrow(summary_df) == 0) {
    message("No valid checkpoint results to plot.")
    return(invisible(NULL))
  }

  if (!is.null(plot_path)) {
    dir.create(dirname(plot_path), recursive = TRUE, showWarnings = FALSE)
    png(plot_path, width = 900, height = 550)
    on.exit(dev.off(), add = TRUE)
  }

  mids <- barplot(
    height = summary_df$mean_brier_score,
    names.arg = paste0(summary_df$checkpoint_s, "s"),
    col = "steelblue",
    border = NA,
    main = "Mean Brier Score by Checkpoint (Recent Rounds)",
    xlab = "Checkpoint",
    ylab = "Mean Brier Score"
  )

  text(
    x = mids,
    y = summary_df$mean_brier_score,
    labels = format(round(summary_df$mean_brier_score, 4), nsmall = 4),
    pos = 3,
    cex = 0.9
  )

  invisible(NULL)
}

plot_hour_brier <- function(summary_df, plot_path = NULL) {
  if (nrow(summary_df) == 0) {
    message("No valid UTC hour results to plot.")
    return(invisible(NULL))
  }

  if (!is.null(plot_path)) {
    dir.create(dirname(plot_path), recursive = TRUE, showWarnings = FALSE)
    png(plot_path, width = 1000, height = 600)
    on.exit(dev.off(), add = TRUE)
  }

  labels <- sprintf("%02d", summary_df$utc_hour)
  mids <- barplot(
    height = summary_df$mean_brier_score,
    names.arg = labels,
    col = "darkorange",
    border = NA,
    main = "Mean Brier Score by UTC Hour",
    xlab = "UTC Hour",
    ylab = "Mean Brier Score",
    ylim = c(0, max(summary_df$mean_brier_score) * 1.15)
  )

  text(
    x = mids,
    y = summary_df$mean_brier_score,
    labels = format(round(summary_df$mean_brier_score, 4), nsmall = 4),
    pos = 3,
    cex = 0.9
  )

  invisible(NULL)
}

plot_reliability_diagram <- function(bins_df, plot_path = NULL) {
  if (nrow(bins_df) == 0) {
    message("No valid calibration bins to plot.")
    return(invisible(NULL))
  }

  if (!is.null(plot_path)) {
    dir.create(dirname(plot_path), recursive = TRUE, showWarnings = FALSE)
    png(plot_path, width = 900, height = 700)
    on.exit(dev.off(), add = TRUE)
  }

  old_par <- par(no.readonly = TRUE)
  on.exit(par(old_par), add = TRUE)
  layout(matrix(c(1, 2), nrow = 2), heights = c(3, 1))

  plot(
    bins_df$mean_predicted_prob,
    bins_df$actual_up_rate,
    type = "b",
    pch = 19,
    lwd = 2,
    col = "royalblue",
    xlim = c(0, 1),
    ylim = c(0, 1),
    xlab = "Mean predicted probability",
    ylab = "Actual UP rate",
    main = "Reliability Diagram"
  )
  abline(0, 1, col = "gray60", lty = 2, lwd = 2)
  text(
    x = bins_df$mean_predicted_prob,
    y = bins_df$actual_up_rate,
    labels = bins_df$count,
    pos = 3,
    cex = 0.8
  )

  mids <- barplot(
    height = bins_df$count,
    names.arg = bins_df$probability_bin,
    col = "gray70",
    border = NA,
    main = "Samples per probability bin",
    ylab = "Count",
    las = 2
  )
  invisible(mids)
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

# 主函数：把前面的步骤串起来。
# 逻辑顺序如下：
# 1. 读取参数
# 2. 找到数据目录
# 3. 列出所有 CSV
# 4. 按修改时间从新到旧排序
# 5. 取最近 n 个文件
# 6. 对每个文件计算单轮 Brier Score
# 7. 打印明细和平均值
main <- function(
  data_dir = NULL,
  n = NULL,
  checkpoints = DEFAULT_CHECKPOINTS,
  plot_path = NULL,
  hour_plot_path = NULL,
  reliability_plot_path = NULL,
  bins_csv_path = NULL
) {
  if (is.null(data_dir) || is.null(n)) {
    opts <- parse_args(commandArgs(trailingOnly = TRUE))
    if (is.null(data_dir)) {
      data_dir <- opts$data_dir
    }
    if (is.null(n)) {
      n <- opts$n
    }
    if (is.null(plot_path)) {
      plot_path <- opts$plot_path
    }
    if (is.null(hour_plot_path)) {
      hour_plot_path <- opts$hour_plot_path
    }
    if (is.null(reliability_plot_path)) {
      reliability_plot_path <- opts$reliability_plot_path
    }
    if (is.null(bins_csv_path)) {
      bins_csv_path <- opts$bins_csv_path
    }
  }

  data_dir <- resolve_data_dir(data_dir)

  csv_files <- list.files(data_dir, pattern = "\\.csv$", full.names = TRUE)
  if (length(csv_files) == 0) {
    stop(sprintf("No CSV files found in %s", data_dir))
  }

  info <- file.info(csv_files)
  info$file <- rownames(info)
  info <- info[order(info$mtime, decreasing = TRUE), , drop = FALSE]

  # 最近轮次 = 最近修改的文件。
  # 这不是唯一方案，但对你当前“快速看最近 10 个 round”的需求足够直接。
  n_use <- min(as.integer(n), nrow(info))
  recent_files <- info$file[seq_len(n_use)]

  results <- do.call(
    rbind,
    lapply(recent_files, score_one_file, checkpoints = checkpoints)
  )
  checkpoint_summary <- summarize_checkpoints(results)
  hour_summary <- summarize_hours(results)
  calibration_bins <- make_calibration_bins(results, bin_size = 0.1)

  cat("Data dir:", data_dir, "\n")
  cat("Files used:", n_use, "\n\n")
  print(results, row.names = FALSE)

  cat("\nMean Brier Score by checkpoint:\n")
  print(checkpoint_summary, row.names = FALSE)

  cat("\nMean Brier Score by UTC hour:\n")
  print(hour_summary, row.names = FALSE)

  cat("\nCalibration bins:\n")
  print(calibration_bins, row.names = FALSE)

  if (nrow(checkpoint_summary) > 0) {
    plot_checkpoint_brier(checkpoint_summary, plot_path = plot_path)
    if (!is.null(plot_path)) {
      cat("\nPlot saved to:", plot_path, "\n")
    }
  }

  if (nrow(hour_summary) > 0) {
    plot_hour_brier(hour_summary, plot_path = hour_plot_path)
    if (!is.null(hour_plot_path)) {
      cat("Hour plot saved to:", hour_plot_path, "\n")
    }
    cat("\nHighest mean Brier Score UTC hour:\n")
    print(hour_summary[which.max(hour_summary$mean_brier_score), , drop = FALSE], row.names = FALSE)
  }

  if (!is.null(bins_csv_path) && nrow(calibration_bins) > 0) {
    dir.create(dirname(bins_csv_path), recursive = TRUE, showWarnings = FALSE)
    write.csv(calibration_bins, bins_csv_path, row.names = FALSE)
    cat("Calibration bins saved to:", bins_csv_path, "\n")
  }

  if (nrow(calibration_bins) > 0) {
    plot_reliability_diagram(calibration_bins, plot_path = reliability_plot_path)
    if (!is.null(reliability_plot_path)) {
      cat("Reliability plot saved to:", reliability_plot_path, "\n")
    }
  }

  if (nrow(checkpoint_summary) == 0 && nrow(hour_summary) == 0) {
    cat("\nNo valid Brier Score summary could be computed.\n")
  }

  invisible(
    list(
      results = results,
      checkpoint_summary = checkpoint_summary,
      hour_summary = hour_summary,
      calibration_bins = calibration_bins
    )
  )
}

run_recent10_brier_interactive <- function() {
  data_dir <- prompt_path_value("数据目录", default = resolve_data_dir())
  n <- prompt_integer_value("最近要分析多少个轮次 n", default = 10L, min_value = 1L)
  artifacts_dir <- default_artifacts_dir()

  plot_path <- prompt_optional_path(
    "checkpoint Brier 图保存路径",
    default = file.path(artifacts_dir, sprintf("checkpoint_brier_%d.png", n))
  )
  hour_plot_path <- prompt_optional_path(
    "按 UTC 小时聚合的 Brier 图保存路径",
    default = file.path(artifacts_dir, sprintf("hour_brier_%d.png", n))
  )
  reliability_plot_path <- prompt_optional_path(
    "reliability diagram 保存路径",
    default = file.path(artifacts_dir, sprintf("reliability_%d.png", n))
  )
  bins_csv_path <- prompt_optional_path(
    "calibration bins CSV 保存路径",
    default = file.path(artifacts_dir, sprintf("calibration_bins_%d.csv", n))
  )

  main(
    data_dir = data_dir,
    n = n,
    plot_path = plot_path,
    hour_plot_path = hour_plot_path,
    reliability_plot_path = reliability_plot_path,
    bins_csv_path = bins_csv_path
  )
}

run_interactive <- run_recent10_brier_interactive

if (sys.nframe() == 0) {
  main()
} else {
  announce_interactive_ready("recent10_brier.R", "run_interactive", parent.frame())
}
