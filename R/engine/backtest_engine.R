# ══════════════════════════════════════════════════════════════
#  backtest_engine.R — 单轮回测引擎
# ══════════════════════════════════════════════════════════════

source("R/utils/helpers.R", local = FALSE)
source("R/engine/fill_model.R", local = FALSE)
source("R/engine/factor_filters.R", local = FALSE)

new_result_template <- function(cfg, round_id = NA_character_) {
  result <- list(
    round_id      = round_id,
    traded        = FALSE,
    skip_reason   = NA_character_,
    state_path    = "WAIT_ROUND>IDLE",
    side          = NA_character_,
    entry_price   = NA_real_,
    entry_time    = as.POSIXct(NA, tz = "UTC"),
    entry_trigger_price = NA_real_,
    exit_price    = NA_real_,
    exit_time     = as.POSIXct(NA, tz = "UTC"),
    exit_trigger_price = NA_real_,
    sell_order_price = NA_real_,
    sell_post_time = as.POSIXct(NA, tz = "UTC"),
    sell_post_elapsed = NA_real_,
    exit_type     = NA_character_,
    qty           = cfg$trade_shares,
    pnl           = 0,
    polarity      = NA_real_,
    er_value      = NA_real_,
    hurst_value   = NA_real_,
    elapsed_entry = NA_real_,
    elapsed_exit  = NA_real_,
    entry_window_start = cfg$entry_window_start %||% 0,
    entry_window_end = cfg$entry_window_end %||% cfg$entry_timeout %||% cfg$round_duration %||% 300,
    sell_window_start = cfg$sell_window_start %||% 0,
    sell_window_end = cfg$sell_window_end %||% cfg$sell_timeout %||% cfg$round_duration %||% 300,
    sell_window_start_effective = NA_real_,
    sell_window_end_effective = NA_real_
  )
  result
}

maybe_skip_round <- function(df, cfg, round_id = NA_character_, result = NULL) {
  if (is.null(result)) {
    result <- new_result_template(cfg, round_id = round_id)
  }

  if (nrow(df) == 0) {
    result$skip_reason <- "empty_data"
    return(result)
  }

  if (cfg$polarity_filter_enabled) {
    delay_rows <- which(df$elapsed >= cfg$polarity_delay)
    if (length(delay_rows) == 0) {
      result$skip_reason <- "no_data_after_delay"
      return(result)
    }
    first_row <- delay_rows[1]
    mid_val <- df$up_midpoint[first_row]
    if (is.na(mid_val)) {
      result$skip_reason <- "no_midpoint"
      return(result)
    }
    polarity <- calc_polarity(mid_val)
    result$polarity <- polarity
    if (polarity > cfg$polarity_max) {
      result$skip_reason <- "polarity"
      return(result)
    }
  }

  # ── 2. 宵禁过滤 ──────────────────────────────
  if (cfg$curfew_enabled && !is.na(round_id)) {
    hour <- as.integer(format(parse_round_time(paste0(round_id, ".csv")), "%H"))
    if (hour %in% cfg$curfew_hours) {
      result$skip_reason <- "curfew"
      return(result)
    }
  }

  if (isTRUE(cfg$weekday_filter_enabled) && !is.na(round_id)) {
    weekday_value <- as.POSIXlt(parse_round_time(paste0(round_id, ".csv")), tz = "UTC")$wday
    is_weekend <- weekday_value %in% c(0, 6)
    weekday_mode <- cfg$weekday_mode %||% "all"
    if (identical(weekday_mode, "weekdays") && is_weekend) {
      result$skip_reason <- "weekday_filter"
      return(result)
    }
    if (identical(weekday_mode, "weekends") && !is_weekend) {
      result$skip_reason <- "weekday_filter"
      return(result)
    }
  }

  # ── 3. 数据空窗过滤 ──────────────────────────
  if (cfg$data_gap_check_enabled) {
    early_bba <- df[df$elapsed <= cfg$gap_threshold &
                    df$event_type == "best_bid_ask", ]
    if (nrow(early_bba) == 0) {
      result$skip_reason <- "data_gap"
      return(result)
    }
  }

  NULL
}

