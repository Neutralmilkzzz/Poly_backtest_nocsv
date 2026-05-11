# ══════════════════════════════════════════════════════════════
#  drawdown_analysis.R — 止损线画线策略回测
# ══════════════════════════════════════════════════════════════
#
#  完整交易模拟：
#    1. 前90秒 ask <= 0.25 → 买入（一次）
#    2. 买入后先等待 cooldown 秒，再逐 tick 扫描 bid：
#       - bid >= 0.26 → 止盈卖出，赚钱走人
#       - bid <= stop_loss → 止损卖出，割肉走人
#       - 距离 round 结束还剩 2 分钟还没走 → timeout
#       - round 结束还没走 → 用当时 bid 平仓
#    3. 测试不同止损线，找最优
#
#  用法:
#    source("scripts/drawdown_analysis.R")
# ══════════════════════════════════════════════════════════════

source("R/utils/helpers.R")
source("R/io/data_reader.R")
source("R/io/data_cleaner.R")
source("R/io/cache_reader.R")
source("R/io/config_loader.R")
source("R/engine/fill_model.R")

# ══════════════════════════════════════════════════════════════
#  核心：模拟单轮交易
# ══════════════════════════════════════════════════════════════

#' 模拟单轮完整交易
#'
#' @param df 清洗后的 data.frame（带 elapsed 列）
#' @param entry_price   买入限价 (ask <= 此值就买)
#' @param profit_price  卖出止盈价 (bid >= 此值就卖)
#' @param stop_loss     止损价 (bid <= 此值就割), NULL = 不设止损
#' @param entry_timeout 入场窗口（秒）
#' @param settle_cutoff 最晚持仓时间（秒），超过用当时 bid 平仓
#' @param time_stop_after_entry 买入后最多持有多少秒；超过则按当时 bid 时间止损
#' @return list 或 NULL（本轮没触发入场）
sim_one_round <- function(df, entry_price = 0.25, profit_price = 0.26,
                          stop_loss = NULL, entry_timeout = 90,
                          round_duration = 300,
                          settle_cutoff = 300,
                          cooldown = 8,
                          time_stop_after_entry = NULL,
                          entry_fill_model = "trader_bot_paper",
                          sell_fill_model = "trader_bot_paper",
                          sell_timeout_remaining = 120,
                          sell_timeout = NULL) {

  # ── 1. 找入场点 ──
  entry_row  <- NA
  entry_side <- NA
  entry_fill <- NULL
  for (i in seq_len(nrow(df))) {
    if (df$elapsed[i] > entry_timeout) break
    fill_candidate <- simulate_entry_fill(
      df[i, , drop = FALSE],
      entry_price = entry_price,
      fill_model = entry_fill_model
    )
    if (isTRUE(fill_candidate$triggered)) {
      entry_row <- i
      entry_side <- fill_candidate$side
      entry_fill <- fill_candidate
      break
    }
  }
  if (is.na(entry_row)) return(NULL)  # 没碰到入场价

  bid_col <- if (entry_side == "up") "up_best_bid" else "down_best_bid"
  entry_elapsed <- df$elapsed[entry_row]
  sell_start_elapsed <- entry_elapsed + cooldown
  time_stop_deadline <- if (!is.null(time_stop_after_entry)) entry_elapsed + time_stop_after_entry else NULL

  # ── 2. 冷静期后逐 tick 扫：止盈 or 止损 or 超时 ──
  exit_price   <- NA
  exit_elapsed <- NA
  exit_type    <- NA
  min_bid_before_exit <- Inf  # 卖出前的最低 bid（真实回撤）

  end_row <- nrow(df)
  if ((entry_row + 1) > end_row) return(NULL)

  for (j in (entry_row + 1):end_row) {
    elapsed_now <- df$elapsed[j]
    if (elapsed_now < sell_start_elapsed) next

    bid_now     <- df[[bid_col]][j]

    if (is.na(bid_now)) next

    # 跟踪卖出前最低价
    if (bid_now < min_bid_before_exit) min_bid_before_exit <- bid_now

    # 止盈：bid >= profit_price
    if (bid_now >= profit_price) {
      exit_price   <- simulate_sell_fill_price(
        target_price = profit_price,
        observed_bid = bid_now,
        fill_model = sell_fill_model
      )
      exit_elapsed <- elapsed_now
      exit_type    <- "profit"
      break
    }

    # 止损：bid <= stop_loss
    if (!is.null(stop_loss) && bid_now <= stop_loss) {
      exit_price   <- bid_now  # 市价止损，用当时 bid
      exit_elapsed <- elapsed_now
      exit_type    <- "stoploss"
      break
    }

    if (!is.null(time_stop_deadline) && elapsed_now > time_stop_deadline) break

    timeout_deadline_info <- resolve_sell_deadline(
      round_duration = round_duration,
      settle_cutoff = settle_cutoff,
      sell_timeout_enabled = !is.null(sell_timeout_remaining) || !is.null(sell_timeout),
      sell_timeout_remaining = sell_timeout_remaining,
      sell_timeout = sell_timeout
    )
    timeout_deadline <- timeout_deadline_info$deadline

    if (elapsed_now > timeout_deadline) break
    if (elapsed_now > settle_cutoff) break
  }

  timeout_deadline_info <- resolve_sell_deadline(
    round_duration = round_duration,
    settle_cutoff = settle_cutoff,
    sell_timeout_enabled = !is.null(sell_timeout_remaining) || !is.null(sell_timeout),
    sell_timeout_remaining = sell_timeout_remaining,
    sell_timeout = sell_timeout
  )
  timeout_deadline <- timeout_deadline_info$deadline

  effective_deadline <- timeout_deadline
  effective_exit_type <- timeout_deadline_info$exit_type_on_timeout
  if (!is.null(time_stop_deadline)) {
    effective_deadline <- min(time_stop_deadline, timeout_deadline)
    if (time_stop_deadline <= timeout_deadline) {
      effective_exit_type <- "time_stop"
    }
  }

  if (is.na(exit_price)) {
    timeout_snapshot <- last_valid_price_before(
      df,
      value_col = bid_col,
      cutoff_elapsed = effective_deadline,
      start_elapsed = sell_start_elapsed
    )
    if (is.na(timeout_snapshot$price)) return(NULL)
    exit_price   <- timeout_snapshot$price
    exit_elapsed <- timeout_snapshot$elapsed
    exit_type    <- effective_exit_type

    observed_bids <- df[df$elapsed >= sell_start_elapsed & df$elapsed <= effective_deadline, bid_col]
    observed_bids <- observed_bids[!is.na(observed_bids)]
    if (min_bid_before_exit == Inf && length(observed_bids) > 0) {
      min_bid_before_exit <- min(observed_bids)
    }
  }

  executed_entry_price <- entry_fill$fill_price
  pnl <- exit_price - executed_entry_price

  list(
    entry_side      = entry_side,
    entry_elapsed   = entry_elapsed,
    entry_price     = executed_entry_price,
    entry_trigger_price = entry_fill$trigger_price,
    exit_price      = exit_price,
    exit_elapsed    = exit_elapsed,
    exit_type       = exit_type,
    pnl             = pnl,
    hold_seconds    = exit_elapsed - entry_elapsed,
    min_bid_before_exit = min_bid_before_exit,
    max_drawdown    = executed_entry_price - min_bid_before_exit
  )
}

