# Poly Backtest Final

这是一个本地运行的 Polymarket 回测平台。

当前重点是把回测平台本身搭稳，尽量贴近你现有 paper bot 的执行语义；Telegram bot 只是最后一层包装，不是核心。

这个仓库里有两套东西：

1. 根目录这套 R 平台
	用来读取本地 CSV、批量回测、输出结果和图表
2. Poly_trader_final/
	这是参考用的在线 trader bot，不是这套回测平台的运行入口

如果你只是想跑本地回测，直接看这份 README 就够了。

## 1. 推荐运行方式

不要依赖 VS Code 里的 R 运行环境。

推荐直接用 Windows PowerShell 或者命令提示符运行 Rscript。

原因很简单：

1. VS Code 里 R 插件和环境变量容易出问题
2. 这套项目本身是脚本式 source 结构，命令行跑更稳定
3. 后面就算接 Telegram bot，底层也是调用本地脚本，不需要 VS Code 参与

## 2. 运行前准备

你机器上需要先装好 R。

安装完成后，确保下面这个命令能在 PowerShell 里执行：

```powershell
Rscript --version
```

如果这条命令报错，说明 R 还没进系统 PATH。这个时候先不要折腾 VS Code，先把命令行跑通。

## 3. 项目结构

最关键的目录是：

1. config/
	策略参数和风控开关
2. data/raw/
	原始 CSV 数据
3. data/cache/fst/
	FST 缓存（由 build_fst_cache.R 自动生成，回测优先读取）
4. R/
	回测平台核心代码
5. scripts/
	可直接执行的入口脚本
6. results/
	回测结果 CSV
7. reports/
	图表输出

## 4. 先安装 R 包

第一次使用时，建议先安装这几个包：

```powershell
Rscript -e "install.packages(scan('requirements-r.txt', what='character', quiet=TRUE), repos='https://cloud.r-project.org')"
```

说明：

1. yaml 是必须的
	因为配置文件在 config/strategy.yaml
2. ggplot2 不是绝对必须
	不装也能跑回测，但图表输出会退化成基础图形
3. fst 用于 CSV -> FST 缓存化改造
	已经实装，回测和扫参默认走 FST 缓存，不装也能跑但会慢

Python 依赖仍然用 [requirements.txt](requirements.txt) 管理；R 包依赖清单放在 [requirements-r.txt](requirements-r.txt)。

## 5. 构建 FST 缓存（推荐）

首次使用或新增 CSV 后，运行一次缓存构建：

```powershell
Rscript scripts/build_fst_cache.R
```

这条命令会做几件事：

1. 扫描 data/raw/ 的所有 CSV
2. 校验文件名、行数、必要列、时间戳
3. 行数 < 500 或字段缺失的文件自动跳过
4. 合格文件转为 FST 格式存入 data/cache/fst/
5. 生成 data/manifest.csv 记录每个文件的状态

增量机制：再次运行只处理新增或修改过的 CSV，已有缓存自动跳过。

强制重建全部缓存：

```powershell
Rscript scripts/build_fst_cache.R --force
```

实测性能：读取 100 轮数据，FST 0.69 秒 vs CSV 8.81 秒，约 12.8 倍加速。

## 6. 最简单的跑法

先进入项目根目录：

```powershell
cd C:\Users\ZHAOKAI\Poly_backtest_Final
```

然后直接跑：

```powershell
Rscript scripts/run_backtest.R
```

这条命令会做几件事：

1. 读取 config/strategy.yaml
2. 扫描 data/raw/ 里的轮次 CSV
3. 批量运行回测
4. 在终端打印绩效摘要
5. 输出结果到 results/backtest_results.csv
6. 输出图表到 reports/

默认输出文件：

1. results/backtest_results.csv
2. reports/cum_pnl.png
3. reports/drawdown.png
4. reports/pnl_dist.png
5. reports/hourly_pnl.png
6. reports/polarity_vs_pnl.png
7. reports/entry_timing.png

## 7. 常用命令

### 7.1 先只跑前 500 轮

```powershell
Rscript scripts/run_backtest.R --max 500
```

这个最适合先试流程通不通。

### 7.2 不使用 FST 缓存（强制走 CSV）

```powershell
Rscript scripts/run_backtest.R --no-cache
```

正常不需要加这个参数。只有在缓存出问题、想对比性能或调试时才需要。

### 7.3 指定配置文件

```powershell
Rscript scripts/run_backtest.R --config config/strategy.yaml
```

后面如果你有多套参数，可以自己复制一份 yaml 再切换。

### 7.4 指定数据目录

```powershell
Rscript scripts/run_backtest.R --data-dir data/raw
```

