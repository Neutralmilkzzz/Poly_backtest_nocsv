from __future__ import annotations

from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from backtest_bot.bot import BacktestTelegramBot

logger = logging.getLogger(__name__)

PARAM_ALIASES = {
    "买入等待": "买入结束",
    "卖出等待": "卖出结束",
}

MODULE_ALIASES = {
    "买入超时": "买入窗口限制",
    "卖出超时": "卖出窗口限制",
}

MODULE_MAP = {
    "极性过滤": "polarity_filter_enabled",
    "宵禁": "curfew_enabled",
    "数据空窗": "data_gap_check_enabled",
    "买入窗口限制": "entry_timeout_enabled",
    "卖出窗口限制": "sell_timeout_enabled",
    "时间止损": "time_stop_enabled",
    "BTC撤单": "btc_guard_enabled",
    "BTC止损": "btc_stoploss_enabled",
    "ER过滤": "er_filter_enabled",
    "赫斯特过滤": "hurst_filter_enabled",
    "工作日过滤": "weekday_filter_enabled",
    "ATR过滤": "atr_filter_enabled",
}

PARAM_MAP = {
    "策略模式": ("strategy_mode", str),
    "趋势方向": ("trend_side", str),
    "买入价": ("entry_price", float),
    "卖出价": ("profit_price", float),
    "趋势买入价": ("trend_entry_price", float),
    "趋势止盈价": ("trend_profit_price", float),
    "趋势止损价": ("trend_stop_price", float),
    "每笔份额": ("trade_shares", int),
    "初始资金": ("initial_capital", float),
    "ER窗口": ("er_window_seconds", int),
    "ER下限": ("er_min", float),
    "ER上限": ("er_max", float),
    "Hurst窗口": ("hurst_window_seconds", int),
    "Hurst下限": ("hurst_min", float),
    "Hurst上限": ("hurst_max", float),
    "日期模式": ("weekday_mode", str),
    "极性阈值": ("polarity_max", float),
    "极性延迟": ("polarity_delay", int),
    "买入开始": ("entry_window_start", int),
    "买入结束": ("entry_window_end", int),
    "卖出开始": ("sell_window_start", int),
    "卖出结束": ("sell_window_end", int),
    "结算等待": ("settle_wait", int),
    "冷却时间": ("cooldown", int),
    "空窗阈值": ("gap_threshold", int),
    "时间止损窗口": ("time_stop_after_entry", int),
    "BTC撤单阈值": ("btc_diff_max", float),
    "BTC止损阈值": ("btc_diff_stoploss", float),
}

async def handle_config(bot: BacktestTelegramBot, _: str) -> str:
    merged = bot.runner._build_config(bot.draft_overrides, active_strategy=bot.active_strategy_name)
    module_lines = [
        f"{label}: {'开' if bool(merged.get(key)) else '关'}"
        for label, key in MODULE_MAP.items()
    ]
    param_lines = [
        f"{label}: {merged.get(key) if merged.get(key) is not None else '未设置'}"
        for label, (key, _) in PARAM_MAP.items()
    ]
    return "\n".join([
        f"当前策略底座: {bot.active_strategy_name}",
        "---",
        "当前回测草稿:",
        f"样本数: {'全部' if bot.max_rounds is None else bot.max_rounds}",
        "模块:",
        *module_lines,
        "参数:",
        *param_lines,
    ])

def _toggle_module(bot: BacktestTelegramBot, label: str) -> str:
    label = MODULE_ALIASES.get(label, label)
    key = MODULE_MAP.get(label)
    if key is None:
        return "模块名不存在。可选项:\n" + "\n".join(MODULE_MAP.keys())

    current_value = bool(bot.runner._build_config(bot.draft_overrides, bot.active_strategy_name).get(key))
    next_value = not current_value
    bot.draft_overrides[key] = next_value
    return f"{label} 已切换为 {'开' if next_value else '关'}。"

def _set_param(bot: BacktestTelegramBot, label: str, raw_value: str) -> str:
    label = PARAM_ALIASES.get(label, label)
    entry = PARAM_MAP.get(label)
    if entry is None:
        return "参数名不存在。可选项:\n" + "\n".join(PARAM_MAP.keys())

    key, caster = entry
    try:
        value = caster(raw_value)
    except ValueError:
        return f"参数值格式错误: {label} 需要 {caster.__name__}"

    if key == "strategy_mode" and value not in ("classic", "trend_breakout"):
        return "策略模式只支持 classic 或 trend_breakout"
    if key == "trend_side" and value not in ("up", "down", "both"):
        return "趋势方向只支持 up、down 或 both"
    if key == "weekday_mode" and value not in ("all", "weekdays", "weekends"):
        return "日期模式只支持 all、weekdays、weekends"

    bot.draft_overrides[key] = value
    return f"{label} 已更新为 {value}。"

def _set_max_rounds(bot: BacktestTelegramBot, raw_value: str) -> str:
    if raw_value.lower() == "all":
        bot.max_rounds = None
        return "样本数已设置为全量。"
    try:
        value = int(raw_value)
    except ValueError:
        return "样本数必须是整数，或者使用 all。"

    if value <= 0:
        return "样本数必须大于 0。"

    bot.max_rounds = value
    return f"样本数已设置为 {value}。"


async def handle_toggle(bot: BacktestTelegramBot, text: str) -> str:
    parts = text.split(maxsplit=1)
    if len(parts) == 1:
        bot.client.set_pending_reply(bot.handle_toggle_reply_bound)
        return "请输入要切换的模块名:\n" + "\n".join(MODULE_MAP.keys())
    return _toggle_module(bot, parts[1].strip())


async def handle_set(bot: BacktestTelegramBot, text: str) -> str:
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        bot.client.set_pending_reply(bot.handle_set_reply_bound)
        options = [f"{label} 当前值" for label in PARAM_MAP.keys()]
        return "请输入 参数名 值，例如: 买入价 0.24\n" + "\n".join(options)
    return _set_param(bot, parts[1], parts[2])


async def handle_max(bot: BacktestTelegramBot, text: str) -> str:
    parts = text.split(maxsplit=1)
    if len(parts) == 1:
        bot.client.set_pending_reply(bot.handle_max_reply_bound)
        return "请输入样本数，例如 500；输入 all 表示全量。"
    return _set_max_rounds(bot, parts[1].strip())

async def handle_reset(bot: BacktestTelegramBot, _: str) -> str:
    bot.draft_overrides.clear()
    bot.max_rounds = 300
    return "草稿已重置。默认样本数恢复为 300。"

async def handle_toggle_reply(bot: BacktestTelegramBot, text: str) -> str:
    bot.client.set_pending_reply(None)
    return _toggle_module(bot, text.strip())

async def handle_set_reply(bot: BacktestTelegramBot, text: str) -> str:
    bot.client.set_pending_reply(None)
    parts = text.split(maxsplit=1)
    if len(parts) != 2:
        return "格式不对，请用 参数名 值，例如: 买入价 0.24"
    return _set_param(bot, parts[0], parts[1])

async def handle_max_reply(bot: BacktestTelegramBot, text: str) -> str:
    bot.client.set_pending_reply(None)
    return _set_max_rounds(bot, text.strip())