# ══════════════════════════════════════════════════════════════
#  批量运行：固定止损线
# ══════════════════════════════════════════════════════════════

#' 用一条止损线跑所有轮次
run_sim <- function(data_dir = "data/raw",
                    entry_price = 0.25, profit_price = 0.26,
                    stop_loss = NULL, entry_timeout = 90,
                    round_duration = 300,
                    settle_cutoff = 300, cooldown = 8, max_rounds = NULL,
                    time_stop_after_entry = NULL,
                    entry_fill_model = "trader_bot_paper",
                    sell_fill_model = "trader_bot_paper",
                    sell_timeout_remaining = 120,
                    sell_timeout = NULL) {
  rounds <- list_rounds(data_dir)
  if (!is.null(max_rounds)) rounds <- head(rounds, max_rounds)
  n <- nrow(rounds)

  price_sl_str <- if (is.null(stop_loss)) "无" else sprintf("%.2f", stop_loss)
  time_sl_str <- if (is.null(time_stop_after_entry)) "无" else sprintf("%ss", time_stop_after_entry)
  message(sprintf("交易模拟: %d 轮 | 买%.2f 卖%.2f | 价格止损=%s | 时间止损=%s",
                  n, entry_price, profit_price, price_sl_str, time_sl_str))

  results <- list()
  for (i in seq_len(n)) {
    if (i %% 500 == 0) message(sprintf("  进度: %d / %d", i, n))
    df  <- read_round_csv(rounds$path[i])
    df  <- clean_round(df, round_start = rounds$round_time[i])
    rid <- tools::file_path_sans_ext(basename(rounds$path[i]))

    res <- sim_one_round(df, entry_price, profit_price, stop_loss,
               entry_timeout, round_duration, settle_cutoff, cooldown, time_stop_after_entry,
                         entry_fill_model,
                         sell_fill_model,
                         sell_timeout_remaining,
                         sell_timeout)
    if (!is.null(res)) {
      res$round_id <- rid
      results[[length(results) + 1]] <- res
    }
  }

  if (length(results) == 0) {
    message("没有任何轮次触发入场")
    return(data.frame())
  }

  out <- do.call(rbind, lapply(results, as.data.frame, stringsAsFactors = FALSE))
  message(sprintf("完成: %d 笔交易", nrow(out)))
  out
}

