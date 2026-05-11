# ══════════════════════════════════════════════════════════════
#  runner.R — 批量回测运行器 (支持多核并行)
# ══════════════════════════════════════════════════════════════

source("R/io/config_loader.R",  local = FALSE)
source("R/io/data_reader.R",    local = FALSE)
source("R/io/data_cleaner.R",   local = FALSE)
source("R/io/cache_reader.R",   local = FALSE)
source("R/engine/fill_model.R", local = FALSE)
source("R/engine/backtest_engine.R", local = FALSE)

# ── 并行辅助 ──────────────────────────────
resolve_cores <- function(n_cores = 1L) {
  if (is.null(n_cores) || n_cores == 0) return(1L)
  available <- parallel::detectCores(logical = FALSE)
  if (is.na(available)) available <- 1L
  if (n_cores == -1L) return(max(1L, available - 1L))
  as.integer(max(1L, min(n_cores, available)))
}

# 源文件列表，用于 Windows PSOCK 集群初始化
.source_files <- c(
  "R/io/config_loader.R",
  "R/io/data_reader.R",
  "R/io/data_cleaner.R",
  "R/io/cache_reader.R",
  "R/engine/fill_model.R",
  "R/engine/factor_filters.R",
  "R/engine/backtest_engine.R"
)

run_parallel <- function(n, fn, cores) {
  if (.Platform$OS.type == "unix") {
    parallel::mclapply(seq_len(n), fn, mc.cores = cores)
  } else {
    cl <- parallel::makeCluster(cores)
    on.exit(parallel::stopCluster(cl), add = TRUE)
    wd <- getwd()
    parallel::clusterExport(cl, ".source_files", envir = environment())
    parallel::clusterCall(cl, function(wd, files) {
      setwd(wd)
      for (f in files) source(f, local = FALSE)
    }, wd = wd, files = .source_files)
    parallel::parLapply(cl, seq_len(n), fn)
  }
}

results_list_to_df <- function(results) {
  if (length(results) == 0) {
    return(data.frame())
  }

  results_df <- do.call(rbind, lapply(results, function(r) {
    r$entry_time <- as.character(r$entry_time)
    r$exit_time  <- as.character(r$exit_time)
    r$sell_post_time <- as.character(r$sell_post_time)
    as.data.frame(r, stringsAsFactors = FALSE)
  }))

  results_df$entry_time <- as.POSIXct(results_df$entry_time, tz = "UTC")
  results_df$exit_time  <- as.POSIXct(results_df$exit_time, tz = "UTC")
  results_df$sell_post_time <- as.POSIXct(results_df$sell_post_time, tz = "UTC")
  results_df$cum_pnl <- cumsum(results_df$pnl)
  results_df
}

prepare_rounds_data <- function(data_dir = "data/raw",
                                max_rounds = NULL,
                                use_latest = FALSE,
                                progress = 100,
                                use_cache = TRUE,
                                cache_dir = "data/cache/fst") {
  rounds <- list_rounds(data_dir)
  if (!is.null(max_rounds)) {
    if (use_latest) {
      rounds <- tail(rounds, max_rounds)
    } else {
      rounds <- head(rounds, max_rounds)
    }
  }

  n <- nrow(rounds)
  message(sprintf("预加载开始: %d 个轮次 (缓存: %s)", n, if (use_cache && fst_is_available()) "FST" else "CSV"))

  round_ids <- character(n)
  all_data <- vector("list", n)
  for (i in seq_len(n)) {
    if (progress > 0 && i %% progress == 0) {
      message(sprintf("  进度: %d / %d (%.0f%%)", i, n, i / n * 100))
    }
    round_ids[i] <- tools::file_path_sans_ext(basename(rounds$path[i]))
    df <- read_round_data(rounds$path[i], use_cache = use_cache, cache_dir = cache_dir)
    all_data[[i]] <- clean_round(df, round_start = rounds$round_time[i])
  }

  list(rounds = rounds, round_ids = round_ids, all_data = all_data)
}

