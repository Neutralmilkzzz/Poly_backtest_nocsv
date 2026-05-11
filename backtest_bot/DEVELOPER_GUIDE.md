# Backtest Telegram Bot 开发者指南

本文档旨在为开发人员提供关于“如何为 Telegram Bot 增量开发新模块/新功能”的详细指南。随着 Bot 改为模块化 Handler 架构，新增指令和交互逻辑变得非常简单且低耦合。

## 📁 目录结构

所有核心调度已剥离至 `handlers/` 包中。
```text
backtest_bot/
├── bot.py                # Bot 核心控制器 (负责组装路由与持有单一状态)
├── main.py               # 启动入口点
├── runner.py             # R脚本派发器 (异步子进程管理)
├── notifier.py           # Telegram API 轮询客户端 (底层通信支持)
└── handlers/             # 💡 增量开发的主阵地
    ├── base.py           # 基础交互 (/start, /help)
    ├── config.py         # 参数与配置相关逻辑
    ├── job.py            # 回测任务运行生命周期控制
    └── strategy.py       # 多策略管理相关
```

---

## 🚀 教程：如何添加一个新的 Telegram 指令

假设你需要开发一个新的分析功能，用户发送 `/analyze`，机器人返回一行自定义分析。

### 步骤 1：在 handlers 中创建新模块（或利用现有模块）
如果功能属于回测任务分析，你可以把它写在 `job.py` 中。如果是一个独立大模块，可以在 `handlers/` 下新建 `analytics.py`，比如：

```python
# backtest_bot/handlers/analytics.py
from __future__ import annotations
from typing import TYPE_CHECKING
import logging

if TYPE_CHECKING:
    # 仅作类型标注使用，避免循环引入
    from backtest_bot.bot import BacktestTelegramBot

logger = logging.getLogger(__name__)

async def handle_analyze(bot: BacktestTelegramBot, text: str) -> str:
    """处理 /analyze 指令的函数"""
    
    # 1. 你可以直接访问 bot 的状态
    draft = bot.draft_overrides
    current_strat = bot.active_strategy_name
    
    # 2. 从 text 中获取命令后的参数，例如 "/analyze BTC_Filter"
    parts = text.split()
    if len(parts) > 1:
        target = parts[1]
    else:
        target = "默认模块"
        
    return f"🛠️ 分析结果准备就绪...\n目标：{target}\n基于策略：{current_strat}"
```

### 步骤 2：在 bot.py 中注册路由
打开 `backtest_bot/bot.py`，找到 `_register_routes` 方法。

```python
    def _register_routes(self) -> None:
        # ... 原有的导入 ...
        import backtest_bot.handlers.analytics as ha  # 👈 1. 导入你的 Handler
        
        # ... 原有的绑定 ...

        # 👈 2. 绑定指令与对应的处理函数，切记使用 partial 注入 bot 自身上下文
        self.client.register_handler("/analyze", partial(ha.handle_analyze, self))
```
**恭喜！重新启动 Bot（`bash start_bot.sh`）后，`/analyze` 就可以像内置命令一样被响应了。**

---

## 💬 进阶：如何开发交互式连续对话？

Bot API 中支持“挂起等待回复”（Pending Reply）。
如果用户第一步只输入了 `/export`，你想反问用户“请输入导出的时间范围:”，你可以这样做：

```python
async def handle_export(bot: BacktestTelegramBot, text: str) -> str:
    # 1. 捕获意图，反问用户。同时指定下一步发消息时，由哪个函数处理
    bot.client.set_pending_reply(bot.handle_export_reply_bound)
    return "请输入你要导出的时间范围 (例如: 7d, 30d):"

async def handle_export_reply(bot: BacktestTelegramBot, text: str) -> str:
    # 2. 处理用户的回复，清理挂起状态以免卡死
    bot.client.set_pending_reply(None)
    
    # 获取用户在上一步反问后输入的内容
    user_input = text.strip()
    return f"✅ 已经按你的要求生成了 {user_input} 的报表！"
```

**⚠️ 注意**：如果使用了 `set_pending_reply(fn)`，你的 `fn` 必须在 `bot.py` 的 `__init__` 中以 `partial` 事先绑定并注册为 `self.xxx_bound` 暴露给 Handler 调用，从而解决函数互相引用时的上下文脱离问题。

```python
# 在 bot.py 的 __init__ 中:
self.handle_export_reply_bound = partial(ha.handle_export_reply, self)
```

## 🛠️ 关于全局状态
当 Handler 触发时，你被授予了唯一的 `bot` 实例权限，这意味着你可以毫无阻碍地访问和修改如下数据：
- `bot.active_strategy_name`：动态读取和写入当前正在使用的 YAML (`config/xxx.yaml`)。
- `bot.draft_overrides`：一个字典，存放用户随时用 `/set` 修改但尚未作为真实 YAML 跑起来的内存参数。
- `bot.max_rounds`：用户的临时样本截断量。
- `bot.runner`：发起、取消、查询 R 进程。
