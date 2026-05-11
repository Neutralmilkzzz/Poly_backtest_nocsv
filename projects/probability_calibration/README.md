# Probability Calibration

这个项目专门回答一个问题：

> **Polymarket 的 5 分钟 BTC 市场给出的隐含概率，到底准不准？校准得好不好？**

## 这个项目会做什么

它会把每一轮市场在不同时间点的 `up_midpoint` 当作**市场给出的隐含概率**，然后拿最终结果去对照。

脚本默认会在这些时间点取快照：

- 30 秒
- 60 秒
- 90 秒
- 120 秒
- 180 秒
- 240 秒
- 270 秒

## 你会得到哪些输出

运行：

```powershell
python projects\probability_calibration\build_calibration_study.py
```

会生成：

- `projects\probability_calibration\artifacts\calibration_snapshots.csv`
- `projects\probability_calibration\artifacts\checkpoint_summary.csv`
- `projects\probability_calibration\artifacts\calibration_bins.csv`
- `projects\probability_calibration\artifacts\skipped_rounds.csv`
- `projects\probability_calibration\artifacts\summary.json`

## 这些文件分别是什么

1. `calibration_snapshots.csv`  
   每一行是“某一轮在某个时间点的一次概率预测”。

2. `checkpoint_summary.csv`  
   看每个时间点整体表现如何，比如 30 秒时准不准，180 秒时准不准。

3. `calibration_bins.csv`  
   用来做校准分析。比如市场说 70% UP 的那些情况，最后真的有多大比例是 UP。

4. `summary.json`  
   一个总览摘要。

## 先别急着跑模型

这个项目的重点不是“训练机器学习模型”，而是先判断：

- 市场概率有没有信息量
- 市场概率是**校准良好**，还是**系统性高估/低估**
- 越接近结算，概率会不会变得更准

## 建议先看什么

先读 `TEACHING_GUIDE.md`。那份文档会从“什么叫 calibration”开始，带你把这个研究问题讲明白。

## 在 RStudio 里怎么跑这些 R 脚本

现在这些 R 脚本都支持同一种 RStudio 用法：

1. `source("...脚本路径...")`
2. `run_interactive()`

例如：

```r
source("projects/probability_calibration/recent10_brier.R")
run_interactive()
```

如果你不想交互式输入，也仍然可以直接调用 `main(...)` 传参数。

## Regime clustering：用 ER / Hurst 分辨单边与震荡

如果你想把市场分成：

- **trend_dangerous**：更像大单边，对均值回归危险
- **mean_reversion_ok**：更像正常震荡，对均值回归友好

可以运行：

```powershell
Rscript -e "source('projects/probability_calibration/cluster_regime_features.R'); main(n=1000)"
```

在 RStudio 里也可以直接：

```r
source("projects/probability_calibration/cluster_regime_features.R")
run_interactive()
```

这个脚本会：

1. 对每一轮提取 `ER`、`Hurst`、净位移、尾盘位移、穿越 0.5 次数等特征
2. 先做归一化，再给 `ER` / `Hurst` 更高权重
3. 用 K-means 分成两类
4. 输出：
   - `projects\probability_calibration\artifacts\regime_cluster_assignments.csv`
   - `projects\probability_calibration\artifacts\regime_cluster_feature_centers.csv`
   - `projects\probability_calibration\artifacts\regime_cluster_summary.csv`
   - `projects\probability_calibration\artifacts\regime_cluster_paths.png`

默认归一化方法是 **z-score**：

```text
z = (x - mean(x)) / sd(x)
```

这样做的原因是：K-means 用的是距离，如果不同特征的数值尺度差太大，不先归一化，权重会失真。

## Entry regime supervised：构建前 30 秒监督学习数据

如果你想训练一个模型，只根据**本轮前 30 秒**判断这轮更像：

- `trend_dangerous`
- `mean_reversion_ok`

可以运行：

```powershell
Rscript -e "source('projects/probability_calibration/entry_regime_supervised/build_first30_regime_dataset.R'); main(n=1000)"
```

在 RStudio 里也可以直接：

```r
source("projects/probability_calibration/entry_regime_supervised/build_first30_regime_dataset.R")
run_interactive()
```

这个子项目会生成监督学习数据集，标签来自整轮聚类结果，但输入特征只使用前 30 秒数据，因此适合后续做入场过滤器。
