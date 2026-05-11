# Three-Minute Winner Prediction

这是一个给你单独开出来的小项目目录：**只用每轮前 180 秒的数据，预测最后是 UP 赢还是 DOWN 赢。**

## 你现在会在这里做什么

1. 先把逐笔事件流 CSV 变成**一轮一行**的机器学习表格。
2. 用**前三分钟**做特征。
3. 用**尾盘结算窗口**做标签。
4. 先把数据看懂；之后再按时间顺序切成 **train / validation / test** 三份。

## 为什么是三份

- `train`：拿来训练模型
- `validation`：拿来选模型、调参数
- `test`：最后一次考试，不能提前偷看

你刚才说“还有一个什么集”，那个就是 **validation set（验证集）**。

## 快速开始

在仓库根目录运行：

```powershell
python projects\three_minute_winner_prediction\build_dataset.py
```

运行后会生成：

- `projects\three_minute_winner_prediction\artifacts\all_rounds.csv`
- `projects\three_minute_winner_prediction\artifacts\skipped_rounds.csv`
- `projects\three_minute_winner_prediction\artifacts\summary.json`

如果你以后想正式切分，再运行：

```powershell
python projects\three_minute_winner_prediction\build_dataset.py --make-splits
```

那时才会额外生成：

- `projects\three_minute_winner_prediction\artifacts\train.csv`
- `projects\three_minute_winner_prediction\artifacts\validation.csv`
- `projects\three_minute_winner_prediction\artifacts\test.csv`

## 这个脚本帮你做了什么

1. 自动扫描 `data\*.csv`；如果根目录没有，就回退到 `data\raw\*.csv`
2. 跳过太短、太空、没有标签的坏文件
3. 用前 180 秒提取一组**容易讲清楚**的基础特征
4. 用晚盘 `up_midpoint` 判定最后赢家
5. 默认先不拆数据，让你先把样本表看懂；需要时再按时间顺序切分，避免时间泄漏

## 建议你先读什么

先看 `TEACHING_GUIDE.md`。那份文档不是冷冰冰的说明书，而是按“老师带你做项目”的顺序写的。
