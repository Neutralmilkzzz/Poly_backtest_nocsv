# ══════════════════════════════════════════════════════════════
#  run_param_sweep.R — 单进程参数扫参入口
# ══════════════════════════════════════════════════════════════

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
summary_out <- "results/param_sweep_summary.csv"
param_key <- NULL
values_arg <- NULL
max_rounds <- NULL
use_latest <- FALSE
use_cache <- TRUE
n_cores <- 1L

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

  if (arg == "--param" && arg_i < length(args)) {
    param_key <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--values" && arg_i < length(args)) {
    values_arg <- args[arg_i + 1]
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

if (is.null(param_key) || !nzchar(param_key)) {
  stop("缺少 --param 参数")
}

if (is.null(values_arg) || !nzchar(values_arg)) {
  stop("缺少 --values 参数")
}

cfg <- load_config(config_path)
if (!(param_key %in% names(cfg))) {
  stop(sprintf("配置中不存在参数: %s", param_key))
}

raw_values <- trimws(strsplit(values_arg, ",")[[1]])
raw_values <- raw_values[nzchar(raw_values)]
if (length(raw_values) == 0) {
  stop("--values 没有解析到有效值")
}

template <- cfg[[param_key]]
cast_values <- function(values, template) {
  if (is.logical(template)) {
    lowered <- tolower(values)
    if (any(!(lowered %in% c("true", "false", "1", "0")))) {
      stop("布尔参数只支持 true/false/1/0")
    }
    return(lowered %in% c("true", "1"))
  }

  if (is.numeric(template)) {
    parsed <- suppressWarnings(as.numeric(values))
    if (any(is.na(parsed))) {
      stop(sprintf("参数 %s 的扫参值必须是数值", param_key))
    }
    return(parsed)
  }

  values
}

values <- cast_values(raw_values, template)

message(sprintf("任务规模: 参数 %s 扫 %d 个值", param_key, length(values)))
preloaded <- prepare_rounds_data(
  data_dir = data_dir,
  max_rounds = max_rounds,
  use_latest = use_latest,
  progress = 100,
  use_cache = use_cache
)

summary_rows <- vector("list", length(values))
for (i in seq_along(values)) {
  cfg_i <- cfg
  cfg_i[[param_key]] <- values[i]
  display_value <- raw_values[i]

  message(sprintf("扫参开始: %s = %s (%d / %d)", param_key, display_value, i, length(values)))
  results_df <- run_backtest_preloaded(
    preloaded,
    cfg_i,
    progress = 0,
    progress_label = sprintf("扫参[%d/%d]", i, length(values)),
    n_cores = n_cores
  )
  perf <- calc_performance(results_df)
  win_rate_pct <- if (is.na(perf$win_rate)) 0 else perf$win_rate * 100

  summary_rows[[i]] <- data.frame(
    value = display_value,
    n_total = perf$n_total,
    n_trades = perf$n_trades,
    total_pnl = perf$total_pnl,
    win_rate_pct = win_rate_pct,
    stringsAsFactors = FALSE
  )

  message(sprintf("SWEEP_RESULT|%s|%d|%.4f|%.1f",
                  display_value,
                  perf$n_trades,
                  perf$total_pnl,
                  win_rate_pct))
}

summary_df <- do.call(rbind, summary_rows)
dir.create(dirname(summary_out), recursive = TRUE, showWarnings = FALSE)
write.csv(summary_df, summary_out, row.names = FALSE)
message(sprintf("结果已保存到 %s", summary_out))