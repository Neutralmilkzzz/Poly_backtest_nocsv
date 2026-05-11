# ══════════════════════════════════════════════════════════════
#  run_time_stop_analysis.R — 时间止损专项分析
# ══════════════════════════════════════════════════════════════
#
#  用法: 在项目根目录下运行
#    Rscript scripts/run_time_stop_analysis.R
#    Rscript scripts/run_time_stop_analysis.R --max 500
#    Rscript scripts/run_time_stop_analysis.R --time-stops 15,20,30,45,60,90,120
#    Rscript scripts/run_time_stop_analysis.R --out-dir reports/time_stop_analysis
# ══════════════════════════════════════════════════════════════

if (interactive()) {
  # setwd("C:/Users/ZHAOKAI/Poly_backtest_Final")
} else {
  script_dir <- dirname(commandArgs(trailingOnly = FALSE)[
    grep("--file=", commandArgs(trailingOnly = FALSE))])
  if (length(script_dir) > 0) setwd(file.path(script_dir, ".."))
}

source("R/io/config_loader.R")
source("scripts/drawdown_analysis.R")

args <- commandArgs(trailingOnly = TRUE)
config_path <- "config/strategy.yaml"
data_dir <- "data/raw"
out_dir <- "reports/time_stop_analysis"
max_rounds <- NULL
time_stops <- c(15, 20, 30, 45, 60, 75, 90, 120)
progress_every <- 50

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
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--max" && arg_i < length(args)) {
    max_rounds <- as.integer(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--time-stops" && arg_i < length(args)) {
    time_stops <- as.numeric(strsplit(args[arg_i + 1], ",")[[1]])
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--progress-every" && arg_i < length(args)) {
    progress_every <- as.integer(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }

  arg_i <- arg_i + 1
}

if (is.na(progress_every) || progress_every <= 0) {
  progress_every <- 50
}

progress_tick <- function(stage, current, total, detail = NULL) {
  if (total <= 0) return(invisible(NULL))
  if (current == 1 || current == total || current %% progress_every == 0) {
    pct <- current / total * 100
    suffix <- if (!is.null(detail) && nzchar(detail)) paste0(" | ", detail) else ""
    message(sprintf("  [%s] %d / %d (%.1f%%)%s", stage, current, total, pct, suffix))
  }
}

cfg <- load_config(config_path)
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

rounds <- list_rounds(data_dir)
if (!is.null(max_rounds)) rounds <- head(rounds, max_rounds)
n <- nrow(rounds)
total_steps <- n * (length(time_stops) + 2)
message(sprintf("任务规模: %d 轮 x (预加载 + 基线 + %d 个时间止损点) = 约 %d 个处理步",
                n, length(time_stops), total_steps))
message(sprintf("预加载 %d 个轮次数据...", n))

all_data <- vector("list", n)
for (i in seq_len(n)) {
  progress_tick("预加载", i, n)
  df <- read_round_csv(rounds$path[i])
  all_data[[i]] <- clean_round(df, round_start = rounds$round_time[i])
}
message("数据加载完成，开始基线统计...")

baseline_results <- list()
for (i in seq_len(n)) {
  progress_tick("基线", i, n)
  res <- sim_one_round(
    all_data[[i]],
    entry_price = cfg$entry_price,
    profit_price = cfg$profit_price,
    stop_loss = NULL,
    entry_timeout = cfg$entry_timeout,
    round_duration = cfg$round_duration,
    settle_cutoff = cfg$settle_wait,
    cooldown = cfg$cooldown,
    time_stop_after_entry = NULL,
    entry_fill_model = cfg$backtest_fill_model,
    sell_fill_model = cfg$sell_fill_model,
    sell_timeout_remaining = cfg$sell_timeout_remaining,
    sell_timeout = cfg$sell_timeout
  )
  if (!is.null(res)) baseline_results[[length(baseline_results) + 1]] <- res
}

baseline <- if (length(baseline_results) > 0) {
  do.call(rbind, lapply(baseline_results, as.data.frame, stringsAsFactors = FALSE))
} else {
  data.frame()
}

profit_hold_stats <- analyze_profit_hold_times(baseline)
profit_trades <- attr(profit_hold_stats, "profit_trades")
profit_hold_summary <- attr(profit_hold_stats, "summary")

