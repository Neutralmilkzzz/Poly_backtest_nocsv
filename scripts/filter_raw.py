"""筛选 data/raw 中的 CSV：删除行数 < 500 的文件。"""
import os, glob

RAW_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "raw")
MIN_LINES = 500

files = sorted(glob.glob(os.path.join(RAW_DIR, "*.csv")))
print(f"总文件数: {len(files)}")

deleted = 0
for f in files:
    with open(f, encoding="utf-8") as fh:
        n = sum(1 for _ in fh)
    if n < MIN_LINES:
        print(f"  删除: {os.path.basename(f):30s}  ({n} 行)")
        os.remove(f)
        deleted += 1

remaining = len(glob.glob(os.path.join(RAW_DIR, "*.csv")))
print(f"\n已删除: {deleted}  |  剩余: {remaining}")
