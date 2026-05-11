# ══════════════════════════════════════════════════════════════
#  run_backtest.R — 一键回测入口
# ══════════════════════════════════════════════════════════════
#
#  用法: 在项目根目录下运行
#    Rscript scripts/run_backtest.R
#    Rscript scripts/run_backtest.R --max 500
#    Rscript scripts/run_backtest.R --cores 4
#    Rscript scripts/run_backtest.R --config config/strategy.yaml
#    Rscript scripts/run_backtest.R --results results/job_001/backtest_results.csv --reports-dir reports/job_001
# ══════════════════════════════════════════════════════════════

# 设工作目录为项目根
if (interactive()) {
  # RStudio / R console: 手动设定
  # setwd("C:/Users/ZHAOKAI/Poly_backtest_Final")
} else {
  # Rscript: 自动设定为 scripts 上级
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
source("R/metrics/plots.R")
source("R/metrics/factor_buckets.R")

# ── 参数 ────────────────────────────────
args <- commandArgs(trailingOnly = TRUE)
max_rounds <- NULL
use_latest <- FALSE
config_path <- "config/strategy.yaml"
data_dir <- "data/raw"
results_path <- "results/backtest_results.csv"
reports_dir <- "reports"
use_cache <- TRUE
n_cores <- 1L

arg_i <- 1
while (arg_i <= length(args)) {
  arg <- args[arg_i]

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

  if (arg == "--results" && arg_i < length(args)) {
    results_path <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--reports-dir" && arg_i < length(args)) {
    reports_dir <- args[arg_i + 1]
    arg_i <- arg_i + 2
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

# ── 运行 ────────────────────────────────
cfg <- load_config(config_path)
results <- run_backtest(data_dir, cfg, max_rounds = max_rounds,
                        use_latest = use_latest, use_cache = use_cache,
                        n_cores = n_cores)

# ── 统计 ────────────────────────────────
perf <- calc_performance(results)
print_performance(perf)

# ── 保存结果 ────────────────────────────
dir.create(dirname(results_path), recursive = TRUE, showWarnings = FALSE)
write.csv(results, results_path, row.names = FALSE)

# ── 画图 ────────────────────────────────
dir.create(reports_dir, recursive = TRUE, showWarnings = FALSE)
plot_cum_pnl(results, file.path(reports_dir, "cum_pnl.png"))
plot_drawdown(results, file.path(reports_dir, "drawdown.png"))
plot_pnl_dist(results, file.path(reports_dir, "pnl_dist.png"))
plot_hourly_pnl(results, file.path(reports_dir, "hourly_pnl.png"))
plot_polarity_vs_pnl(results, file.path(reports_dir, "polarity_vs_pnl.png"))
plot_entry_timing(results, file.path(reports_dir, "entry_timing.png"))

generate_factor_bucket_reports(
  results_df = results,
  data_dir = data_dir,
  cfg = cfg,
  out_dir = reports_dir,
  use_cache = use_cache
)

message(sprintf("结果已保存到 %s", results_path))
message(sprintf("图表已保存到 %s", reports_dir))
