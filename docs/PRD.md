# Poly Backtest — 产品需求文档 (PRD)

> 版本: 1.0  
> 日期: 2026-03-25  
> 作者: 自动生成（基于 Poly_trader_final Python 代码库逆向）

---

## 一、项目背景

### 1.1 现有系统

团队已有一套 Python 实盘/模拟盘交易引擎（`Poly_trader_final`），核心能力如下：

| 组件 | 文件 | 功能 |
|------|------|------|
| 引擎状态机 | `engine.py` (~1800行) | 管理整个 5 分钟轮次周期 |
| 配置 | `config/strategy.yaml` + `config/__init__.py` | 策略参数加载/保存 |
| 撮合/下单 | `exchange/broker.py` | GTC 买卖、FOK 市价卖、Paper 模拟 |
| BTC 行情 | `feeds/binance.py` | Binance WebSocket 实时 BTC 价格 |
| Poly 盘口 | `feeds/polymarket.py` | Polymarket WebSocket 实时 bid/ask |
| 轮次发现 | `feeds/discovery.py` | Gamma API 查找 5 分钟轮次 |
| 指标 | `indicators/hurst.py`, `indicators/atr.py` | Hurst 指数、ATR |
| 通知 | `notify/telegram.py` | Telegram 命令控制 |

### 1.2 问题

现有系统是交易引擎，不是回测引擎。它的 Paper Mode 只是不调真实 API，但仍然依赖：
- 实时 WebSocket 数据
- 异步事件驱动（asyncio）
- Telegram 交互
- 每次只跑一轮

无法用它来：
- 批量回放历史数据
- 系统化测参数
- 生成标准化绩效报告

### 1.3 目标

用 R 语言构建一套独立的回测平台，能够：
1. 读取现有 CSV 历史数据（data/raw/ 中的 5 分钟盘口流）
2. 完整复现 Poly Trader 的交易逻辑
3. 支持策略参数批量测试
4. 输出专业级绩效统计

---

## 二、Python 交易引擎逻辑——完整还原

以下是从代码中提取的全部逻辑，作为 R 回测平台的实现依据。

### 2.1 状态机

Python 引擎的状态转移：

```
WAIT_ROUND → IDLE → READY → POSITION → SELLING → SETTLE → 下一轮
```

| 状态 | 含义 | 引擎做什么 |
|------|------|-----------|
| WAIT_ROUND | 等待新轮次 | 等到 5 分钟边界，发现市场 |
| IDLE | 数据就绪检查 | 等 WS 数据到达，检查极性 |
| READY | 进入挂单阶段 | 双边挂 GTC 买单，监控 BTC 偏离 |
| POSITION | 一边成交 | 撤另一边，等卖出信号 |
| SELLING | 挂 GTC 卖单 | 等待成交或超时 |
| SETTLE | 结算 | 等到 4:30 标记，计算 PnL |

### 2.2 策略参数（默认值）

```yaml
entry_price:       0.25      # 买入限价
profit_price:      0.26      # 卖出限价
btc_diff_max:      50        # BTC 偏离撤单阈值 ($)
btc_diff_stoploss: 80        # BTC 持仓止损阈值 ($)
polarity_max:      0.35      # 极性过滤阈值
entry_timeout:     90        # 买入等待超时 (秒)
sell_timeout:      180       # 卖出等待超时 (秒)
settle_wait:       270       # 结算等待 (秒)
cooldown:          8         # 买成后冷却 (秒)
gap_threshold:     7         # 数据空窗阈值 (秒)
polarity_delay:    2         # 极性检查延迟 (秒)
hurst_threshold:   0.5       # Hurst 过滤阈值
hurst_window:      900       # Hurst 计算窗口 (秒)
atr_window:        1800      # ATR 计算窗口 (秒)
atr_threshold:     100.0     # ATR 过滤阈值
```

### 2.3 风控模块开关

