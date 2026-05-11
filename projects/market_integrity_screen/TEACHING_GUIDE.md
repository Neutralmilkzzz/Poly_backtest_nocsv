# Teaching Guide: Studying Market Integrity Without Overclaiming

同学，这个题目很吸引人，但也很危险。危险不在于技术难，而在于：

> **你很容易在证据不够的时候，说得太满。**

所以这份指南最重要的任务，是教你怎么把这个问题做得**严谨**。

## 1. 这个题目真正能问什么

你给的问题是：

> To what extent does the market exhibit natural trading patterns versus signs of market manipulation or wash trading?

把它翻成数据科学语言，更稳妥的版本是：

> **在公开事件流层面，这个市场有哪些轮次看起来更自然，哪些轮次出现了值得复核的异常微观结构信号？**

注意这里的关键词是：

- **natural patterns**
- **suspicious signals**
- **review**

而不是：

- “我证明了有人操纵”
- “我证明了 wash trading”

## 2. 为什么不能直接证明 wash trading

因为 wash trading 的核心是：

> 同一主体或关联主体，自己和自己成交，制造虚假活跃度。

要证明这个，通常需要：

1. 账户层面的身份信息
2. 订单层面的买卖匹配关系
3. 自成交标记或更细粒度撮合数据

而你现在只有公开事件流，所以你只能说：

> 这些模式和操纵/刷量“相容”或“可疑”，但不能单凭这份数据定罪。

这句话在报告里必须写。

## 3. 那我们还能做什么

很多。

即使不能“证明”，你仍然可以做一个很扎实的**异常筛查项目**：

1. 定义“更自然”的交易特征
2. 定义“更可疑”的微观结构信号
3. 给每一轮打一个 suspicious score
4. 对高分轮次做人工复核和案例分析

这已经是一个非常完整的课程项目了。

## 4. 这个脚本是怎么设计的

它会给每一轮计算一些你可以解释得清楚的特征。

### 4.1 尾盘大幅波动

`tail_range`

看 240 秒以后，`up_midpoint` / `down_midpoint` 的波动区间有多大。  
如果尾盘突然大幅拉升或砸盘，这个值通常会变大。

### 4.2 方向反转

`reversal`

看 240 秒时市场隐含的方向，和最终结果是不是反过来了。  
反转本身不等于操纵，但如果反转和剧烈波动、价差放大一起出现，就更值得复核。

### 4.3 最后 30 秒价差变宽

`mean_spread_last30`

如果临近结算时流动性突然变差、挂单稀薄，价差可能会明显变宽。  
这可能意味着自然紧张交易，也可能意味着市场深度不足、容易被推着走。

### 4.4 尾盘大跳动和来回抖动

- `n_big_moves_tail`
- `max_single_move_tail`
- `tail_direction_switches`

这些在看尾盘是不是出现了很多大幅跳动，或者价格方向来回剧烈切换。

### 4.5 重复 size 模式

- `repeated_trade_size_ratio`
- `modal_trade_size_share`

如果交易 size 反复出现非常相似的数值，这可能只是算法交易，也可能是刷量嫌疑的一个弱信号。  
所以它只能是**辅助证据**，不能单独下结论。

### 4.6 尾盘成交占比异常集中

`tail_trade_share`

如果很多成交都挤在最后阶段，也值得关注。

## 5. 脚本怎么把这些特征变成“复核优先级”

脚本会给每一轮打一些 flag：

1. `flag_tail_dislocation`
2. `flag_tail_reversal`
3. `flag_wide_tail_spread`
4. `flag_jagged_tail`
5. `flag_repeated_trade_sizes`
6. `flag_tail_activity`

然后把这些 flag 加总成 `suspicious_score`。

大致上：

- `0-1`：`mostly_natural`
- `2`：`review`
- `3+`：`high_review_priority`

这是个**研究筛查分数**，不是法律结论。

## 6. 你应该怎么看输出文件

### `round_metrics.csv`

这是主表。每一行是一轮，包含所有特征和 flag。

### `suspicious_rounds.csv`

这是优先看名单。你做案例分析时先从这里挑样本。

### `flag_summary.csv`

看不同异常信号出现得多不多。

### `score_summary.csv`

看不同 suspicious score 档位的轮次数量和均值特征。

## 7. 这个题目最适合怎么写报告

如果我是你的导师，我会建议你这样讲：

1. **研究目标**  
   不是证明违法，而是评估市场微观结构中异常信号的普遍程度。

2. **数据局限性**  
   没有账户级数据，因此只能做 anomaly screening。

3. **特征设计**  
   尾盘波动、反转、价差、重复 size、活动集中度。

4. **筛查方法**  
   用多个 flag 组成 suspicious score。

5. **结果**  
   高分轮次占比多少？主要由哪些 flag 驱动？

6. **案例分析**  
   选几轮高分样本，逐笔观察尾盘行为。

7. **结论与边界**  
   我们发现了“可疑模式”，但不能仅凭这些数据直接证明 wash trading。

## 8. 你最容易犯的错误

### 错误 1：把异常等同于操纵

异常只能说明“值得看”，不能自动推出“有人操纵”。

### 错误 2：阈值写得像天条

比如 `tail_range >= 0.30` 只是研究阈值，不是自然法则。  
你应该把它写成“screening heuristic（筛查启发式规则）”。

### 错误 3：忽略自然解释

有些异常可能来自：

- 临近结算的信息更新
- 流动性本来就薄
- 正常的大资金交易

所以你要给出多种可能解释。

## 9. 你下一步怎么推进

1. 跑 `build_market_integrity_screen.py`
2. 看 `summary.json`
3. 打开 `flag_summary.csv`
4. 挑几轮 `high_review_priority` 做案例分析
5. 再和“看起来较自然”的轮次对比

## 10. 老教授最后提醒一句

这个题目真正厉害的地方不是“我抓到了坏人”，而是：

> **我知道在证据有限时，怎样做出严格、克制、可信的研究结论。**

这比随便喊一句“市场被操纵了”要强得多。
