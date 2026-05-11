# ══════════════════════════════════════════════════════════════
#  data_cleaner.R — 数据清洗与标准化
# ══════════════════════════════════════════════════════════════

source("R/utils/helpers.R", local = FALSE)

#' 清洗单个轮次数据
#' @param df 从 read_round_csv 得到的 data.frame
#' @param round_start 轮次开始时间 (POSIXct)。NULL 时从文件名推断
#' @param fill_na 是否前向填充 NA
#' @return 清洗后的 data.frame，新增 elapsed 列
clean_round <- function(df, round_start = NULL, fill_na = TRUE) {
  if (is.null(round_start)) {
    if ("round_id" %in% names(df)) {
      round_start <- parse_round_time(paste0(df$round_id[1], ".csv"))
    } else {
      round_start <- df$timestamp[1]
    }
  }

  # 前向填充盘口字段
  if (fill_na) {
    fill_cols <- c("up_best_bid", "up_best_ask", "up_midpoint",
                   "down_best_bid", "down_best_ask", "down_midpoint")
    for (col in fill_cols) {
      if (col %in% names(df)) {
        df[[col]] <- forward_fill(df[[col]])
      }
    }
  }

  # 补充 midpoint（如果仍有 NA）
  if (all(c("up_best_bid", "up_best_ask") %in% names(df))) {
    na_mid <- is.na(df$up_midpoint)
    df$up_midpoint[na_mid] <- calc_midpoint(df$up_best_bid[na_mid],
                                             df$up_best_ask[na_mid])
  }
  if (all(c("down_best_bid", "down_best_ask") %in% names(df))) {
    na_mid <- is.na(df$down_midpoint)
    df$down_midpoint[na_mid] <- calc_midpoint(df$down_best_bid[na_mid],
                                               df$down_best_ask[na_mid])
  }

  # 计算 elapsed（相对于轮次开始的秒数）
  df$elapsed <- elapsed_seconds(df$timestamp, round_start)

  # 按时间排序
  df <- df[order(df$timestamp), ]
  rownames(df) <- NULL
  df
}

#' 批量清洗：对已有 round_id 列的合并 data.frame 逐轮清洗
#' @param df 带 round_id 的大 data.frame
#' @return 清洗后的 data.frame
clean_all_rounds <- function(df) {
  rounds <- split(df, df$round_id)
  cleaned <- lapply(rounds, function(rdf) {
    clean_round(rdf)
  })
  do.call(rbind, cleaned)
}
