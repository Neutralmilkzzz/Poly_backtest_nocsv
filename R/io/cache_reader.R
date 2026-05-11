# ══════════════════════════════════════════════════════════════
#  cache_reader.R — FST 缓存读取（带 CSV 回退）
# ══════════════════════════════════════════════════════════════

source("R/utils/helpers.R", local = FALSE)

# ── fst 可用性检测（启动时做一次）──────────
.fst_available <- requireNamespace("fst", quietly = TRUE)

#' 检测 fst 包是否可用
#' @return logical
fst_is_available <- function() .fst_available

#' 根据 CSV 路径推导对应的 FST 缓存路径
#' @param csv_path  原始 CSV 路径，例如 "data/raw/2026-03-17_06-30-00.csv"
#' @param cache_dir FST 缓存目录，默认 "data/cache/fst"
#' @return FST 文件路径（不保证存在）
fst_path_for <- function(csv_path, cache_dir = "data/cache/fst") {
  base <- tools::file_path_sans_ext(basename(csv_path))
  file.path(cache_dir, paste0(base, ".fst"))
}

#' 读取单个轮次数据：优先 FST，回退 CSV
#'
#' @param csv_path  原始 CSV 路径
#' @param use_cache 是否使用 FST 缓存 (TRUE/FALSE)
#' @param cache_dir FST 缓存目录
#' @return data.frame（列类型已标准化）
read_round_data <- function(csv_path,
                            use_cache = TRUE,
                            cache_dir = "data/cache/fst") {
  if (use_cache && .fst_available) {
    fst_file <- fst_path_for(csv_path, cache_dir)
    if (file.exists(fst_file)) {
      return(fst::read_fst(fst_file))
    }
  }
  # 回退到 CSV
  read_round_csv(csv_path)
}

#' 批量读取多个轮次：优先 FST，回退 CSV
#'
#' @param paths     CSV 文件路径向量
#' @param use_cache 是否使用 FST 缓存
#' @param cache_dir FST 缓存目录
#' @param progress  是否打印进度
#' @return 合并的 data.frame，带 round_id 列
read_rounds_data <- function(paths,
                             use_cache = TRUE,
                             cache_dir = "data/cache/fst",
                             progress = TRUE) {
  dfs <- vector("list", length(paths))
  n_fst <- 0L
  n_csv <- 0L

  for (i in seq_along(paths)) {
    if (progress && i %% 100 == 0) {
      message(sprintf("  读取 %d / %d ...", i, length(paths)))
    }

    fst_hit <- FALSE
    if (use_cache && .fst_available) {
      fst_file <- fst_path_for(paths[i], cache_dir)
      if (file.exists(fst_file)) {
        df <- fst::read_fst(fst_file)
        fst_hit <- TRUE
        n_fst <- n_fst + 1L
      }
    }
    if (!fst_hit) {
      df <- read_round_csv(paths[i])
      n_csv <- n_csv + 1L
    }

    df$round_id <- tools::file_path_sans_ext(basename(paths[i]))
    dfs[[i]] <- df
  }

  if (progress) {
    message(sprintf("  读取完成: %d 来自 FST, %d 来自 CSV", n_fst, n_csv))
  }
  do.call(rbind, dfs)
}