| 模块 | 默认 | 功能 |
|------|------|------|
| BTC Guard (偏离撤单) | ✅ | 买入阶段：BTC 实时价格 vs 轮次开始价格偏离 > btc_diff_max → 撤单 |
| BTC StopLoss (持仓止损) | ✅ | 持仓阶段：BTC偏离 >= btc_diff_stoploss → 市价割肉 |
| Polarity Filter (极性过滤) | ✅ | 轮次开始：\|up_mid - 0.5\| > polarity_max → 跳过本轮 |
| Hurst Filter (赫斯特过滤) | ❌ | 轮次开始：H > hurst_threshold → 跳过 |
| ATR Filter | ❌ | 轮次开始：ATR > atr_threshold → 跳过 |
| Curfew (宵禁) | ✅ | 指定小时跳过（默认 hour=20） |
| Data Gap (数据空窗) | ✅ | 最新盘口时间戳距今 > gap_threshold → 跳过 |
| Entry Timeout (买入超时) | ✅ | 超过 entry_timeout 秒未成交 → 撤单 |
| Sell Timeout (卖出超时) | ✅ | 超过 sell_timeout 秒未卖出 → 市价止损 |

### 2.4 Paper Mode 成交逻辑（回测的直接依据）

这是最关键的部分——Python Paper Mode 的成交判定直接定义了回测应该怎么模拟。

#### 买入成交条件

```python
# engine.py ~L875
# 在 READY 状态循环中，每 0.1 秒检查一次：
ps = self.poly_feed.snapshot
if ps.up_bid > 0 and ps.up_bid <= P_ENTRY:   # up 侧 bid <= 0.25
    filled_side = "up"
    fill_price = P_ENTRY                       # 成交价 = 限价
    fill_qty = trade_shares                    # 全额成交
if ps.dn_bid > 0 and ps.dn_bid <= P_ENTRY:   # down 侧 bid <= 0.25
    filled_side = "down"
    fill_price = P_ENTRY
    fill_qty = trade_shares
```

**注意**：Paper Mode 用的是 `bid <= entry_price` 判断成交。
这存在一个微妙之处：实际挂的是买单（限价 0.25），理论上应该是 `ask <= 0.25` 才能吃到。
但 Python 代码在 `_run_one_round` 的 entry loop 中实际用了 `bid`。
而在 `_paper_fill_monitor` 中用的是 `ask <= entry_price`。  
**回测引擎应提供两种模式供切换测试。**

#### 卖出成交条件

Paper Mode 卖出逻辑位于 `_paper_sell_monitor`（如果卖单挂上后）：

```python
# 等待 bid >= profit_price 或 超时
# 超时后：市价止损（Paper 模式直接用当前 bid 作为成交价）
```

#### Paper 结算 PnL

```python
# engine.py ~L1818
if self._sell_filled.is_set():
    sell_price = getattr(self, '_sell_fill_price', P_PROFIT)
    pnl = (sell_price - fill_price) * fill_qty
else:
    # 超时/killed：用当前 bid 估算
    bid = ps.up_bid if side == "up" else ps.dn_bid
    pnl = (bid - fill_price) * fill_qty if bid > 0 else 0
```

### 2.5 完整单轮交易流程（回测需复现）

```
1. 轮次开始（CSV 文件名即轮次开始时间）
2. 等待 polarity_delay 秒后读取盘口
3. 计算极性 = |up_midpoint - 0.5|
4. [极性过滤] 极性 > polarity_max → 跳过
5. [宵禁过滤] 当前小时在 curfew_hours → 跳过
6. [数据空窗] 数据间隔 > gap_threshold → 跳过
7. [Hurst 过滤] H > hurst_threshold → 跳过（如果开启）
8. 进入 READY：开始 entry 阶段
   a. 记录此刻 BTC 价格作为基准 btc_target
   b. 每 0.1 秒检查盘口
   c. [BTC Guard] |当前BTC - btc_target| > btc_diff_max → 撤单
   d. 成交条件：ask <= entry_price（某一边先到）
   e. [Entry Timeout] 超过 entry_timeout 秒 → 撤单退出
9. 成交后进入 POSITION：
  a. 冷却 cooldown 秒
  b. 再开始监控 profit_price
10. 进入 SELLING：
    a. 等待 bid >= profit_price → 成交
    b. [BTC StopLoss] BTC 偏离 >= btc_diff_stoploss → 市价止损
    c. [Sell Timeout] 超过 sell_timeout → 市价止损
11. 结算：
    a. PnL = (sell_price - buy_price) × qty
    b. 如果超时/止损：sell_price = 当前 bid
12. 等轮次结束（总 300 秒）
```