# ══════════════════════════════════════════════════════════════
#  核心：扫描多条止损线，找最优
# ══════════════════════════════════════════════════════════════

#' 扫描多个止损价位，对比绩效
scan_stop_losses <- function(data_dir = "data/raw",
                             entry_price = 0.25, profit_price = 0.26,
                             stop_losses = seq(0.05, 0.24, by = 0.01),
                             entry_timeout = 90,
                             round_duration = 300, settle_cutoff = 300,
                             cooldown = 8,
                             max_rounds = NULL,
                             time_stop_after_entry = NULL,
                             entry_fill_model = "trader_bot_paper",
                             sell_fill_model = "trader_bot_paper",
                             sell_timeout_remaining = 120,
                             sell_timeout = NULL) {

  # 先读一次数据缓存起来，避免重复 IO
  rounds <- list_rounds(data_dir)
  if (!is.null(max_rounds)) rounds <- head(rounds, max_rounds)
  n <- nrow(rounds)
  message(sprintf("预加载 %d 个轮次数据...", n))

  all_data <- vector("list", n)
  all_ids  <- character(n)
  for (i in seq_len(n)) {
    if (i %% 500 == 0) message(sprintf("  加载: %d / %d", i, n))
    df <- read_round_csv(rounds$path[i])
    all_data[[i]] <- clean_round(df, round_start = rounds$round_time[i])
    all_ids[i] <- tools::file_path_sans_ext(basename(rounds$path[i]))
  }
  message("数据加载完成，开始扫描止损线...")

  # 加入 "无止损" 作为基线
  test_levels <- c(NA, stop_losses)

  summary_rows <- list()
  for (k in seq_along(test_levels)) {
    sl <- test_levels[k]
    sl_val <- if (is.na(sl)) NULL else sl
    sl_label <- if (is.na(sl)) "无止损" else sprintf("%.2f", sl)

    results <- list()
    for (i in seq_len(n)) {
      res <- sim_one_round(all_data[[i]], entry_price, profit_price,
                           sl_val, entry_timeout, round_duration, settle_cutoff,
                           cooldown, time_stop_after_entry, entry_fill_model,
                           sell_fill_model, sell_timeout_remaining, sell_timeout)
      if (!is.null(res)) results[[length(results) + 1]] <- res
    }

    if (length(results) == 0) next

    pnls <- sapply(results, `[[`, "pnl")
    exit_types <- sapply(results, `[[`, "exit_type")

    n_trades   <- length(pnls)
    n_profit   <- sum(exit_types == "profit")
    n_stoploss <- sum(exit_types == "stoploss")
    n_timeout  <- sum(exit_types == "timeout")
    win_rate   <- mean(pnls > 0) * 100
    total_pnl  <- sum(pnls)
    avg_pnl    <- mean(pnls)
    avg_win    <- if (sum(pnls > 0) > 0) mean(pnls[pnls > 0]) else 0
    avg_loss   <- if (sum(pnls < 0) > 0) mean(pnls[pnls < 0]) else 0

    summary_rows[[length(summary_rows) + 1]] <- data.frame(
      stop_loss  = sl_label,
      trades     = n_trades,
      profit     = n_profit,
      stoploss   = n_stoploss,
      timeout    = n_timeout,
      win_rate   = win_rate,
      total_pnl  = total_pnl,
      avg_pnl    = avg_pnl,
      avg_win    = avg_win,
      avg_loss   = avg_loss,
      stringsAsFactors = FALSE
    )

    if (k %% 5 == 0) message(sprintf("  已测试 %d / %d 条止损线", k, length(test_levels)))
  }

  out <- do.call(rbind, summary_rows)
  message("止损线扫描完成")
  out
}

