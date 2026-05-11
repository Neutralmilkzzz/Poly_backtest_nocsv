# ══════════════════════════════════════════════════════════════
#  whale_follow.R — 跟庄策略：双边波动率套利
# ══════════════════════════════════════════════════════════════
#
#  便宜端: ask ≤ cheap_threshold → 市价买入，挂 cheap_take_profit 止盈
#  贵  端: bid ≥ expensive_threshold → 挂 dip_buy_price 限价接盘
#  结  算: 赢方 = $1.00/share，输方 = $0.00/share

source("R/utils/helpers.R", local = FALSE)

#' 跟庄策略结果模板
new_whale_result <- function(leg, round_id = NA_character_) {
  list(
    round_id     = round_id,
    leg          = leg,           # "cheap" | "expensive"
    traded       = FALSE,
    side         = NA_character_, # "up" | "down"
    entry_price  = NA_real_,
    entry_elapsed = NA_real_,
    exit_price   = NA_real_,
    exit_elapsed = NA_real_,
    exit_type    = NA_character_, # "take_profit" | "settle_win" | "settle_lose"
    qty          = 0L,
    pnl          = 0
  )
}

#' 判定结算方向: btc_diff > 0 → UP 赢, 否则 DOWN 赢
#'
#' @param df 本轮全量 tick 数据
#' @return "up" | "down" | NA
determine_settlement <- function(df) {
  last_btc <- tail(df$btc_diff[!is.na(df$btc_diff)], 1)
  if (length(last_btc) == 0) return(NA_character_)
  if (last_btc > 0) "up" else "down"
}