---

## 三、R 回测平台需求

### 3.1 核心目标

将上述 Python 实时交易逻辑，转换为"基于历史 CSV 数据的事件驱动回测引擎"。

### 3.2 数据输入

| 来源 | 文件 | 字段 |
|------|------|------|
| data/raw/ | 每个 CSV 对应一个 5 分钟市场 | timestamp, up_best_bid, up_best_ask, up_midpoint, down_best_bid, down_best_ask, down_midpoint, event_type, volume |

- 文件名格式：`YYYY-MM-DD_HH-MM-SS.csv`
- event_type: `best_bid_ask`, `last_trade_price`, `volume_poll`
- 每个文件内的行按 timestamp 排序

### 3.3 模块清单

下面定义 R 回测平台需要实现的模块，每个模块对应 Python 代码中的哪些功能。

#### M1: 配置加载器 — `R/io/config_loader.R`

**对应 Python**: `config/__init__.py`

| 需求 | 说明 |
|------|------|
| 读取 YAML | 从 `config/strategy.yaml` 加载参数 |
| 默认值 | 所有参数有默认值，缺失字段自动补全 |
| 返回值 | 返回一个 named list |

#### M2: 数据读取器 — `R/io/data_reader.R`

**对应 Python**: `feeds/polymarket.py` + `feeds/discovery.py`

| 需求 | 说明 |
|------|------|
| 读单文件 | `read_round_csv(path)` → data.frame |
| 批量读取 | `list_rounds(dir)` → 按时间排序的文件路径向量 |
| 时间解析 | 文件名解析为 POSIXct，CSV 内 timestamp 解析为 POSIXct |
| 空值处理 | up/down 字段 NA 前向填充 |

#### M3: 数据清洗 — `R/io/data_cleaner.R`

**对应 Python**: `feeds/polymarket.py._handle()` 中的异步更新逻辑

| 需求 | 说明 |
|------|------|
| 前向填充 | 盘口异步到达，NA 用前值填充 |
| 计算 midpoint | 如果 midpoint 为 NA，从 bid/ask 计算 |
| event_type 过滤 | 可选只保留 best_bid_ask 行 |
| 时间偏移量 | 计算每行相对于轮次开始的 elapsed 秒数 |

#### M4: 回测引擎 — `R/engine/backtest_engine.R`

**对应 Python**: `engine.py` 的 `_run_one_round()` (~500行)

这是最核心、最复杂的模块。

| 需求 | 说明 |
|------|------|
| 输入 | 一个轮次的 data.frame + 配置参数 |
| 输出 | 单轮结果 list（是否交易、方向、买入价、卖出价、PnL 等） |
| 状态机 | IDLE → READY → POSITION → SELLING → SETTLE |

**详细逻辑（逐步）**：

```
函数签名:
  run_one_round(df, cfg, btc_prices = NULL)

参数:
  df       — 该轮次的盘口 data.frame（已清洗，带 elapsed 列）
  cfg      — 配置 named list
  btc_prices — 可选：该轮次期间的 BTC 价格序列（用于 BTC Guard）

返回值:
  list(
    round_id      = "2026-03-12_13-35-00",
    traded        = TRUE/FALSE,
    skip_reason   = NA / "polarity" / "curfew" / "timeout" / ...,
    side          = "up" / "down" / NA,
    entry_price   = numeric,
    entry_time    = POSIXct,
    exit_price    = numeric,
    exit_time     = POSIXct,
    exit_type     = "profit" / "timeout" / "stoploss" / "btc_stoploss",
    qty           = numeric,
    pnl           = numeric,
    polarity      = numeric,
    elapsed_entry = numeric,  # 入场耗时（秒）
    elapsed_exit  = numeric   # 出场耗时（秒）
  )
```

