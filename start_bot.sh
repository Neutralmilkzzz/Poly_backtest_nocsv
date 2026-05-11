#!/usr/bin/env bash

# 进入项目根目录
cd "$(dirname "$0")"

# 如果存在虚拟环境则激活它 (可选，根据用户系统调整)
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# 检查日志目录是否存在，不存在则创建
mkdir -p logs

echo "正在启动 Backtest Telegram Bot..."

# 使用 nohup 后台运行并重定向输出到 bot.log
nohup python -m backtest_bot.main > logs/telegram_bot.log 2>&1 &

PID=$!
echo "启动成功! 进程 PID: $PID"
echo "日志将输出至 logs/telegram_bot.log"
echo "使用 tail -f logs/telegram_bot.log 可查看实时日志。"
