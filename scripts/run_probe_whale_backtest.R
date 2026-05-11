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
source("R/engine/orchestrator.R")
source("R/metrics/performance.R")

# ── 命令行参数解析 ────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
config_path <- "strategies/probe_whale.yaml"
data_dir    <- "data/raw"
out_dir     <- "results/probe_whale"
max_rounds  <- NULL
use_latest  <- FALSE
use_cache   <- TRUE

arg_i <- 1
while (arg_i <= length(args)) {
  arg <- args[arg_i]

  if (arg == "--config" && arg_i < length(args)) {
    config_path <- args[arg_i + 1]
    arg_i <- arg_i + 2; next
  }
  if (arg == "--data-dir" && arg_i < length(args)) {
    data_dir <- args[arg_i + 1]
    arg_i <- arg_i + 2; next
  }
  if (arg == "--out-dir" && arg_i < length(args)) {
    out_dir <- args[arg_i + 1]
    arg_i <- arg_i + 2; next
  }
  if (arg == "--max" && arg_i < length(args)) {
    max_rounds <- as.integer(args[arg_i + 1])
    arg_i <- arg_i + 2; next
  }
  if (arg == "--latest") {
    use_latest <- TRUE
    arg_i <- arg_i + 1; next
  }
  if (arg == "--no-cache") {
    use_cache <- FALSE
    arg_i <- arg_i + 1; next
  }
  arg_i <- arg_i + 1
}

# ── 加载配置 ──────────────────────────────────
cfg <- load_config(config_path)

# ── 预加载数据 ────────────────────────────────
message("预加载轮次数据...")
preloaded <- prepare_rounds_data(
  data_dir    = data_dir,
  max_rounds  = max_rounds,
  use_latest  = use_latest,
  progress    = 100,
  use_cache   = use_cache
)

# ── 运行探针-跟庄回测 ────────────────────────
results_df <- run_probe_whale_backtest(preloaded, cfg, progress = 100)

# ── 保存结果 ──────────────────────────────────
dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)
results_path <- file.path(out_dir, "probe_whale_results.csv")
write.csv(results_df, results_path, row.names = FALSE)

# ── 分层统计 ──────────────────────────────────
message("\n═══ 分层统计 ═══")

# 1) 按 regime 统计
regime_summary <- aggregate(
  cbind(grid_pnl, tail_pnl, whale_cheap_pnl, whale_exp_pnl, total_real_pnl) ~ regime,
  data = results_df,
  FUN = sum
)
regime_counts <- as.data.frame(table(results_df$regime), stringsAsFactors = FALSE)
names(regime_counts) <- c("regime", "n_rounds")
regime_summary <- merge(regime_counts, regime_summary, by = "regime")
write.csv(regime_summary, file.path(out_dir, "regime_summary.csv"), row.names = FALSE)
message("\n按 Regime 分组:")
print(regime_summary)

# 2) 跟庄策略明细
whale_rounds <- results_df[results_df$regime == "WHALE_ACTIVE", ]
if (nrow(whale_rounds) > 0) {
  # 便宜端
  cheap_traded <- whale_rounds[whale_rounds$whale_cheap_traded, ]
  message(sprintf("\n便宜端: %d 笔交易, 总PnL=%.4f",
                  nrow(cheap_traded), sum(cheap_traded$whale_cheap_pnl)))
  if (nrow(cheap_traded) > 0) {
    cheap_exits <- table(cheap_traded$whale_cheap_exit_type)
    message(sprintf("  退出分布: %s",
                    paste(paste(names(cheap_exits), as.integer(cheap_exits), sep="="), collapse=", ")))
  }

  # 贵端
  exp_traded <- whale_rounds[whale_rounds$whale_exp_traded, ]
  message(sprintf("\n贵端: %d 笔交易, 总PnL=%.4f",
                  nrow(exp_traded), sum(exp_traded$whale_exp_pnl)))
  if (nrow(exp_traded) > 0) {
    exp_exits <- table(exp_traded$whale_exp_exit_type)
    message(sprintf("  退出分布: %s",
                    paste(paste(names(exp_exits), as.integer(exp_exits), sep="="), collapse=", ")))
  }
} else {
  message("\n未触发 WHALE_ACTIVE 模式 — 庄家未被检测到")
}

# 3) 探针基线表现 (仅 NORMAL 模式下的实盘)
normal_rounds <- results_df[results_df$regime == "NORMAL", ]
if (nrow(normal_rounds) > 0) {
  grid_real <- normal_rounds[normal_rounds$grid_traded & !normal_rounds$grid_is_paper, ]
  tail_real <- normal_rounds[normal_rounds$tail_traded & !normal_rounds$tail_is_paper, ]
  message(sprintf("\n探针基线 (NORMAL 模式):\n  网格: %d 笔, 总PnL=%.4f, 胜率=%.1f%%\n  尾盘: %d 笔, 总PnL=%.4f, 胜率=%.1f%%",
                  nrow(grid_real), sum(grid_real$grid_pnl),
                  if (nrow(grid_real) > 0) mean(grid_real$grid_pnl > 0) * 100 else 0,
                  nrow(tail_real), sum(tail_real$tail_pnl),
                  if (nrow(tail_real) > 0) mean(tail_real$tail_pnl > 0) * 100 else 0))
}

message(sprintf("\n结果文件: %s", results_path))
message(sprintf("Regime 汇总: %s", file.path(out_dir, "regime_summary.csv")))
