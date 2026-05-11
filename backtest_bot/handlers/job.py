from __future__ import annotations

import os
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from backtest_bot.bot import BacktestTelegramBot

logger = logging.getLogger(__name__)


def _count_csv_files(data_dir) -> int:
    try:
        return len([f for f in os.listdir(data_dir) if f.endswith(".csv")])
    except OSError:
        return 0


def _build_run_confirm(bot: BacktestTelegramBot) -> str:
    from backtest_bot.handlers.config import PARAM_MAP, MODULE_MAP

    merged = bot.runner._build_config(bot.draft_overrides, bot.active_strategy_name)
    rounds_text = "全部" if bot.max_rounds is None else f"最新 {bot.max_rounds} 个"

    param_lines = []
    for label, (key, _) in PARAM_MAP.items():
        val = merged.get(key)
        display = "未设置" if val is None else val
        param_lines.append(f"  {label}: {display}")

    module_lines = []
    for label, key in MODULE_MAP.items():
        module_lines.append(f"  {label}: {'开' if bool(merged.get(key)) else '关'}")

    csv_count = _count_csv_files(bot.settings.data_dir)

    return "\n".join([
        "🔧 回测配置确认",
        "",
        f"🧱 策略: {bot.active_strategy_name}",
        f"📁 数据: {rounds_text} (可用: {csv_count} 个)",
        "",
        "当前参数:",
        *param_lines,
        "",
        "模块开关:",
        *module_lines,
        "",
        "操作:",
        '  回复 "跑" → 开始回测',
        '  改 参数名 值 → 修改参数',
        '  开/关 模块名 → 切换开关',
        '  样本 数量/all → 设置数据范围',
        '  取消 → 返回菜单',
    ])


async def handle_run(bot: BacktestTelegramBot, _: str) -> str:
    """Show confirm page before running backtest."""
    bot.client.set_pending_reply(bot.handle_run_reply_bound)
    return _build_run_confirm(bot)


async def handle_run_reply(bot: BacktestTelegramBot, text: str) -> str:
    """Interactive confirm page: adjust params/modules then run."""
    raw = text.strip()

    if raw == "取消":
        bot.client.set_pending_reply(None)
        return "已取消。"

    if raw.startswith("改"):
        parts = raw.split(maxsplit=2)
        if len(parts) < 3:
            bot.client.set_pending_reply(bot.handle_run_reply_bound)
            return '格式: 改 参数名 值\n例如: 改 买入价 0.24'
        from backtest_bot.handlers.config import _set_param
        result = _set_param(bot, parts[1], parts[2])
        bot.client.set_pending_reply(bot.handle_run_reply_bound)
        return f"{result}\n\n{_build_run_confirm(bot)}"

    if raw.startswith("开 ") or raw.startswith("关 "):
        action = raw[0]
        module_label = raw[2:].strip()
        from backtest_bot.handlers.config import MODULE_MAP
        if module_label not in MODULE_MAP:
            bot.client.set_pending_reply(bot.handle_run_reply_bound)
            return "模块名不存在。可选:\n" + "\n".join(MODULE_MAP.keys())
        mkey = MODULE_MAP[module_label]
        target_val = (action == "开")
        bot.draft_overrides[mkey] = target_val
        bot.client.set_pending_reply(bot.handle_run_reply_bound)
        return f"{module_label} 已设为{'开' if target_val else '关'}\n\n{_build_run_confirm(bot)}"

    if raw.startswith("样本"):
        val = raw[2:].strip()
        if not val:
            parts = raw.split(maxsplit=1)
            val = parts[1] if len(parts) > 1 else ""
        from backtest_bot.handlers.config import _set_max_rounds
        result = _set_max_rounds(bot, val)
        bot.client.set_pending_reply(bot.handle_run_reply_bound)
        return f"{result}\n\n{_build_run_confirm(bot)}"

    if raw in ("跑", "确认", "开始", "run", "go"):
        if bot.runner.current_job is not None:
            bot.client.set_pending_reply(bot.handle_run_reply_bound)
            return "当前有任务运行中，请等待或 /cancel。"

        bot.client.set_pending_reply(None)
        try:
            job = await bot.runner.start_job(
                overrides=bot.draft_overrides,
                max_rounds=bot.max_rounds,
                active_strategy=bot.active_strategy_name,
                on_update=bot.push_update,
                on_photos=bot.push_photos,
                use_latest=True,
            )
        except RuntimeError as exc:
            return str(exc)

        return "\n".join([
            f"🚀 已启动回测: {job.job_id}",
            f"📁 样本: {'全部' if job.max_rounds is None else f'最新 {job.max_rounds} 个'}",
            f"🧱 策略: {bot.active_strategy_name}",
            f"⏳ 执行中，完成后推送结果...",
            "发送 /status 查看进度。",
        ])

    bot.client.set_pending_reply(bot.handle_run_reply_bound)
    return '未识别。可用: "跑" | "改 参数名 值" | "开/关 模块名" | "样本 数量" | "取消"'


async def handle_status(bot: BacktestTelegramBot, _: str) -> str:
    return bot.runner.get_status()


async def handle_cancel(bot: BacktestTelegramBot, _: str) -> str:
    cancelled = await bot.runner.cancel_current_job()
    if cancelled:
        return "已发送取消信号，当前回测任务会尽快停止。"
    return "当前没有可取消的运行中任务。"


async def handle_outputs(bot: BacktestTelegramBot, _: str) -> str:
    job = bot.runner.last_job or bot.runner.current_job
    if job is None:
        return "还没有生成过回测输出。"
    return "\n".join([
        f"最近任务: {job.job_id}",
        f"结果文件: {job.results_path}",
        f"图表目录: {job.reports_dir}",
        f"配置快照: {job.config_path}",
    ])