**引擎内部步骤**:

1. **极性检查**  
   取 `elapsed >= polarity_delay` 的第一行，计算 `polarity = abs(up_midpoint - 0.5)`。  
   如果 `polarity > cfg$polarity_max`，返回 skip。

2. **宵禁检查**  
   从文件名或 timestamp 提取小时，检查是否在 `cfg$curfew_hours`。

3. **数据空窗检查**  
   检查首行的 event_type。如果轮次内前 `gap_threshold` 秒没有 `best_bid_ask` 事件，skip。

4. **Entry 阶段**  
   遍历 `elapsed < entry_timeout` 的行：
   - 找到第一行满足 `up_best_ask <= entry_price` → 买入 up
   - 或 `down_best_ask <= entry_price` → 买入 down  
   - 如果有 btc_prices 且偏离 > btc_diff_max → 暂停/撤单
   - 超时返回 skip

5. **Selling 阶段**  
  从成交行开始，跳过 cooldown 秒：
   - 找到 `{side}_best_bid >= profit_price` → 卖出获利
   - 超过 sell_timeout → 用当前 bid 市价止损
   - 如果有 BTC 止损检查

6. **PnL 计算**  
   `pnl = (exit_price - entry_price) × qty`

#### M5: 批量运行器 — `R/engine/runner.R`

**对应 Python**: `engine.py._trading_loop()`

| 需求 | 说明 |
|------|------|
| 批量回测 | 遍历所有轮次 CSV，逐个调用引擎 |
| 结果汇总 | 合并所有轮次结果为一个 data.frame |
| 进度提示 | 每 N 轮打印进度 |
| 参数网格 | 支持多组参数，每组都跑一遍 |

#### M6: 绩效统计 — `R/metrics/performance.R`

**对应 Python**: 目前 Python 只算累计 PnL，回测版要做全面统计

| 指标 | 公式/说明 |
|------|----------|
| 总交易次数 | 所有 traded=TRUE 的轮次 |
| 跳过次数 | 所有 traded=FALSE 的轮次，按 skip_reason 分类 |
| 胜率 | win_count / trade_count |
| 总 PnL | sum(pnl) |
| 平均 PnL | mean(pnl) |
| 最大单笔盈利 | max(pnl) |
| 最大单笔亏损 | min(pnl) |
| 盈亏比 | mean(winning_pnl) / abs(mean(losing_pnl)) |
| 累计净值曲线 | cumsum(pnl) |
| 最大回撤 | max drawdown from cumulative curve |
| Sharpe Ratio | mean(pnl) / sd(pnl) × sqrt(N) |
| 按小时统计 | 每小时的胜率、PnL 分布 |
| 按 exit_type 统计 | profit / timeout / stoploss 各占比 |

#### M7: 可视化 — `R/metrics/plots.R`

| 图表 | 说明 |
|------|------|
| 累计 PnL 曲线 | x=轮次序号, y=cumsum(pnl) |
| 回撤曲线 | 最大回撤区间标注 |
| PnL 分布直方图 | 每笔交易 PnL 分布 |
| 按小时热力图 | x=小时, y=日期, color=PnL |
| 入场时间分布 | elapsed_entry 的直方图 |
| 极性 vs PnL 散点图 | polarity 与 PnL 的关系 |

#### M8: 工具函数 — `R/utils/helpers.R`

