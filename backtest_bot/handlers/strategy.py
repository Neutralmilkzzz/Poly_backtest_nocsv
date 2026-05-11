from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backtest_bot.bot import BacktestTelegramBot

logger = logging.getLogger(__name__)


def _get_available_strategies(root_dir: Path) -> list[str]:
    strategies = []
    # 查找 config 目录中的 yaml
    config_dir = root_dir / "config"
    if config_dir.exists():
        for file in config_dir.glob("*.yaml"):
            strategies.append(f"config/{file.name}")
            
    # 如果有 strategies 目录，也可以扩充
    strat_dir = root_dir / "strategies"
    if strat_dir.exists():
        for file in strat_dir.glob("*.yaml"):
            strategies.append(f"strategies/{file.name}")
            
    return sorted(strategies)


async def handle_strategy(bot: BacktestTelegramBot, text: str) -> str:
    parts = text.split(maxsplit=1)
    strats = _get_available_strategies(bot.settings.root_dir)
    
    if len(parts) == 1:
        if not strats:
            return "未在 config/ 或 strategies/ 目录下找到可用策略 YAML。"
        
        menu_items = [f"{i}. {s}" for i, s in enumerate(strats, start=1)]
        
        bot.client.set_pending_reply(bot.handle_strategy_reply_bound)
        return "\n".join([
            f"当前使用的策略: {bot.active_strategy_name}",
            "\n请回复你要切换的策略编号:",
            *menu_items
        ])
    
    # 支持 /strategy <序号或名称>
    return _apply_strategy_choice(bot, parts[1].strip(), strats)

async def handle_strategy_reply(bot: BacktestTelegramBot, text: str) -> str:
    bot.client.set_pending_reply(None)
    strats = _get_available_strategies(bot.settings.root_dir)
    return _apply_strategy_choice(bot, text.strip(), strats)

def _apply_strategy_choice(bot: BacktestTelegramBot, choice: str, strats: list[str]) -> str:
    if not strats:
        return "没有可用的策略配置。"

    # Check if user passed number
    try:
        idx = int(choice)
        if 1 <= idx <= len(strats):
            bot.active_strategy_name = strats[idx - 1]
            # 切换策略时最好清空草稿以便从新配置加载
            bot.draft_overrides.clear()
            return f"✅ 策略底座已切换至: {bot.active_strategy_name}\n(参数草稿已重置)"
        else:
            return f"❌ 编号无效，请输入 1 ~ {len(strats)}"
    except ValueError:
        pass
        
    # Check if user typed the name
    if choice in strats:
        bot.active_strategy_name = choice
        bot.draft_overrides.clear()
        return f"✅ 策略底座已切换至: {bot.active_strategy_name}\n(参数草稿已重置)"
    
    # Try just filename
    matched = [s for s in strats if choice in s]
    if len(matched) == 1:
        bot.active_strategy_name = matched[0]
        bot.draft_overrides.clear()
        return f"✅ 策略底座已切换至: {bot.active_strategy_name}\n(参数草稿已重置)"
        
    return f"❌ 未找到对应策略，请检查输入或发送 /strategy 选择。"