message("开始扫描时间止损点...")
summary_rows <- list()
for (k in seq_along(time_stops)) {
  ts_value <- time_stops[k]
  message(sprintf("  扫描时间止损点 %ss (%d / %d)", ts_value, k, length(time_stops)))
  results <- list()
  for (i in seq_len(n)) {
    progress_tick("时间止损", i, n, detail = paste0("time_stop=", ts_value, "s"))
    res <- sim_one_round(
      all_data[[i]],
      entry_price = cfg$entry_price,
      profit_price = cfg$profit_price,
      stop_loss = NULL,
      entry_timeout = cfg$entry_timeout,
      round_duration = cfg$round_duration,
      settle_cutoff = cfg$settle_wait,
      cooldown = cfg$cooldown,
      time_stop_after_entry = ts_value,
      entry_fill_model = cfg$backtest_fill_model,
      sell_fill_model = cfg$sell_fill_model,
      sell_timeout_remaining = cfg$sell_timeout_remaining,
      sell_timeout = cfg$sell_timeout
    )
    if (!is.null(res)) results[[length(results) + 1]] <- res
  }

  if (length(results) == 0) next

  pnls <- sapply(results, `[[`, "pnl")
  exit_types <- sapply(results, `[[`, "exit_type")
  hold_seconds <- sapply(results, `[[`, "hold_seconds")

  summary_rows[[length(summary_rows) + 1]] <- data.frame(
    time_stop = ts_value,
    trades = length(pnls),
    profit = sum(exit_types == "profit"),
    time_stop_exit = sum(exit_types == "time_stop"),
    timeout = sum(exit_types == "timeout"),
    win_rate = mean(pnls > 0) * 100,
    total_pnl = sum(pnls),
    avg_pnl = mean(pnls),
    avg_hold = mean(hold_seconds),
    avg_win = if (sum(pnls > 0) > 0) mean(pnls[pnls > 0]) else 0,
    avg_loss = if (sum(pnls < 0) > 0) mean(pnls[pnls < 0]) else 0,
    stringsAsFactors = FALSE
  )

  message(sprintf("  时间止损点 %ss 完成: %d / %d", ts_value, k, length(time_stops)))
}

time_stop_scan <- if (length(summary_rows) > 0) {
  do.call(rbind, summary_rows)
} else {
  data.frame()
}

write.csv(baseline, file.path(out_dir, "baseline_trades.csv"), row.names = FALSE)
write.csv(profit_hold_stats, file.path(out_dir, "profit_hold_time_buckets.csv"), row.names = FALSE)
write.csv(profit_trades, file.path(out_dir, "profit_trades_with_hold_time.csv"), row.names = FALSE)
write.csv(time_stop_scan, file.path(out_dir, "time_stop_scan.csv"), row.names = FALSE)

cat("═══════════════════════════════════════\n")
cat("      盈利耗时与时间止损分析\n")
cat("═══════════════════════════════════════\n")
cat("  说明: --time-stops 表示买入后最多持有多少秒\n")
cat("       不是距离 round 结束还剩多少秒\n")
if (!is.null(profit_hold_summary)) {
  cat(sprintf("  盈利单数量:        %d\n", profit_hold_summary$n))
  cat(sprintf("  平均止盈耗时:      %.2f 秒\n", profit_hold_summary$avg_hold))
  cat(sprintf("  中位止盈耗时:      %.2f 秒\n", profit_hold_summary$median_hold))
  cat(sprintf("  P25 / P50 / P75:   %.2f / %.2f / %.2f 秒\n",
              profit_hold_summary$p25,
              profit_hold_summary$p50,
              profit_hold_summary$p75))
  cat(sprintf("  P90:              %.2f 秒\n", profit_hold_summary$p90))
}
cat("───────────────────────────────────────\n")
cat("  盈利耗时分桶:\n")
if (nrow(profit_hold_stats) > 0) {
  for (i in seq_len(nrow(profit_hold_stats))) {
    cat(sprintf("    %-12s count=%3d avg=%7.4f total=%8.4f\n",
                as.character(profit_hold_stats$hold_bucket[i]),
                profit_hold_stats$count[i],
                profit_hold_stats$avg_pnl[i],
                profit_hold_stats$total_pnl[i]))
  }
}
cat("───────────────────────────────────────\n")
print_time_stop_report(time_stop_scan)
cat(sprintf("  结果已输出到: %s\n", out_dir))
cat(sprintf("    %s\n", file.path(out_dir, "profit_hold_time_buckets.csv")))
cat(sprintf("    %s\n", file.path(out_dir, "profit_trades_with_hold_time.csv")))
cat(sprintf("    %s\n", file.path(out_dir, "time_stop_scan.csv")))
cat("═══════════════════════════════════════\n")