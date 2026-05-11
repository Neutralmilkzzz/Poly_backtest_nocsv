# CSV 到 FST/Parquet 缓存化改造方案

> **状态：第一阶段已完成 (2026-03-25)**
>
> - FST 缓存已落地，实测读取加速约 12.8x
> - 回测和扫参默认走 FST，fst 包不存在或缓存缺失时自动回退 CSV
> - Telegram Bot 无需修改，自动受益

## 1. 目标

当前回测和扫参的主要耗时，不在 Python 控制层，而在下面三件事：

1. 反复读取 data/raw 下的大量 CSV
2. 每次读取后重复做 timestamp / numeric 解析
3. 扫参时对同一批文件重复做读取和清洗

这份方案的目标是：

1. 保留原始 CSV 作为存档层
2. 先过滤掉明显无效的垃圾文件
3. 把合格数据转换为更适合 R 高频读取的缓存格式
4. 后续回测和分析统一优先读取缓存格式

## 2. 推荐结论

对当前项目，推荐先落地 FST，而不是直接上 Parquet。

原因：

1. 当前主消费方是 R
2. 当前目标是尽快提升本地回测和扫参速度
3. FST 对 data.frame 读写速度和接入成本更合适
4. Parquet 更适合未来要和 Python / Rust / 数据仓库共享同一层数据时再引入

建议路线：

1. 第一阶段：CSV -> FST
2. 第二阶段：如未来要跨语言协作，再补 CSV/FST -> Parquet

## 3. 目标目录结构

建议把数据层拆成 4 层：

1. data/raw
   原始 CSV，只进不改

2. data/cache/fst
   合格 CSV 转换后的 FST 文件

3. data/rejected
   不合格文件清单，或直接移动垃圾文件到这里

4. data/manifest.csv
   记录每个原始文件的校验和转换状态

建议的 manifest 字段：

1. source_file
2. row_count
3. is_valid
4. reject_reason
5. cache_format
6. cache_file
7. converted_at
8. source_mtime

## 4. 数据管线

推荐固定流程如下：

1. 扫描 data/raw
2. 检查文件名、行数、必要列、时间戳可解析性
3. 行数少于 500 的文件直接判定为垃圾数据
4. 对通过校验的 CSV 做一次标准化清洗
5. 输出为 FST
6. 写 manifest
7. 回测和分析统一优先读 FST

一句话概括：

原始 CSV 负责保真，FST 负责计算。

## 5. 过滤规则建议

当前你已经有一个很明确的业务规则：

1. 少于 500 行的文件视为垃圾数据

建议转换前至少做以下校验：

1. 行数 >= 500
2. 必要列齐全：timestamp, up_best_bid, up_best_ask, up_midpoint, down_best_bid, down_best_ask, down_midpoint, event_type, volume
3. timestamp 可解析为 POSIXct
4. 文件名能解析出 round 起始时间

manifest 里的 reject_reason 建议标准化为：

1. too_few_rows
2. missing_columns
3. bad_timestamp
4. bad_filename
5. read_error

## 6. FST 与 Parquet 取舍

### FST

优点：

1. 更贴当前 R 主场景
2. 接入简单
3. 表格读写快
4. 适合本地回测缓存层

缺点：

1. 跨语言一般

### Parquet

优点：

1. 跨语言好
2. 列式格式，长期通用性更强

缺点：

1. 接入成本更高
2. 当前项目短期收益不一定高于 FST

当前建议：

1. 先上 FST
2. Parquet 作为未来扩展选项保留

## 7. 依赖清单

### Python

Python 依赖继续放在 [requirements.txt](requirements.txt)：

1. aiohttp
2. PyYAML

### R

R 包依赖建议统一放到 [requirements-r.txt](requirements-r.txt)：

1. yaml
2. ggplot2
3. fst

如果未来切换到 Parquet，再额外加入：

1. arrow

安装命令：

```powershell
Rscript -e "install.packages(scan('requirements-r.txt', what='character', quiet=TRUE), repos='https://cloud.r-project.org')"
```

## 8. 代码改造顺序

> **以下 5 步全部已完成 (2026-03-25)**

1. ✅ 新增转换脚本
   [scripts/build_fst_cache.R](../scripts/build_fst_cache.R)

2. ✅ 新增缓存读取器
   [R/io/cache_reader.R](../R/io/cache_reader.R)

3. ✅ 在主读取链路里增加优先级：
   先查 cache，再回退 CSV

4. ✅ 在 runner 里增加开关：
   use_cache = TRUE/FALSE（默认 TRUE）

5. ✅ Telegram bot 保持不感知底层差异
   只调用回测入口，不关心数据来自 CSV 还是 FST

## 9. 关键改动点

### 9.1 新增转换脚本

建议职责：

1. 扫 data/raw
2. 校验 CSV
3. 清洗并标准化列类型
4. 写入 data/cache/fst
5. 生成 manifest

### 9.2 修改读取器

> **已完成** — `read_round_data()` 在 [R/io/cache_reader.R](../R/io/cache_reader.R) 中实现

当前读取入口主要在：

1. [R/io/data_reader.R](../R/io/data_reader.R) — 原始 CSV 读取（保留）
2. [R/io/cache_reader.R](../R/io/cache_reader.R) — FST 优先，CSV 回退
3. [R/engine/runner.R](../R/engine/runner.R) — 已切换到 `read_round_data()`

改造结果：

1. list_rounds 仍然按原始文件名顺序管理轮次
2. read_round_data 优先读 FST cache_file
3. 若 cache 不存在或 fst 包不可用，自动回退读 CSV

## 10. 增量转换策略

不要每次分析都全量重转。

建议只做增量：

1. 原始文件没变，并且 cache 存在 -> 跳过
2. 原始文件新增 -> 新转
3. 原始文件更新时间变化 -> 重转
4. manifest 缺记录 -> 补录

这样才能真正把 CSV 的重复解析成本打掉。

## 11. 回测层改造收益

做完后，性能收益主要来自：

1. 数值列不再反复 as.numeric
2. 时间列不再反复 as.POSIXct
3. 文本 CSV 解析大幅减少
4. 单次回测和扫参都能明显收益

对于当前项目，收益最大的是：

1. 单次大样本回测
2. 单因子扫参
3. 专题分析脚本

## 12. 推荐落地顺序

建议分 3 步：

### Step 1

先引入 FST 和 manifest，不改业务语义。

目标：

1. 保证读出来的数据和 CSV 结果一致
2. 只是更快，不改变回测结论

### Step 2

把 run_backtest.R、扫参脚本、专题分析脚本都切到缓存优先读取。

### Step 3

如未来确实需要跨语言，再评估是否补 Parquet 层。

## 13. 不建议的做法

1. 直接删掉原始 CSV
2. 每次分析前全量重转全部文件
3. 在没有 manifest 的前提下盲目混用 CSV 和缓存
4. 一开始同时维护 FST 和 Parquet 两套主链路

## 14. 最终建议

当前最合适的方案是：

1. 保留 data/raw 作为原始层
2. 过滤掉 500 行以下垃圾 CSV
3. 把合格文件转换到 data/cache/fst
4. 统一让回测和分析优先读 FST
5. 用 requirements-r.txt 管理新增 R 包 fst

这条路的收益/改造成本比最高，也最符合当前项目以 R 为核心的现实。