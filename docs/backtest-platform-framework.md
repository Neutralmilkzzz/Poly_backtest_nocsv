# 回测平台框架

## 定位

这个项目下一步要先做成完整的本地回测平台，再在最外层加 Telegram 控制面。

顺序不能反过来。

如果先做 bot，再补平台，后面一定会出现两个问题：

1. 控制层和执行层耦合，参数一多就会失控
2. 成交逻辑还没定型，bot 菜单和输出格式会反复改

所以平台的正确顺序是：

1. 数据层
2. 清洗层
3. 撮合执行层
4. 单轮状态机引擎
5. 批量运行器
6. 绩效统计与报告
7. 最后才是 Telegram bot

## 平台分层

### 1. 数据层

目录：
[data/raw](data/raw)
[R/io/data_reader.R](R/io/data_reader.R)

职责：

1. 列出全部轮次文件
2. 解析文件名时间
3. 读取单轮 CSV
4. 提供统一字段结构

### 2. 清洗层

目录：
[R/io/data_cleaner.R](R/io/data_cleaner.R)
[R/utils/helpers.R](R/utils/helpers.R)

职责：

1. 前向填充盘口
2. 补 midpoint
3. 计算 elapsed 秒数
4. 保证后续引擎看到的是标准化数据

### 3. 撮合执行层

目录：
[R/engine/fill_model.R](R/engine/fill_model.R)

这是平台核心，不是附属细节。

原因很简单：
回测最终可信不可信，不取决于图表画得多漂亮，而取决于你的成交模型是否接近真实市场行为。

当前平台已经引入两个关键概念：

1. 买入触发条件
由 [config/strategy.yaml](config/strategy.yaml) 里的 backtest_fill_model 控制

2. 卖出成交模型
由 [config/strategy.yaml](config/strategy.yaml) 里的 sell_fill_model 控制

### 4. 为什么要单独抽撮合层

因为 Polymarket 的限价单并不是“碰到阈值就永远按挂单价成交”。

你提到的这个例子就是关键：

1. 你挂 0.26 的卖单
2. 市场盘口从 0.20 直接跳到 0.80
3. 实际不应该还记成 0.26 成交
4. 更合理的回测是按改善后的价格成交

这意味着回测里至少要区分：

1. 触发价
2. 挂单价
3. 实际成交价

现在主引擎已经新增：

1. entry_trigger_price
2. exit_trigger_price
3. exit_price

后面如果要再细化，还可以继续扩展成：

1. order_price
2. fill_price
3. slippage
4. price_improvement

### 5. 当前卖出成交模型

当前默认是：

sell_fill_model: price_improve

含义：

1. 当 best bid 首次大于等于 profit_price 时，卖单触发成交
2. 实际成交价取 max(profit_price, observed_bid)

这比“固定按 0.26 成交”更贴近你描述的真实行为。

同时也保留了其他模型接口：

1. limit_price
始终按挂单限价成交，偏保守但会低估价格跳变收益

2. price_improve
按观察到的 bid 给予价格改善，适合当前数据粒度

3. midpoint_cap
取挂单价和触发 bid 的中间值，适合做敏感性分析

### 6. 单轮引擎层

目录：
[R/engine/backtest_engine.R](R/engine/backtest_engine.R)

职责：

1. 做轮次过滤
2. 找入场点
3. 找卖出点
4. 调撮合层决定实际成交价
5. 输出单轮交易结果

这层不该自己硬编码成交细节。

### 7. 批量运行器

目录：
[R/engine/runner.R](R/engine/runner.R)
[scripts/run_backtest.R](scripts/run_backtest.R)

职责：

1. 跑 3000 个 CSV
2. 输出每轮结果
3. 生成总结果 CSV
4. 生成图表

### 8. 专题分析层

目录：
[scripts/drawdown_analysis.R](scripts/drawdown_analysis.R)

这层不是主引擎，但必须复用同一套撮合逻辑。

否则会出现：

1. 主回测一套成交规则
2. 止损分析又一套成交规则

那样最后结论会彼此冲突。

现在这个脚本已经接入同一套 sell_fill_model。

### 9. Telegram 控制层

目录：
[backtest_bot](backtest_bot)

它的地位是“包装层”，不是“平台核心”。

它负责：

1. 选参数
2. 选开关
3. 启动本机回测
4. 回传结果

它不负责决定成交规则。

## 当前平台已经具备的基础

1. 配置加载
2. 批量读轮次
3. 数据清洗
4. 单轮回测
5. 批量运行
6. 绩效统计
7. 图表输出
8. 卖出价格改善撮合模型

## 还需要补的核心能力

按优先级排序：

1. BTC Guard 与 BTC StopLoss 的历史数据接入
2. Hurst / ATR 所需 BTC 序列计算
3. 更细的撮合模型
内容包括盘口穿价、成交延迟、数据缺口处理
4. 参数组合批跑
5. Markdown / HTML 报告生成
6. 最后再把这些能力稳态接到 Telegram bot

## 当前推荐开发顺序

1. 先把主引擎撮合层做稳
2. 再补足缺失风控模块
3. 再做批跑和报告
4. 最后再丰富 Telegram 菜单

这个顺序是为了保证：

平台的“结论正确性”优先于“交互方便性”。