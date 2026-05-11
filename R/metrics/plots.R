# ══════════════════════════════════════════════════════════════
#  plots.R — 可视化图表
# ══════════════════════════════════════════════════════════════

#' 累计 PnL 曲线
#' @param results_df 回测结果 data.frame
#' @param save_path 保存路径 (NULL 不保存)
plot_cum_pnl <- function(results_df, save_path = NULL) {
  trades <- results_df[results_df$traded, ]
  if (nrow(trades) == 0) { message("无交易数据"); return(invisible(NULL)) }

  cum <- cumsum(trades$pnl)
  idx <- seq_along(cum)

  if (requireNamespace("ggplot2", quietly = TRUE)) {
    library(ggplot2)
    p <- ggplot(data.frame(x = idx, y = cum), aes(x, y)) +
      geom_line(color = "#2196F3", linewidth = 0.8) +
      geom_hline(yintercept = 0, linetype = "dashed", color = "grey50") +
      labs(title = "累计 PnL 曲线", x = "交易序号", y = "累计 PnL") +
      theme_minimal()
    if (!is.null(save_path)) ggsave(save_path, p, width = 10, height = 5)
    print(p)
  } else {
    plot(idx, cum, type = "l", col = "blue",
         main = "累计 PnL 曲线", xlab = "交易序号", ylab = "累计 PnL")
    abline(h = 0, lty = 2, col = "grey50")
    if (!is.null(save_path)) dev.copy(png, save_path, width = 1000, height = 500)
  }
}

#' 回撤曲线
plot_drawdown <- function(results_df, save_path = NULL) {
  trades <- results_df[results_df$traded, ]
  if (nrow(trades) == 0) return(invisible(NULL))

  cum <- cumsum(trades$pnl)
  peak <- cummax(cum)
  dd <- peak - cum
  idx <- seq_along(dd)

  if (requireNamespace("ggplot2", quietly = TRUE)) {
    library(ggplot2)
    p <- ggplot(data.frame(x = idx, y = dd), aes(x, y)) +
      geom_area(fill = "#f44336", alpha = 0.3) +
      geom_line(color = "#f44336", linewidth = 0.5) +
      labs(title = "回撤曲线", x = "交易序号", y = "回撤") +
      theme_minimal()
    if (!is.null(save_path)) ggsave(save_path, p, width = 10, height = 4)
    print(p)
  } else {
    plot(idx, dd, type = "l", col = "red",
         main = "回撤曲线", xlab = "交易序号", ylab = "回撤")
  }
}

#' PnL 分布直方图
plot_pnl_dist <- function(results_df, save_path = NULL) {
  trades <- results_df[results_df$traded, ]
  if (nrow(trades) == 0) return(invisible(NULL))

  if (requireNamespace("ggplot2", quietly = TRUE)) {
    library(ggplot2)
    p <- ggplot(trades, aes(x = pnl)) +
      geom_histogram(bins = 50, fill = "#4CAF50", alpha = 0.7, color = "white") +
      geom_vline(xintercept = 0, linetype = "dashed", color = "red") +
      labs(title = "每笔 PnL 分布", x = "PnL", y = "频次") +
      theme_minimal()
    if (!is.null(save_path)) ggsave(save_path, p, width = 8, height = 5)
    print(p)
  } else {
    hist(trades$pnl, breaks = 50, col = "lightgreen",
         main = "每笔 PnL 分布", xlab = "PnL")
    abline(v = 0, lty = 2, col = "red")
  }
}

#' 按小时汇总 PnL 柱状图
plot_hourly_pnl <- function(results_df, save_path = NULL) {
  trades <- results_df[results_df$traded, ]
  if (nrow(trades) == 0) return(invisible(NULL))
  trades$hour <- as.integer(format(trades$entry_time, "%H"))
  hourly <- aggregate(pnl ~ hour, data = trades, FUN = sum)

  if (requireNamespace("ggplot2", quietly = TRUE)) {
    library(ggplot2)
    p <- ggplot(hourly, aes(x = factor(hour), y = pnl,
                            fill = ifelse(pnl >= 0, "盈利", "亏损"))) +
      geom_col() +
      scale_fill_manual(values = c("盈利" = "#4CAF50", "亏损" = "#f44336")) +
      labs(title = "按小时 PnL", x = "小时 (UTC)", y = "总 PnL", fill = "") +
      theme_minimal()
    if (!is.null(save_path)) ggsave(save_path, p, width = 10, height = 5)
    print(p)
  } else {
    barplot(hourly$pnl, names.arg = hourly$hour,
            col = ifelse(hourly$pnl >= 0, "green", "red"),
            main = "按小时 PnL", xlab = "小时", ylab = "总 PnL")
  }
}

