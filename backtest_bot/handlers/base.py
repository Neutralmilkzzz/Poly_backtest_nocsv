from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtest_bot.bot import BacktestTelegramBot

logger = logging.getLogger(__name__)


def render_main_menu() -> str:
    return "\n".join([
        "📊 回测菜单",
        "",
        "核心功能:",
        "  1  单因子扫参测试（引导式）",
        "  2  快速回测",
        "  3  🏆 参数排行榜",
        " 14  趋势参数扫描",
        " 15  ER 分桶分析",
        " 16  Hurst 分桶分析",
        "",
        "配置:",
        "  4  查看当前配置",
        "  5  修改参数",
        "  6  功能开关",
        "  7  设置样本数",
        "  8  切换策略底座",
        "",
        "数据:",
        " 13  🔄 同步缓存 (CSV→FST)",
        "",
        "任务:",
        "  9  任务状态",
        " 10  取消任务",
        " 11  最近输出",
        " 12  重置配置",
        "",
        "回复编号操作。",
    ])


async def handle_start(bot: BacktestTelegramBot, _: str) -> str:
    bot.client.set_pending_reply(bot.handle_menu_reply_bound)
    return render_main_menu()


async def handle_help(bot: BacktestTelegramBot, _: str) -> str:
    return "\n".join([
        "可用命令:",
        "/start - 打开菜单",
        "/config - 查看当前草稿配置",
        "/strategy - 查看并切换基础策略",
        "/analyze - 执行单因子控制变量参数扫描",
        "/trendscan - 运行趋势策略参数组合扫描，可带 entry/profit/stop/side",
        "/er - 运行 ER 分桶分析，可带 window/breaks",
        "/hurst - 运行 Hurst 分桶分析，可带 window/breaks",
        "/run - 调整参数后启动回测",
        "/leaderboard - 🏆 参数表现排行榜",
        "/toggle 模块名 - 开关功能",
        "/set 参数名 值 - 修改参数",
        "/max 数量 - 设置回测轮次数，all 表示全量",
        "/status - 查看运行状态",
        "/cancel - 取消当前任务",
        "/reset - 丢弃当前草稿",
        "/outputs - 查看最近一次输出目录",
        "/sync - 🔄 扫描 raw/ 新 CSV → 转 FST 缓存（自动筛除<500行）",
    ])


async def handle_leaderboard(bot: BacktestTelegramBot, text: str) -> str:
    """Show top N parameter configs by PnL."""
    from backtest_bot.leaderboard import format_leaderboard
    parts = text.strip().split()
    n = 10
    if len(parts) > 1:
        try:
            n = int(parts[1])
            n = max(1, min(n, 50))
        except ValueError:
            pass
    return format_leaderboard(n)


async def handle_menu_reply(bot: BacktestTelegramBot, text: str) -> str:
    choice = text.strip()

    from backtest_bot.handlers.config import handle_config, handle_toggle, handle_set, handle_max, handle_reset
    from backtest_bot.handlers.job import handle_run, handle_status, handle_cancel, handle_outputs
    from backtest_bot.handlers.strategy import handle_strategy
    from backtest_bot.handlers.analytics import handle_analyze, handle_trendscan, handle_er, handle_hurst

    if choice == "1":
        return await handle_analyze(bot, "/analyze")
    if choice == "2":
        return await handle_run(bot, "/run")
    if choice == "3":
        bot.client.set_pending_reply(None)
        return await handle_leaderboard(bot, "/leaderboard")
    if choice == "4":
        bot.client.set_pending_reply(None)
        return await handle_config(bot, "/config")
    if choice == "5":
        bot.client.set_pending_reply(bot.handle_set_reply_bound)
        return "请输入 参数名 值，例如: 买入价 0.24"
    if choice == "6":
        bot.client.set_pending_reply(bot.handle_toggle_reply_bound)
        from backtest_bot.handlers.config import MODULE_MAP
        return "请输入要切换的模块名:\n" + "\n".join(MODULE_MAP.keys())
    if choice == "7":
        bot.client.set_pending_reply(bot.handle_max_reply_bound)
        return "请输入样本数，或输入 all 跑全量。"
    if choice == "8":
        bot.client.set_pending_reply(None)
        return await handle_strategy(bot, "/strategy")
    if choice == "9":
        bot.client.set_pending_reply(None)
        return await handle_status(bot, "/status")
    if choice == "10":
        bot.client.set_pending_reply(None)
        return await handle_cancel(bot, "/cancel")
    if choice == "11":
        bot.client.set_pending_reply(None)
        return await handle_outputs(bot, "/outputs")
    if choice == "12":
        bot.client.set_pending_reply(None)
        return await handle_reset(bot, "/reset")
    if choice == "13":
        bot.client.set_pending_reply(None)
        from backtest_bot.handlers.sync import handle_sync
        return await handle_sync(bot, "/sync")
    if choice == "14":
        bot.client.set_pending_reply(None)
        return await handle_trendscan(bot, "/trendscan")
    if choice == "15":
        bot.client.set_pending_reply(None)
        return await handle_er(bot, "/er")
    if choice == "16":
        bot.client.set_pending_reply(None)
        return await handle_hurst(bot, "/hurst")

    bot.client.set_pending_reply(bot.handle_menu_reply_bound)
    return "未识别编号。\n\n" + render_main_menu()
