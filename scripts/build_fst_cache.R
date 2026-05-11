# ══════════════════════════════════════════════════════════════
#  build_fst_cache.R — CSV → FST 缓存生成器
# ══════════════════════════════════════════════════════════════
#
#  用法: 在项目根目录下运行
#    Rscript scripts/build_fst_cache.R
#    Rscript scripts/build_fst_cache.R --raw-dir data/raw
#    Rscript scripts/build_fst_cache.R --force          # 强制重建全部缓存
#    Rscript scripts/build_fst_cache.R --min-rows 500   # 最少行数阈值
# ══════════════════════════════════════════════════════════════

# 设工作目录为项目根
if (!interactive()) {
  args_all <- commandArgs(trailingOnly = FALSE)
  file_args <- args_all[grep("^--file=", args_all)]
  if (length(file_args) > 0) {
    script_path <- sub("^--file=", "", file_args[1])
    script_dir  <- dirname(script_path)
    setwd(file.path(script_dir, ".."))
  }
}

source("R/utils/helpers.R")

# ── 检查 fst 包是否可用 ────────────────
if (!requireNamespace("fst", quietly = TRUE)) {
  stop("缺少 fst 包。请运行: install.packages('fst')")
}
library(fst)

# ── 参数 ────────────────────────────────
args     <- commandArgs(trailingOnly = TRUE)
raw_dir  <- "data/raw"
cache_dir <- "data/cache/fst"
manifest_path <- "data/manifest.csv"
min_rows <- 500L
force_rebuild <- FALSE

REQUIRED_COLS <- c("timestamp", "up_best_bid", "up_best_ask", "up_midpoint",
                   "down_best_bid", "down_best_ask", "down_midpoint",
                   "event_type", "volume")

arg_i <- 1
while (arg_i <= length(args)) {
  a <- args[arg_i]
  if (a == "--raw-dir"  && arg_i < length(args)) { raw_dir   <- args[arg_i + 1]; arg_i <- arg_i + 2; next }
  if (a == "--cache-dir" && arg_i < length(args)) { cache_dir <- args[arg_i + 1]; arg_i <- arg_i + 2; next }
  if (a == "--min-rows" && arg_i < length(args))  { min_rows  <- as.integer(args[arg_i + 1]); arg_i <- arg_i + 2; next }
  if (a == "--force") { force_rebuild <- TRUE; arg_i <- arg_i + 1; next }
  arg_i <- arg_i + 1
}

# ── 创建输出目录 ────────────────────────
dir.create(cache_dir, recursive = TRUE, showWarnings = FALSE)
dir.create(dirname(manifest_path), recursive = TRUE, showWarnings = FALSE)

# ── 扫描 CSV ────────────────────────────
csv_pattern <- "^\\d{4}-\\d{2}-\\d{2}_.*\\.csv$"
csv_files   <- list.files(raw_dir, pattern = csv_pattern, full.names = FALSE)

if (length(csv_files) == 0) {
  stop("在 ", raw_dir, " 中未找到 CSV 文件")
}

message(sprintf("扫描到 %d 个 CSV 文件", length(csv_files)))

# ── 如果非强制重建，加载已有 manifest 做增量判断 ──
old_manifest <- NULL
if (!force_rebuild && file.exists(manifest_path)) {
  old_manifest <- read.csv(manifest_path, stringsAsFactors = FALSE)
}

# ── 逐文件处理 ──────────────────────────
manifest_rows <- vector("list", length(csv_files))

n_valid   <- 0L
n_reject  <- 0L
n_skipped <- 0L

