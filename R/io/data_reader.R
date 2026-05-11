# ══════════════════════════════════════════════════════════════
#  data_reader.R — 数据读取
# ══════════════════════════════════════════════════════════════

source("R/utils/helpers.R", local = FALSE)

#' 读取单个轮次 CSV
#' @param path CSV 文件路径
#' @return data.frame，timestamp 转为 POSIXct
read_round_csv <- function(path) {
  df <- read.csv(path, stringsAsFactors = FALSE)
  # 解析 ISO-8601 时间戳
  df$timestamp <- as.POSIXct(df$timestamp, format = "%Y-%m-%dT%H:%M:%OS", tz = "UTC")
  # 数值列确保为 numeric
  num_cols <- c("up_best_bid", "up_best_ask", "up_midpoint",
                "down_best_bid", "down_best_ask", "down_midpoint", "volume",
                "btc_diff")
  for (col in num_cols) {
    if (col %in% names(df)) {
      df[[col]] <- as.numeric(df[[col]])
    }
  }
  df
}

#' 列出 data/raw 目录下所有轮次文件，按时间排序
#' @param dir 目录路径
#' @return data.frame: path, round_time (POSIXct)
list_rounds <- function(dir = "data/raw") {
  files <- list.files(dir, pattern = "^\\d{4}-\\d{2}-\\d{2}_.*\\.csv$",
                      full.names = TRUE)
  if (length(files) == 0) stop("在 ", dir, " 中未找到 CSV 文件")
  round_times <- vapply(files, parse_round_time, FUN.VALUE = as.POSIXct(NA, tz = "UTC"))
  # 转回 POSIXct（vapply 返回 numeric）
  round_times <- as.POSIXct(round_times, origin = "1970-01-01", tz = "UTC")
  out <- data.frame(path = files, round_time = round_times, stringsAsFactors = FALSE)
  out <- out[order(out$round_time), ]
  rownames(out) <- NULL
  out
}

#' 批量读取多个轮次，返回合并的 data.frame（带 round_id 列）
#' @param paths 文件路径向量
#' @param progress 是否打印进度
#' @return data.frame
read_rounds <- function(paths, progress = TRUE) {
  dfs <- vector("list", length(paths))
  for (i in seq_along(paths)) {
    if (progress && i %% 100 == 0) {
      message(sprintf("  读取 %d / %d ...", i, length(paths)))
    }
    df <- read_round_csv(paths[i])
    df$round_id <- tools::file_path_sans_ext(basename(paths[i]))
    dfs[[i]] <- df
  }
  do.call(rbind, dfs)
}
