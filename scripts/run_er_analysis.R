if (interactive()) {
  # setwd("C:/Users/ZHAOKAI/Poly_backtest_Final")
} else {
  args_all <- commandArgs(trailingOnly = FALSE)
  file_args <- args_all[grep("^--file=", args_all)]
  if (length(file_args) > 0) {
    script_path <- sub("^--file=", "", file_args[1])
    script_dir <- dirname(script_path)
    setwd(file.path(script_dir, ".."))
  }
}

source("R/engine/runner.R")
source("R/metrics/performance.R")

calc_efficiency_ratio <- function(series) {
  series <- series[!is.na(series)]
  if (length(series) < 2) return(NA_real_)
  path_length <- sum(abs(diff(series)))
  if (is.na(path_length) || path_length <= 0) return(NA_real_)
  displacement <- abs(tail(series, 1) - series[1])
  displacement / path_length
}

compute_opening_factor <- function(df, side, window_seconds) {
  midpoint_col <- if (identical(side, "down")) "down_midpoint" else "up_midpoint"
  window_df <- df[df$elapsed >= 0 & df$elapsed <= window_seconds, ]
  if (nrow(window_df) < 2) return(NA_real_)
  calc_efficiency_ratio(window_df[[midpoint_col]])
}

parse_breaks <- function(x) {
  parts <- trimws(strsplit(x, ",")[[1]])
  vals <- suppressWarnings(as.numeric(parts))
  if (length(vals) < 2 || any(is.na(vals))) {
    stop("--breaks 必须是逗号分隔的数值列表")
  }
  vals <- unique(vals)
  vals[order(vals)]
}

args <- commandArgs(trailingOnly = TRUE)
config_path <- "config/strategy.yaml"
data_dir <- "data/raw"
out_dir <- "reports/er_analysis"
summary_out <- file.path(out_dir, "er_bucket_summary.csv")
max_rounds <- NULL
use_latest <- FALSE
use_cache <- TRUE
n_cores <- 1L
window_seconds <- NULL
breaks_arg <- "0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.01"

