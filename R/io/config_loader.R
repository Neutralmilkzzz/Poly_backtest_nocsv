# ══════════════════════════════════════════════════════════════
#  config_loader.R — YAML 配置加载器
# ══════════════════════════════════════════════════════════════

DEFAULTS <- list(
  strategy_mode     = "classic",

  # 核心参数
  entry_price       = 0.25,
  profit_price      = 0.26,
  trade_shares      = 100,
  initial_capital   = 1000,

  # 趋势突破策略
  trend_side        = "both",
  trend_entry_price = 0.60,
  trend_profit_price = 0.80,
  trend_stop_price  = 0.50,

  # ER / Hurst 因子分析
  er_filter_enabled = FALSE,
  er_window_seconds = 30,
  er_min = 0,
  er_max = 1,
  hurst_filter_enabled = FALSE,
  hurst_window_seconds = 60,
  hurst_min = 0,
  hurst_max = 1,

 # 风控开关
  polarity_filter_enabled  = FALSE,
  curfew_enabled           = TRUE,
  data_gap_check_enabled   = FALSE,
  entry_timeout_enabled    = TRUE,
  sell_timeout_enabled     = TRUE,
  time_stop_enabled        = FALSE,
  btc_guard_enabled        = FALSE,
  btc_stoploss_enabled     = FALSE,
  weekday_filter_enabled   = FALSE,
  weekday_mode             = "all",
  atr_filter_enabled       = FALSE,

  # 风控阈值
  polarity_max     = 0.35,
  polarity_delay   = 2,
  btc_diff_max     = 50,
  btc_diff_stoploss = 80,
  round_duration   = 300,
  entry_window_start = 0,
  entry_window_end = 90,
  entry_timeout    = 90,
  sell_window_start = 0,
  sell_window_end = 180,
  time_stop_after_entry = NULL,
  sell_timeout_remaining = 120,
  settle_wait      = 300,
  cooldown         = 8,
  gap_threshold    = 7,
  curfew_hours     = c(20),

  # 回测专用
  backtest_fill_model = "trader_bot_paper",
  sell_fill_model     = "trader_bot_paper"
)

