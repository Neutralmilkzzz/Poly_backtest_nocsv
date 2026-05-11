library(testthat)

source("R/utils/helpers.R", local = FALSE)
source("R/io/config_loader.R", local = FALSE)
source("R/engine/fill_model.R", local = FALSE)
source("R/engine/backtest_engine.R", local = FALSE)

make_round_df <- function(up_ask, up_bid, down_ask = NULL, down_bid = NULL) {
  n <- length(up_ask)
  timestamps <- as.POSIXct("2026-01-01 00:00:00", tz = "UTC") + seq_len(n) - 1
  data.frame(
    timestamp = timestamps,
    elapsed = seq(0, by = 1, length.out = n),
    event_type = rep("best_bid_ask", n),
    up_best_ask = up_ask,
    up_best_bid = up_bid,
    up_midpoint = (up_ask + up_bid) / 2,
    down_best_ask = down_ask %||% rep(NA_real_, n),
    down_best_bid = down_bid %||% rep(NA_real_, n),
    down_midpoint = rep(NA_real_, n),
    btc_diff = rep(0, n)
  )
}

test_that("trend breakout captures profit on up side", {
  cfg <- load_config()
  cfg$strategy_mode <- "trend_breakout"
  cfg$trend_side <- "up"
  cfg$trend_entry_price <- 0.60
  cfg$trend_profit_price <- 0.80
  cfg$trend_stop_price <- 0.50
  cfg$curfew_enabled <- FALSE

  df <- make_round_df(
    up_ask = c(0.55, 0.60, 0.66, 0.82),
    up_bid = c(0.54, 0.59, 0.64, 0.81)
  )

  res <- run_one_round(df, cfg, round_id = "2026-01-01_00-00-00")

  expect_true(res$traded)
  expect_equal(res$side, "up")
  expect_equal(res$entry_price, 0.60)
  expect_equal(res$exit_type, "trend_profit")
  expect_equal(res$exit_price, 0.81)
  expect_gt(res$pnl, 0)
})

test_that("trend breakout stops out when bid falls through stop line", {
  cfg <- load_config()
  cfg$strategy_mode <- "trend_breakout"
  cfg$trend_side <- "up"
  cfg$trend_entry_price <- 0.60
  cfg$trend_profit_price <- 0.80
  cfg$trend_stop_price <- 0.50
  cfg$curfew_enabled <- FALSE

  df <- make_round_df(
    up_ask = c(0.58, 0.62, 0.57, 0.49),
    up_bid = c(0.57, 0.61, 0.55, 0.48)
  )

  res <- run_one_round(df, cfg, round_id = "2026-01-01_00-00-00")

  expect_true(res$traded)
  expect_equal(res$exit_type, "trend_stop")
  expect_equal(res$exit_price, 0.48)
  expect_lt(res$pnl, 0)
})

test_that("trend breakout with both sides picks the first side that breaks out", {
  cfg <- load_config()
  cfg$strategy_mode <- "trend_breakout"
  cfg$trend_side <- "both"
  cfg$trend_entry_price <- 0.60
  cfg$trend_profit_price <- 0.80
  cfg$trend_stop_price <- 0.50
  cfg$curfew_enabled <- FALSE

  df <- make_round_df(
    up_ask = c(0.55, 0.58, 0.61, 0.79, 0.83),
    up_bid = c(0.54, 0.57, 0.60, 0.78, 0.82),
    down_ask = c(0.56, 0.65, 0.66, 0.67, 0.68),
    down_bid = c(0.55, 0.64, 0.65, 0.66, 0.67)
  )

  res <- run_one_round(df, cfg, round_id = "2026-01-01_00-00-00")

  expect_true(res$traded)
  expect_equal(res$side, "down")
  expect_equal(res$entry_price, 0.65)
})
