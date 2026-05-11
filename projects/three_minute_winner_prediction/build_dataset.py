from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "artifacts"
REQUIRED_COLUMNS = {
    "timestamp",
    "up_best_bid",
    "up_best_ask",
    "up_midpoint",
    "down_best_bid",
    "down_best_ask",
    "down_midpoint",
    "event_type",
    "volume",
    "size",
    "btc_price",
    "btc_target",
    "btc_diff",
}
QUOTE_COLUMNS = [
    "up_best_bid",
    "up_best_ask",
    "up_midpoint",
    "down_best_bid",
    "down_best_ask",
    "down_midpoint",
]
NUMERIC_COLUMNS = QUOTE_COLUMNS + ["volume", "size", "btc_price", "btc_target", "btc_diff"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a round-level dataset for predicting the winner from the first 180 seconds."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory containing round CSV files. Defaults to repo data/ if CSVs exist there, otherwise data/raw/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory for dataset artifacts. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--make-splits",
        action="store_true",
        help="Also create chronological train/validation/test CSVs. Default is off for the teaching-first workflow.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Optional limit for quick experiments or teaching demos.",
    )
    parser.add_argument("--train-ratio", type=float, default=0.70, help="Training split ratio.")
    parser.add_argument("--val-ratio", type=float, default=0.15, help="Validation split ratio.")
    parser.add_argument("--test-ratio", type=float, default=0.15, help="Test split ratio.")
    parser.add_argument("--min-rows", type=int, default=500, help="Skip CSVs with fewer rows than this.")
    return parser.parse_args()


def resolve_data_dir(explicit_dir: Path | None) -> Path:
    if explicit_dir is not None:
        return explicit_dir

    primary = REPO_ROOT / "data"
    if list(primary.glob("*.csv")):
        return primary

    fallback = primary / "raw"
    return fallback


def determine_settlement(df: pd.DataFrame) -> tuple[int | None, str | None]:
    for start, end in ((285, 298), (240, 285)):
        window = df[(df["elapsed"] >= start) & (df["elapsed"] <= end)]
        up_vals = window["up_midpoint"].dropna()
        if up_vals.empty:
            continue
        last_up = float(up_vals.iloc[-1])
        if last_up > 0.5:
            return 1, "up"
        if last_up < 0.5:
            return 0, "down"
    return None, None


def safe_float(value: float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, (np.floating, float, np.integer, int)):
        if pd.isna(value):
            return None
        return float(value)
    return float(value)


def summarize_series(series: pd.Series, prefix: str) -> dict[str, float | None]:
    clean = series.dropna()
    if clean.empty:
        return {
            f"{prefix}_first": None,
            f"{prefix}_last": None,
            f"{prefix}_mean": None,
            f"{prefix}_std": None,
            f"{prefix}_min": None,
            f"{prefix}_max": None,
            f"{prefix}_range": None,
            f"{prefix}_slope_per_sec": None,
        }

    first = clean.iloc[0]
    last = clean.iloc[-1]
    elapsed_span = clean.index[-1] - clean.index[0]
    slope = None if elapsed_span == 0 else (last - first) / elapsed_span
    return {
        f"{prefix}_first": safe_float(first),
        f"{prefix}_last": safe_float(last),
        f"{prefix}_mean": safe_float(clean.mean()),
        f"{prefix}_std": safe_float(clean.std(ddof=0)),
        f"{prefix}_min": safe_float(clean.min()),
        f"{prefix}_max": safe_float(clean.max()),
        f"{prefix}_range": safe_float(clean.max() - clean.min()),
        f"{prefix}_slope_per_sec": safe_float(slope),
    }


def load_round(csv_path: Path, min_rows: int) -> tuple[pd.DataFrame | None, str | None]:
    try:
        df = pd.read_csv(csv_path, usecols=lambda col: col in REQUIRED_COLUMNS)
    except Exception as exc:  # pragma: no cover - only used for bad input files
        return None, f"read_error: {exc}"

    if len(df) < min_rows:
        return None, f"too_few_rows:{len(df)}"

    if "timestamp" not in df.columns or "up_midpoint" not in df.columns or "event_type" not in df.columns:
        return None, "missing_required_columns"

    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        return None, "invalid_timestamps"

    start_ts = df["timestamp"].iloc[0]
    df["elapsed"] = (df["timestamp"] - start_ts).dt.total_seconds()

    for column in NUMERIC_COLUMNS:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in QUOTE_COLUMNS:
        if column in df.columns:
            df[column] = df[column].ffill()

    if df["elapsed"].max() < 180:
        return None, "round_shorter_than_180s"

    label, winning_side = determine_settlement(df)
    if label is None:
        return None, "missing_settlement_label"

    df.attrs["round_id"] = csv_path.stem
    df.attrs["round_start_time"] = start_ts
    df.attrs["label"] = label
    df.attrs["winning_side"] = winning_side
    return df, None