#' 回测单个轮次（经典网格逻辑）
run_one_round_classic <- function(df, cfg, round_id = NA_character_, history_rounds = list()) {
  result <- new_result_template(cfg, round_id = round_id)

  entry_window_start <- cfg$entry_window_start %||% 0
  entry_window_end <- cfg$entry_window_end %||% cfg$entry_timeout %||% cfg$round_duration %||% 300
  sell_window_start <- cfg$sell_window_start %||% 0
  sell_window_end <- cfg$sell_window_end %||% cfg$sell_timeout %||% cfg$round_duration %||% 300

  result$entry_window_start <- entry_window_start
  result$entry_window_end <- entry_window_end
  result$sell_window_start <- sell_window_start
  result$sell_window_end <- sell_window_end

  skip_result <- maybe_skip_round(df, cfg, round_id = round_id, result = result)
  if (!is.null(skip_result)) {
    return(skip_result)
  }

  P_ENTRY  <- cfg$entry_price
  P_PROFIT <- cfg$profit_price
  fill_cfg <- get_fill_model_config(cfg)

  # ── 4. Entry 阶段 (对齐实盘: 双边同时挂单 + BTC Guard 一击必杀) ──
  entry_df <- df
  if (cfg$entry_timeout_enabled) {
    entry_df <- df[df$elapsed >= entry_window_start & df$elapsed <= entry_window_end, ]
  }

  entry_row <- NA_integer_
  entry_side <- NA_character_
  entry_fill <- NULL
  btc_killed <- FALSE   # 实盘 one-strike: 一旦触发，本轮永久撤单

  for (i in seq_len(nrow(entry_df))) {
    # ── 实盘逻辑: 订单已在簿上，先检查成交 ──
    # 实盘中成交检测与 BTC guard 并行运行;
    # 同一时刻如果订单被撮合，成交优先于 guard 撤单
    if (!btc_killed) {
      fill_candidate <- simulate_entry_fill(
        entry_df[i, , drop = FALSE],
        entry_price = P_ENTRY,
        fill_model = fill_cfg$entry_fill_model,
        row_idx = i
      )
      if (isTRUE(fill_candidate$triggered)) {
        entry_row <- i
        entry_side <- fill_candidate$side
        entry_fill <- fill_candidate
        break
      }
    }

    # ── BTC Guard 一击必杀 (One-Strike) ──
    # 实盘: btc_killed = TRUE → 永久停止下单，不可恢复
    if (isTRUE(cfg$btc_guard_enabled) && !btc_killed) {
      b_diff <- entry_df$btc_diff[i]
      if (!is.na(b_diff) && abs(b_diff) >= cfg$btc_diff_max) {
        btc_killed <- TRUE
        break
      }
    }
  }

  if (is.na(entry_row)) {
    result$skip_reason <- if (btc_killed) "btc_guard" else "no_entry"
    return(result)
  }

  result$side          <- entry_side
  result$entry_price   <- entry_fill$fill_price
  result$entry_time    <- entry_df$timestamp[entry_row]
  result$entry_trigger_price <- entry_fill$trigger_price
  result$elapsed_entry <- entry_df$elapsed[entry_row]

  factor_skip <- apply_factor_filters(history_rounds, cfg, side = entry_side, result = result)
  if (!is.null(factor_skip)) {
    return(factor_skip)
  }

  result$traded        <- TRUE
  result$state_path    <- paste(result$state_path, "READY", "POSITION", sep = ">")

  # ── 5. Selling 阶段 ──────────────────────────
  # 实盘口径：入场后先等待 cooldown，再开始监控止盈
  entry_elapsed <- entry_df$elapsed[entry_row]
  sell_start <- entry_elapsed + cfg$cooldown
  time_stop_deadline <- NULL
  if (isTRUE(cfg$time_stop_enabled) && !is.null(cfg$time_stop_after_entry)) {
    time_stop_deadline <- entry_elapsed + cfg$time_stop_after_entry
  }
  sell_post_snapshot <- last_valid_price_before(
    df,
    value_col = bid_col <- if (entry_side == "up") "up_best_bid" else "down_best_bid",
    cutoff_elapsed = sell_start,
    start_elapsed = entry_elapsed
  )

  result$sell_order_price <- P_PROFIT
  result$sell_post_time <- if (!is.na(sell_post_snapshot$timestamp)) sell_post_snapshot$timestamp else entry_df$timestamp[entry_row]
  result$sell_post_elapsed <- sell_start
  result$state_path <- paste(result$state_path, "SELLING", sep = ">")

  sell_deadline_info <- resolve_sell_deadline(
    round_duration = cfg$round_duration %||% cfg$settle_wait %||% 300,
    settle_cutoff = cfg$settle_wait,
    sell_timeout_enabled = cfg$sell_timeout_enabled,
    sell_window_start = sell_window_start,
    sell_window_end = sell_window_end,
    sell_timeout_remaining = cfg$sell_timeout_remaining,
    sell_timeout = cfg$sell_timeout
  )
  effective_sell_start <- max(sell_start, sell_deadline_info$window_start)
  sell_deadline <- sell_deadline_info$deadline
  effective_deadline <- sell_deadline
  effective_exit_type <- sell_deadline_info$exit_type_on_timeout
  if (!is.null(time_stop_deadline)) {
    effective_deadline <- min(time_stop_deadline, sell_deadline)
    if (time_stop_deadline <= sell_deadline) {
      effective_exit_type <- "time_stop"
    }
  }
  result$sell_window_start_effective <- effective_sell_start
  result$sell_window_end_effective <- effective_deadline
  sell_df <- df[df$elapsed >= effective_sell_start & df$elapsed <= effective_deadline, ]

  exit_row <- NA_integer_
  stoploss_row <- NA_integer_

  for (i in seq_len(nrow(sell_df))) {
    # ── 获利检测: GTC 限价卖单 (bid >= P_PROFIT 触发成交) ──
    bid_val <- sell_df[[bid_col]][i]
    if (!is.na(bid_val) && bid_val >= P_PROFIT) {
      exit_row <- i
      break
    }
    # ── BTC 止损检测 (对齐实盘 _btc_stoploss_watchdog) ──
    if (isTRUE(cfg$btc_stoploss_enabled)) {
      b_diff <- sell_df$btc_diff[i]
      if (!is.na(b_diff) && abs(b_diff) >= (cfg$btc_diff_stoploss %||% Inf)) {
        stoploss_row <- i
        break
      }
    }
  }

  timeout_slippage <- cfg$timeout_slippage %||% 0

  if (!is.na(exit_row)) {
    # 正常获利
    observed_bid <- sell_df[[bid_col]][exit_row]
    result$exit_price  <- simulate_sell_fill_price(
      target_price = P_PROFIT,
      observed_bid = observed_bid,
      fill_model = fill_cfg$sell_fill_model
    )
    result$exit_time   <- sell_df$timestamp[exit_row]
    result$exit_trigger_price <- observed_bid
    result$exit_type   <- "profit"
    result$elapsed_exit <- sell_df$elapsed[exit_row]
  } else if (!is.na(stoploss_row)) {
    # ── BTC 止损: 撤销 GTC，执行 FOK 市价卖出 ──
    observed_bid <- sell_df[[bid_col]][stoploss_row]
    result$exit_price <- simulate_forced_sell_price(
      observed_bid = if (!is.na(observed_bid) && observed_bid > 0) observed_bid else P_ENTRY,
      slippage = timeout_slippage
    )
    result$exit_time   <- sell_df$timestamp[stoploss_row]
    result$exit_trigger_price <- observed_bid
    result$exit_type   <- "btc_stoploss"
    result$elapsed_exit <- sell_df$elapsed[stoploss_row]
  } else {
    # ── 超时/结算: 撤销 GTC，执行 FOK 市价卖出 ──
    timeout_snapshot <- last_valid_price_before(
      df,
      value_col = bid_col,
      cutoff_elapsed = effective_deadline,
      start_elapsed = entry_elapsed
    )

    if (!is.na(timeout_snapshot$price)) {
      result$exit_price <- simulate_forced_sell_price(
        observed_bid = timeout_snapshot$price,
        slippage = timeout_slippage
      )
      result$exit_trigger_price <- timeout_snapshot$price
      result$exit_time <- timeout_snapshot$timestamp
      result$elapsed_exit <- timeout_snapshot$elapsed
    } else {
      result$exit_price <- P_ENTRY
      result$exit_trigger_price <- NA_real_
      result$exit_time  <- entry_df$timestamp[entry_row]
      result$elapsed_exit <- entry_elapsed
    }
    result$exit_type <- effective_exit_type
  }

  result$state_path <- paste(result$state_path, "SETTLE", sep = ">")

  result$pnl <- (result$exit_price - result$entry_price) * result$qty
  result
}