| 函数 | 说明 |
|------|------|
| `parse_round_time(filename)` | 从文件名提取 POSIXct |
| `forward_fill(x)` | 向量前向填充 NA |
| `calc_midpoint(bid, ask)` | 安全计算中位价 |
| `calc_polarity(up_mid)` | `abs(up_mid - 0.5)` |
| `elapsed_seconds(ts, start_ts)` | 计算时间差（秒） |

---

## 四、对应关系总表

| R 模块 | R 文件路径 | 对应 Python 代码 | 优先级 |
|--------|-----------|-----------------|--------|
| 配置加载器 | R/io/config_loader.R | config/__init__.py | P0 |
| 数据读取器 | R/io/data_reader.R | feeds/discovery.py + polymarket.py | P0 |
| 数据清洗 | R/io/data_cleaner.R | feeds/polymarket.py._handle() | P0 |
| 回测引擎 | R/engine/backtest_engine.R | engine.py._run_one_round() | P0 |
| 批量运行器 | R/engine/runner.R | engine.py._trading_loop() | P0 |
| 绩效统计 | R/metrics/performance.R | (新增，Python 未有) | P1 |
| 可视化 | R/metrics/plots.R | (新增，Python 未有) | P1 |
| 工具函数 | R/utils/helpers.R | 散落在各模块中 | P0 |

---

## 五、不需要移植的部分

以下 Python 模块是实时交易专用的，回测不需要：

| Python 模块 | 原因 |
|-------------|------|
| `feeds/binance.py` | 回测用历史 BTC 数据，不需要 WebSocket |
| `feeds/polymarket.py` | 回测从 CSV 读取，不需要 WebSocket |
| `feeds/discovery.py` | 回测直接遍历文件，不需要 API 发现 |
| `exchange/broker.py` | 回测模拟成交，不需要真实 CLOB |
| `notify/telegram.py` | 回测不需要 Telegram |
| `main.py` | 回测有自己的入口 |
| `scripts/*` | 部署脚本不适用 |

---

## 六、成交模型设计

这是回测平台最关键的设计决策。

### 6.1 推荐默认模型：Ask/Bid 成交

```
买入成交条件: ask <= entry_price
卖出成交条件: bid >= profit_price

买入成交价: entry_price（限价单，不会比限价更差）
卖出成交价: profit_price（限价单）

市价止损价: 当前 bid（最坏情况）
```

### 6.2 可选宽松模型：Midpoint 成交

```
买入成交条件: midpoint <= entry_price
卖出成交条件: midpoint >= profit_price
成交价: entry_price / profit_price
```

### 6.3 可选严格模型：Bid 成交（最保守）

```
买入成交条件: bid <= entry_price（有人愿意以更低价卖）
卖出成交条件: ask >= profit_price
```

### 6.4 配置方式

在 `config/strategy.yaml` 中新增：

```yaml
backtest_fill_model: "ask_bid"  # "ask_bid" | "midpoint" | "conservative"
```

---

## 七、数据缺失处理

### 7.1 BTC 价格

当前 CSV 中没有 BTC 价格列。回测运行 BTC Guard / StopLoss 需要额外数据。

**方案 A**: 暂不实现 BTC Guard 和 StopLoss，先跑纯盘口策略  
**方案 B**: 后续补充 BTC 历史价格 CSV，通过时间戳对齐  
**建议**: 先走方案 A，把核心流程跑通，BTC 风控作为 Phase 2

### 7.2 Hurst / ATR

这两个指标也依赖 BTC 历史价格序列。同样建议 Phase 2 补充。

---

## 八、分阶段实施计划

### Phase 1: 最小可用回测（核心流程）

