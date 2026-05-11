# Market Integrity Screen

这个项目专门回答另一个更像研究论文的问题：

> **这个市场更多是在自然交易，还是能看到明显的操纵/刷量嫌疑信号？**

## 先说清楚一件事

这个项目做的是 **screening（筛查）**，不是法庭判决。

你现在手里的数据是公开事件流，不包含：

- 交易者身份
- 账户归属
- 对手盘关系
- 自成交标记

所以你可以做的是：

- 找出**异常轮次**
- 量化**可疑微观结构特征**
- 比较“更自然”的轮次和“更需要复核”的轮次

但你**不能仅凭这份数据直接证明 wash trading**。

## 快速开始

运行：

```powershell
python projects\market_integrity_screen\build_market_integrity_screen.py
```

会生成：

- `projects\market_integrity_screen\artifacts\round_metrics.csv`
- `projects\market_integrity_screen\artifacts\suspicious_rounds.csv`
- `projects\market_integrity_screen\artifacts\flag_summary.csv`
- `projects\market_integrity_screen\artifacts\score_summary.csv`
- `projects\market_integrity_screen\artifacts\skipped_rounds.csv`
- `projects\market_integrity_screen\artifacts\summary.json`

## 这个脚本会看哪些信号

1. 尾盘价格区间是否异常大
2. 240 秒时的方向和最终结果是否发生反转
3. 最后 30 秒的价差是否明显变宽
4. 尾盘是否出现很多大跳动
5. 交易 size 是否出现异常重复
6. 尾盘成交活跃度是否突然集中

## 结果怎么用

你可以把它理解成一个“研究生助教先帮你筛一遍”的工具：

- `mostly_natural`：看起来比较自然
- `review`：值得人工复核
- `high_review_priority`：优先细看

## 建议先读什么

先读 `TEACHING_GUIDE.md`。那份文档会解释为什么这个题目要非常小心措辞，以及你该怎么把“异常筛查”写成一个严谨的课题。
