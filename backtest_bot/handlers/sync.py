from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtest_bot.bot import BacktestTelegramBot

logger = logging.getLogger(__name__)


async def handle_sync(bot: BacktestTelegramBot, _: str) -> str:
    """Scan raw/ for new CSVs, filter <500 rows, convert to FST cache."""
    if bot.runner.current_job is not None and bot.runner.current_job.status == "running":
        return "当前有任务在运行，请等待完成或 /cancel 后再同步。"

    try:
        job = await bot.runner.start_sync_job(on_update=bot.push_update)
    except RuntimeError as exc:
        return str(exc)

    return (
        f"🔄 缓存同步已启动: {job.job_id}\n"
        f"📁 扫描目录: {bot.settings.data_dir}\n"
        f"📏 行数阈值: ≥500 行\n"
        f"⏳ 执行中，完成后推送结果..."
    )
