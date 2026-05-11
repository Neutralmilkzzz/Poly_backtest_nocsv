from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR / "artifacts"
REQUIRED_COLUMNS = ["timestamp", "up_midpoint", "down_midpoint", "event_type"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Study the calibration and accuracy of Polymarket implied probabilities."
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
        help=f"Output directory for study artifacts. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--checkpoint-seconds",
        type=str,
        default="30,60,90,120,180,240,270",
        help="Comma-separated checkpoints (in seconds) at which to sample implied probabilities.",
    )
    parser.add_argument("--bin-size", type=float, default=0.05, help="Calibration bin width.")
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


def parse_checkpoints(raw: str) -> list[int]:
    checkpoints = sorted({int(part.strip()) for part in raw.split(",") if part.strip()})
    if not checkpoints:
        raise ValueError("At least one checkpoint is required.")
    return checkpoints


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

    if "timestamp" not in df.columns or "up_midpoint" not in df.columns:
        return None, "missing_required_columns"

    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", errors="coerce", utc=True)
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        return None, "invalid_timestamps"

    df["elapsed"] = (df["timestamp"] - df["timestamp"].iloc[0]).dt.total_seconds()
    if df["elapsed"].max() < 240:
        return None, "round_too_short"

    for column in ("up_midpoint", "down_midpoint"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").ffill()

    label, winning_side = determine_settlement(df)
    if label is None:
        return None, "missing_settlement_label"

    df.attrs["round_id"] = csv_path.stem
    df.attrs["label"] = label
    df.attrs["winning_side"] = winning_side
    return df, None


def snapshot_rows(df: pd.DataFrame, checkpoints: list[int]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    label = int(df.attrs["label"])

    for checkpoint in checkpoints:
        window = df[df["elapsed"] <= checkpoint]
        if window.empty:
            continue

        valid = window.dropna(subset=["up_midpoint"])
        if valid.empty:
            continue

        snap = valid.iloc[-1]
        implied_up = float(snap["up_midpoint"])
        if math.isnan(implied_up):
            continue

        implied_down = None
        if "down_midpoint" in snap and pd.notna(snap["down_midpoint"]):
            implied_down = float(snap["down_midpoint"])

        clipped = min(max(implied_up, 1e-6), 1 - 1e-6)
        predicted_side = "up" if implied_up >= 0.5 else "down"
        confidence = implied_up if implied_up >= 0.5 else 1 - implied_up

        rows.append(
            {
                "round_id": df.attrs["round_id"],
                "checkpoint_s": checkpoint,
                "snapshot_elapsed_s": float(snap["elapsed"]),
                "implied_prob_up": implied_up,
                "implied_prob_down": implied_down,
                "overround": None if implied_down is None else implied_up + implied_down - 1.0,
                "label": label,
                "winning_side": df.attrs["winning_side"],
                "predicted_side": predicted_side,
                "confidence": confidence,
                "correct_direction": int((implied_up >= 0.5) == (label == 1)),
                "brier_score": (implied_up - label) ** 2,
                "log_loss": -(label * math.log(clipped) + (1 - label) * math.log(1 - clipped)),
                "abs_error": abs(implied_up - label),
            }
        )

    return rows


def make_bins(df: pd.DataFrame, bin_size: float) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    edges = np.arange(0.0, 1.0 + bin_size + 1e-9, bin_size)
    if edges[-1] < 1.0:
        edges = np.append(edges, 1.0)
    categories = pd.cut(df["implied_prob_up"], bins=edges, include_lowest=True)

    binned = (
        df.assign(probability_bin=categories)
        .groupby(["checkpoint_s", "probability_bin"], observed=False)
        .agg(
            count=("label", "size"),
            mean_implied_up=("implied_prob_up", "mean"),
            actual_up_rate=("label", "mean"),
            accuracy=("correct_direction", "mean"),
            mean_brier=("brier_score", "mean"),
        )
        .reset_index()
    )
    binned["calibration_gap"] = binned["mean_implied_up"] - binned["actual_up_rate"]
    binned["bin_midpoint"] = binned["probability_bin"].apply(lambda interval: interval.mid if pd.notna(interval) else np.nan)
    binned["probability_bin"] = binned["probability_bin"].astype(str)
    return binned


def summarize_checkpoints(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()

    summary = (
        df.groupby("checkpoint_s")
        .agg(
            count=("label", "size"),
            mean_implied_up=("implied_prob_up", "mean"),
            actual_up_rate=("label", "mean"),
            accuracy=("correct_direction", "mean"),
            mean_confidence=("confidence", "mean"),
            mean_brier=("brier_score", "mean"),
            mean_log_loss=("log_loss", "mean"),
            mean_abs_error=("abs_error", "mean"),
            mean_overround=("overround", "mean"),
        )
        .reset_index()
    )
    summary["calibration_gap"] = summary["mean_implied_up"] - summary["actual_up_rate"]
    return summary.sort_values("checkpoint_s").reset_index(drop=True)


def main() -> None:
    args = parse_args()
    data_dir = resolve_data_dir(args.data_dir)
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    checkpoints = parse_checkpoints(args.checkpoint_seconds)
    csv_files = sorted(path for path in data_dir.glob("*.csv") if path.is_file())
    if args.max_files is not None:
        csv_files = csv_files[: args.max_files]
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {data_dir}")

    snapshot_records: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for csv_path in csv_files:
        round_df, reason = load_round(csv_path, min_rows=args.min_rows)
        if round_df is None:
            skipped.append({"file": csv_path.name, "reason": reason or "unknown"})
            continue
        snapshot_records.extend(snapshot_rows(round_df, checkpoints))

    snapshots = pd.DataFrame(snapshot_records).sort_values(["checkpoint_s", "round_id"]).reset_index(drop=True)
    skipped_df = pd.DataFrame(skipped, columns=["file", "reason"])
    if not skipped_df.empty:
        skipped_df = skipped_df.sort_values(["reason", "file"]).reset_index(drop=True)

    if snapshots.empty:
        raise RuntimeError("No usable calibration snapshots were produced.")

    checkpoint_summary = summarize_checkpoints(snapshots)
    calibration_bins = make_bins(snapshots, args.bin_size)

    snapshots_path = output_dir / "calibration_snapshots.csv"
    summary_path = output_dir / "checkpoint_summary.csv"
    bins_path = output_dir / "calibration_bins.csv"
    skipped_path = output_dir / "skipped_rounds.csv"
    json_path = output_dir / "summary.json"

    snapshots.to_csv(snapshots_path, index=False)
    checkpoint_summary.to_csv(summary_path, index=False)
    calibration_bins.to_csv(bins_path, index=False)
    skipped_df.to_csv(skipped_path, index=False)

    overall = {
        "rows": int(len(snapshots)),
        "rounds": int(snapshots["round_id"].nunique()),
        "accuracy": round(float(snapshots["correct_direction"].mean()), 4),
        "mean_brier": round(float(snapshots["brier_score"].mean()), 6),
        "mean_log_loss": round(float(snapshots["log_loss"].mean()), 6),
        "mean_abs_error": round(float(snapshots["abs_error"].mean()), 6),
        "mean_overround": round(float(snapshots["overround"].dropna().mean()), 6) if snapshots["overround"].notna().any() else None,
    }
    report = {
        "question": "How calibrated and accurate are Polymarket implied probabilities?",
        "data_dir": str(data_dir),
        "checkpoints": checkpoints,
        "bin_size": args.bin_size,
        "total_csv_files_seen": len(csv_files),
        "skipped_rounds": int(len(skipped_df)),
        "overall": overall,
    }
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"Data directory: {data_dir}")
    print(f"Usable snapshot rows: {len(snapshots)}")
    print(f"Unique rounds: {snapshots['round_id'].nunique()}")
    print(f"Skipped rounds: {len(skipped_df)}")
    print(f"Wrote: {snapshots_path}")
    print(f"Wrote: {summary_path}")
    print(f"Wrote: {bins_path}")
    print(f"Wrote: {skipped_path}")
    print(f"Wrote: {json_path}")


if __name__ == "__main__":
    main()