#' 对盈利单的持仓时间做分桶统计
analyze_profit_hold_times <- function(results_df,
                                      bucket_breaks = c(0, 10, 20, 30, 45, 60, 90, 120, Inf)) {
  profits <- results_df[results_df$exit_type == "profit" & results_df$pnl > 0, ]
  if (nrow(profits) == 0) return(data.frame())

  profits$pnl <- as.numeric(profits$pnl)
  profits$entry_elapsed <- as.numeric(profits$entry_elapsed)
  profits$exit_elapsed <- as.numeric(profits$exit_elapsed)
  profits$hold_seconds <- as.numeric(profits$exit_elapsed) - as.numeric(profits$entry_elapsed)
  profits$hold_bucket <- cut(
    profits$hold_seconds,
    breaks = bucket_breaks,
    include.lowest = TRUE,
    right = FALSE
  )

  bucket_levels <- levels(profits$hold_bucket)
  bucket_stats <- lapply(bucket_levels, function(bucket) {
    bucket_pnls <- profits$pnl[profits$hold_bucket == bucket]
    bucket_pnls <- bucket_pnls[!is.na(bucket_pnls)]
    data.frame(
      hold_bucket = bucket,
      count = length(bucket_pnls),
      avg_pnl = if (length(bucket_pnls) > 0) mean(bucket_pnls) else NA_real_,
      total_pnl = if (length(bucket_pnls) > 0) sum(bucket_pnls) else NA_real_,
      stringsAsFactors = FALSE
    )
  })
  bucket_stats <- do.call(rbind, bucket_stats)

  hold_values <- profits$hold_seconds
  attr(bucket_stats, "summary") <- list(
    n = length(hold_values),
    avg_hold = mean(hold_values),
    median_hold = median(hold_values),
    p25 = unname(quantile(hold_values, 0.25)),
    p50 = unname(quantile(hold_values, 0.50)),
    p75 = unname(quantile(hold_values, 0.75)),
    p90 = unname(quantile(hold_values, 0.90))
  )
  attr(bucket_stats, "profit_trades") <- profits
  bucket_stats
}

