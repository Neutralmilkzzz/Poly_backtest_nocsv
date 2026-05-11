# ══════════════════════════════════════════════════════════════
#  run_timeout_analysis.R — timeout 专项诊断
# ══════════════════════════════════════════════════════════════
#
#  用法: 在项目根目录下运行
#    Rscript scripts/run_timeout_analysis.R
#    Rscript scripts/run_timeout_analysis.R --results results/backtest_results.csv
#    Rscript scripts/run_timeout_analysis.R --out-dir reports/timeout_analysis --top 20
# ══════════════════════════════════════════════════════════════

if (interactive()) {
  # setwd("C:/Users/ZHAOKAI/Poly_backtest_Final")
} else {
  script_dir <- dirname(commandArgs(trailingOnly = FALSE)[
    grep("--file=", commandArgs(trailingOnly = FALSE))])
  if (length(script_dir) > 0) setwd(file.path(script_dir, ".."))
}

args <- commandArgs(trailingOnly = TRUE)
results_path <- "results/backtest_results.csv"
out_dir <- "reports/timeout_analysis"
top_n <- 20

arg_i <- 1
while (arg_i <= length(args)) {
  arg <- args[arg_i]

  if (arg == "--results" && arg_i < length(args)) {
    results_path <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--out-dir" && arg_i < length(args)) {
    out_dir <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--top" && arg_i < length(args)) {
    top_n <- as.integer(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }

  arg_i <- arg_i + 1
}

if (!file.exists(results_path)) {
  stop(sprintf("结果文件不存在: %s", results_path))
}

results <- read.csv(results_path, stringsAsFactors = FALSE)

if (!"exit_type" %in% names(results)) {
  stop("结果文件缺少 exit_type 列，无法分析 timeout")
}

timeouts <- results[results$traded & results$exit_type == "timeout", ]

if (nrow(timeouts) == 0) {
  message("没有 timeout 交易，无需分析")
  quit(save = "no", status = 0)
}

dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

numeric_cols <- c("entry_price", "entry_trigger_price", "exit_price", "exit_trigger_price",
                  "sell_order_price", "sell_post_elapsed", "qty", "pnl",
                  "elapsed_entry", "elapsed_exit")
for (col in numeric_cols) {
  if (col %in% names(timeouts)) {
    timeouts[[col]] <- suppressWarnings(as.numeric(timeouts[[col]]))
  }
}

timeouts$hold_seconds <- timeouts$elapsed_exit - timeouts$elapsed_entry
timeouts$slippage_vs_target <- timeouts$exit_price - timeouts$sell_order_price
timeouts$drawdown_from_entry <- timeouts$exit_price - timeouts$entry_price
timeouts$entry_hour <- substr(timeouts$entry_time, 12, 13)
timeouts$entry_minute_bucket <- floor(timeouts$elapsed_entry / 10) * 10

timeouts$loss_bucket <- cut(
  timeouts$pnl,
  breaks = c(-Inf, -20, -10, -5, 0),
  labels = c("<=-20", "-20~-10", "-10~-5", "-5~0"),
  right = FALSE
)

summary_side <- aggregate(
  pnl ~ side,
  data = timeouts,
  FUN = function(x) c(count = length(x), total_pnl = sum(x), avg_pnl = mean(x), median_pnl = median(x))
)

summary_hour <- aggregate(
  pnl ~ entry_hour,
  data = timeouts,
  FUN = function(x) c(count = length(x), total_pnl = sum(x), avg_pnl = mean(x))
)

summary_bucket <- as.data.frame(table(timeouts$loss_bucket), stringsAsFactors = FALSE)
names(summary_bucket) <- c("loss_bucket", "count")

worst_timeouts <- timeouts[order(timeouts$pnl), c(
  "round_id", "side", "entry_time", "entry_price", "entry_trigger_price",
  "exit_time", "exit_price", "exit_trigger_price", "pnl",
  "elapsed_entry", "elapsed_exit", "hold_seconds", "sell_post_elapsed"
)]
worst_timeouts <- head(worst_timeouts, top_n)

write.csv(timeouts, file.path(out_dir, "timeout_trades.csv"), row.names = FALSE)
write.csv(summary_side, file.path(out_dir, "timeout_by_side.csv"), row.names = FALSE)
write.csv(summary_hour, file.path(out_dir, "timeout_by_hour.csv"), row.names = FALSE)
write.csv(summary_bucket, file.path(out_dir, "timeout_loss_buckets.csv"), row.names = FALSE)
write.csv(worst_timeouts, file.path(out_dir, "worst_timeouts.csv"), row.names = FALSE)

cat("═══════════════════════════════════════\n")
cat("        Timeout 专项诊断报告\n")
cat("═══════════════════════════════════════\n")
cat(sprintf("  Timeout 笔数:        %d\n", nrow(timeouts)))
cat(sprintf("  Timeout 总 PnL:      %.4f\n", sum(timeouts$pnl, na.rm = TRUE)))
cat(sprintf("  Timeout 平均 PnL:    %.4f\n", mean(timeouts$pnl, na.rm = TRUE)))
cat(sprintf("  Timeout 中位 PnL:    %.4f\n", median(timeouts$pnl, na.rm = TRUE)))
cat(sprintf("  最差一笔:           %.4f\n", min(timeouts$pnl, na.rm = TRUE)))
cat(sprintf("  最好一笔:           %.4f\n", max(timeouts$pnl, na.rm = TRUE)))
cat(sprintf("  平均持仓秒数:        %.2f\n", mean(timeouts$hold_seconds, na.rm = TRUE)))
cat("───────────────────────────────────────\n")
cat("  按 side 统计:\n")
for (i in seq_len(nrow(summary_side))) {
  cell <- summary_side$pnl[[i]]
  cat(sprintf("    %-6s count=%3d total=%8.4f avg=%7.4f median=%7.4f\n",
              summary_side$side[i], cell[1], cell[2], cell[3], cell[4]))
}
cat("───────────────────────────────────────\n")
cat("  亏损分桶:\n")
for (i in seq_len(nrow(summary_bucket))) {
  cat(sprintf("    %-8s %d\n", summary_bucket$loss_bucket[i], summary_bucket$count[i]))
}
cat("───────────────────────────────────────\n")
cat(sprintf("  明细已输出到: %s\n", out_dir))
cat("  重点文件:\n")
cat(sprintf("    %s\n", file.path(out_dir, "timeout_trades.csv")))
cat(sprintf("    %s\n", file.path(out_dir, "timeout_by_side.csv")))
cat(sprintf("    %s\n", file.path(out_dir, "timeout_by_hour.csv")))
cat(sprintf("    %s\n", file.path(out_dir, "timeout_loss_buckets.csv")))
cat(sprintf("    %s\n", file.path(out_dir, "worst_timeouts.csv")))
cat("═══════════════════════════════════════\n")