```
目标: 能跑通单轮 + 批量回测 + 基本统计

实现:
  ✅ R/utils/helpers.R          — 工具函数
  ✅ R/io/config_loader.R       — 配置加载
  ✅ R/io/data_reader.R         — 数据读取
  ✅ R/io/data_cleaner.R        — 数据清洗
  ✅ R/engine/backtest_engine.R  — 回测引擎（不含 BTC Guard）
  ✅ R/engine/runner.R           — 批量运行
  ✅ R/metrics/performance.R     — 基本绩效统计

风控模块:
  ✅ Polarity Filter
  ✅ Entry Timeout
  ✅ Sell Timeout
  ❌ BTC Guard (需要额外数据)
  ❌ BTC StopLoss (需要额外数据)
  ❌ Hurst Filter (需要额外数据)
  ❌ ATR Filter (需要额外数据)
  ✅ Curfew
  ✅ Data Gap Check
```

### Phase 2: BTC 风控 + 指标

```
目标: 支持 BTC Guard、StopLoss、Hurst、ATR

新增:
  R/io/btc_reader.R            — BTC 历史价格读取
  R/indicators/hurst.R          — Hurst 指数计算
  R/indicators/atr.R            — ATR 计算

引擎升级:
  backtest_engine.R 增加 BTC Guard 和 StopLoss 逻辑
```

### Phase 3: 参数优化 + 报告

```
目标: 支持参数网格搜索和自动化报告

新增:
  R/engine/param_grid.R         — 参数网格生成 + 并行运行
  R/metrics/plots.R             — 可视化图表
  scripts/run_backtest.R        — 一键运行脚本
  scripts/generate_report.R     — 一键生成报告
```

### Phase 4: 策略接入

```
目标: 你们自己的策略能即插即用

新增:
  strategies/ 目录下放策略文件
  每个策略是一个函数: strategy(df, cfg) → list(side, entry_row, exit_row)
  引擎支持传入策略函数
```

---

## 九、配置文件模板

建议在 `config/strategy.yaml` 中使用以下结构（兼容 Python 版）：

```yaml
# ══════════════════════════════════════
#  Poly Backtest — 策略参数
# ══════════════════════════════════════

# 核心参数
entry_price: 0.25
profit_price: 0.26

# 风控模块开关
polarity_filter_enabled: true
btc_guard_enabled: true
btc_stoploss_enabled: true
curfew_enabled: true
data_gap_check_enabled: true
entry_timeout_enabled: true
sell_timeout_enabled: true
hurst_filter_enabled: false
atr_filter_enabled: false

# 风控阈值
polarity_max: 0.35
btc_diff_max: 50
btc_diff_stoploss: 80
entry_timeout: 90
sell_timeout: 180
settle_wait: 270
cooldown: 8
gap_threshold: 7
polarity_delay: 2
hurst_threshold: 0.5
hurst_window: 900
curfew_hours: [20]

# 回测专用
backtest_fill_model: "ask_bid"
trade_shares: 100
initial_capital: 1000
```

---

## 十、R 依赖建议

| 包 | 用途 |
|----|------|
| `data.table` | 高性能数据读取和操作 |
| `yaml` | 读取 strategy.yaml |
| `ggplot2` | 可视化 |
| `lubridate` | 时间处理 |
| `foreach` + `doParallel` | 参数网格并行 (Phase 3) |

---

## 十一、验收标准

### Phase 1 验收

1. **能读取**: `read_round_csv()` 能正确读取任意一个 raw CSV
2. **能清洗**: 前向填充后无 NA（除首行）
3. **能跑单轮**: `run_one_round()` 对任意 CSV 返回正确结构的 list
4. **能批量跑**: `run_backtest()` 遍历全部 CSV，返回完整 data.frame
5. **成交逻辑正确**: 手工验证 3~5 个轮次的买卖判定与预期一致
6. **绩效输出**: 胜率、总 PnL、最大回撤等数字合理
7. **可复现**: 同一份数据、同一份配置，多次运行结果完全一致

---

## 十二、一句话总结

> 这份 PRD 的本质是：把 Poly Trader 的 `engine.py._run_one_round()` + Paper Mode 成交逻辑，从 Python 异步实时引擎，翻译成 R 语言的"逐行遍历历史 CSV"回测引擎，并补上 Python 版没有的绩效统计和可视化。
