from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "artifacts"
REQUIRED_COLUMNS = [
    "timestamp",
    "up_best_bid",
    "up_best_ask",
    "up_midpoint",
    "down_best_bid",
    "down_best_ask",
    "down_midpoint",
    "event_type",
    "size",
    "volume",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Screen rounds for natural trading patterns versus suspicious manipulation-like behavior."
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
        help=f"Output directory for screen artifacts. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--min-rows", type=int, default=500, help="Skip CSVs with fewer rows than this.")
    parser.add_argument("--max-files", type=int, default=None, help="Optional limit for quick smoke tests.")
    return parser.parse_args()


def resolve_data_dir(explicit_dir: Path | None) -> Path:
    if explicit_dir is not None:
        return explicit_dir

    primary = REPO_ROOT / "data"
    if list(primary.glob("*.csv")):
        return primary
    return primary / "raw"


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


def load_round(csv_path: Path, min_rows: int) -> tuple[pd.DataFrame | None, str | None]:
    try:
        df = pd.read_csv(csv_path, usecols=lambda col: col in REQUIRED_COLUMNS)
    except Exception as exc:  # pragma: no cover
        return None, f"read_error:{exc}"

    if len(df) < min_rows:
        return None, f"too_few_rows:{len(df)}"

    if "timestamp" not in df.columns or "up_midpoint" not in df.columns or "event_type" not in df.columns:
        return None, "missing_required_columns"

    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        return None, "invalid_timestamps"

    df["elapsed"] = (df["timestamp"] - df["timestamp"].iloc[0]).dt.total_seconds()
    if df["elapsed"].max() < 240:
        return None, "round_too_short"

    for column in ("up_best_bid", "up_best_ask", "up_midpoint", "down_best_bid", "down_best_ask", "down_midpoint", "size", "volume"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    for column in ("up_best_bid", "up_best_ask", "up_midpoint", "down_best_bid", "down_best_ask", "down_midpoint"):
        if column in df.columns:
            df[column] = df[column].ffill()

    label, winning_side = determine_settlement(df)
    if label is None:
        return None, "missing_settlement_label"

    df.attrs["round_id"] = csv_path.stem
    df.attrs["label"] = label
    df.attrs["winning_side"] = winning_side
    return df, None


def side_spread_stats(chunk: pd.DataFrame) -> tuple[float | None, float | None]:
    spreads = []
    for bid_col, ask_col in (("up_best_bid", "up_best_ask"), ("down_best_bid", "down_best_ask")):
        if bid_col in chunk.columns and ask_col in chunk.columns:
            spread = (chunk[ask_col] - chunk[bid_col]).dropna()
            if not spread.empty:
                spreads.append(spread)
    if not spreads:
        return None, None
    merged = pd.concat(spreads, ignore_index=True)
    return float(merged.mean()), float(merged.max())


def repeated_size_metrics(trades: pd.DataFrame) -> tuple[float | None, float | None, int]:
    sizes = trades["size"].dropna().round(6)
    if sizes.empty:
        return None, None, 0
    repeated_ratio = float((sizes == sizes.shift(1)).iloc[1:].mean()) if len(sizes) > 1 else 0.0
    modal_share = float(sizes.value_counts(normalize=True).iloc[0])
    return repeated_ratio, modal_share, int(sizes.nunique())


def count_sign_switches(series: pd.Series) -> int:
    diffs = series.dropna().diff().dropna()
    if diffs.empty:
        return 0
    signs = np.sign(diffs)
    signs = signs[signs != 0]
    if len(signs) <= 1:
        return 0
    return int((signs != signs.shift(1)).sum() - 1)


def build_round_metrics(df: pd.DataFrame) -> dict[str, object]:
    early = df[df["elapsed"] <= 90]
    tail = df[df["elapsed"] >= 240]
    last30 = df[(df["elapsed"] >= 270) & (df["elapsed"] <= 300)]
    trades = df[df["event_type"] == "last_trade_price"]
    tail_trades = trades[trades["elapsed"] >= 240]

    up_mid_tail = tail["up_midpoint"].dropna()
    down_mid_tail = tail["down_midpoint"].dropna()
    up_tail_range = float(up_mid_tail.max() - up_mid_tail.min()) if len(up_mid_tail) > 1 else 0.0
    down_tail_range = float(down_mid_tail.max() - down_mid_tail.min()) if len(down_mid_tail) > 1 else 0.0
    tail_range = max(up_tail_range, down_tail_range)

    mean_spread_early, max_spread_early = side_spread_stats(early)
    mean_spread_tail, max_spread_tail = side_spread_stats(last30)

    t240 = df[df["elapsed"] <= 240].dropna(subset=["up_midpoint"])
    up_mid_240 = float(t240.iloc[-1]["up_midpoint"]) if not t240.empty else None
    direction_240 = None
    reversal = None
    if up_mid_240 is not None:
        direction_240 = "up" if up_mid_240 > 0.5 else "down"
        reversal = int(direction_240 != df.attrs["winning_side"])

    big_moves = up_mid_tail.diff().abs().dropna()
    n_big_moves_tail = int((big_moves >= 0.05).sum()) if not big_moves.empty else 0
    max_single_move_tail = float(big_moves.max()) if not big_moves.empty else 0.0
    tail_switches = count_sign_switches(up_mid_tail)

    repeated_trade_size_ratio, modal_trade_size_share, unique_trade_sizes = repeated_size_metrics(trades)

    volume_start = df["volume"].dropna().iloc[0] if df["volume"].notna().any() else None
    volume_end = df["volume"].dropna().iloc[-1] if df["volume"].notna().any() else None
    volume_change = None if volume_start is None or volume_end is None else float(volume_end - volume_start)

    total_trade_prints = int(len(trades))
    tail_trade_share = float(len(tail_trades) / total_trade_prints) if total_trade_prints else None
    quote_updates = int((df["event_type"] == "best_bid_ask").sum())

    flag_tail_dislocation = int(tail_range >= 0.30)
    flag_tail_reversal = int((reversal == 1) and tail_range >= 0.20)
    flag_wide_tail_spread = int((mean_spread_tail or 0.0) >= 0.03)
    flag_jagged_tail = int((n_big_moves_tail >= 3) or (max_single_move_tail >= 0.10) or (tail_switches >= 8))
    flag_repeated_trade_sizes = int(
        total_trade_prints >= 20
        and (repeated_trade_size_ratio or 0.0) >= 0.25
        and (modal_trade_size_share or 0.0) >= 0.12
    )
    flag_tail_activity = int(total_trade_prints >= 20 and (tail_trade_share or 0.0) >= 0.45)

    suspicious_score = (
        flag_tail_dislocation
        + flag_tail_reversal
        + flag_wide_tail_spread
        + flag_jagged_tail
        + flag_repeated_trade_sizes
        + flag_tail_activity
    )

    if suspicious_score >= 3:
        screen_bucket = "high_review_priority"
    elif suspicious_score >= 2:
        screen_bucket = "review"
    else:
        screen_bucket = "mostly_natural"

    return {
        "round_id": df.attrs["round_id"],
        "winning_side": df.attrs["winning_side"],
        "duration_s": float(df["elapsed"].max()),
        "rows_total": int(len(df)),
        "trade_prints": total_trade_prints,
        "quote_updates": quote_updates,
        "tail_trade_share": tail_trade_share,
        "tail_range": tail_range,
        "tail_up_range": up_tail_range,
        "tail_down_range": down_tail_range,
        "mean_spread_early": mean_spread_early,
        "max_spread_early": max_spread_early,
        "mean_spread_last30": mean_spread_tail,
        "max_spread_last30": max_spread_tail,
        "up_mid_240": up_mid_240,
        "direction_240": direction_240,
        "reversal": reversal,
        "n_big_moves_tail": n_big_moves_tail,
        "max_single_move_tail": max_single_move_tail,
        "tail_direction_switches": tail_switches,
        "repeated_trade_size_ratio": repeated_trade_size_ratio,
        "modal_trade_size_share": modal_trade_size_share,
        "unique_trade_sizes": unique_trade_sizes,
        "volume_change": volume_change,
        "flag_tail_dislocation": flag_tail_dislocation,
        "flag_tail_reversal": flag_tail_reversal,
        "flag_wide_tail_spread": flag_wide_tail_spread,
        "flag_jagged_tail": flag_jagged_tail,
        "flag_repeated_trade_sizes": flag_repeated_trade_sizes,
        "flag_tail_activity": flag_tail_activity,
        "suspicious_score": suspicious_score,
        "screen_bucket": screen_bucket,
    }


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_files = sorted(path for path in data_dir.glob("*.csv") if path.is_file())
    if args.max_files is not None:
        csv_files = csv_files[: args.max_files]
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {data_dir}")

    metrics: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for csv_path in csv_files:
        round_df, reason = load_round(csv_path, min_rows=args.min_rows)
        if round_df is None:
            skipped.append({"file": csv_path.name, "reason": reason or "unknown"})
            continue
        metrics.append(build_round_metrics(round_df))

    rounds = pd.DataFrame(metrics).sort_values(["suspicious_score", "tail_range", "round_id"], ascending=[False, False, True]).reset_index(drop=True)
    skipped_df = pd.DataFrame(skipped, columns=["file", "reason"])
    if not skipped_df.empty:
        skipped_df = skipped_df.sort_values(["reason", "file"]).reset_index(drop=True)

    if rounds.empty:
        raise RuntimeError("No usable rounds were produced for integrity screening.")

    suspicious = rounds[rounds["suspicious_score"] >= 2].copy()

    flag_columns = [
        "flag_tail_dislocation",
        "flag_tail_reversal",
        "flag_wide_tail_spread",
        "flag_jagged_tail",
        "flag_repeated_trade_sizes",
        "flag_tail_activity",
    ]
    flag_summary = pd.DataFrame(
        {
            "flag_name": flag_columns,
            "count": [int(rounds[col].sum()) for col in flag_columns],
            "rate": [float(rounds[col].mean()) for col in flag_columns],
        }
    )

    score_summary = (
        rounds.groupby("suspicious_score")
        .agg(rounds=("round_id", "size"), mean_tail_range=("tail_range", "mean"), reversal_rate=("reversal", "mean"))
        .reset_index()
        .sort_values("suspicious_score")
    )

    rounds_path = output_dir / "round_metrics.csv"
    suspicious_path = output_dir / "suspicious_rounds.csv"
    flag_path = output_dir / "flag_summary.csv"
    score_path = output_dir / "score_summary.csv"
    skipped_path = output_dir / "skipped_rounds.csv"
    json_path = output_dir / "summary.json"

    rounds.to_csv(rounds_path, index=False)
    suspicious.to_csv(suspicious_path, index=False)
    flag_summary.to_csv(flag_path, index=False)
    score_summary.to_csv(score_path, index=False)
    skipped_df.to_csv(skipped_path, index=False)

    report = {
        "question": "To what extent does the market exhibit natural trading patterns versus suspicious manipulation-like or wash-trading-like signals?",
        "important_caveat": "This project is a screening tool. With only public event-stream data and no trader identities, it cannot prove wash trading or manipulation.",
        "data_dir": str(data_dir),
        "total_csv_files_seen": len(csv_files),
        "usable_rounds": int(len(rounds)),
        "skipped_rounds": int(len(skipped_df)),
        "review_rounds": int((rounds["suspicious_score"] >= 2).sum()),
        "high_review_priority_rounds": int((rounds["suspicious_score"] >= 3).sum()),
        "mean_tail_range": round(float(rounds["tail_range"].mean()), 6),
        "mean_reversal_rate": round(float(rounds["reversal"].dropna().mean()), 6) if rounds["reversal"].notna().any() else None,
    }
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Data directory: {data_dir}")
    print(f"Usable rounds: {len(rounds)}")
    print(f"Suspicious score >= 2: {(rounds['suspicious_score'] >= 2).sum()}")
    print(f"High review priority: {(rounds['suspicious_score'] >= 3).sum()}")
    print(f"Wrote: {rounds_path}")
    print(f"Wrote: {suspicious_path}")
    print(f"Wrote: {flag_path}")
    print(f"Wrote: {score_path}")
    print(f"Wrote: {skipped_path}")
    print(f"Wrote: {json_path}")


if __name__ == "__main__":
    main()