arg_i <- 1
while (arg_i <= length(args)) {
  arg <- args[arg_i]

  if (arg == "--config" && arg_i < length(args)) {
    config_path <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }
  if (arg == "--data-dir" && arg_i < length(args)) {
    data_dir <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }
  if (arg == "--out-dir" && arg_i < length(args)) {
    out_dir <- args[arg_i + 1]
    summary_out <- file.path(out_dir, "er_bucket_summary.csv")
    arg_i <- arg_i + 2
    next
  }
  if (arg == "--summary-out" && arg_i < length(args)) {
    summary_out <- args[arg_i + 1]
    out_dir <- dirname(summary_out)
    arg_i <- arg_i + 2
    next
  }
  if (arg == "--window" && arg_i < length(args)) {
    window_seconds <- as.numeric(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }
  if (arg == "--breaks" && arg_i < length(args)) {
    breaks_arg <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }
  if (arg == "--max" && arg_i < length(args)) {
    max_rounds <- as.integer(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }
  if (arg == "--latest") {
    use_latest <- TRUE
    arg_i <- arg_i + 1
    next
  }
  if (arg == "--no-cache") {
    use_cache <- FALSE
    arg_i <- arg_i + 1
    next
  }
  if (arg == "--cores" && arg_i < length(args)) {
    n_cores <- as.integer(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }
  arg_i <- arg_i + 1
}

cfg <- load_config(config_path)
if (is.null(window_seconds)) {
  window_seconds <- cfg$er_window_seconds
}
if (is.na(window_seconds) || window_seconds <= 1) {
  stop("--window / er_window_seconds 必须大于 1")
}

bucket_breaks <- parse_breaks(breaks_arg)

dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
message(sprintf("ER 分析开始: window=%.0fs", window_seconds))

preloaded <- prepare_rounds_data(
  data_dir = data_dir,
  max_rounds = max_rounds,
  use_latest = use_latest,
  progress = 100,
  use_cache = use_cache
)
results_df <- run_backtest_preloaded(
  preloaded,
  cfg,
  progress = 0,
  progress_label = "ER基线回测",
  n_cores = n_cores
)

trades <- results_df[results_df$traded, ]
if (nrow(trades) == 0) {
  write.csv(data.frame(), summary_out, row.names = FALSE)
  message("没有交易数据，ER 分析结束")
  quit(save = "no", status = 0)
}

row_index_by_round <- setNames(seq_along(preloaded$round_ids), preloaded$round_ids)
trade_rows <- vector("list", nrow(trades))
for (i in seq_len(nrow(trades))) {
  round_id <- trades$round_id[i]
  idx <- row_index_by_round[[round_id]]
  df <- preloaded$all_data[[idx]]
  er_value <- compute_opening_factor(df, side = trades$side[i], window_seconds = window_seconds)
  trade_rows[[i]] <- data.frame(
    round_id = round_id,
    side = trades$side[i],
    er_value = er_value,
    pnl = trades$pnl[i],
    exit_type = trades$exit_type[i],
    won = trades$pnl[i] > 0,
    stringsAsFactors = FALSE
  )
}

trade_factor_df <- do.call(rbind, trade_rows)
trade_factor_df <- trade_factor_df[!is.na(trade_factor_df$er_value), ]
if (nrow(trade_factor_df) == 0) {
  write.csv(data.frame(), summary_out, row.names = FALSE)
  message("没有可计算 ER 的交易数据")
  quit(save = "no", status = 0)
}

trade_factor_df$er_bucket <- cut(
  trade_factor_df$er_value,
  breaks = bucket_breaks,
  include.lowest = TRUE,
  right = FALSE
)

overall_win_rate <- mean(trade_factor_df$won)
bucket_levels <- levels(trade_factor_df$er_bucket)
bucket_rows <- lapply(bucket_levels, function(bucket) {
  bucket_df <- trade_factor_df[trade_factor_df$er_bucket == bucket, ]
  if (nrow(bucket_df) == 0) {
    return(data.frame(
      er_bucket = bucket,
      n_trades = 0,
      win_rate_pct = NA_real_,
      total_pnl = NA_real_,
      avg_pnl = NA_real_,
      median_pnl = NA_real_,
      avg_er = NA_real_,
      delta_win_rate_pct = NA_real_,
      p_value_win_rate = NA_real_,
      significant_5pct = FALSE,
      stringsAsFactors = FALSE
    ))
  }

  wins <- sum(bucket_df$won)
  p_val <- tryCatch(
    prop.test(
      x = c(wins, sum(trade_factor_df$won) - wins),
      n = c(nrow(bucket_df), nrow(trade_factor_df) - nrow(bucket_df))
    )$p.value,
    error = function(e) NA_real_
  )

  data.frame(
    er_bucket = bucket,
    n_trades = nrow(bucket_df),
    win_rate_pct = mean(bucket_df$won) * 100,
    total_pnl = sum(bucket_df$pnl),
    avg_pnl = mean(bucket_df$pnl),
    median_pnl = median(bucket_df$pnl),
    avg_er = mean(bucket_df$er_value),
    delta_win_rate_pct = (mean(bucket_df$won) - overall_win_rate) * 100,
    p_value_win_rate = p_val,
    significant_5pct = !is.na(p_val) && p_val < 0.05,
    stringsAsFactors = FALSE
  )
})

summary_df <- do.call(rbind, bucket_rows)
write.csv(trade_factor_df, file.path(out_dir, "er_trade_level.csv"), row.names = FALSE)
write.csv(summary_df, summary_out, row.names = FALSE)

cat("═══════════════════════════════════════\n")
cat("          ER 分桶分析\n")
cat("═══════════════════════════════════════\n")
cat(sprintf("  窗口秒数:         %.0f\n", window_seconds))
cat(sprintf("  交易笔数:         %d\n", nrow(trade_factor_df)))
cat(sprintf("  总体胜率:         %.1f%%\n", overall_win_rate * 100))
cat("───────────────────────────────────────\n")
for (i in seq_len(nrow(summary_df))) {
  row <- summary_df[i, ]
  cat(sprintf("  %-12s n=%3d win=%6.1f%% pnl=%9.4f p=%s\n",
              as.character(row$er_bucket),
              row$n_trades,
              ifelse(is.na(row$win_rate_pct), 0, row$win_rate_pct),
              ifelse(is.na(row$total_pnl), 0, row$total_pnl),
              ifelse(is.na(row$p_value_win_rate), "NA", sprintf("%.4f", row$p_value_win_rate))))
}
cat("───────────────────────────────────────\n")
cat(sprintf("  结果已输出到: %s\n", out_dir))
cat("═══════════════════════════════════════\n")
