# ══════════════════════════════════════════════════════════════
#  fill_model.R — 回测撮合模型
# ══════════════════════════════════════════════════════════════

#' 解析卖出成交模型
#'
#' @param cfg 配置 list
#' @return list
get_fill_model_config <- function(cfg) {
  list(
    entry_fill_model = cfg$backtest_fill_model %||% "trader_bot_paper",
    sell_fill_model = cfg$sell_fill_model %||% "trader_bot_paper"
  )
}

#' 模拟买入限价单触发与成交
#'
#' trader_bot_paper 对齐 limit_hedge 的 paper_bot.py：
#' 买入限价单按 ask <= entry_price 触发，并以限价成交。
#'
#' @param row 单行 data.frame
#' @param entry_price 买入限价
#' @param fill_model 成交模型
#' @return list(triggered, side, trigger_price, fill_price)
simulate_entry_fill <- function(row, entry_price,
                                fill_model = "trader_bot_paper",
                                row_idx = 1L) {
  check_and_build <- function(side, trigger_price, fill_price) {
    list(
      triggered = TRUE,
      side = side,
      trigger_price = trigger_price,
      fill_price = fill_price
    )
  }

  no_fill <- list(
    triggered = FALSE,
    side = NA_character_,
    trigger_price = NA_real_,
    fill_price = NA_real_
  )

  # ── 对齐实盘: 双边同时挂单，选择更优侧成交 ──────────────
  # 实盘下两边 GTC 限价买单同时挂出，哪边 ask 更低先被撮合
  if (fill_model == "trader_bot_paper" || fill_model == "ask_bid") {
    up_ask <- row$up_best_ask
    dn_ask <- row$down_best_ask
    up_ok <- !is.na(up_ask) && up_ask > 0 && up_ask <= entry_price
    dn_ok <- !is.na(dn_ask) && dn_ask > 0 && dn_ask <= entry_price

    if (up_ok && dn_ok) {
      # 两边都能成交 → 选 ask 更低的 (CLOB 上先被撮合)
      if (up_ask < dn_ask) {
        return(check_and_build("up", up_ask, entry_price))
      } else if (dn_ask < up_ask) {
        return(check_and_build("down", dn_ask, entry_price))
      } else {
        # ask 相等 → 交替选择避免系统性偏向 UP
        side <- if (row_idx %% 2L == 1L) "up" else "down"
        ask_val <- if (side == "up") up_ask else dn_ask
        return(check_and_build(side, ask_val, entry_price))
      }
    } else if (up_ok) {
      return(check_and_build("up", up_ask, entry_price))
    } else if (dn_ok) {
      return(check_and_build("down", dn_ask, entry_price))
    }
    return(no_fill)
  }

  if (fill_model == "midpoint") {
    up_mid <- row$up_midpoint
    dn_mid <- row$down_midpoint
    up_ok <- !is.na(up_mid) && up_mid <= entry_price
    dn_ok <- !is.na(dn_mid) && dn_mid <= entry_price
    if (up_ok && dn_ok) {
      if (up_mid < dn_mid) return(check_and_build("up", up_mid, entry_price))
      if (dn_mid < up_mid) return(check_and_build("down", dn_mid, entry_price))
      side <- if (row_idx %% 2L == 1L) "up" else "down"
      return(check_and_build(side, if (side == "up") up_mid else dn_mid, entry_price))
    }
    if (up_ok) return(check_and_build("up", up_mid, entry_price))
    if (dn_ok) return(check_and_build("down", dn_mid, entry_price))
    return(no_fill)
  }

  # fallback: bid-based
  up_bid <- row$up_best_bid
  dn_bid <- row$down_best_bid
  up_ok <- !is.na(up_bid) && up_bid > 0 && up_bid <= entry_price
  dn_ok <- !is.na(dn_bid) && dn_bid > 0 && dn_bid <= entry_price
  if (up_ok && dn_ok) {
    if (up_bid < dn_bid) return(check_and_build("up", up_bid, entry_price))
    if (dn_bid < up_bid) return(check_and_build("down", dn_bid, entry_price))
    side <- if (row_idx %% 2L == 1L) "up" else "down"
    return(check_and_build(side, if (side == "up") up_bid else dn_bid, entry_price))
  }
  if (up_ok) return(check_and_build("up", up_bid, entry_price))
  if (dn_ok) return(check_and_build("down", dn_bid, entry_price))

  no_fill
}

#' 模拟卖出限价单成交
#'
#' Polymarket 的实际行为更接近价格改善：
#' 如果你挂 0.26 的卖单，而盘口直接跳到 0.40 / 0.50，
#' 实际成交不应仍然固定记为 0.26。
#'
#' @param target_price 挂单限价
#' @param observed_bid 当前观察到的 best bid
#' @param fill_model 成交模型
#' @return numeric
simulate_sell_fill_price <- function(target_price, observed_bid,
                                     fill_model = "trader_bot_paper") {
  if (is.na(observed_bid) || observed_bid <= 0) {
    return(target_price)
  }

  # 实盘: maker 的 GTC 卖单按限价成交 (CLOB 标准行为)
  # 即使 bid 跳到 0.50，maker 的 0.26 卖单仍以 0.26 成交
  if (fill_model == "trader_bot_paper") {
    return(target_price)
  }

  if (fill_model == "limit_price") {
    return(target_price)
  }

  if (fill_model == "price_improve") {
    return(max(target_price, observed_bid))
  }

  if (fill_model == "midpoint_cap") {
    return((target_price + observed_bid) / 2)
  }

  target_price
}

#' 模拟强制卖出 (超时 / BTC止损) 的市价成交
#'
#' 实盘中强制退出使用 FOK 市价卖出，以当前 bid 成交并可能有滑点。
#' 与正常获利退出 (GTC限价) 不同，强制退出不能保证限价成交。
#'
#' @param observed_bid 当前观察到的 best bid
#' @param slippage 滑点 (小数，如 0.01 = 1%)
#' @return numeric
simulate_forced_sell_price <- function(observed_bid, slippage = 0) {
  if (is.na(observed_bid) || observed_bid <= 0) return(NA_real_)
  if (slippage <= 0) return(observed_bid)
  observed_bid * (1 - slippage)
}