### 7.5 指定结果输出位置

```powershell
Rscript scripts/run_backtest.R --results results/job_001/backtest_results.csv --reports-dir reports/job_001
```

这个很实用。

当你开始批量试参数时，建议每次单独放一个 job 目录，不然结果容易互相覆盖。

## 8. 当前默认策略语义

当前默认配置在 [config/strategy.yaml](config/strategy.yaml)。

几个关键参数：

1. entry_price
	入场价
2. profit_price
	止盈价
3. entry_window_start / entry_window_end
	买入窗口起止秒数；只有 elapsed 落在这个窗口内才允许入场
4. round_duration
	round 总长度，当前默认 300 秒
5. time_stop_after_entry
	买入后最多持有多少秒；当前配置可设为 30 秒时间止损
6. sell_window_start / sell_window_end
	卖出窗口起止秒数；只有 elapsed 落在这个窗口内才允许按止盈逻辑卖出
7. settle_wait
	最晚结算时间，当前默认 300 秒
8. cooldown
	买入后冷静期，当前默认是 8 秒
9. backtest_fill_model
	入场成交模型
10. sell_fill_model
	卖出成交模型

当前默认配置已经按你说的实盘口径处理：

1. 不用 data_gap 作为默认过滤条件
2. 不用 polarity 作为默认决策条件
3. 默认情况下，不交易的主要原因应该是买入窗口内没有打到 0.25
4. 默认卖出窗口是 0 到 180 秒；超过窗口仍未止盈，则按 timeout / time stop / settle 逻辑处理

当前回测现在对齐的是你指定的实盘口径：

1. 买入按 ask <= entry_price 触发
2. 买入后先冷静 8 秒，再开始监控止盈
3. 可以配置买入后 30 秒时间止损
4. hard close / timeout 仍按当时盘口快照处理
5. 默认 round timeout 语义是“距离 round 结束还剩两分钟”

## 9. drawdown 分析怎么跑

这个脚本不是主回测入口，它是专题分析脚本：

[scripts/drawdown_analysis.R](scripts/drawdown_analysis.R)

它适合做：

1. 扫不同止损线
2. 看每条止损线的收益和回撤
3. 做专项敏感性分析

当前更稳的用法是进入 R 交互式终端后手动 source：

```powershell
R
```

进入 R 后：

```r
source("scripts/drawdown_analysis.R")

res <- run_sim(max_rounds = 200)
head(res)

scan <- scan_stop_losses(max_rounds = 200)
print(scan)
```

如果你不想进交互式，也可以后面我再给你补一个专门的命令行入口脚本，比如 scripts/run_drawdown_analysis.R。

## 9.1 timeout 专项诊断怎么跑

如果你现在重点要研究 timeout，直接跑：

```powershell
Rscript scripts/run_timeout_analysis.R
```

也可以指定结果文件和输出目录：

```powershell
Rscript scripts/run_timeout_analysis.R --results results/backtest_results.csv --out-dir reports/timeout_analysis --top 20
```

这个脚本会专门分析 exit_type = timeout 的交易，并输出：

1. timeout_trades.csv
	所有 timeout 明细
2. timeout_by_side.csv
	按 up/down 分组统计
3. timeout_by_hour.csv
	按入场小时统计
4. timeout_loss_buckets.csv
	按亏损区间分桶
5. worst_timeouts.csv
	最差的几笔 timeout

## 9.2 时间止损测试怎么跑

如果你要研究“盈利单通常多久从 0.25 走到 0.26”，以及“不同时间止损点哪个更好”，直接跑：

```powershell
Rscript scripts/run_time_stop_analysis.R --max 500
```

也可以自定义要扫描的时间止损点：

```powershell
Rscript scripts/run_time_stop_analysis.R --max 500 --time-stops 15,20,30,45,60,75,90,120
```

这里的 `time-stops` 不是“距离 round 结束还剩多少秒”，而是：

1. 买入后最多持有 15 秒
2. 买入后最多持有 20 秒
3. 买入后最多持有 30 秒

也就是“按持仓时长做时间止损测试”。

这个脚本会做两件事：

1. 统计盈利单从买入到止盈的持仓时长
2. 扫描不同时间止损点，比较总 PnL、胜率、time_stop 次数和 timeout 次数

输出文件：

1. profit_hold_time_buckets.csv
	盈利单持仓时间分桶
2. profit_trades_with_hold_time.csv
	每笔盈利单的持仓时长明细
3. time_stop_scan.csv
	不同时间止损点的对比结果

## 9.3 趋势突破策略怎么跑

现在已经支持一套独立于默认网格逻辑的新状态机：

