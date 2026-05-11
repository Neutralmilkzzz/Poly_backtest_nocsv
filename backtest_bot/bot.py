from __future__ import annotations

import logging
from functools import partial
from typing import Any

from .config import BotSettings
from .notifier import TelegramBotClient
from .runner import BacktestRunner

logger = logging.getLogger(__name__)


class BacktestTelegramBot:
    def __init__(self, settings: BotSettings):
        self.settings = settings
        self.client = TelegramBotClient(
            token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            proxy=settings.proxy,
        )
        self.runner = BacktestRunner(settings)
        
        # --- State ---
        self.draft_overrides: dict[str, Any] = {}
        self.max_rounds: int | None = 1200
        self.active_strategy_name: str = "strategies/grid_er_hurst_1200.yaml"
        self.analyze_state: dict[str, Any] = {}
        
        # --- Bound Callbacks for Pending Replies ---
        import backtest_bot.handlers.base as hb
        import backtest_bot.handlers.config as hc
        import backtest_bot.handlers.job as hj
        import backtest_bot.handlers.strategy as hs
        import backtest_bot.handlers.analytics as ha
        
        self.handle_menu_reply_bound = partial(hb.handle_menu_reply, self)
        self.handle_toggle_reply_bound = partial(hc.handle_toggle_reply, self)
        self.handle_set_reply_bound = partial(hc.handle_set_reply, self)
        self.handle_max_reply_bound = partial(hc.handle_max_reply, self)
        self.handle_strategy_reply_bound = partial(hs.handle_strategy_reply, self)
        self.handle_analyze_reply_bound = partial(ha.handle_analyze_reply, self)
        self.handle_run_reply_bound = partial(hj.handle_run_reply, self)

        # Register routing
        self._register_routes()

    def _register_routes(self) -> None:
        import backtest_bot.handlers.base as hb
        import backtest_bot.handlers.config as hc
        import backtest_bot.handlers.job as hj
        import backtest_bot.handlers.strategy as hs
        import backtest_bot.handlers.analytics as ha
        import backtest_bot.handlers.sync as hsync

        # Nav
        self.client.register_handler("/start", partial(hb.handle_start, self))
        self.client.register_handler("/menu", partial(hb.handle_start, self))
        self.client.register_handler("/help", partial(hb.handle_help, self))
        
        # Config & Overrides
        self.client.register_handler("/config", partial(hc.handle_config, self))
        self.client.register_handler("/toggle", partial(hc.handle_toggle, self))
        self.client.register_handler("/set", partial(hc.handle_set, self))
        self.client.register_handler("/max", partial(hc.handle_max, self))
        self.client.register_handler("/reset", partial(hc.handle_reset, self))
        
        # Job Execution
        self.client.register_handler("/run", partial(hj.handle_run, self))
        self.client.register_handler("/status", partial(hj.handle_status, self))
        self.client.register_handler("/cancel", partial(hj.handle_cancel, self))
        self.client.register_handler("/outputs", partial(hj.handle_outputs, self))
        
        # Strategy selection & sweeping
        self.client.register_handler("/strategy", partial(hs.handle_strategy, self))
        self.client.register_handler("/analyze", partial(ha.handle_analyze, self))
        self.client.register_handler("/trendscan", partial(ha.handle_trendscan, self))
        self.client.register_handler("/er", partial(ha.handle_er, self))
        self.client.register_handler("/hurst", partial(ha.handle_hurst, self))

        # Data sync
        self.client.register_handler("/sync", partial(hsync.handle_sync, self))

        # Leaderboard
        from backtest_bot.handlers.base import handle_leaderboard
        self.client.register_handler("/leaderboard", partial(handle_leaderboard, self))

    async def start(self) -> None:
        logger.info("Bot 正在启动...")
        await self.client.start()
        logger.info("Bot 已启动，开始轮询消息")
        self.client.send(
            "🤖 回测 Bot 已启动\n\n"
            "快速开始:\n"
            "• /analyze → 单因子扫参引导\n"
            "• /run → 用当前配置快速回测\n"
            "• /start → 打开完整菜单\n"
            "• /help → 查看所有命令\n\n"
            f"当前策略: {self.active_strategy_name}"
        )

    async def stop(self) -> None:
        await self.client.stop()

    async def push_update(self, message: str) -> None:
        logger.debug("push_update: %s", message[:80])
        self.client.send(message)

    async def push_photos(self, photos: list) -> None:
        """Send a batch of (Path, caption) photos via Telegram media group."""
        self.client.send_photos(photos)