#' 加载配置文件，缺失字段用默认值补全
#' @param path YAML 文件路径（可选）
#' @return named list
load_config <- function(path = NULL) {
  cfg <- DEFAULTS
  user_cfg <- list()
  if (!is.null(path) && file.exists(path)) {
    if (!requireNamespace("yaml", quietly = TRUE)) {
      stop("需要安装 yaml 包: install.packages('yaml')")
    }
    user_cfg <- yaml::read_yaml(path)
    # 用户值覆盖默认值
    for (nm in names(user_cfg)) {
      cfg[[nm]] <- user_cfg[[nm]]
    }
  }

  if (is.null(cfg$entry_window_start)) {
    cfg$entry_window_start <- 0
  }

  if (is.null(cfg$entry_window_end)) {
    cfg$entry_window_end <- cfg$entry_timeout %||% cfg$round_duration %||% 300
  }

  round_duration <- cfg$round_duration %||% cfg$settle_wait %||% 300
  cfg$entry_window_start <- max(0, min(cfg$entry_window_start, round_duration))
  cfg$entry_window_end <- max(0, min(cfg$entry_window_end, round_duration))

  if (cfg$entry_window_start > cfg$entry_window_end) {
    stop("entry_window_start 不能大于 entry_window_end")
  }

  # 兼容旧脚本：仍然暴露 entry_timeout，语义等同于 entry_window_end。
  cfg$entry_timeout <- cfg$entry_window_end

  if (is.null(cfg$sell_window_start)) {
    cfg$sell_window_start <- 0
  }

  if (is.null(cfg$sell_window_end)) {
    if (!is.null(cfg$sell_timeout_remaining)) {
      cfg$sell_window_end <- round_duration - cfg$sell_timeout_remaining
    } else if (!is.null(cfg$sell_timeout)) {
      cfg$sell_window_end <- cfg$sell_timeout
    } else {
      cfg$sell_window_end <- cfg$settle_wait %||% round_duration
    }
  }

  cfg$sell_window_start <- max(0, min(cfg$sell_window_start, round_duration))
  cfg$sell_window_end <- max(0, min(cfg$sell_window_end, round_duration))

  if (cfg$sell_window_start > cfg$sell_window_end) {
    stop("sell_window_start 不能大于 sell_window_end")
  }

  # 兼容旧脚本：保留旧字段，但语义由卖出窗口终点反推。
  cfg$sell_timeout <- cfg$sell_window_end
  cfg$sell_timeout_remaining <- round_duration - cfg$sell_window_end

  strategy_mode <- cfg$strategy_mode %||% "classic"
  valid_strategy_modes <- c("classic", "trend_breakout")
  if (!(strategy_mode %in% valid_strategy_modes)) {
    stop(sprintf("strategy_mode 必须是以下之一: %s", paste(valid_strategy_modes, collapse = ", ")))
  }
  cfg$strategy_mode <- strategy_mode

  trend_side <- cfg$trend_side %||% "both"
  valid_trend_sides <- c("up", "down", "both")
  if (!(trend_side %in% valid_trend_sides)) {
    stop(sprintf("trend_side 必须是以下之一: %s", paste(valid_trend_sides, collapse = ", ")))
  }
  cfg$trend_side <- trend_side

  if (!is.numeric(cfg$trend_entry_price) || length(cfg$trend_entry_price) != 1 || is.na(cfg$trend_entry_price)) {
    stop("trend_entry_price 必须是单个数值")
  }
  if (!is.numeric(cfg$trend_profit_price) || length(cfg$trend_profit_price) != 1 || is.na(cfg$trend_profit_price)) {
    stop("trend_profit_price 必须是单个数值")
  }
  if (!is.numeric(cfg$trend_stop_price) || length(cfg$trend_stop_price) != 1 || is.na(cfg$trend_stop_price)) {
    stop("trend_stop_price 必须是单个数值")
  }

  if (cfg$strategy_mode == "trend_breakout") {
    if (cfg$trend_profit_price <= cfg$trend_entry_price) {
      stop("trend_profit_price 必须大于 trend_entry_price")
    }
    if (cfg$trend_stop_price >= cfg$trend_entry_price) {
      stop("trend_stop_price 必须小于 trend_entry_price")
    }
  }

  if (!is.numeric(cfg$er_window_seconds) || length(cfg$er_window_seconds) != 1 || is.na(cfg$er_window_seconds) || cfg$er_window_seconds <= 1) {
    stop("er_window_seconds 必须是大于 1 的单个数值")
  }
  if (!is.numeric(cfg$er_min) || !is.numeric(cfg$er_max) || length(cfg$er_min) != 1 || length(cfg$er_max) != 1 || is.na(cfg$er_min) || is.na(cfg$er_max)) {
    stop("er_min / er_max 必须是单个数值")
  }
  if (cfg$er_min > cfg$er_max) {
    stop("er_min 不能大于 er_max")
  }
  if (!is.numeric(cfg$hurst_window_seconds) || length(cfg$hurst_window_seconds) != 1 || is.na(cfg$hurst_window_seconds) || cfg$hurst_window_seconds <= 4) {
    stop("hurst_window_seconds 必须是大于 4 的单个数值")
  }
  if (!is.numeric(cfg$hurst_min) || !is.numeric(cfg$hurst_max) || length(cfg$hurst_min) != 1 || length(cfg$hurst_max) != 1 || is.na(cfg$hurst_min) || is.na(cfg$hurst_max)) {
    stop("hurst_min / hurst_max 必须是单个数值")
  }
  if (cfg$hurst_min > cfg$hurst_max) {
    stop("hurst_min 不能大于 hurst_max")
  }

  weekday_mode <- cfg$weekday_mode %||% "all"
  valid_weekday_modes <- c("all", "weekdays", "weekends")
  if (!(weekday_mode %in% valid_weekday_modes)) {
    stop(sprintf("weekday_mode 必须是以下之一: %s", paste(valid_weekday_modes, collapse = ", ")))
  }
  cfg$weekday_mode <- weekday_mode

  cfg
}
