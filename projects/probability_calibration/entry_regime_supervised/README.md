# Entry Regime Supervised

这个子项目专门为**监督学习入场判断**准备数据。

目标不是预测最后涨跌，而是预测：

- `trend_dangerous`：更像单边危险行情，不适合均值回归
- `mean_reversion_ok`：更像震荡友好行情，更适合均值回归

标签来自上一级脚本 `cluster_regime_features.R` 的整轮聚类结果。  
特征只使用**本轮前 30 秒**可观测到的数据，避免未来信息泄露。

## 运行

在仓库根目录执行：

```powershell
Rscript -e "source('projects/probability_calibration/entry_regime_supervised/build_first30_regime_dataset.R'); main(n=1000)"
```

如果你在 RStudio 里跑，推荐这样：

```r
source("projects/probability_calibration/entry_regime_supervised/build_first30_regime_dataset.R")
run_interactive()
```

## 输出

默认会生成到：

- `projects\probability_calibration\entry_regime_supervised\artifacts\first30_regime_dataset.csv`
- `projects\probability_calibration\entry_regime_supervised\artifacts\first30_regime_summary.csv`

## 数据集含义

每一行是一轮市场，包含：

1. 前 30 秒特征，例如：
   - `er_0_30`
   - `hurst_0_30`
   - `path_length_0_30`
   - `crossing_count_0_30`
   - `current_up_prob_30`
   - `move_0_30`
2. 监督学习目标：
   - `regime`
   - `target_trend_dangerous`

所以这个数据集可以直接拿去做：

- logistic regression
- decision tree
- random forest

用于判断这轮市场**是否值得做均值回归入场**。
