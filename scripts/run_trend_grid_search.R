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

args <- commandArgs(trailingOnly = TRUE)
config_path <- "config/strategy.yaml"
data_dir <- "data/raw"
summary_out <- "results/trend_grid_search_summary.csv"
max_rounds <- NULL
use_latest <- FALSE
use_cache <- TRUE
n_cores <- 1L
entry_values_arg <- "0.60,0.65,0.70"
profit_values_arg <- "0.80,0.85"
stop_values_arg <- "0.40,0.50"
trend_side <- "both"

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

  if (arg == "--summary-out" && arg_i < length(args)) {
    summary_out <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--entry-values" && arg_i < length(args)) {
    entry_values_arg <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--profit-values" && arg_i < length(args)) {
    profit_values_arg <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--stop-values" && arg_i < length(args)) {
    stop_values_arg <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--trend-side" && arg_i < length(args)) {
    trend_side <- args[arg_i + 1]
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

parse_numeric_csv <- function(x, label) {
  parts <- trimws(strsplit(x, ",")[[1]])
  parts <- parts[nzchar(parts)]
  values <- suppressWarnings(as.numeric(parts))
  if (length(values) == 0 || any(is.na(values))) {
    stop(sprintf("%s 必须是逗号分隔的数值列表", label))
  }
  unique(values)
}

entry_values <- parse_numeric_csv(entry_values_arg, "--entry-values")
profit_values <- parse_numeric_csv(profit_values_arg, "--profit-values")
stop_values <- parse_numeric_csv(stop_values_arg, "--stop-values")

grid <- expand.grid(
  trend_entry_price = entry_values,
  trend_profit_price = profit_values,
  trend_stop_price = stop_values,
  stringsAsFactors = FALSE
)
grid <- grid[grid$trend_profit_price > grid$trend_entry_price &
               grid$trend_stop_price < grid$trend_entry_price, ]

if (nrow(grid) == 0) {
  stop("没有合法参数组合：必须满足 profit > entry > stop")
}

cfg <- load_config(config_path)
cfg$strategy_mode <- "trend_breakout"
cfg$trend_side <- trend_side

message(sprintf("任务规模: 趋势组合 %d 组", nrow(grid)))
preloaded <- prepare_rounds_data(
  data_dir = data_dir,
  max_rounds = max_rounds,
  use_latest = use_latest,
  progress = 100,
  use_cache = use_cache
)

summary_rows <- vector("list", nrow(grid))
for (i in seq_len(nrow(grid))) {
  cfg_i <- cfg
  cfg_i$trend_entry_price <- grid$trend_entry_price[i]
  cfg_i$trend_profit_price <- grid$trend_profit_price[i]
  cfg_i$trend_stop_price <- grid$trend_stop_price[i]

  message(sprintf(
    "趋势组合开始: entry=%.2f profit=%.2f stop=%.2f (%d / %d)",
    cfg_i$trend_entry_price,
    cfg_i$trend_profit_price,
    cfg_i$trend_stop_price,
    i,
    nrow(grid)
  ))

  results_df <- run_backtest_preloaded(
    preloaded,
    cfg_i,
    progress = 0,
    progress_label = sprintf("趋势组合[%d/%d]", i, nrow(grid)),
    n_cores = n_cores
  )
  perf <- calc_performance(results_df)
  win_rate_pct <- if (is.na(perf$win_rate)) 0 else perf$win_rate * 100

  summary_rows[[i]] <- data.frame(
    trend_side = cfg_i$trend_side,
    trend_entry_price = cfg_i$trend_entry_price,
    trend_profit_price = cfg_i$trend_profit_price,
    trend_stop_price = cfg_i$trend_stop_price,
    n_total = perf$n_total,
    n_trades = perf$n_trades,
    total_pnl = perf$total_pnl,
    avg_pnl = if (is.na(perf$avg_pnl)) 0 else perf$avg_pnl,
    win_rate_pct = win_rate_pct,
    max_drawdown = perf$max_drawdown,
    stringsAsFactors = FALSE
  )
}

summary_df <- do.call(rbind, summary_rows)
summary_df <- summary_df[order(-summary_df$total_pnl, -summary_df$win_rate_pct), ]
dir.create(dirname(summary_out), recursive = TRUE, showWarnings = FALSE)
write.csv(summary_df, summary_out, row.names = FALSE)
message(sprintf("结果已保存到 %s", summary_out))