run_backtest_preloaded <- function(preloaded,
                                   cfg,
                                   progress = 100,
                                   progress_label = "回测",
                                   n_cores = 1L) {
  n <- length(preloaded$all_data)
  cores <- resolve_cores(n_cores)
  max_history_seconds <- max(c(cfg$er_window_seconds %||% 0,
                               cfg$hurst_window_seconds %||% 0), na.rm = TRUE)
  round_duration <- cfg$round_duration %||% 300
  history_round_count <- max(0L, ceiling(max_history_seconds / round_duration))
  history_data <- vector("list", n)
  for (i in seq_len(n)) {
    start_idx <- max(1L, i - history_round_count)
    end_idx <- i - 1L
    history_data[[i]] <- if (end_idx >= start_idx) preloaded$all_data[start_idx:end_idx] else list()
  }

  if (cores > 1L) {
    message(sprintf("%s开始: %d 个轮次 (%d 核并行)", progress_label, n, cores))
    all_data <- preloaded$all_data
    round_ids <- preloaded$round_ids
    history_list <- history_data
    results <- run_parallel(n, function(i) {
      run_one_round(all_data[[i]], cfg, round_id = round_ids[i], history_rounds = history_list[[i]])
    }, cores)
  } else {
    message(sprintf("%s开始: %d 个轮次", progress_label, n))
    results <- vector("list", n)
    for (i in seq_len(n)) {
      if (progress > 0 && i %% progress == 0) {
        message(sprintf("  进度: %d / %d (%.0f%%)", i, n, i / n * 100))
      }
      results[[i]] <- run_one_round(
        preloaded$all_data[[i]],
        cfg,
        round_id = preloaded$round_ids[i],
        history_rounds = history_data[[i]]
      )
    }
  }

  results_df <- results_list_to_df(results)
  message(sprintf("%s完成: %d 笔交易, 总 PnL = %.4f",
                  progress_label, sum(results_df$traded), sum(results_df$pnl)))
  results_df
}

#' 运行全量回测
#'
#' @param data_dir CSV 目录路径
#' @param cfg 配置 list (可选，默认从 config/strategy.yaml 加载)
#' @param max_rounds 最大轮次数 (NULL = 全部)
#' @param progress 打印进度间隔 (每 N 轮)
#' @return data.frame 每行一轮
run_backtest <- function(data_dir = "data/raw",
                         cfg = NULL,
                         max_rounds = NULL,
                         use_latest = FALSE,
                         progress = 100,
                         use_cache = TRUE,
                         cache_dir = "data/cache/fst",
                         n_cores = 1L) {
  if (is.null(cfg)) cfg <- load_config("config/strategy.yaml")

  preloaded <- prepare_rounds_data(
    data_dir = data_dir,
    max_rounds = max_rounds,
    use_latest = use_latest,
    progress = progress,
    use_cache = use_cache,
    cache_dir = cache_dir
  )

  run_backtest_preloaded(preloaded, cfg,
                         progress = progress,
                         progress_label = "回测",
                         n_cores = n_cores)
}

#' 参数网格搜索
#'
#' @param param_grid data.frame 每行一组参数 (列名须与 cfg 字段名匹配)
#' @param data_dir CSV 目录路径
#' @param base_cfg 基础配置
#' @return list，每个元素为一组参数的回测结果
run_grid_search <- function(param_grid, data_dir = "data/raw", base_cfg = NULL,
                            use_cache = TRUE, cache_dir = "data/cache/fst",
                            n_cores = 1L) {
  if (is.null(base_cfg)) base_cfg <- load_config("config/strategy.yaml")

  # 预加载数据一次，所有参数组共享
  preloaded <- prepare_rounds_data(
    data_dir = data_dir,
    use_cache = use_cache,
    cache_dir = cache_dir
  )

  results <- vector("list", nrow(param_grid))
  for (g in seq_len(nrow(param_grid))) {
    cfg <- base_cfg
    for (nm in names(param_grid)) {
      cfg[[nm]] <- param_grid[[nm]][g]
    }
    message(sprintf("\n=== 参数组 %d / %d ===", g, nrow(param_grid)))
    results[[g]] <- list(
      params = as.list(param_grid[g, ]),
      results = run_backtest_preloaded(preloaded, cfg,
                                       progress = 0,
                                       progress_label = sprintf("参数组[%d]", g),
                                       n_cores = n_cores)
    )
  }
  results
}
