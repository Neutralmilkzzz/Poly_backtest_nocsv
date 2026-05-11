#!/usr/bin/env Rscript
# ══════════════════════════════════════════════════════════════
#  run_btc_guard_analysis.R — BTC Diff 撤单阈值扫参分析
# ══════════════════════════════════════════════════════════════

if (interactive()) {
  # setwd("C:/Users/ZHAOKAI/Poly_backtest_Final")
} else {
  script_dir <- dirname(commandArgs(trailingOnly = FALSE)[
    grep("--file=", commandArgs(trailingOnly = FALSE))])
  if (length(script_dir) > 0 && script_dir[1] != "") {
     setwd(file.path(script_dir[1], ".."))
  }
}

args <- commandArgs(trailingOnly = TRUE)
data_dir <- "data/raw"
config_path <- "config/strategy.yaml"
max_val <- NULL
out_dir <- "reports/btc_guard_analysis"
thresholds_str <- "40,50,60,80,100,999"

arg_i <- 1
while (arg_i <= length(args)) {
  arg <- args[arg_i]
  if (arg == "--data-dir" && arg_i < length(args)) { data_dir <- args[arg_i + 1]; arg_i <- arg_i + 2; next }
  if (arg == "--config" && arg_i < length(args)) { config_path <- args[arg_i + 1]; arg_i <- arg_i + 2; next }
  if (arg == "--max" && arg_i < length(args)) { max_val <- as.integer(args[arg_i + 1]); arg_i <- arg_i + 2; next }
  if (arg == "--out-dir" && arg_i < length(args)) { out_dir <- args[arg_i + 1]; arg_i <- arg_i + 2; next }
  if (arg == "--thresholds" && arg_i < length(args)) { thresholds_str <- args[arg_i + 1]; arg_i <- arg_i + 2; next }
  # fallback
  arg_i <- arg_i + 1
}

source("R/engine/runner.R")

if (!dir.exists(out_dir)) {
  dir.create(out_dir, recursive = TRUE)
}

base_cfg <- load_config(config_path)

# 强制开启 guard 用于测试
base_cfg$btc_guard_enabled <- TRUE

thresholds <- as.numeric(strsplit(thresholds_str, ",")[[1]])

results_summary <- data.frame()

message("==========================================================")
message(" 开始执行 BTC Guard 模块参数扫描分析")
message(sprintf(" 数据目录: %s", data_dir))
message(sprintf(" 需要扫描的阈值: %s", thresholds_str))
message("==========================================================\n")

for (i in seq_along(thresholds)) {
  th <- thresholds[i]
  message(sprintf("\n>>> 正在测试阈值: btc_diff_max = %.1f (%d/%d)", th, i, length(thresholds)))
  
  cfg <- base_cfg
  cfg$btc_diff_max <- th
  
  res_df <- run_backtest(data_dir = data_dir, cfg = cfg, max_rounds = max_val, progress = 500)
  
  total_pnl <- sum(res_df$pnl, na.rm = TRUE)
  trade_count <- sum(res_df$traded, na.rm = TRUE)
  win_rate <- if (trade_count > 0) sum(res_df$pnl > 0, na.rm = TRUE) / trade_count else 0
  
  # 统计被拦下来的单子数量
  guard_blocks <- sum(!is.na(res_df$skip_reason) & res_df$skip_reason == "btc_guard")
  
  summary_row <- data.frame(
    btc_diff_max = th,
    trades = trade_count,
    blocked_by_guard = guard_blocks,
    win_rate = round(win_rate * 100, 2),
    total_pnl = total_pnl
  )
  
  results_summary <- rbind(results_summary, summary_row)
}

message("\n=== BTC Guard 扫参对比结果 ===")
print(results_summary)

out_csv <- file.path(out_dir, "btc_guard_scan_summary.csv")
write.csv(results_summary, out_csv, row.names = FALSE)
message("\n✅ 扫参结果已保存至: ", out_csv)
