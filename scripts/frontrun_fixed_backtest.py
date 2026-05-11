"""
FIXED Frontrun Strategy Backtest

BUG FIX: Settlement determination now uses btc_diff at ~295s (before reset),
not the last row which may be contaminated by next round's target reset.

Also filters out broken rounds (< 30s of data).
"""
import pandas as pd
import numpy as np
import os
import glob
import warnings
warnings.filterwarnings("ignore")

data_dir = r"C:\Users\ZHAOKAI\data"
files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
print(f"Total CSV files: {len(files)}")

COMPETITIVE_LOW = 0.30
COMPETITIVE_HIGH = 0.70
EXTREME_THRESHOLD = 0.85
MAX_ENTRY_ASK = 0.15
TP_OFFSET = 0.10
SHARES = 10
ENTRY_WINDOW_START = 240
ENTRY_WINDOW_END = 255
EARLY_START = 30
EARLY_END = 180

trades = []
round_count = 0
skipped = 0
settlement_methods = {"use_290": 0, "use_last": 0, "broken": 0}


def determine_settlement(df):
    """Determine settlement using btc_diff BEFORE potential reset.
    Use the last non-NaN btc_diff in 285-298s range. If not available,
    use last non-zero btc_diff overall. Skip if ambiguous."""
    # Try 285-298s window first (right before settlement, before reset)
    late = df[(df["elapsed"] >= 285) & (df["elapsed"] <= 298)]
    if len(late) > 0:
        diffs = late["btc_diff"].dropna()
        # Find last non-zero value
        nonzero = diffs[diffs != 0]
        if len(nonzero) > 0:
            val = float(nonzero.iloc[-1])
            return ("up" if val > 0 else "down"), val, "window_285_298"

    # Fallback: last non-zero btc_diff in entire round (excluding last 2s)
    before_end = df[df["elapsed"] <= 298]
    if len(before_end) > 0:
        diffs = before_end["btc_diff"].dropna()
        nonzero = diffs[diffs != 0]
        if len(nonzero) > 0:
            val = float(nonzero.iloc[-1])
            return ("up" if val > 0 else "down"), val, "last_nonzero"

    return None, 0.0, "ambiguous"


