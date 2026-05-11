# ══════════════════════════════════════════════════════════════
#  regime_detector.R — 微结构驱动的 Regime 状态机
# ══════════════════════════════════════════════════════════════
#
#  核心信号: 尾盘 (240-300s) 微结构异常
#  - tail_range: UP/DOWN midpoint 在尾盘的最大波动幅度
#  - tail_spread: 最后 30s 的平均盘口 spread
#  - 每轮判定: tail_range >= threshold → 本轮为 whale 信号
#  - 滑动窗口: 过去 N 轮中 whale 信号计数 → 判定 regime

#' 初始化 regime 状态
#'
#' @return list 初始 regime 上下文
new_regime_context <- function() {
  list(
    state             = "NORMAL",
    signal_history    = integer(0),    # 每轮 whale 信号 (0/1)
    tail_range_history = numeric(0),   # 每轮 tail_range 值
    rounds_in_whale   = 0L,
    switch_log        = list()
  )
}

#' 从单轮原始数据中计算尾盘微结构信号
#'
#' @param df 已清洗的 data.frame (含 elapsed, midpoint, bid/ask)
#' @param cfg 配置 list (需含 regime 子项)
#' @return list(tail_range, tail_spread, is_whale_signal)
compute_tail_signal <- function(df, cfg) {
  rcfg <- cfg$regime %||% list()
  tail_start     <- rcfg$tail_start %||% 240
  spread_start   <- rcfg$spread_start %||% 270
  range_threshold <- rcfg$range_threshold %||% 0.50

  result <- list(
    tail_range      = 0,
    tail_spread     = 0,
    is_whale_signal = FALSE,
    n_tail_ticks    = 0L
  )

  if (nrow(df) == 0 || !("elapsed" %in% names(df))) return(result)

  # Tail section (240-300s by default)
  tail_mask <- df$elapsed >= tail_start
  tail_df <- df[tail_mask, , drop = FALSE]
  result$n_tail_ticks <- nrow(tail_df)

  if (nrow(tail_df) < 5) return(result)

  # Tail range: max swing of either side's midpoint
  up_mid <- tail_df$up_midpoint[!is.na(tail_df$up_midpoint)]
  dn_mid <- tail_df$down_midpoint[!is.na(tail_df$down_midpoint)]

  up_range <- if (length(up_mid) > 1) max(up_mid) - min(up_mid) else 0
  dn_range <- if (length(dn_mid) > 1) max(dn_mid) - min(dn_mid) else 0
  result$tail_range <- max(up_range, dn_range)

  # Tail spread: mean spread in last 30s (270-300s)
  spread_mask <- df$elapsed >= spread_start
  spread_df <- df[spread_mask, , drop = FALSE]
  if (nrow(spread_df) > 0) {
    up_sp <- spread_df$up_best_ask - spread_df$up_best_bid
    dn_sp <- spread_df$down_best_ask - spread_df$down_best_bid
    up_sp <- up_sp[!is.na(up_sp)]
    dn_sp <- dn_sp[!is.na(dn_sp)]
    result$tail_spread <- max(
      if (length(up_sp) > 0) mean(up_sp) else 0,
      if (length(dn_sp) > 0) mean(dn_sp) else 0
    )
  }

  # Per-round whale signal
  result$is_whale_signal <- (result$tail_range >= range_threshold)

  result
}

#' 更新 regime 上下文 (基于微结构信号)
#'
#' @param ctx       当前 regime 上下文
#' @param df        本轮原始数据 data.frame
#' @param cfg       配置 list, 需含 regime 子项
#' @param round_id  当前轮次 ID
#' @return list 更新后的 regime 上下文
update_regime <- function(ctx, df, cfg, round_id = NA_character_) {

  rcfg <- cfg$regime %||% list()
  window_size       <- rcfg$window_size %||% 20L
  whale_count_threshold <- rcfg$whale_count %||% 7L
  normal_count_threshold <- rcfg$normal_count %||% 3L

  # ── 计算本轮尾盘信号 ──
  sig <- compute_tail_signal(df, cfg)

  # ── 追加到滑动窗口 ──
  ctx$signal_history <- c(ctx$signal_history, as.integer(sig$is_whale_signal))
  ctx$tail_range_history <- c(ctx$tail_range_history, sig$tail_range)

  if (length(ctx$signal_history) > window_size) {
    ctx$signal_history <- tail(ctx$signal_history, window_size)
    ctx$tail_range_history <- tail(ctx$tail_range_history, window_size)
  }

  # ── 计算窗口内 whale 信号计数 ──
  whale_count <- if (length(ctx$signal_history) >= window_size) {
    sum(ctx$signal_history)
  } else {
    NA_integer_
  }

  prev_state <- ctx$state

  # ── 状态转移 ──
  if (ctx$state == "NORMAL") {
    if (!is.na(whale_count) && whale_count >= whale_count_threshold) {
      ctx$state <- "WHALE_ACTIVE"
      ctx$rounds_in_whale <- 0L
    }
  } else if (ctx$state == "WHALE_ACTIVE") {
    ctx$rounds_in_whale <- ctx$rounds_in_whale + 1L
    if (!is.na(whale_count) && whale_count <= normal_count_threshold) {
      ctx$state <- "NORMAL"
      ctx$rounds_in_whale <- 0L
    }
  }

  # 记录状态切换
  if (ctx$state != prev_state) {
    ctx$switch_log[[length(ctx$switch_log) + 1L]] <- list(
      round_id    = round_id,
      from        = prev_state,
      to          = ctx$state,
      whale_count = whale_count,
      tail_range  = sig$tail_range
    )
  }

  # 附加快照
  ctx$whale_count    <- whale_count %||% NA_integer_
  ctx$tail_range     <- sig$tail_range
  ctx$tail_spread    <- sig$tail_spread
  ctx$is_whale_signal <- sig$is_whale_signal

  ctx
}