def build_feature_row(df: pd.DataFrame) -> dict[str, object] | None:
    early = df[df["elapsed"] <= 180].copy()
    if len(early) < 20:
        return None

    early = early.set_index("elapsed")
    row: dict[str, object] = {
        "round_id": df.attrs["round_id"],
        "round_start_time": df.attrs["round_start_time"].isoformat(),
        "label": df.attrs["label"],
        "winning_side": df.attrs["winning_side"],
        "rows_first_180s": int(len(early)),
        "event_type_count": int(early["event_type"].nunique()),
        "last_trade_price_count": int((early["event_type"] == "last_trade_price").sum()),
        "best_bid_ask_count": int((early["event_type"] == "best_bid_ask").sum()),
        "orderbook_poll_count": int((early["event_type"] == "orderbook_poll").sum()),
        "btc_price_update_count": int((early["event_type"] == "btc_price_update").sum()),
    }

    row.update(summarize_series(early["up_midpoint"], "up_mid"))
    row.update(summarize_series(early["down_midpoint"], "down_mid"))

    if {"up_best_ask", "up_best_bid"}.issubset(early.columns):
        row.update(summarize_series(early["up_best_ask"] - early["up_best_bid"], "up_spread"))
    if {"down_best_ask", "down_best_bid"}.issubset(early.columns):
        row.update(summarize_series(early["down_best_ask"] - early["down_best_bid"], "down_spread"))

    if "btc_price" in early.columns:
        row.update(summarize_series(early["btc_price"], "btc_price"))
        clean = early["btc_price"].dropna()
        row["btc_price_return_pct"] = safe_float(
            None if clean.empty or clean.iloc[0] == 0 else ((clean.iloc[-1] / clean.iloc[0]) - 1.0) * 100.0
        )
    if "btc_diff" in early.columns:
        row.update(summarize_series(early["btc_diff"], "btc_diff"))
    if "volume" in early.columns:
        row.update(summarize_series(early["volume"], "volume"))
        clean = early["volume"].dropna()
        row["volume_change"] = safe_float(None if clean.empty else clean.iloc[-1] - clean.iloc[0])
    if "size" in early.columns:
        clean = early["size"].dropna()
        row["trade_size_sum"] = safe_float(clean.sum()) if not clean.empty else None
        row["trade_size_mean"] = safe_float(clean.mean()) if not clean.empty else None
        row["trade_size_max"] = safe_float(clean.max()) if not clean.empty else None

    return row


def split_dataset(df: pd.DataFrame, train_ratio: float, val_ratio: float, test_ratio: float) -> dict[str, pd.DataFrame]:
    total_ratio = train_ratio + val_ratio + test_ratio
    if not np.isclose(total_ratio, 1.0):
        raise ValueError("train_ratio + val_ratio + test_ratio must equal 1.0")
    if len(df) < 3:
        raise ValueError("Need at least 3 rounds to create train/validation/test splits.")

    train_end = max(1, int(len(df) * train_ratio))
    val_end = max(train_end + 1, int(len(df) * (train_ratio + val_ratio)))
    if val_end >= len(df):
        val_end = len(df) - 1

    return {
        "train": df.iloc[:train_end].copy(),
        "validation": df.iloc[train_end:val_end].copy(),
        "test": df.iloc[val_end:].copy(),
    }


def label_summary(df: pd.DataFrame) -> dict[str, object]:
    if df.empty:
        return {"rows": 0, "up_win_rate": None, "down_win_rate": None}
    up_rate = float((df["label"] == 1).mean())
    return {
        "rows": int(len(df)),
        "up_win_rate": round(up_rate, 4),
        "down_win_rate": round(1.0 - up_rate, 4),
    }


def iter_csv_files(data_dir: Path) -> Iterable[Path]:
    return sorted(path for path in data_dir.glob("*.csv") if path.is_file())


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []

    csv_files = list(iter_csv_files(data_dir))
    if args.max_files is not None:
        csv_files = csv_files[: args.max_files]
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {data_dir}")

    for csv_path in csv_files:
        round_df, skip_reason = load_round(csv_path, min_rows=args.min_rows)
        if round_df is None:
            skipped.append({"file": csv_path.name, "reason": skip_reason})
            continue

        feature_row = build_feature_row(round_df)
        if feature_row is None:
            skipped.append({"file": csv_path.name, "reason": "too_few_rows_in_first_180s"})
            continue
        rows.append(feature_row)

    dataset = pd.DataFrame(rows).sort_values(["round_start_time", "round_id"]).reset_index(drop=True)
    skipped_df = pd.DataFrame(skipped, columns=["file", "reason"])
    if not skipped_df.empty:
        skipped_df = skipped_df.sort_values(["reason", "file"]).reset_index(drop=True)

    if dataset.empty:
        raise RuntimeError("No usable rounds were found. Check skipped_rounds.csv for details.")

    all_path = output_dir / "all_rounds.csv"
    skipped_path = output_dir / "skipped_rounds.csv"
    summary_path = output_dir / "summary.json"

    dataset.to_csv(all_path, index=False)
    skipped_df.to_csv(skipped_path, index=False)

    summary = {
        "data_dir": str(data_dir),
        "total_csv_files_seen": len(csv_files),
        "usable_rounds": int(len(dataset)),
        "skipped_rounds": int(len(skipped_df)),
        "feature_window_seconds": 180,
        "label_window": "285-298s with fallback to 240-285s using last up_midpoint",
        "split_strategy": "not_created_yet",
    }

    if args.make_splits:
        splits = split_dataset(dataset, args.train_ratio, args.val_ratio, args.test_ratio)
        for split_name, split_df in splits.items():
            split_df.to_csv(output_dir / f"{split_name}.csv", index=False)
        summary["split_strategy"] = "chronological"
        summary["splits"] = {name: label_summary(split_df) for name, split_df in splits.items()}

    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Data directory: {data_dir}")
    print(f"Usable rounds: {len(dataset)}")
    print(f"Skipped rounds: {len(skipped_df)}")
    print(f"Wrote: {all_path}")
    print(f"Wrote: {skipped_path}")
    print(f"Wrote: {summary_path}")
    if args.make_splits:
        for split_name, split_df in splits.items():
            print(f"{split_name:>10}: {len(split_df)} rows")


if __name__ == "__main__":
    main()
