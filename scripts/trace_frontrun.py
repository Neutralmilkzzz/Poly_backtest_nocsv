"""Trace 3 specific frontrun_slam trades step by step with full detail."""
import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings("ignore")

# Load the results to find example trades
res = pd.read_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\whale_follow_results.csv")
fr = res[(res["strategy"] == "frontrun_slam") & (res["traded"] == True)].copy()

# Pick 3 diverse examples: 1 slam_tp win, 1 settle_win, 1 settle_lose
examples = []
for et in ["slam_tp", "settle_win", "settle_lose"]:
    sub = fr[fr["exit_type"] == et]
    if len(sub) > 0:
        examples.append(sub.iloc[0])

data_dir = r"C:\Users\ZHAOKAI\data"

for idx, ex in enumerate(examples):
    fname = ex["file"]
    fpath = os.path.join(data_dir, fname)
    print(f"\n{'='*80}")
    print(f"EXAMPLE {idx+1}: {fname}")
    print(f"  Exit type: {ex['exit_type']}, PnL: {ex['pnl']:+.2f}")
    print(f"  Side bought: {ex['slam_side']}, Entry: {ex['entry_price']:.3f} @ {ex['entry_time']:.1f}s")
    print(f"  Settlement: {ex['settlement']}")
    print(f"{'='*80}")

    df = pd.read_csv(fpath, low_memory=False)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"])
    t0 = df["ts"].iloc[0]
    df["elapsed"] = (df["ts"] - t0).dt.total_seconds()
    for c in ["up_best_bid", "up_best_ask", "up_midpoint",
              "down_best_bid", "down_best_ask", "down_midpoint", "btc_diff"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df[c] = df[c].ffill()

    last_diff = df["btc_diff"].dropna()
    settlement = "up" if float(last_diff.iloc[-1]) > 0 else "down"

    # STEP 1: Show early phase (30-180s) to prove "competitive"
    early = df[(df["elapsed"] >= 30) & (df["elapsed"] <= 180)]
    early_up_avg = early["up_midpoint"].mean()
    print(f"\n--- STEP 1: Is it competitive? (early 30-180s) ---")
    print(f"  UP midpoint avg in 30-180s: {early_up_avg:.3f}")
    print(f"  Competitive? (0.30 <= avg <= 0.70): {0.30 <= early_up_avg <= 0.70}")
    # Show some snapshots
    for t in [30, 60, 90, 120, 150, 180]:
        row = df[df["elapsed"] <= t].iloc[-1] if len(df[df["elapsed"] <= t]) > 0 else None
        if row is not None:
            print(f"  @{t:3d}s: UP mid={row['up_midpoint']:.2f}, DOWN mid={row['down_midpoint']:.2f}, "
                  f"btc_diff={row['btc_diff']:.1f}" if not np.isnan(row.get('btc_diff', np.nan)) else
                  f"  @{t:3d}s: UP mid={row['up_midpoint']:.2f}, DOWN mid={row['down_midpoint']:.2f}")

    # STEP 2: Show pre-tail (180-250s) - did one side go extreme?
    print(f"\n--- STEP 2: Did one side go extreme? (180-250s) ---")
    pre_tail = df[(df["elapsed"] >= 180) & (df["elapsed"] <= 250)]
    for t in [200, 220, 240, 250]:
        row = df[df["elapsed"] <= t].iloc[-1] if len(df[df["elapsed"] <= t]) > 0 else None
        if row is not None:
            print(f"  @{t:3d}s: UP mid={row['up_midpoint']:.2f}, DOWN mid={row['down_midpoint']:.2f}, "
                  f"btc_diff={row['btc_diff']:.1f}" if not np.isnan(row.get('btc_diff', np.nan)) else
                  f"  @{t:3d}s: UP mid={row['up_midpoint']:.2f}, DOWN mid={row['down_midpoint']:.2f}")

    up_mid_240 = df[df["elapsed"] <= 240].iloc[-1]["up_midpoint"]
    down_mid_240 = df[df["elapsed"] <= 240].iloc[-1]["down_midpoint"]
    print(f"  UP mid @240s: {up_mid_240:.2f}, DOWN mid @240s: {down_mid_240:.2f}")
    extreme_side = "up" if up_mid_240 >= 0.85 else ("down" if down_mid_240 >= 0.85 else "neither")
    print(f"  Extreme side (>=0.85): {extreme_side}")

    # STEP 3: Entry - buy the OTHER side cheap
    other_side = ex["slam_side"]
    other_ask = f"{other_side}_best_ask"
    other_bid = f"{other_side}_best_bid"
    print(f"\n--- STEP 3: Buy {other_side.upper()} cheap (240-255s) ---")
    early_tail = df[(df["elapsed"] >= 240) & (df["elapsed"] <= 255)]
    print(f"  Looking for {other_side}_best_ask <= 0.20 in 240-255s:")
    for _, row in early_tail.iterrows():
        ask_val = row[other_ask]
        bid_val = row[other_bid]
        if not np.isnan(ask_val) and ask_val > 0 and ask_val <= 0.25:
            print(f"    @{row['elapsed']:.1f}s: {other_side}_ask={ask_val:.3f}, "
                  f"{other_side}_bid={bid_val:.3f}, event={row['event_type']}")
    print(f"  >>> ENTRY: Buy {other_side.upper()} @ {ex['entry_price']:.3f} at {ex['entry_time']:.1f}s")

    # STEP 4: What happens after entry?
    print(f"\n--- STEP 4: After entry - looking for exit ---")
    post = df[df["elapsed"] > ex["entry_time"]]
    tp_target = ex["entry_price"] + 0.10
    print(f"  Take-profit target: {other_side}_bid >= {tp_target:.3f}")

    # Show key moments after entry
    shown = 0
    for _, row in post.iterrows():
        bid_val = row[other_bid]
        if shown < 20 and (row["elapsed"] % 5 < 1 or
                           (not np.isnan(bid_val) and bid_val >= tp_target - 0.02)):
            print(f"    @{row['elapsed']:.1f}s: {other_side}_bid={bid_val:.3f}, "
                  f"{other_side}_ask={row[other_ask]:.3f}, "
                  f"UP mid={row['up_midpoint']:.2f}, btc_diff={row['btc_diff']:.1f}"
                  if not np.isnan(row.get('btc_diff', np.nan)) else
                  f"    @{row['elapsed']:.1f}s: {other_side}_bid={bid_val:.3f}")
            shown += 1

    # Final settlement
    last = df.iloc[-1]
    print(f"\n  >>> SETTLEMENT: btc_diff={last['btc_diff']:.1f}, winner={settlement}")
    print(f"  >>> EXIT: {ex['exit_type']}, exit_price={ex['exit_price']:.3f}")
    print(f"  >>> PnL: ({ex['exit_price']:.3f} - {ex['entry_price']:.3f}) * 10 = {ex['pnl']:+.2f}")
