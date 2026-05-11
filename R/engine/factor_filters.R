calc_efficiency_ratio <- function(series) {
  series <- series[!is.na(series)]
  if (length(series) < 2) return(NA_real_)
  path_length <- sum(abs(diff(series)))
  if (is.na(path_length) || path_length <= 0) return(NA_real_)
  abs(tail(series, 1) - series[1]) / path_length
}

build_history_window_df <- function(history_rounds,
                                    side,
                                    window_seconds,
                                    round_duration = 300) {
  if (length(history_rounds) == 0 || is.null(window_seconds) || is.na(window_seconds) || window_seconds <= 0) {
    return(data.frame())
  }

  midpoint_col <- if (identical(side, "down")) "down_midpoint" else "up_midpoint"
  if (!all(vapply(history_rounds, function(df) midpoint_col %in% names(df), logical(1)))) {
    return(data.frame())
  }

  history_frames <- vector("list", length(history_rounds))
  for (i in seq_along(history_rounds)) {
    df <- history_rounds[[i]]
    frame <- data.frame(
      history_elapsed = (i - 1) * round_duration + df$elapsed,
      midpoint = suppressWarnings(as.numeric(df[[midpoint_col]])),
      stringsAsFactors = FALSE
    )
    history_frames[[i]] <- frame
  }

  history_df <- do.call(rbind, history_frames)
  if (is.null(history_df) || nrow(history_df) == 0) {
    return(data.frame())
  }

  max_elapsed <- max(history_df$history_elapsed, na.rm = TRUE)
  min_elapsed <- max(0, max_elapsed - window_seconds)
  history_df[history_df$history_elapsed >= min_elapsed & history_df$history_elapsed <= max_elapsed, , drop = FALSE]
}

compute_opening_er <- function(history_rounds, side, window_seconds, round_duration = 300) {
  history_df <- build_history_window_df(history_rounds, side, window_seconds, round_duration = round_duration)
  if (nrow(history_df) < 2) return(NA_real_)
  calc_efficiency_ratio(history_df$midpoint)
}

compute_opening_hurst <- function(history_rounds, side, window_seconds, round_duration = 300) {
  if (!requireNamespace("pracma", quietly = TRUE)) {
    return(NA_real_)
  }

  history_df <- build_history_window_df(history_rounds, side, window_seconds, round_duration = round_duration)
  if (nrow(history_df) < 8) return(NA_real_)

  series <- history_df$midpoint
  series <- series[!is.na(series)]
  if (length(series) < 8) return(NA_real_)

  out <- tryCatch(pracma::hurstexp(series, display = FALSE), error = function(e) NULL)
  if (is.null(out)) return(NA_real_)

  candidates <- c(out$Hs, out$Hal, out$He, out$Ht)
  candidates <- candidates[is.finite(candidates)]
  if (length(candidates) == 0) return(NA_real_)
  candidates[1]
}

value_in_range <- function(value, min_value = NULL, max_value = NULL) {
  if (is.na(value)) return(FALSE)
  if (!is.null(min_value) && !is.na(min_value) && value < min_value) return(FALSE)
  if (!is.null(max_value) && !is.na(max_value) && value > max_value) return(FALSE)
  TRUE
}

apply_factor_filters <- function(history_rounds, cfg, side, result) {
  round_duration <- cfg$round_duration %||% 300
  result$er_value <- compute_opening_er(
    history_rounds = history_rounds,
    side = side,
    window_seconds = cfg$er_window_seconds,
    round_duration = round_duration
  )
  result$hurst_value <- compute_opening_hurst(
    history_rounds = history_rounds,
    side = side,
    window_seconds = cfg$hurst_window_seconds,
    round_duration = round_duration
  )

  if (isTRUE(cfg$er_filter_enabled)) {
    if (!value_in_range(result$er_value, cfg$er_min, cfg$er_max)) {
      result$skip_reason <- "er_filter"
      return(result)
    }
  }

  if (isTRUE(cfg$hurst_filter_enabled)) {
    if (!value_in_range(result$hurst_value, cfg$hurst_min, cfg$hurst_max)) {
      result$skip_reason <- "hurst_filter"
      return(result)
    }
  }

  NULL
}
