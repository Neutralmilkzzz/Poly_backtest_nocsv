# 本地 Telegram 回测 Bot

这个目录提供一个本机运行的 Telegram 控制面，用来驱动 R 回测脚本。

## 目标

1. 在 Telegram 上开关回测功能模块
2. 在 Telegram 上修改回测参数
3. 在 Telegram 上选择样本数或全量跑 3000 份 CSV
4. 回测实际在本机执行，不依赖云服务器

## 启动前准备

1. 安装 Python 依赖
   - aiohttp
   - python-dotenv
   - pyyaml

2. 确认本机可以在命令行运行 Rscript

3. 复制 backtest_bot/.env.example 为 backtest_bot/.env，并填写 Telegram 配置

## 启动方式

在项目根目录运行:

python -m backtest_bot.main

## Telegram 命令

/start
/help
/config
/toggle 模块名
/set 参数名 值
/max 500
/max all
/run
/trendscan
/er
/hurst
/status
/cancel
/reset
/outputs

## 输出目录

每次回测都会生成独立任务目录:

results/telegram_jobs/<job_id>/strategy.yaml
results/telegram_jobs/<job_id>/backtest_results.csv
results/telegram_jobs/<job_id>/reports/

这样不会覆盖你之前的结果，也方便比较不同参数组。

参数示例：

```text
/trendscan entry=0.60,0.65,0.70 profit=0.80,0.85 stop=0.40,0.50 side=both
/er window=30 breaks=0,0.1,0.2,0.3,0.4,0.5,1.01
/hurst window=60 breaks=0,0.3,0.4,0.5,0.6,0.7,1.01
```
