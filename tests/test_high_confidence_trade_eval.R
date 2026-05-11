library(testthat)

source("projects/probability_calibration/high_confidence_trade_eval.R", local = FALSE)

make_round_df <- function(elapsed, up_midpoint, up_ask = NULL, down_ask = NULL) {
  ts0 <- as.POSIXct("2026-01-01 00:00:00", tz = "UTC")

  if (is.null(up_ask)) {
    up_ask <- pmin(1, up_midpoint + 0.01)
  }
  if (is.null(down_ask)) {
    down_ask <- pmin(1, (1 - up_midpoint) + 0.01)
  }

  data.frame(
    timestamp = ts0 + elapsed,
    elapsed = elapsed,
    up_best_bid = pmax(0, up_ask - 0.02),
    up_best_ask = up_ask,
    up_midpoint = up_midpoint,
    down_best_bid = pmax(0, down_ask - 0.02),
    down_best_ask = down_ask,
    down_midpoint = 1 - up_midpoint,
    stringsAsFactors = FALSE
  )
}

test_that("determine_final_side uses fallback settlement window when needed", {
  df <- make_round_df(
    elapsed = c(0, 60, 120, 240, 250),
    up_midpoint = c(0.52, 0.55, 0.48, 0.12, 0.08)
  )

  expect_equal(determine_final_side(df), "down")
})

test_that("extract_threshold_hits captures first UP threshold cross and pnl", {
  df <- make_round_df(
    elapsed = c(0, 60, 120, 240, 290),
    up_midpoint = c(0.55, 0.62, 0.82, 0.93, 0.95),
    up_ask = c(0.56, 0.63, 0.83, 0.94, 0.95),
    down_ask = c(0.46, 0.39, 0.19, 0.07, 0.05)
  )

  hits60 <- extract_threshold_hits(df, "up_round.csv", threshold = 0.6, first_only = TRUE)
  expect_equal(nrow(hits60), 1)
  expect_equal(hits60$side, "up")
  expect_equal(hits60$hit_elapsed, 60)
  expect_equal(hits60$entry_price, 0.63)
  expect_equal(hits60$win, 1L)
  expect_equal(hits60$pnl, 0.37)

  hits80_all <- extract_threshold_hits(df, "up_round.csv", threshold = 0.8, first_only = FALSE)
  expect_equal(nrow(hits80_all), 3)
  expect_true(all(hits80_all$side == "up"))
})

test_that("extract_threshold_hits recognizes DOWN trades from low UP midpoint", {
  df <- make_round_df(
    elapsed = c(0, 100, 200, 250),
    up_midpoint = c(0.48, 0.35, 0.18, 0.05),
    up_ask = c(0.49, 0.36, 0.19, 0.06),
    down_ask = c(0.53, 0.66, 0.83, 0.95)
  )

  hits80 <- extract_threshold_hits(df, "down_round.csv", threshold = 0.8, first_only = TRUE)
  expect_equal(nrow(hits80), 1)
  expect_equal(hits80$side, "down")
  expect_equal(hits80$implied_prob_selected_side, 0.82)
  expect_equal(hits80$entry_price, 0.83)
  expect_equal(hits80$final_side, "down")
  expect_equal(hits80$win, 1L)
  expect_equal(hits80$pnl, 0.17)
})

test_that("summarize_threshold_hits aggregates wins and pnl by threshold", {
  df <- make_round_df(
    elapsed = c(0, 60, 120, 240, 290),
    up_midpoint = c(0.55, 0.62, 0.82, 0.93, 0.95),
    up_ask = c(0.56, 0.63, 0.83, 0.94, 0.95),
    down_ask = c(0.46, 0.39, 0.19, 0.07, 0.05)
  )

  hits <- rbind(
    extract_threshold_hits(df, "round_a.csv", threshold = 0.9, first_only = TRUE),
    extract_threshold_hits(df, "round_a.csv", threshold = 0.8, first_only = TRUE),
    extract_threshold_hits(df, "round_a.csv", threshold = 0.6, first_only = TRUE)
  )
  summary <- summarize_threshold_hits(hits, thresholds = c(0.9, 0.8, 0.6))

  expect_equal(summary$opportunities, c(1L, 1L, 1L))
  expect_equal(summary$wins, c(1L, 1L, 1L))
  expect_equal(summary$losses, c(0L, 0L, 0L))
  expect_equal(summary$avg_entry_price, c(0.94, 0.83, 0.63))
  expect_equal(summary$total_pnl, c(0.06, 0.17, 0.37))
})
