# ══════════════════════════════════════════════════════════════
#  performance.R — 绩效统计
# ══════════════════════════════════════════════════════════════

#' 计算综合绩效指标
#'
#' @param results_df run_backtest 返回的 data.frame
#' @return named list
calc_performance <- function(results_df) {
  trades    <- results_df[results_df$traded, ]
  skipped   <- results_df[!results_df$traded, ]
  n_total   <- nrow(results_df)
  n_trades  <- nrow(trades)
  n_skipped <- n_total - n_trades

  if (n_trades == 0) {
    return(list(
      n_total = n_total, n_trades = 0, n_skipped = n_skipped,
      skip_breakdown = table(skipped$skip_reason),
      win_rate = NA, total_pnl = 0, avg_pnl = NA,
      max_win = NA, max_loss = NA, profit_factor = NA,
      max_drawdown = 0, sharpe = NA
    ))
  }

  wins   <- trades$pnl > 0
  losses <- trades$pnl < 0

  win_rate <- mean(wins, na.rm = TRUE)
  total_pnl <- sum(trades$pnl, na.rm = TRUE)
  avg_pnl   <- mean(trades$pnl, na.rm = TRUE)
  max_win   <- max(trades$pnl, na.rm = TRUE)
  max_loss  <- min(trades$pnl, na.rm = TRUE)

  avg_win  <- if (sum(wins, na.rm = TRUE) > 0) mean(trades$pnl[wins], na.rm = TRUE) else 0
  avg_loss <- if (sum(losses, na.rm = TRUE) > 0) abs(mean(trades$pnl[losses], na.rm = TRUE)) else 0
  profit_factor <- if (avg_loss > 0) avg_win / avg_loss else Inf

  # 最大回撤
  cum <- cumsum(trades$pnl)
  peak <- cummax(cum)
  dd <- peak - cum
  max_drawdown <- max(dd, na.rm = TRUE)

  # Sharpe (年化，假设每轮5分钟，一天288轮)
  sharpe <- NA
  pnl_sd <- sd(trades$pnl, na.rm = TRUE)
  if (!is.na(pnl_sd) && pnl_sd > 0) {
    sharpe <- mean(trades$pnl, na.rm = TRUE) / pnl_sd * sqrt(288)
  }

  # 按退出类型统计
  exit_breakdown <- if (nrow(trades) > 0) table(trades$exit_type) else table(character(0))

  # 按小时统计
  trades$hour <- as.integer(format(trades$entry_time, "%H"))
  hourly <- aggregate(pnl ~ hour, data = trades, FUN = function(x) {
    c(n = length(x), win_rate = mean(x > 0, na.rm = TRUE), total_pnl = sum(x, na.rm = TRUE), avg_pnl = mean(x, na.rm = TRUE))
  })

  list(
    n_total        = n_total,
    n_trades       = n_trades,
    n_skipped      = n_skipped,
    skip_breakdown = table(skipped$skip_reason),
    win_rate       = win_rate,
    total_pnl      = total_pnl,
    avg_pnl        = avg_pnl,
    max_win        = max_win,
    max_loss       = max_loss,
    profit_factor  = profit_factor,
    max_drawdown   = max_drawdown,
    sharpe         = sharpe,
    exit_breakdown = exit_breakdown,
    hourly_stats   = hourly
  )
}

#' 打印绩效报告
print_performance <- function(perf) {
  cat("═══════════════════════════════════════\n")
  cat("          回测绩效报告\n")
  cat("═══════════════════════════════════════\n")
  cat(sprintf("  总轮次:       %d\n", perf$n_total))
  cat(sprintf("  交易次数:     %d\n", perf$n_trades))
  cat(sprintf("  跳过次数:     %d\n", perf$n_skipped))
  cat("───────────────────────────────────────\n")

  if (perf$n_trades > 0) {
    cat(sprintf("  胜率:         %.1f%%\n", perf$win_rate * 100))
    cat(sprintf("  总 PnL:       %.4f\n", perf$total_pnl))
    cat(sprintf("  平均 PnL:     %.4f\n", perf$avg_pnl))
    cat(sprintf("  最大单笔盈利: %.4f\n", perf$max_win))
    cat(sprintf("  最大单笔亏损: %.4f\n", perf$max_loss))
    cat(sprintf("  盈亏比:       %.2f\n", perf$profit_factor))
    cat(sprintf("  最大回撤:     %.4f\n", perf$max_drawdown))
    cat(sprintf("  Sharpe:       %.2f\n", ifelse(is.na(perf$sharpe), 0, perf$sharpe)))
  }

  cat("───────────────────────────────────────\n")
  cat("  跳过原因分布:\n")
  if (length(perf$skip_breakdown) > 0) {
    for (nm in names(perf$skip_breakdown)) {
      cat(sprintf("    %-15s %d\n", nm, perf$skip_breakdown[nm]))
    }
  }

  cat("───────────────────────────────────────\n")
  cat("  退出类型分布:\n")
  if (length(perf$exit_breakdown) > 0) {
    for (nm in names(perf$exit_breakdown)) {
      cat(sprintf("    %-15s %d\n", nm, perf$exit_breakdown[nm]))
    }
  }
  cat("═══════════════════════════════════════\n")
}
