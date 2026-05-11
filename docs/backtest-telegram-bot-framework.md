# 本地 Telegram 回测平台框架

## 目标

这套框架的目标不是把 3000 份 CSV 放到服务器上跑，而是在你自己的电脑上保留数据、在本机执行回测，只把 Telegram 当作控制面。

## 架构分层

### 1. 控制面

目录: backtest_bot/

职责:
1. 接收 Telegram 命令
2. 维护当前回测草稿参数
3. 触发本地 R 回测任务
4. 把进度和结果推送回 Telegram

### 2. 回测执行层

入口: scripts/run_backtest.R

职责:
1. 接收动态配置路径
2. 接收动态输出目录
3. 跑完整批量回测
4. 产出结果 CSV 和图表

### 3. 回测引擎层

目录: R/

职责:
1. 读取 CSV
2. 清洗盘口数据
3. 执行单轮回测逻辑
4. 统计绩效

## 运行链路

1. 你在 Telegram 里发送 /start
2. Bot 给你菜单
3. 你选择开关、参数和样本数
4. Bot 把这次任务的配置写到独立 strategy.yaml
5. Bot 在本机启动 Rscript scripts/run_backtest.R
6. R 回测读取 data/raw 下的 CSV 批量运行
7. 结果写入 results/telegram_jobs/<job_id>/
8. Bot 把关键结果发回 Telegram

## 为什么要用本机控制面

因为你的瓶颈不是 Telegram，而是数据体积和 IO。

3000 份 CSV 不适合云端来回搬运，也没必要部署成远程服务。最稳妥的方式是:

1. 数据留在本机
2. Rscript 在本机读取本地磁盘
3. Telegram 只负责发命令和收结果

这样延迟最低，也最省事。

## 这版框架已经具备的能力

1. Telegram 文本菜单
2. 开关模块
3. 修改参数
4. 设置样本数或全量
5. 单任务运行保护
6. 独立任务输出目录
7. 回测完成后自动汇总总PnL、交易数、胜率、退出分布、跳过分布

## 下一步建议

1. 给回测任务增加参数组合批跑模式
2. 增加任务队列，而不是一次只跑一个任务
3. 把 drawdown_analysis.R 也接入 Telegram，作为专题分析命令
4. 给结果目录增加 Markdown 汇总报告
5. 给 R 引擎补齐 BTC Guard、BTC StopLoss、Hurst、ATR 的完整复刻