for (i in seq_along(csv_files)) {
  fname     <- csv_files[i]
  csv_path  <- file.path(raw_dir, fname)
  base_name <- tools::file_path_sans_ext(fname)
  fst_name  <- paste0(base_name, ".fst")
  fst_path  <- file.path(cache_dir, fst_name)

  source_mtime <- as.character(file.mtime(csv_path))

  # ── 增量跳过: 缓存已存在 && mtime 没变 ──
  if (!force_rebuild && !is.null(old_manifest)) {
    prev <- old_manifest[old_manifest$source_file == fname, ]
    if (nrow(prev) == 1 &&
        prev$is_valid &&
        prev$source_mtime == source_mtime &&
        file.exists(fst_path)) {
      manifest_rows[[i]] <- prev
      n_skipped <- n_skipped + 1L
      next
    }
  }

  # ── 校验: 文件名可解析为时间 ──
  round_time <- tryCatch(parse_round_time(fname), error = function(e) NA)
  if (is.na(round_time)) {
    manifest_rows[[i]] <- data.frame(
      source_file = fname, row_count = NA_integer_, is_valid = FALSE,
      reject_reason = "bad_filename", cache_format = NA_character_,
      cache_file = NA_character_, converted_at = NA_character_,
      source_mtime = source_mtime, stringsAsFactors = FALSE
    )
    n_reject <- n_reject + 1L
    next
  }

  # ── 读取 CSV ──
  df <- tryCatch(read.csv(csv_path, stringsAsFactors = FALSE, nrows = -1L),
                 error = function(e) NULL)
  if (is.null(df)) {
    manifest_rows[[i]] <- data.frame(
      source_file = fname, row_count = NA_integer_, is_valid = FALSE,
      reject_reason = "read_error", cache_format = NA_character_,
      cache_file = NA_character_, converted_at = NA_character_,
      source_mtime = source_mtime, stringsAsFactors = FALSE
    )
    n_reject <- n_reject + 1L
    next
  }

  row_count <- nrow(df)

  # ── 校验: 行数 ──
  if (row_count < min_rows) {
    manifest_rows[[i]] <- data.frame(
      source_file = fname, row_count = row_count, is_valid = FALSE,
      reject_reason = "too_few_rows", cache_format = NA_character_,
      cache_file = NA_character_, converted_at = NA_character_,
      source_mtime = source_mtime, stringsAsFactors = FALSE
    )
    n_reject <- n_reject + 1L
    next
  }

  # ── 校验: 必要列 ──
  missing_cols <- setdiff(REQUIRED_COLS, names(df))
  if (length(missing_cols) > 0) {
    manifest_rows[[i]] <- data.frame(
      source_file = fname, row_count = row_count, is_valid = FALSE,
      reject_reason = "missing_columns", cache_format = NA_character_,
      cache_file = NA_character_, converted_at = NA_character_,
      source_mtime = source_mtime, stringsAsFactors = FALSE
    )
    n_reject <- n_reject + 1L
    next
  }

  # ── 校验: timestamp 可解析 ──
  ts_parsed <- tryCatch(
    as.POSIXct(df$timestamp[1:min(5, nrow(df))],
               format = "%Y-%m-%dT%H:%M:%OS", tz = "UTC"),
    error = function(e) rep(NA, 5)
  )
  if (all(is.na(ts_parsed))) {
    manifest_rows[[i]] <- data.frame(
      source_file = fname, row_count = row_count, is_valid = FALSE,
      reject_reason = "bad_timestamp", cache_format = NA_character_,
      cache_file = NA_character_, converted_at = NA_character_,
      source_mtime = source_mtime, stringsAsFactors = FALSE
    )
    n_reject <- n_reject + 1L
    next
  }

  # ── 标准化列类型（与 read_round_csv 一致）──
  df$timestamp <- as.POSIXct(df$timestamp,
                             format = "%Y-%m-%dT%H:%M:%OS", tz = "UTC")
  num_cols <- c("up_best_bid", "up_best_ask", "up_midpoint",
                "down_best_bid", "down_best_ask", "down_midpoint", "volume")
  for (col in num_cols) {
    if (col %in% names(df)) df[[col]] <- as.numeric(df[[col]])
  }

  # ── 写入 FST ──
  write_fst(df, fst_path, compress = 50)
  n_valid <- n_valid + 1L

  manifest_rows[[i]] <- data.frame(
    source_file = fname, row_count = row_count, is_valid = TRUE,
    reject_reason = NA_character_, cache_format = "fst",
    cache_file = fst_name, converted_at = as.character(Sys.time()),
    source_mtime = source_mtime, stringsAsFactors = FALSE
  )

  if ((n_valid + n_reject) %% 50 == 0) {
    message(sprintf("  进度: %d / %d", n_valid + n_reject + n_skipped, length(csv_files)))
  }
}

# ── 写 manifest ─────────────────────────
manifest <- do.call(rbind, manifest_rows)
write.csv(manifest, manifest_path, row.names = FALSE)

# ── 汇总 ────────────────────────────────
message("══════════════════════════════════════")
message(sprintf("总文件:   %d", length(csv_files)))
message(sprintf("有效转换: %d", n_valid))
message(sprintf("跳过:     %d (缓存已是最新)", n_skipped))
message(sprintf("拒绝:     %d", n_reject))
message(sprintf("FST 缓存: %s", cache_dir))
message(sprintf("Manifest:  %s", manifest_path))
message("══════════════════════════════════════")

if (n_reject > 0) {
  rejected <- manifest[!manifest$is_valid, ]
  message("\n被拒绝的文件:")
  for (r in seq_len(nrow(rejected))) {
    message(sprintf("  %s — %s", rejected$source_file[r], rejected$reject_reason[r]))
  }
}
