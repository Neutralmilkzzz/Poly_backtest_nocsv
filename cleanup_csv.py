import os
from datetime import date

RAW_DIR = "data/raw"
MIN_ROWS = 500
DATE_THRESHOLD = date(2026, 3, 17)

deleted_rows = 0
deleted_date = 0
kept = 0

for filename in os.listdir(RAW_DIR):
    if not filename.endswith(".csv"):
        continue
    filepath = os.path.join(RAW_DIR, filename)

    # Check row count
    with open(filepath, "r", encoding="utf-8") as f:
        row_count = sum(1 for _ in f)
    if row_count < MIN_ROWS:
        os.remove(filepath)
        deleted_rows += 1
        print(f"[DELETED - rows] {filename} ({row_count} rows)")
        continue

    # Check date
    # Filename format: YYYY-MM-DD_HH-MM-SS.csv
    date_str = filename[:10]
    try:
        file_date = date.fromisoformat(date_str)
    except ValueError:
        print(f"[SKIP - bad date] {filename}")
        continue

    if file_date < DATE_THRESHOLD:
        os.remove(filepath)
        deleted_date += 1
        print(f"[DELETED - date] {filename} ({date_str})")
        continue

    kept += 1

print(f"\nDone. Deleted {deleted_rows} files (< {MIN_ROWS} rows), "
      f"deleted {deleted_date} files (before {DATE_THRESHOLD}), "
      f"kept {kept} files.")
