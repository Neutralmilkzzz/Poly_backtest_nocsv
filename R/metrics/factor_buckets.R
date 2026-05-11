source("R/engine/factor_filters.R", local = FALSE)

summarize_factor_buckets <- function(trade_factor_df, factor_col, bucket_col) {
  if (nrow(trade_factor_df) == 0) {
    return(data.frame())
  }

  overall_win_rate <- mean(trade_factor_df$won)
  bucket_levels <- levels(trade_factor_df[[bucket_col]])
  rows <- lapply(bucket_levels, function(bucket) {
    bucket_df <- trade_factor_df[trade_factor_df[[bucket_col]] == bucket, ]
    if (nrow(bucket_df) == 0) {
      return(data.frame(
        bucket = bucket,
        n_trades = 0,
        win_rate_pct = NA_real_,
        total_pnl = NA_real_,
        avg_pnl = NA_real_,
        median_pnl = NA_real_,
        avg_factor = NA_real_,
        delta_win_rate_pct = NA_real_,
        p_value_win_rate = NA_real_,
        significant_5pct = FALSE,
        stringsAsFactors = FALSE
      ))
    }

    wins <- sum(bucket_df$won)
    p_val <- tryCatch(
      prop.test(
        x = c(wins, sum(trade_factor_df$won) - wins),
        n = c(nrow(bucket_df), nrow(trade_factor_df) - nrow(bucket_df))
      )$p.value,
      error = function(e) NA_real_
    )

    data.frame(
      bucket = bucket,
      n_trades = nrow(bucket_df),
      win_rate_pct = mean(bucket_df$won) * 100,
      total_pnl = sum(bucket_df$pnl),
      avg_pnl = mean(bucket_df$pnl),
      median_pnl = median(bucket_df$pnl),
      avg_factor = mean(bucket_df[[factor_col]]),
      delta_win_rate_pct = (mean(bucket_df$won) - overall_win_rate) * 100,
      p_value_win_rate = p_val,
      significant_5pct = !is.na(p_val) && p_val < 0.05,
      stringsAsFactors = FALSE
    )
  })

  do.call(rbind, rows)
}

generate_factor_bucket_reports <- function(results_df,
                                           data_dir,
                                           cfg,
                                           out_dir,
                                           use_cache = TRUE,
                                           cache_dir = "data/cache/fst") {
  trades <- results_df[results_df$traded, ]
  if (nrow(trades) == 0) {
    return(invisible(NULL))
  }

  rounds <- list_rounds(data_dir)
  round_map <- setNames(seq_len(nrow(rounds)), tools::file_path_sans_ext(basename(rounds$path)))
  loaded_rounds <- vector("list", nrow(rounds))
  for (i in seq_len(nrow(rounds))) {
    df <- read_round_data(rounds$path[i], use_cache = use_cache, cache_dir = cache_dir)
    loaded_rounds[[i]] <- clean_round(df, round_start = rounds$round_time[i])
  }
  max_history_seconds <- max(c(cfg$er_window_seconds %||% 0,
                               cfg$hurst_window_seconds %||% 0), na.rm = TRUE)
  round_duration <- cfg$round_duration %||% 300
  history_round_count <- max(0L, ceiling(max_history_seconds / round_duration))

  trade_rows <- vector("list", nrow(trades))
  for (i in seq_len(nrow(trades))) {
    round_id <- trades$round_id[i]
    round_idx <- round_map[[round_id]]
    if (is.null(round_idx) || is.na(round_idx)) next

    start_idx <- max(1L, round_idx - history_round_count)
    end_idx <- round_idx - 1L
    history_rounds <- if (end_idx >= start_idx) loaded_rounds[start_idx:end_idx] else list()

    er_value <- compute_opening_er(
      history_rounds,
      side = trades$side[i],
      window_seconds = cfg$er_window_seconds,
      round_duration = round_duration
    )
    hurst_value <- compute_opening_hurst(
      history_rounds,
      side = trades$side[i],
      window_seconds = cfg$hurst_window_seconds,
      round_duration = round_duration
    )

    trade_rows[[i]] <- data.frame(
      round_id = round_id,
      side = trades$side[i],
      pnl = trades$pnl[i],
      won = trades$pnl[i] > 0,
      er_value = er_value,
      hurst_value = hurst_value,
      stringsAsFactors = FALSE
    )
  }

  factor_df <- do.call(rbind, trade_rows)
  if (is.null(factor_df) || nrow(factor_df) == 0) {
    return(invisible(NULL))
  }

  dir.create(out_dir, recursive = TRUE, showWarnings = FALSE)

  er_df <- factor_df[!is.na(factor_df$er_value), ]
  if (nrow(er_df) > 0) {
    er_df$er_bucket <- cut(
      er_df$er_value,
      breaks = c(0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01),
      include.lowest = TRUE,
      right = FALSE
    )
    er_summary <- summarize_factor_buckets(er_df, "er_value", "er_bucket")
    names(er_summary)[1] <- "er_bucket"
    names(er_summary)[which(names(er_summary) == "avg_factor")] <- "avg_er"
    write.csv(er_df, file.path(out_dir, "er_trade_level.csv"), row.names = FALSE)
    write.csv(er_summary, file.path(out_dir, "er_bucket_summary.csv"), row.names = FALSE)
  }

  hurst_df <- factor_df[!is.na(factor_df$hurst_value), ]
  if (nrow(hurst_df) > 0) {
    hurst_df$hurst_bucket <- cut(
      hurst_df$hurst_value,
      breaks = c(0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.01),
      include.lowest = TRUE,
      right = FALSE
    )
    hurst_summary <- summarize_factor_buckets(hurst_df, "hurst_value", "hurst_bucket")
    names(hurst_summary)[1] <- "hurst_bucket"
    names(hurst_summary)[which(names(hurst_summary) == "avg_factor")] <- "avg_hurst"
    write.csv(hurst_df, file.path(out_dir, "hurst_trade_level.csv"), row.names = FALSE)
    write.csv(hurst_summary, file.path(out_dir, "hurst_bucket_summary.csv"), row.names = FALSE)
  }

  invisible(NULL)
}
