# ══════════════════════════════════════════════════════════════
#  orchestrator.R — 探针-跟庄系统编排器
# ══════════════════════════════════════════════════════════════
#
#  逐轮顺序执行:
#  1. 运行网格探针 (classic, entry 0-90s)
#  2. 运行尾盘探针 (classic, entry 240-280s)
#  3. 更新 regime detector
#  4. NORMAL  → 探针实盘 PnL 计入总账，跟庄休眠
#     WHALE   → 探针切模拟(PnL 不计入总账)，启动跟庄
#  5. 汇总结果

source("R/io/config_loader.R",  local = FALSE)
source("R/io/data_reader.R",    local = FALSE)
source("R/io/data_cleaner.R",   local = FALSE)
source("R/io/cache_reader.R",   local = FALSE)
source("R/engine/fill_model.R", local = FALSE)
source("R/engine/backtest_engine.R", local = FALSE)
source("R/engine/regime_detector.R", local = FALSE)
source("R/engine/whale_follow.R", local = FALSE)

#' 构建探针配置 (从主配置衍生)
#'
#' @param base_cfg 主配置
#' @param probe_type "grid" | "tail"
#' @return 探针专用 cfg
build_probe_cfg <- function(base_cfg, probe_type = "grid") {
  pcfg <- base_cfg

  probe_section <- base_cfg$probes[[probe_type]] %||% list()

  pcfg$trade_shares    <- probe_section$trade_shares %||% 10L
  pcfg$entry_price     <- probe_section$entry_price %||% 0.25
  pcfg$profit_price    <- probe_section$profit_price %||% 0.26

  if (probe_type == "grid") {
    pcfg$strategy_mode <- "classic"
    pcfg$entry_window_start <- probe_section$entry_window_start %||% 0
    pcfg$entry_window_end   <- probe_section$entry_window_end %||% 90
    pcfg$sell_window_start  <- probe_section$sell_window_start %||% 0
    pcfg$sell_window_end    <- probe_section$sell_window_end %||% 180
  } else if (probe_type == "tail") {
    pcfg$strategy_mode <- probe_section$strategy_mode %||% "trend_breakout"
    pcfg$entry_window_start <- probe_section$entry_window_start %||% 240
    pcfg$entry_window_end   <- probe_section$entry_window_end %||% 280
    pcfg$sell_window_start  <- probe_section$sell_window_start %||% 240
    pcfg$sell_window_end    <- probe_section$sell_window_end %||% 300
    # 尾盘趋势参数
    pcfg$trend_side         <- probe_section$trend_side %||% "both"
    pcfg$trend_entry_price  <- probe_section$trend_entry_price %||% 0.60
    pcfg$trend_profit_price <- probe_section$trend_profit_price %||% 0.80
    pcfg$trend_stop_price   <- probe_section$trend_stop_price %||% 0.50
  }

  # 探针使用宽松风控: 关闭 btc_guard，关闭宵禁
  pcfg$btc_guard_enabled <- probe_section$btc_guard_enabled %||% FALSE
  pcfg$curfew_enabled    <- probe_section$curfew_enabled %||% FALSE
  pcfg$polarity_filter_enabled <- FALSE
  pcfg$entry_timeout_enabled   <- TRUE
  pcfg$sell_timeout_enabled    <- TRUE

  pcfg
}