1. 当同一侧价格涨到 `trend_entry_price` 时买入
2. 买入后如果涨到 `trend_profit_price` 就止盈
3. 如果回落到 `trend_stop_price` 就止损
4. `trend_side` 可设为 `up`、`down` 或 `both`
5. 当 `trend_side: both` 时，哪一侧先碰到买入线就买哪一侧

对应配置项在 [config/strategy.yaml](config/strategy.yaml)：

```yaml
strategy_mode: "trend_breakout"
trend_side: "both"
trend_entry_price: 0.60
trend_profit_price: 0.80
trend_stop_price: 0.50
```

直接运行：

```powershell
Rscript scripts/run_backtest.R --config config/strategy.yaml
```

如果你要专门扫描趋势策略参数组合，直接跑：

```powershell
Rscript scripts/run_trend_grid_search.R --max 500 --entry-values 0.60,0.65,0.70 --profit-values 0.80,0.85 --stop-values 0.40,0.50 --trend-side both
```

输出文件默认在：

1. results/trend_grid_search_summary.csv
	趋势策略参数组合汇总

## 9.4 单组回测后的 ER / Hurst 分桶

现在普通回测跑完后，会在 `reports/` 里额外输出两组分桶统计：

1. `er_bucket_summary.csv`
2. `hurst_bucket_summary.csv`

分析口径是：

1. 用本轮开头窗口的 ER / Hurst
2. 预测本轮这笔交易最终是赢还是输
3. 赢 = `pnl > 0`
4. 输 = `pnl < 0`

这样你不需要全量扫很多组合，也可以先固定一组参数，只看这组策略在不同因子区间下的表现。

另外现在也支持把 ER / Hurst 直接作为交易过滤条件：

```yaml
er_filter_enabled: true
er_min: 0.2
er_max: 0.5

hurst_filter_enabled: true
hurst_min: 0.45
hurst_max: 0.7
```

只有当本轮开头窗口算出来的因子值落在设定区间内，这轮才允许参与交易；否则直接跳过。

也支持按日期筛选：

```yaml
weekday_filter_enabled: true
weekday_mode: "weekdays"   # weekdays | weekends | all
```

这样可以只测周一到周五，或者只测周六到周日。

## 10. 回测结果里能看到什么

主回测输出 CSV 里，重点关注这些字段：

1. round_id
2. traded
3. skip_reason
4. side
5. entry_price
6. entry_trigger_price
7. exit_price
8. exit_trigger_price
9. exit_type
10. pnl
11. state_path
12. sell_order_price
13. sell_post_time
14. sell_post_elapsed

这些字段是为了把“触发价、挂单价、实际成交价、状态路径”分开，避免把回测写成一团黑箱。

## 11. 常见问题

### Q1. 为什么我不用 VS Code 直接跑？

因为你这里已经明确遇到过很多次 R 环境问题，而这个项目现阶段重点不是解决编辑器问题，是先把平台逻辑做准。

直接用 PowerShell 跑 Rscript 更省事。

### Q2. 为什么跑不出图？

大概率是没装 ggplot2。

安装命令：

```powershell
Rscript -e "install.packages('ggplot2', repos='https://cloud.r-project.org')"
```

### Q3. 为什么结果 CSV 为空或者交易数很少？

通常看这几个方向：

1. entry_price 太苛刻
2. entry_timeout 太短
3. curfew_enabled 限制了时段
4. 你手动打开了 polarity_filter_enabled 或 data_gap_check_enabled
5. data/raw/ 里的数据字段结构和当前清洗逻辑不匹配

### Q4. 如果我要改参数，改哪里？

直接改 [config/strategy.yaml](config/strategy.yaml)。

## 12. 一个推荐的实际使用流程

第一次建议这么跑：

1. 先确认 Rscript 可用
2. 安装 yaml、ggplot2 和 fst
3. 构建 FST 缓存

```powershell
Rscript scripts/build_fst_cache.R
```

4. 跑一小批

```powershell
Rscript scripts/run_backtest.R --max 100
```

4. 看 results/backtest_results.csv 有没有输出
5. 看 reports/ 里图有没有生成
6. 再跑大批量

```powershell
Rscript scripts/run_backtest.R --max 1000
```

7. 最后再跑全量

## 13. 之后怎么扩展

当前推荐顺序：

1. 先把回测状态机和撮合语义继续做稳
2. 再补 BTC Guard、BTC StopLoss、Hurst、ATR 这些模块
3. 再做参数组合批跑
4. 最后接 Telegram bot

如果你愿意，下一步我可以继续补两样最实用的东西：

1. 一个专门给 drawdown_analysis 用的命令行脚本
2. 一个 Windows 一键运行的 .bat 启动脚本