#' 扫描不同时间止损点
scan_time_stops <- function(data_dir = "data/raw",
                            entry_price = 0.25, profit_price = 0.26,
                            time_stops = c(15, 20, 30, 45, 60, 75, 90, 120),
                            entry_timeout = 90,
                            round_duration = 300, settle_cutoff = 300,
                            cooldown = 8,
                            max_rounds = NULL,
                            entry_fill_model = "trader_bot_paper",
                            sell_fill_model = "trader_bot_paper",
                            sell_timeout_remaining = 120,
                            sell_timeout = NULL) {
  rounds <- list_rounds(data_dir)
  if (!is.null(max_rounds)) rounds <- head(rounds, max_rounds)
  n <- nrow(rounds)
  message(sprintf("预加载 %d 个轮次数据...", n))

  all_data <- vector("list", n)
  for (i in seq_len(n)) {
    if (i %% 500 == 0) message(sprintf("  加载: %d / %d", i, n))
    df <- read_round_csv(rounds$path[i])
    all_data[[i]] <- clean_round(df, round_start = rounds$round_time[i])
  }
  message("数据加载完成，开始扫描时间止损点...")

  summary_rows <- list()
  for (k in seq_along(time_stops)) {
    ts_value <- time_stops[k]
    results <- list()
    for (i in seq_len(n)) {
      res <- sim_one_round(
        all_data[[i]],
        entry_price = entry_price,
        profit_price = profit_price,
        stop_loss = NULL,
        entry_timeout = entry_timeout,
        round_duration = round_duration,
        settle_cutoff = settle_cutoff,
        cooldown = cooldown,
        time_stop_after_entry = ts_value,
        entry_fill_model = entry_fill_model,
        sell_fill_model = sell_fill_model,
        sell_timeout_remaining = sell_timeout_remaining,
        sell_timeout = sell_timeout
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

    if (k %% 5 == 0) message(sprintf("  已测试 %d / %d 个时间止损点", k, length(time_stops)))
  }

  out <- do.call(rbind, summary_rows)
  message("时间止损扫描完成")
  out
}

#' 打印时间止损报告
print_time_stop_report <- function(scan_df) {
  cat("═══════════════════════════════════════════════════════════════════════════════\n")
  cat("  时间止损对比报告\n")
  cat("═══════════════════════════════════════════════════════════════════════════════\n\n")

  cat(sprintf("  %-10s %6s %6s %8s %8s %8s %10s %10s %10s\n",
              "时间止损", "交易数", "止盈", "时止损", "超时", "胜率%", "总PnL", "均PnL", "均持仓"))
  cat("  ", paste(rep("─", 90), collapse = ""), "\n")

  for (i in seq_len(nrow(scan_df))) {
    r <- scan_df[i, ]
    cat(sprintf("  %-10s %6d %6d %8d %8d %7.1f%% %10.4f %10.4f %10.2f\n",
                paste0(r$time_stop, "s"), r$trades, r$profit, r$time_stop_exit,
                r$timeout, r$win_rate, r$total_pnl, r$avg_pnl, r$avg_hold))
  }

  best_idx <- which.max(scan_df$total_pnl)
  cat("\n───────────────────────────────────────────────────\n")
  cat(sprintf("  总 PnL 最优时间止损: %ss (总PnL = %.4f)\n",
              scan_df$time_stop[best_idx], scan_df$total_pnl[best_idx]))
  cat("═══════════════════════════════════════════════════════════════════════════════\n")
}

# ══════════════════════════════════════════════════════════════
#  打印报告
# ══════════════════════════════════════════════════════════════

#' 打印止损线对比报告
print_stoploss_report <- function(scan_df) {
  cat("═══════════════════════════════════════════════════════════════════════════════\n")
  cat("  止损线对比报告 — 哪条线亏最少、赚最多？\n")
  cat("═══════════════════════════════════════════════════════════════════════════════\n\n")

  cat(sprintf("  %-8s %6s %6s %6s %6s %8s %10s %10s %10s %10s\n",
              "止损线", "交易数", "止盈", "止损", "超时",
              "胜率%", "总PnL", "均PnL", "均盈利", "均亏损"))
  cat("  ", paste(rep("─", 90), collapse = ""), "\n")

  for (i in seq_len(nrow(scan_df))) {
    r <- scan_df[i, ]
    cat(sprintf("  %-8s %6d %6d %6d %6d %7.1f%% %10.4f %10.4f %10.4f %10.4f\n",
                r$stop_loss, r$trades, r$profit, r$stoploss, r$timeout,
                r$win_rate, r$total_pnl, r$avg_pnl, r$avg_win, r$avg_loss))
  }

  # 找最优
  cat("\n───────────────────────────────────────────────────\n")
  best_pnl_idx <- which.max(scan_df$total_pnl)
  best_wr_idx  <- which.max(scan_df$win_rate)
  cat(sprintf("  总 PnL 最高:  止损线 = %s (总PnL = %.4f)\n",
              scan_df$stop_loss[best_pnl_idx], scan_df$total_pnl[best_pnl_idx]))
  cat(sprintf("  胜率最高:     止损线 = %s (胜率 = %.1f%%)\n",
              scan_df$stop_loss[best_wr_idx], scan_df$win_rate[best_wr_idx]))
  cat("═══════════════════════════════════════════════════════════════════════════════\n")
}

#' 打印单条止损线的详细统计
print_sim_report <- function(sim_df, stop_loss = NULL) {
  sl_str <- if (is.null(stop_loss)) "无" else sprintf("%.2f", stop_loss)
  cat("═══════════════════════════════════════════════════\n")
  cat(sprintf("  交易模拟报告 | 止损线: %s\n", sl_str))
  cat("═══════════════════════════════════════════════════\n\n")

  n <- nrow(sim_df)
  cat(sprintf("  总交易数:   %d\n", n))
  cat(sprintf("  止盈卖出:   %d (%.1f%%)\n",
              sum(sim_df$exit_type == "profit"),
              mean(sim_df$exit_type == "profit") * 100))
  cat(sprintf("  止损卖出:   %d (%.1f%%)\n",
              sum(sim_df$exit_type == "stoploss"),
              mean(sim_df$exit_type == "stoploss") * 100))
  cat(sprintf("  超时平仓:   %d (%.1f%%)\n",
              sum(sim_df$exit_type == "timeout"),
              mean(sim_df$exit_type == "timeout") * 100))

  cat(sprintf("\n  总 PnL:     %.4f\n", sum(sim_df$pnl)))
  cat(sprintf("  平均 PnL:   %.4f\n", mean(sim_df$pnl)))
  cat(sprintf("  胜率:       %.1f%%\n", mean(sim_df$pnl > 0) * 100))

  wins <- sim_df$pnl[sim_df$pnl > 0]
  losses <- sim_df$pnl[sim_df$pnl < 0]
  if (length(wins) > 0) cat(sprintf("  平均盈利:   +%.4f\n", mean(wins)))
  if (length(losses) > 0) cat(sprintf("  平均亏损:   %.4f\n", mean(losses)))

  cat(sprintf("\n  卖出前最低 bid (中位): %.4f\n", median(sim_df$min_bid_before_exit)))
  cat(sprintf("  卖出前最大回撤 (中位): %.4f\n", median(sim_df$max_drawdown)))
  cat("═══════════════════════════════════════════════════\n")
}

# ══════════════════════════════════════════════════════════════
#  可视化
# ══════════════════════════════════════════════════════════════

#' 画止损线 vs 总PnL 对比图
plot_stoploss_pnl <- function(scan_df, save_path = NULL) {
  if (!requireNamespace("ggplot2", quietly = TRUE)) return(invisible(NULL))
  library(ggplot2)

  has_no_sl <- scan_df$stop_loss == "无止损"
  plot_df <- scan_df[!has_no_sl, ]
  plot_df$sl_num <- as.numeric(plot_df$stop_loss)
  baseline_pnl <- scan_df$total_pnl[has_no_sl]

  p <- ggplot(plot_df, aes(x = .data$sl_num, y = .data$total_pnl)) +
    geom_line(color = "#2196F3", linewidth = 1.2) +
    geom_point(color = "#2196F3", size = 2) +
    {if (length(baseline_pnl) > 0)
      geom_hline(yintercept = baseline_pnl, linetype = "dashed",
                 color = "red", linewidth = 0.8)} +
    {if (length(baseline_pnl) > 0)
      annotate("text", x = min(plot_df$sl_num), y = baseline_pnl,
               label = sprintf("无止损基线: %.4f", baseline_pnl),
               vjust = -1, hjust = 0, color = "red")} +
    labs(title = "不同止损线 vs 总 PnL",
         subtitle = "找最高点 = 最佳止损位",
         x = "止损价格", y = "总 PnL") +
    theme_minimal()

  if (!is.null(save_path)) ggsave(save_path, p, width = 10, height = 5)
  print(p)
}

#' 画止损线 vs 胜率对比图
plot_stoploss_winrate <- function(scan_df, save_path = NULL) {
  if (!requireNamespace("ggplot2", quietly = TRUE)) return(invisible(NULL))
  library(ggplot2)

  has_no_sl <- scan_df$stop_loss == "无止损"
  plot_df <- scan_df[!has_no_sl, ]
  plot_df$sl_num <- as.numeric(plot_df$stop_loss)
  baseline_wr <- scan_df$win_rate[has_no_sl]

  p <- ggplot(plot_df, aes(x = .data$sl_num, y = .data$win_rate)) +
    geom_line(color = "#4CAF50", linewidth = 1.2) +
    geom_point(color = "#4CAF50", size = 2) +
    {if (length(baseline_wr) > 0)
      geom_hline(yintercept = baseline_wr, linetype = "dashed",
                 color = "red", linewidth = 0.8)} +
    labs(title = "不同止损线 vs 胜率",
         x = "止损价格", y = "胜率 %") +
    theme_minimal()

  if (!is.null(save_path)) ggsave(save_path, p, width = 10, height = 5)
  print(p)
}


# ══════════════════════════════════════════════════════════════
#  直接 source 运行
# ══════════════════════════════════════════════════════════════
if (sys.nframe() == 0) {
  if (!file.exists("data/raw")) {
    script_dir <- dirname(commandArgs(trailingOnly = FALSE)[
      grep("--file=", commandArgs(trailingOnly = FALSE))])
    if (length(script_dir) > 0) setwd(file.path(script_dir, ".."))
  }

  scan <- scan_stop_losses()
  print_stoploss_report(scan)

  dir.create("results", showWarnings = FALSE)
  dir.create("reports", showWarnings = FALSE)
  write.csv(scan, "results/stoploss_scan.csv", row.names = FALSE)
  plot_stoploss_pnl(scan, "reports/stoploss_vs_pnl.png")
  plot_stoploss_winrate(scan, "reports/stoploss_vs_winrate.png")
}