#' 运行探针-跟庄系统完整回测
#'
#' @param preloaded 由 prepare_rounds_data() 返回的预加载数据
#' @param cfg       主配置 (含 probes, whale, regime 子节点)
#' @param progress  打印进度间隔
#' @return data.frame 每轮一行的完整结果
run_probe_whale_backtest <- function(preloaded, cfg, progress = 100) {

  n <- length(preloaded$all_data)

  # 构建两个探针的配置
  grid_cfg <- build_probe_cfg(cfg, "grid")
  tail_cfg <- build_probe_cfg(cfg, "tail")

  # 初始化 regime 上下文
  regime_ctx <- new_regime_context()

  # 结果收集
  results <- vector("list", n)

  message(sprintf("探针-跟庄回测开始: %d 个轮次", n))

  for (i in seq_len(n)) {
    if (progress > 0 && i %% progress == 0) {
      message(sprintf("  进度: %d / %d (%.0f%%) [regime: %s]",
                      i, n, i / n * 100, regime_ctx$state))
    }

    df       <- preloaded$all_data[[i]]
    round_id <- preloaded$round_ids[i]

    # ── 1. 运行两个探针 ──
    grid_result <- run_one_round_classic(df, grid_cfg, round_id = round_id)
    tail_result <- run_one_round(df, tail_cfg, round_id = round_id)

    # ── 2. 更新 regime (基于微结构信号，直接分析原始数据) ──
    regime_ctx <- update_regime(regime_ctx, df, cfg, round_id = round_id)

    current_regime <- regime_ctx$state
    is_whale <- (current_regime == "WHALE_ACTIVE")

    # ── 3. 跟庄策略 (仅 WHALE_ACTIVE 时运行) ──
    if (is_whale) {
      whale_result <- run_one_round_whale(df, cfg, round_id = round_id)
    } else {
      whale_result <- list(
        cheap    = new_whale_result("cheap", round_id),
        expensive = new_whale_result("expensive", round_id)
      )
    }

    # ── 4. 汇总本轮结果 ──
    # 探针 PnL: NORMAL 模式计入实盘，WHALE 模式标记为模拟
    grid_real_pnl <- if (!is_whale && grid_result$traded) grid_result$pnl else 0
    tail_real_pnl <- if (!is_whale && tail_result$traded) tail_result$pnl else 0

    # 跟庄 PnL: 仅 WHALE_ACTIVE 时计入
    whale_cheap_pnl <- whale_result$cheap$pnl
    whale_exp_pnl   <- whale_result$expensive$pnl

    total_real_pnl <- grid_real_pnl + tail_real_pnl + whale_cheap_pnl + whale_exp_pnl

    results[[i]] <- list(
      round_id           = round_id,
      regime             = current_regime,
      whale_count        = regime_ctx$whale_count %||% NA_integer_,
      tail_range         = regime_ctx$tail_range %||% NA_real_,
      tail_spread        = regime_ctx$tail_spread %||% NA_real_,
      is_whale_signal    = regime_ctx$is_whale_signal %||% FALSE,

      # 网格探针
      grid_traded        = grid_result$traded,
      grid_side          = grid_result$side %||% NA_character_,
      grid_pnl           = grid_result$pnl,
      grid_is_paper      = is_whale,

      # 尾盘探针
      tail_traded        = tail_result$traded,
      tail_side          = tail_result$side %||% NA_character_,
      tail_pnl           = tail_result$pnl,
      tail_is_paper      = is_whale,

      # 跟庄-便宜端
      whale_cheap_traded = whale_result$cheap$traded,
      whale_cheap_side   = whale_result$cheap$side %||% NA_character_,
      whale_cheap_entry  = whale_result$cheap$entry_price %||% NA_real_,
      whale_cheap_exit   = whale_result$cheap$exit_price %||% NA_real_,
      whale_cheap_exit_type = whale_result$cheap$exit_type %||% NA_character_,
      whale_cheap_qty    = whale_result$cheap$qty %||% 0L,
      whale_cheap_pnl    = whale_cheap_pnl,

      # 跟庄-贵端
      whale_exp_traded   = whale_result$expensive$traded,
      whale_exp_side     = whale_result$expensive$side %||% NA_character_,
      whale_exp_entry    = whale_result$expensive$entry_price %||% NA_real_,
      whale_exp_exit     = whale_result$expensive$exit_price %||% NA_real_,
      whale_exp_exit_type = whale_result$expensive$exit_type %||% NA_character_,
      whale_exp_qty      = whale_result$expensive$qty %||% 0L,
      whale_exp_pnl      = whale_exp_pnl,

      # 总实盘 PnL
      total_real_pnl     = total_real_pnl
    )
  }

  # ── 转换为 data.frame ──
  results_df <- do.call(rbind, lapply(results, function(r) {
    as.data.frame(r, stringsAsFactors = FALSE)
  }))
  results_df$cum_pnl <- cumsum(results_df$total_real_pnl)

  # ── 汇总报告 ──
  n_whale_rounds <- sum(results_df$regime == "WHALE_ACTIVE")
  n_switches <- length(regime_ctx$switch_log)
  total_pnl <- sum(results_df$total_real_pnl)

  probe_real_pnl <- sum(results_df$grid_pnl[!results_df$grid_is_paper]) +
                    sum(results_df$tail_pnl[!results_df$tail_is_paper])
  whale_total_pnl <- sum(results_df$whale_cheap_pnl) + sum(results_df$whale_exp_pnl)

  message(sprintf(paste0(
    "\n═══ 探针-跟庄回测完成 ═══\n",
    "总轮次: %d | WHALE_ACTIVE 轮: %d (%.1f%%)\n",
    "Regime 切换次数: %d\n",
    "Whale 信号轮次: %d (tail_range >= threshold)\n",
    "探针实盘 PnL: %.4f\n",
    "跟庄总 PnL: %.4f (便宜端: %.4f, 贵端: %.4f)\n",
    "系统总实盘 PnL: %.4f"
  ), n, n_whale_rounds, n_whale_rounds / n * 100,
     n_switches,
     sum(results_df$is_whale_signal, na.rm = TRUE),
     probe_real_pnl,
     whale_total_pnl,
     sum(results_df$whale_cheap_pnl),
     sum(results_df$whale_exp_pnl),
     total_pnl))

  # 打印 regime 切换日志
  if (n_switches > 0) {
    message("\nRegime 切换日志:")
    for (sw in regime_ctx$switch_log) {
      message(sprintf("  %s: %s → %s (whale_count=%d, tail_range=%.3f)",
                      sw$round_id, sw$from, sw$to,
                      sw$whale_count %||% 0L,
                      sw$tail_range %||% 0))
    }
  }

  results_df
}