for i, fpath in enumerate(files):
    try:
        df = pd.read_csv(fpath, low_memory=False)
        if len(df) < 20:
            skipped += 1
            continue

        df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        if len(df) < 20:
            skipped += 1
            continue

        t0 = df["ts"].iloc[0]
        df["elapsed"] = (df["ts"] - t0).dt.total_seconds()

        # Skip broken rounds (< 200s of data)
        max_elapsed = df["elapsed"].max()
        if max_elapsed < 200:
            skipped += 1
            settlement_methods["broken"] += 1
            continue

        for c in ["up_best_bid", "up_best_ask", "up_midpoint",
                   "down_best_bid", "down_best_ask", "down_midpoint", "btc_diff"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
                df[c] = df[c].ffill()

        # FIXED settlement determination
        settlement, final_diff, method = determine_settlement(df)
        if settlement is None:
            skipped += 1
            settlement_methods["broken"] += 1
            continue

        if "285" in method:
            settlement_methods["use_290"] += 1
        else:
            settlement_methods["use_last"] += 1

        round_count += 1

        # STEP 1: Competitive check
        early = df[(df["elapsed"] >= EARLY_START) & (df["elapsed"] <= EARLY_END)]
        if len(early) == 0:
            continue
        early_up_avg = early["up_midpoint"].mean()
        if np.isnan(early_up_avg) or early_up_avg < COMPETITIVE_LOW or early_up_avg > COMPETITIVE_HIGH:
            continue

        # STEP 2: Extreme at 240s
        at240 = df[df["elapsed"] <= 240]
        if len(at240) == 0:
            continue
        up_mid_240 = at240["up_midpoint"].iloc[-1]
        down_mid_240 = at240["down_midpoint"].iloc[-1]

        if up_mid_240 >= EXTREME_THRESHOLD:
            extreme_side = "up"
            buy_side = "down"
        elif down_mid_240 >= EXTREME_THRESHOLD:
            extreme_side = "down"
            buy_side = "up"
        else:
            continue

        buy_ask_col = f"{buy_side}_best_ask"
        buy_bid_col = f"{buy_side}_best_bid"

        # STEP 3: Entry
        entry_window = df[(df["elapsed"] >= ENTRY_WINDOW_START) &
                          (df["elapsed"] <= ENTRY_WINDOW_END)]
        cheap_rows = entry_window[
            (entry_window[buy_ask_col].notna()) &
            (entry_window[buy_ask_col] > 0) &
            (entry_window[buy_ask_col] <= MAX_ENTRY_ASK)
        ]
        if len(cheap_rows) == 0:
            continue

        entry_row = cheap_rows.iloc[0]
        entry_price = float(entry_row[buy_ask_col])
        entry_time = float(entry_row["elapsed"])

        # STEP 4: Take-profit
        tp_target = entry_price + TP_OFFSET
        post_entry = df[df["elapsed"] > entry_time]

        tp_rows = post_entry[
            (post_entry[buy_bid_col].notna()) &
            (post_entry[buy_bid_col] >= tp_target)
        ]

        if len(tp_rows) > 0:
            exit_price = tp_target  # sell at limit
            exit_time = float(tp_rows.iloc[0]["elapsed"])
            exit_type = "take_profit"
            pnl = (tp_target - entry_price) * SHARES
        else:
            exit_price = 1.0 if settlement == buy_side else 0.0
            exit_time = max_elapsed
            exit_type = "settle_win" if exit_price > 0.5 else "settle_lose"
            pnl = (exit_price - entry_price) * SHARES

        trades.append({
            "file": os.path.basename(fpath),
            "round_num": round_count,
            "early_up_avg": round(early_up_avg, 3),
            "up_mid_240": round(float(up_mid_240), 3),
            "down_mid_240": round(float(down_mid_240), 3),
            "extreme_side": extreme_side,
            "buy_side": buy_side,
            "entry_price": round(entry_price, 3),
            "entry_time": round(entry_time, 1),
            "tp_target": round(tp_target, 3),
            "exit_price": round(exit_price, 3),
            "exit_time": round(exit_time, 1),
            "exit_type": exit_type,
            "pnl": round(pnl, 2),
            "settlement": settlement,
            "btc_diff_final": round(final_diff, 1),
            "settlement_method": method,
        })

    except Exception as e:
        skipped += 1
        continue

    if (i + 1) % 1000 == 0:
        print(f"  Processed {i+1}/{len(files)}, trades so far: {len(trades)}")

tdf = pd.DataFrame(trades)
tdf.to_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\frontrun_fixed.csv", index=False)

print(f"\n{'='*70}")
print(f"FIXED BACKTEST RESULTS")
print(f"{'='*70}")
print(f"Total rounds parsed: {round_count}")
print(f"Skipped: {skipped}")
print(f"Settlement methods: {settlement_methods}")
print(f"Total trades: {len(tdf)}")
print()

if len(tdf) == 0:
    print("NO TRADES!")
    exit()

# Overall stats
wins = tdf[tdf["pnl"] > 0]
losses = tdf[tdf["pnl"] <= 0]
print(f"Win rate: {len(wins)}/{len(tdf)} = {len(wins)/len(tdf)*100:.1f}%")
print(f"Total PnL: {tdf['pnl'].sum():+.2f}")
print(f"Avg PnL per trade: {tdf['pnl'].mean():+.2f}")
print(f"Avg entry price: {tdf['entry_price'].mean():.3f}")
print()

# Exit type breakdown
print("Exit type breakdown:")
for et in ["take_profit", "settle_win", "settle_lose"]:
    sub = tdf[tdf["exit_type"] == et]
    if len(sub) > 0:
        print(f"  {et:15s}: {len(sub):4d} ({len(sub)/len(tdf)*100:5.1f}%), "
              f"avgPnL={sub['pnl'].mean():+.2f}, total={sub['pnl'].sum():+.1f}")

print()

# Buy side breakdown
print("Buy side breakdown:")
for side in ["up", "down"]:
    sub = tdf[tdf["buy_side"] == side]
    if len(sub) > 0:
        wr = sub["pnl"].gt(0).mean() * 100
        print(f"  Buy {side.upper():5s}: {len(sub):4d} trades, WR={wr:.1f}%, "
              f"avgPnL={sub['pnl'].mean():+.2f}, total={sub['pnl'].sum():+.1f}")

print()

# Settlement check: UP vs DOWN frequency
print("Settlement frequency (FIXED):")
up_settle = (tdf["settlement"] == "up").sum()
down_settle = (tdf["settlement"] == "down").sum()
print(f"  UP wins:   {up_settle} ({up_settle/len(tdf)*100:.1f}%)")
print(f"  DOWN wins: {down_settle} ({down_settle/len(tdf)*100:.1f}%)")

print()

# Cumulative PnL
print("Cumulative PnL (every 100 trades):")
cum_pnl = tdf["pnl"].cumsum()
for j in range(0, len(tdf), 100):
    end_idx = min(j + 99, len(tdf) - 1)
    print(f"  Trade {j:4d}-{end_idx:4d}: cumPnL={cum_pnl.iloc[end_idx]:+.1f}")

print()

# First 10 trades
print("First 10 trades:")
for _, row in tdf.head(10).iterrows():
    print(f"  {row['file']:30s} buy={row['buy_side']} @{row['entry_price']:.3f} "
          f"tp={row['tp_target']:.3f} → {row['exit_type']:13s} "
          f"PnL={row['pnl']:+.2f} diff={row['btc_diff_final']}")

print(f"\n{'='*70}")
print("DONE")