resolve_trend_side_candidates <- function(trend_side) {
  if (identical(trend_side, "up")) {
    return("up")
  }
  if (identical(trend_side, "down")) {
    return("down")
  }
  c("up", "down")
}

pick_trend_entry <- function(row, entry_price, trend_side, row_idx = 1L) {
  sides <- resolve_trend_side_candidates(trend_side)
  candidates <- list()

  for (side in sides) {
    ask_col <- if (side == "up") "up_best_ask" else "down_best_ask"
    ask_val <- row[[ask_col]]
    if (!is.na(ask_val) && ask_val > 0 && ask_val >= entry_price) {
      candidates[[length(candidates) + 1L]] <- list(
        side = side,
        ask = ask_val
      )
    }
  }

  if (length(candidates) == 0) {
    return(NULL)
  }

  if (length(candidates) == 2L && identical(candidates[[1]]$ask, candidates[[2]]$ask)) {
    return(candidates[[if (row_idx %% 2L == 1L) 1L else 2L]])
  }

  asks <- vapply(candidates, function(x) x$ask, numeric(1))
  candidates[[which.min(asks)]]
}

run_one_round_trend <- function(df, cfg, round_id = NA_character_, history_rounds = list()) {
  result <- new_result_template(cfg, round_id = round_id)
  fill_cfg <- get_fill_model_config(cfg)
  entry_window_start <- cfg$entry_window_start %||% 0
  entry_window_end <- cfg$entry_window_end %||% cfg$entry_timeout %||% cfg$round_duration %||% 300
  sell_window_start <- cfg$sell_window_start %||% 0
  sell_window_end <- cfg$sell_window_end %||% cfg$sell_timeout %||% cfg$round_duration %||% 300

  result$entry_window_start <- entry_window_start
  result$entry_window_end <- entry_window_end
  result$sell_window_start <- sell_window_start
  result$sell_window_end <- sell_window_end

  skip_result <- maybe_skip_round(df, cfg, round_id = round_id, result = result)
  if (!is.null(skip_result)) {
    return(skip_result)
  }

  entry_df <- df[df$elapsed >= entry_window_start & df$elapsed <= entry_window_end, ]
  if (nrow(entry_df) == 0) {
    result$skip_reason <- "no_entry_window_data"
    return(result)
  }

  entry_row <- NA_integer_
  entry_fill <- NULL
  trend_side <- cfg$trend_side %||% "both"

  for (i in seq_len(nrow(entry_df))) {
    candidate <- pick_trend_entry(
      entry_df[i, , drop = FALSE],
      entry_price = cfg$trend_entry_price,
      trend_side = trend_side,
      row_idx = i
    )
    if (!is.null(candidate)) {
      entry_row <- i
      entry_fill <- candidate
      break
    }
  }

  if (is.na(entry_row)) {
    result$skip_reason <- "no_trend_entry"
    return(result)
  }

  entry_side <- entry_fill$side
  bid_col <- if (entry_side == "up") "up_best_bid" else "down_best_bid"

  result$side <- entry_side
  result$entry_price <- entry_fill$ask
  result$entry_time <- entry_df$timestamp[entry_row]
  result$entry_trigger_price <- entry_fill$ask
  result$elapsed_entry <- entry_df$elapsed[entry_row]

  factor_skip <- apply_factor_filters(history_rounds, cfg, side = entry_side, result = result)
  if (!is.null(factor_skip)) {
    return(factor_skip)
  }

  result$traded <- TRUE
  result$state_path <- paste(result$state_path, "TREND_BREAKOUT", "POSITION", sep = ">")

  sell_deadline_info <- resolve_sell_deadline(
    round_duration = cfg$round_duration %||% cfg$settle_wait %||% 300,
    settle_cutoff = cfg$settle_wait,
    sell_timeout_enabled = cfg$sell_timeout_enabled,
    sell_window_start = sell_window_start,
    sell_window_end = sell_window_end,
    sell_timeout_remaining = cfg$sell_timeout_remaining,
    sell_timeout = cfg$sell_timeout
  )
  effective_sell_start <- max(entry_df$elapsed[entry_row], sell_deadline_info$window_start)
  effective_deadline <- sell_deadline_info$deadline
  result$sell_window_start_effective <- effective_sell_start
  result$sell_window_end_effective <- effective_deadline
  result$sell_post_time <- entry_df$timestamp[entry_row]
  result$sell_post_elapsed <- effective_sell_start
  result$sell_order_price <- cfg$trend_profit_price
  result$state_path <- paste(result$state_path, "MONITORING", sep = ">")

  sell_df <- df[df$elapsed >= effective_sell_start & df$elapsed <= effective_deadline, ]
  exit_row <- NA_integer_
  exit_type <- NA_character_

  for (i in seq_len(nrow(sell_df))) {
    bid_val <- sell_df[[bid_col]][i]
    if (is.na(bid_val) || bid_val <= 0) {
      next
    }
    if (bid_val >= cfg$trend_profit_price) {
      exit_row <- i
      exit_type <- "trend_profit"
      break
    }
    if (bid_val <= cfg$trend_stop_price) {
      exit_row <- i
      exit_type <- "trend_stop"
      break
    }
  }

  timeout_slippage <- cfg$timeout_slippage %||% 0
  if (!is.na(exit_row)) {
    observed_bid <- sell_df[[bid_col]][exit_row]
    if (identical(exit_type, "trend_profit")) {
      result$exit_price <- simulate_sell_fill_price(
        target_price = cfg$trend_profit_price,
        observed_bid = observed_bid,
        fill_model = fill_cfg$sell_fill_model
      )
    } else {
      result$exit_price <- simulate_forced_sell_price(
        observed_bid = observed_bid,
        slippage = timeout_slippage
      )
    }
    result$exit_time <- sell_df$timestamp[exit_row]
    result$exit_trigger_price <- observed_bid
    result$exit_type <- exit_type
    result$elapsed_exit <- sell_df$elapsed[exit_row]
  } else {
    timeout_snapshot <- last_valid_price_before(
      df,
      value_col = bid_col,
      cutoff_elapsed = effective_deadline,
      start_elapsed = result$elapsed_entry
    )
    if (!is.na(timeout_snapshot$price)) {
      result$exit_price <- simulate_forced_sell_price(
        observed_bid = timeout_snapshot$price,
        slippage = timeout_slippage
      )
      result$exit_trigger_price <- timeout_snapshot$price
      result$exit_time <- timeout_snapshot$timestamp
      result$elapsed_exit <- timeout_snapshot$elapsed
    } else {
      result$exit_price <- result$entry_price
      result$exit_time <- result$entry_time
      result$elapsed_exit <- result$elapsed_entry
    }
    result$exit_type <- sell_deadline_info$exit_type_on_timeout
  }

  result$state_path <- paste(result$state_path, "SETTLE", sep = ">")
  result$pnl <- (result$exit_price - result$entry_price) * result$qty
  result
}

#' 回测单个轮次
#'
#' @param df 已清洗的 data.frame（需含 elapsed, up/down bid/ask/midpoint）
#' @param cfg 配置 list (由 load_config 返回)
#' @param round_id 轮次 ID (字符串，如 "2026-03-12_14-20-00")
#' @return list 包含本轮交易结果
run_one_round <- function(df, cfg, round_id = NA_character_, history_rounds = list()) {
  strategy_mode <- cfg$strategy_mode %||% "classic"
  if (identical(strategy_mode, "trend_breakout")) {
    return(run_one_round_trend(df, cfg, round_id = round_id, history_rounds = history_rounds))
  }
  run_one_round_classic(df, cfg, round_id = round_id, history_rounds = history_rounds)
}