#' 运行跟庄策略单轮
#'
#' @param df   已清洗的 data.frame (含 elapsed, up/down bid/ask, btc_diff)
#' @param cfg  配置 list (需含 whale 子项)
#' @param round_id 轮次 ID
#' @return list(cheap = whale_result, expensive = whale_result)
run_one_round_whale <- function(df, cfg, round_id = NA_character_) {

  wcfg <- cfg$whale %||% list()
  cheap_threshold     <- wcfg$cheap_threshold %||% 0.15
  cheap_take_profit   <- wcfg$cheap_take_profit %||% 0.40
  cheap_budget        <- wcfg$cheap_budget %||% 10
  expensive_threshold <- wcfg$expensive_threshold %||% 0.80
  dip_buy_price       <- wcfg$dip_buy_price %||% 0.65
  dip_take_profit     <- wcfg$dip_take_profit %||% 0.85
  expensive_budget    <- wcfg$expensive_budget %||% 10

  cheap_res    <- new_whale_result("cheap", round_id)
  expensive_res <- new_whale_result("expensive", round_id)

  if (nrow(df) == 0) {
    return(list(cheap = cheap_res, expensive = expensive_res))
  }

  settlement_side <- determine_settlement(df)

  # ── 逐 tick 模拟 ─────────────────────────────────────
  cheap_entered     <- FALSE
  cheap_exited      <- FALSE
  expensive_entered <- FALSE
  expensive_exited  <- FALSE

  for (i in seq_len(nrow(df))) {
    row <- df[i, , drop = FALSE]

    # ── 便宜端: 寻找 ask ≤ cheap_threshold 的一侧 ──
    if (!cheap_entered && !cheap_exited) {
      up_ask <- row$up_best_ask
      dn_ask <- row$down_best_ask
      up_ok <- !is.na(up_ask) && up_ask > 0 && up_ask <= cheap_threshold
      dn_ok <- !is.na(dn_ask) && dn_ask > 0 && dn_ask <= cheap_threshold

      if (up_ok || dn_ok) {
        # 选更便宜的一侧
        if (up_ok && dn_ok) {
          cheap_side <- if (up_ask <= dn_ask) "up" else "down"
          cheap_price <- min(up_ask, dn_ask)
        } else if (up_ok) {
          cheap_side <- "up"
          cheap_price <- up_ask
        } else {
          cheap_side <- "down"
          cheap_price <- dn_ask
        }
        cheap_qty <- max(1L, floor(cheap_budget / cheap_price))
        cheap_res$traded <- TRUE
        cheap_res$side   <- cheap_side
        cheap_res$entry_price <- cheap_price
        cheap_res$entry_elapsed <- row$elapsed
        cheap_res$qty <- cheap_qty
        cheap_entered <- TRUE
      }
    }

    # ── 便宜端: 止盈检测 ──
    if (cheap_entered && !cheap_exited) {
      bid_col <- if (cheap_res$side == "up") "up_best_bid" else "down_best_bid"
      bid_val <- row[[bid_col]]
      if (!is.na(bid_val) && bid_val >= cheap_take_profit) {
        cheap_res$exit_price <- bid_val
        cheap_res$exit_elapsed <- row$elapsed
        cheap_res$exit_type <- "take_profit"
        cheap_res$pnl <- (cheap_res$exit_price - cheap_res$entry_price) * cheap_res$qty
        cheap_exited <- TRUE
      }
    }

    # ── 贵端: 寻找 bid ≥ expensive_threshold 的一侧，挂限价接盘 ──
    if (!expensive_entered && !expensive_exited) {
      up_bid <- row$up_best_bid
      dn_bid <- row$down_best_bid
      up_exp <- !is.na(up_bid) && up_bid >= expensive_threshold
      dn_exp <- !is.na(dn_bid) && dn_bid >= expensive_threshold

      if (up_exp || dn_exp) {
        # 选更贵的一侧（它更可能被砸下来）
        if (up_exp && dn_exp) {
          exp_side <- if (up_bid >= dn_bid) "up" else "down"
        } else if (up_exp) {
          exp_side <- "up"
        } else {
          exp_side <- "down"
        }

        # 挂限价: 我们选择该侧的 ask ≤ dip_buy_price 时成交
        # 此处只标记"已选定贵端"，等价格跌到 dip_buy_price 再实际入场
        expensive_res$side <- exp_side
        expensive_entered <- "pending"  # 标记为待成交
      }
    }

    # ── 贵端: 限价单成交检测 (ask 跌到 dip_buy_price) ──
    if (identical(expensive_entered, "pending")) {
      ask_col <- if (expensive_res$side == "up") "up_best_ask" else "down_best_ask"
      ask_val <- row[[ask_col]]
      if (!is.na(ask_val) && ask_val > 0 && ask_val <= dip_buy_price) {
        exp_qty <- max(1L, floor(expensive_budget / dip_buy_price))
        expensive_res$traded <- TRUE
        expensive_res$entry_price <- dip_buy_price
        expensive_res$entry_elapsed <- row$elapsed
        expensive_res$qty <- exp_qty
        expensive_entered <- TRUE
      }
    }

    # ── 贵端: 止盈检测 ──
    if (isTRUE(expensive_entered) && !expensive_exited) {
      bid_col <- if (expensive_res$side == "up") "up_best_bid" else "down_best_bid"
      bid_val <- row[[bid_col]]
      if (!is.na(bid_val) && bid_val >= dip_take_profit) {
        expensive_res$exit_price <- bid_val
        expensive_res$exit_elapsed <- row$elapsed
        expensive_res$exit_type <- "take_profit"
        expensive_res$pnl <- (expensive_res$exit_price - expensive_res$entry_price) * expensive_res$qty
        expensive_exited <- TRUE
      }
    }

    # 两边都已退出，无需继续
    if (cheap_exited && (expensive_exited || !isTRUE(expensive_entered))) {
      break
    }
  }

  # ── 结算: 未止盈的持仓按 settlement 结算 ──
  if (cheap_entered && !cheap_exited) {
    if (!is.na(settlement_side) && settlement_side == cheap_res$side) {
      cheap_res$exit_price <- 1.0
      cheap_res$exit_type <- "settle_win"
    } else {
      cheap_res$exit_price <- 0.0
      cheap_res$exit_type <- "settle_lose"
    }
    cheap_res$exit_elapsed <- max(df$elapsed, na.rm = TRUE)
    cheap_res$pnl <- (cheap_res$exit_price - cheap_res$entry_price) * cheap_res$qty
  }

  if (isTRUE(expensive_entered) && !expensive_exited) {
    if (!is.na(settlement_side) && settlement_side == expensive_res$side) {
      expensive_res$exit_price <- 1.0
      expensive_res$exit_type <- "settle_win"
    } else {
      expensive_res$exit_price <- 0.0
      expensive_res$exit_type <- "settle_lose"
    }
    expensive_res$exit_elapsed <- max(df$elapsed, na.rm = TRUE)
    expensive_res$pnl <- (expensive_res$exit_price - expensive_res$entry_price) * expensive_res$qty
  }

  list(cheap = cheap_res, expensive = expensive_res)
}