#' 极性 vs PnL 散点图
plot_polarity_vs_pnl <- function(results_df, save_path = NULL) {
  trades <- results_df[results_df$traded & !is.na(results_df$polarity), ]
  if (nrow(trades) == 0) return(invisible(NULL))

  if (requireNamespace("ggplot2", quietly = TRUE)) {
    library(ggplot2)
    p <- ggplot(trades, aes(x = polarity, y = pnl)) +
      geom_point(alpha = 0.4, color = "#9C27B0") +
      geom_hline(yintercept = 0, linetype = "dashed") +
      geom_smooth(method = "lm", se = TRUE, color = "#FF9800") +
      labs(title = "极性 vs PnL", x = "极性 |up_mid - 0.5|", y = "PnL") +
      theme_minimal()
    if (!is.null(save_path)) ggsave(save_path, p, width = 8, height = 5)
    print(p)
  } else {
    plot(trades$polarity, trades$pnl, pch = 16, col = rgb(0.6, 0.2, 0.8, 0.4),
         main = "极性 vs PnL", xlab = "极性", ylab = "PnL")
    abline(h = 0, lty = 2)
  }
}

#' 入场时间分布
plot_entry_timing <- function(results_df, save_path = NULL) {
  trades <- results_df[results_df$traded & !is.na(results_df$elapsed_entry), ]
  if (nrow(trades) == 0) return(invisible(NULL))

  if (requireNamespace("ggplot2", quietly = TRUE)) {
    library(ggplot2)
    p <- ggplot(trades, aes(x = elapsed_entry)) +
      geom_histogram(bins = 40, fill = "#00BCD4", alpha = 0.7, color = "white") +
      labs(title = "入场耗时分布", x = "入场耗时 (秒)", y = "频次") +
      theme_minimal()
    if (!is.null(save_path)) ggsave(save_path, p, width = 8, height = 5)
    print(p)
  } else {
    hist(trades$elapsed_entry, breaks = 40, col = "cyan",
         main = "入场耗时分布", xlab = "入场耗时 (秒)")
  }
}

#' 扫参对比: 多条累计 PnL 曲线叠加
#' @param results_list list, 每个元素 list(label=, results_df=)
#' @param param_label 扫描参数名称文本
#' @param save_path 保存路径
plot_sweep_comparison <- function(results_list, param_label = "", save_path = NULL) {
  if (!requireNamespace("ggplot2", quietly = TRUE)) {
    message("ggplot2 不可用，跳过扫参对比图")
    return(invisible(NULL))
  }
  library(ggplot2)

  all_frames <- list()
  for (item in results_list) {
    trades <- item$results_df[item$results_df$traded, ]
    if (nrow(trades) == 0) next
    cum <- cumsum(trades$pnl)
    all_frames[[length(all_frames) + 1]] <- data.frame(
      idx   = seq_along(cum),
      pnl   = cum,
      group = as.character(item$label),
      stringsAsFactors = FALSE
    )
  }
  if (length(all_frames) == 0) {
    message("无交易数据，跳过扫参对比图")
    return(invisible(NULL))
  }

  df <- do.call(rbind, all_frames)
  title_text <- if (nzchar(param_label)) {
    paste0("扫参对比: ", param_label)
  } else {
    "扫参对比 PnL 曲线"
  }

  p <- ggplot(df, aes(x = idx, y = pnl, color = group)) +
    geom_line(linewidth = 0.8, alpha = 0.85) +
    geom_hline(yintercept = 0, linetype = "dashed", color = "grey50") +
    labs(title = title_text, x = "交易序号", y = "累计 PnL", color = param_label) +
    theme_minimal() +
    theme(legend.position = "bottom")
  if (!is.null(save_path)) ggsave(save_path, p, width = 12, height = 6)
  print(p)
}
