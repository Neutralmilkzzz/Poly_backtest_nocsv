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
config_path <- "strategies/grid_er_hurst_1200.yaml"
data_dir <- "data/raw"
out_dir <- "results/regime_backtest"
max_rounds <- NULL
use_latest <- FALSE
use_cache <- TRUE
n_cores <- 1L
regime_side <- "up"
high_threshold <- 0.8
low_threshold <- 0.7
er_window_override <- NULL
hurst_window_override <- NULL

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

  if (arg == "--regime-side" && arg_i < length(args)) {
    regime_side <- args[arg_i + 1]
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--high-threshold" && arg_i < length(args)) {
    high_threshold <- as.numeric(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--low-threshold" && arg_i < length(args)) {
    low_threshold <- as.numeric(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--er-window" && arg_i < length(args)) {
    er_window_override <- as.numeric(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }

  if (arg == "--hurst-window" && arg_i < length(args)) {
    hurst_window_override <- as.numeric(args[arg_i + 1])
    arg_i <- arg_i + 2
    next
  }

  arg_i <- arg_i + 1
}

if (!(regime_side %in% c("up", "down"))) {
  stop("--regime-side 只支持 up 或 down")
}
if (is.na(high_threshold) || is.na(low_threshold)) {
  stop("high / low threshold 必须是数值")
}
if (low_threshold > high_threshold) {
  stop("low_threshold 不能大于 high_threshold")
}

flatten_table <- function(x) {
  if (length(x) == 0) return("")
  paste(paste(names(x), as.integer(x), sep = ":"), collapse = ", ")
}

compute_history_list <- function(preloaded, cfg) {
  n <- length(preloaded$all_data)
  max_history_seconds <- max(c(cfg$er_window_seconds %||% 0,
                               cfg$hurst_window_seconds %||% 0), na.rm = TRUE)
  round_duration <- cfg$round_duration %||% 300
  history_round_count <- max(0L, ceiling(max_history_seconds / round_duration))

  history_list <- vector("list", n)
  for (i in seq_len(n)) {
    start_idx <- max(1L, i - history_round_count)
    end_idx <- i - 1L
    history_list[[i]] <- if (end_idx >= start_idx) preloaded$all_data[start_idx:end_idx] else list()
  }
  history_list
}

classify_rounds <- function(preloaded, history_list, cfg, side, high_threshold, low_threshold) {
  rows <- vector("list", length(preloaded$all_data))
  round_duration <- cfg$round_duration %||% 300

  for (i in seq_along(preloaded$all_data)) {
    er_value <- compute_opening_er(
      history_rounds = history_list[[i]],
      side = side,
      window_seconds = cfg$er_window_seconds,
      round_duration = round_duration
    )
    hurst_value <- compute_opening_hurst(
      history_rounds = history_list[[i]],
      side = side,
      window_seconds = cfg$hurst_window_seconds,
      round_duration = round_duration
    )
    score <- if (is.na(er_value) || is.na(hurst_value)) NA_real_ else er_value + hurst_value
    regime <- if (is.na(score)) {
      "unclassified"
    } else if (score > high_threshold) {
      "trend_bucket"
    } else if (score < low_threshold) {
      "grid_bucket"
    } else {
      "neutral_bucket"
    }

    rows[[i]] <- data.frame(
      round_index = i,
      round_id = preloaded$round_ids[i],
      er_value = er_value,
      hurst_value = hurst_value,
      score = score,
      regime = regime,
      stringsAsFactors = FALSE
    )
  }

  do.call(rbind, rows)
}

run_subset_backtest <- function(preloaded, history_list, cfg, selected_idx, label, out_dir, n_cores = 1L) {
  if (length(selected_idx) == 0) {
    return(list(
      results = data.frame(),
      perf = list(
        n_total = 0,
        n_trades = 0,
        n_skipped = 0,
        skip_breakdown = table(character(0)),
        win_rate = NA_real_,
        total_pnl = 0,
        avg_pnl = NA_real_,
        max_drawdown = 0
      ),
      results_path = NA_character_
    ))
  }

  cores <- resolve_cores(n_cores)
  selected_idx <- as.integer(selected_idx)

  if (cores > 1L) {
    all_data <- preloaded$all_data
    round_ids <- preloaded$round_ids
    history_copy <- history_list
    cfg_copy <- cfg
    results_list <- run_parallel(length(selected_idx), function(j) {
      idx <- selected_idx[j]
      run_one_round(
        all_data[[idx]],
        cfg_copy,
        round_id = round_ids[idx],
        history_rounds = history_copy[[idx]]
      )
    }, cores)
  } else {
    results_list <- vector("list", length(selected_idx))
    for (j in seq_along(selected_idx)) {
      idx <- selected_idx[j]
      results_list[[j]] <- run_one_round(
        preloaded$all_data[[idx]],
        cfg,
        round_id = preloaded$round_ids[idx],
        history_rounds = history_list[[idx]]
      )
    }
  }

  results_df <- results_list_to_df(results_list)
  perf <- calc_performance(results_df)

  results_path <- file.path(out_dir, paste0(label, "_results.csv"))
  write.csv(results_df, results_path, row.names = FALSE)

  list(results = results_df, perf = perf, results_path = results_path)
}

cfg <- load_config(config_path)
if (!is.null(er_window_override)) {
  cfg$er_window_seconds <- er_window_override
}
if (!is.null(hurst_window_override)) {
  cfg$hurst_window_seconds <- hurst_window_override
}

dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

message("预加载轮次数据...")
preloaded <- prepare_rounds_data(
  data_dir = data_dir,
  max_rounds = max_rounds,
  use_latest = use_latest,
  progress = 100,
  use_cache = use_cache
)
history_list <- compute_history_list(preloaded, cfg)

message("计算 ER / Hurst 市场分类...")
classification_df <- classify_rounds(
  preloaded = preloaded,
  history_list = history_list,
  cfg = cfg,
  side = regime_side,
  high_threshold = high_threshold,
  low_threshold = low_threshold
)
classification_path <- file.path(out_dir, "round_regimes.csv")
write.csv(classification_df, classification_path, row.names = FALSE)

regime_summary <- aggregate(
  cbind(er_value, hurst_value, score) ~ regime,
  data = classification_df,
  FUN = function(x) mean(x, na.rm = TRUE)
)
regime_counts <- as.data.frame(table(classification_df$regime), stringsAsFactors = FALSE)
names(regime_counts) <- c("regime", "n_rounds")
regime_summary <- merge(regime_counts, regime_summary, by = "regime", all.x = TRUE)
write.csv(regime_summary, file.path(out_dir, "regime_summary.csv"), row.names = FALSE)

trend_idx <- classification_df$round_index[classification_df$regime == "trend_bucket"]
grid_idx <- classification_df$round_index[classification_df$regime == "grid_bucket"]

classic_cfg <- cfg
classic_cfg$strategy_mode <- "classic"
classic_cfg$er_filter_enabled <- FALSE
classic_cfg$hurst_filter_enabled <- FALSE

trend_cfg <- cfg
trend_cfg$strategy_mode <- "trend_breakout"
trend_cfg$er_filter_enabled <- FALSE
trend_cfg$hurst_filter_enabled <- FALSE

jobs <- list(
  list(regime = "trend_bucket", strategy = "trend_breakout", idx = trend_idx, cfg = trend_cfg),
  list(regime = "trend_bucket", strategy = "classic", idx = trend_idx, cfg = classic_cfg),
  list(regime = "grid_bucket", strategy = "classic", idx = grid_idx, cfg = classic_cfg),
  list(regime = "grid_bucket", strategy = "trend_breakout", idx = grid_idx, cfg = trend_cfg)
)

summary_rows <- vector("list", length(jobs))
for (i in seq_along(jobs)) {
  job <- jobs[[i]]
  label <- paste(job$regime, job$strategy, sep = "_")
  message(sprintf("运行子回测: %s (%d 轮)", label, length(job$idx)))
  out <- run_subset_backtest(
    preloaded = preloaded,
    history_list = history_list,
    cfg = job$cfg,
    selected_idx = job$idx,
    label = label,
    out_dir = out_dir,
    n_cores = n_cores
  )

  regime_slice <- classification_df[classification_df$round_index %in% job$idx, ]
  summary_rows[[i]] <- data.frame(
    regime = job$regime,
    strategy_mode = job$strategy,
    n_rounds = length(job$idx),
    avg_er = if (nrow(regime_slice) > 0) mean(regime_slice$er_value, na.rm = TRUE) else NA_real_,
    avg_hurst = if (nrow(regime_slice) > 0) mean(regime_slice$hurst_value, na.rm = TRUE) else NA_real_,
    avg_score = if (nrow(regime_slice) > 0) mean(regime_slice$score, na.rm = TRUE) else NA_real_,
    n_trades = out$perf$n_trades,
    win_rate_pct = if (is.na(out$perf$win_rate)) NA_real_ else out$perf$win_rate * 100,
    total_pnl = out$perf$total_pnl,
    avg_pnl = out$perf$avg_pnl,
    max_drawdown = out$perf$max_drawdown,
    skip_breakdown = flatten_table(out$perf$skip_breakdown),
    results_file = basename(out$results_path),
    stringsAsFactors = FALSE
  )
}

summary_df <- do.call(rbind, summary_rows)
summary_df <- summary_df[order(summary_df$regime, -summary_df$total_pnl), ]
summary_path <- file.path(out_dir, "regime_strategy_summary.csv")
write.csv(summary_df, summary_path, row.names = FALSE)

message("══════════════════════════════════════")
message(sprintf("分类明细: %s", classification_path))
message(sprintf("分类汇总: %s", file.path(out_dir, "regime_summary.csv")))
message(sprintf("策略对比: %s", summary_path))
message("══════════════════════════════════════")
print(summary_df)
