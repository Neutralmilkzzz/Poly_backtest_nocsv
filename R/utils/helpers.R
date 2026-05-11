# ══════════════════════════════════════════════════════════════
#  helpers.R — 通用工具函数
# ══════════════════════════════════════════════════════════════

#' 从文件名解析轮次开始时间
#' @param filename 文件名或完整路径，例如 "2026-03-12_14-20-00.csv"
#' @return POSIXct (UTC)
parse_round_time <- function(filename) {
  base <- tools::file_path_sans_ext(basename(filename))
  # "2026-03-12_14-20-00" → "2026-03-12 14:20:00"
  ts_str <- sub("^(\\d{4}-\\d{2}-\\d{2})_(\\d{2})-(\\d{2})-(\\d{2})$",
                "\\1 \\2:\\3:\\4", base)
  as.POSIXct(ts_str, tz = "UTC")
}

#' 向量前向填充 NA
#' @param x 任意向量
#' @return 同长度向量，NA 用前一个非 NA 值填充
forward_fill <- function(x) {
  if (all(is.na(x))) return(x)
  idx <- which(!is.na(x))
  if (idx[1] > 1) {
    # 开头的 NA 保留（没有前值可填）
  }
  for (i in seq_along(x)) {
    if (is.na(x[i]) && i > 1) x[i] <- x[i - 1]
  }
  x
}

#' 安全计算 midpoint
calc_midpoint <- function(bid, ask) {
  ifelse(!is.na(bid) & !is.na(ask), (bid + ask) / 2, NA_real_)
}

#' 计算极性（偏离 0.5 的程度）
calc_polarity <- function(up_mid) {
  abs(up_mid - 0.5)
}

#' 计算相对于起始时间的秒数
elapsed_seconds <- function(ts, start_ts) {
  as.numeric(difftime(ts, start_ts, units = "secs"))
}

#' 类似 Python 的空值回退
`%||%` <- function(x, y) {
  if (is.null(x) || length(x) == 0) y else x
}

#' 获取某个时刻之前最近一次有效快照价格
#'
#' @param df data.frame
#' @param value_col 价格列名
#' @param cutoff_elapsed 截止秒数（含）
#' @param start_elapsed 起始秒数（含）
#' @return list(price, timestamp, elapsed)
last_valid_price_before <- function(df, value_col, cutoff_elapsed,
                                    start_elapsed = -Inf) {
  window_df <- df[df$elapsed >= start_elapsed & df$elapsed <= cutoff_elapsed, ]
  if (nrow(window_df) == 0) {
    return(list(price = NA_real_, timestamp = as.POSIXct(NA, tz = "UTC"), elapsed = NA_real_))
  }

  values <- window_df[[value_col]]
  valid_idx <- which(!is.na(values) & values > 0)
  if (length(valid_idx) == 0) {
    return(list(price = NA_real_, timestamp = as.POSIXct(NA, tz = "UTC"), elapsed = NA_real_))
  }

  idx <- tail(valid_idx, 1)
  list(
    price = values[idx],
    timestamp = window_df$timestamp[idx],
    elapsed = window_df$elapsed[idx]
  )
}

#' 解析卖出超时截止秒数
#'
#' @param round_duration 轮次总长度（秒）
#' @param settle_cutoff 最晚结算秒数
#' @param sell_timeout_enabled 是否启用卖出超时
#' @param sell_window_start 卖出窗口起始秒数
#' @param sell_window_end 卖出窗口结束秒数
#' @param sell_timeout_remaining 距离轮次结束剩余多少秒触发 timeout
#' @param sell_timeout 旧版绝对秒数写法，保留兼容
#' @return list(window_start, deadline, exit_type_on_timeout)
resolve_sell_deadline <- function(round_duration,
                                  settle_cutoff,
                                  sell_timeout_enabled = TRUE,
                                  sell_window_start = NULL,
                                  sell_window_end = NULL,
                                  sell_timeout_remaining = NULL,
                                  sell_timeout = NULL) {
  if (!isTRUE(sell_timeout_enabled)) {
    return(list(window_start = 0, deadline = settle_cutoff, exit_type_on_timeout = "settle"))
  }

  timeout_point <- NULL
  if (!is.null(sell_window_end)) {
    timeout_point <- sell_window_end
  } else if (!is.null(sell_timeout_remaining)) {
    timeout_point <- round_duration - sell_timeout_remaining
  } else if (!is.null(sell_timeout)) {
    timeout_point <- sell_timeout
  }

  if (is.null(timeout_point) || is.na(timeout_point)) {
    timeout_point <- settle_cutoff
  }

  window_start <- sell_window_start %||% 0
  window_start <- max(0, min(window_start, round_duration))
  timeout_point <- max(0, min(timeout_point, round_duration))

  list(
    window_start = window_start,
    deadline = min(timeout_point, settle_cutoff),
    exit_type_on_timeout = "timeout"
  )
}
