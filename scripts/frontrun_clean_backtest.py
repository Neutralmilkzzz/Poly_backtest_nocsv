"""
Clean Frontrun Strategy Backtest - 3000+ rounds

Strategy logic (user-specified):
1. Round must be "competitive" (焦灼盘): early phase (30-180s) UP midpoint avg in [0.30, 0.70]
2. At t=240s, check if either side is extreme (>=0.85)
3. If yes, buy the OTHER side at best_ask <= 0.15
4. Take-profit: sell when bid >= entry_price + 0.10
5. If no TP hit, hold to settlement

Entry: taker buy at best_ask (must be <= 0.15)
Exit: maker sell at bid >= entry + 0.10, OR settlement (1.0 or 0.0)
Shares: 10 per trade
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

# Parameters
COMPETITIVE_LOW = 0.30
COMPETITIVE_HIGH = 0.70
EXTREME_THRESHOLD = 0.85
MAX_ENTRY_ASK = 0.15
TP_OFFSET = 0.10
SHARES = 10
ENTRY_WINDOW_START = 240
ENTRY_WINDOW_END = 255  # buy early in tail, before slam
EARLY_START = 30
EARLY_END = 180

trades = []
round_count = 0
skipped = 0

for i, fpath in enumerate(files):
    try:
        df = pd.read_csv(fpath, low_memory=False)
        if len(df) < 20:
            skipped += 1
            continue

        # Parse timestamps
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        if len(df) < 20:
            skipped += 1
            continue

        t0 = df["ts"].iloc[0]
        df["elapsed"] = (df["ts"] - t0).dt.total_seconds()

        # Numeric + forward-fill
        for c in ["up_best_bid", "up_best_ask", "up_midpoint",
                   "down_best_bid", "down_best_ask", "down_midpoint", "btc_diff"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
                df[c] = df[c].ffill()

        # Settlement
        last_diff = df["btc_diff"].dropna()
        if len(last_diff) == 0:
            skipped += 1
            continue
        final_diff = float(last_diff.iloc[-1])
        settlement = "up" if final_diff > 0 else "down"

        round_count += 1

        # STEP 1: Check competitive (early avg UP mid in [0.30, 0.70])
        early = df[(df["elapsed"] >= EARLY_START) & (df["elapsed"] <= EARLY_END)]
        if len(early) == 0:
            continue
        early_up_avg = early["up_midpoint"].mean()
        if np.isnan(early_up_avg) or early_up_avg < COMPETITIVE_LOW or early_up_avg > COMPETITIVE_HIGH:
            continue

        # STEP 2: Check extreme at t=240
        at240 = df[df["elapsed"] <= 240]
        if len(at240) == 0:
            continue
        up_mid_240 = at240["up_midpoint"].iloc[-1]
        down_mid_240 = at240["down_midpoint"].iloc[-1]

        # Determine which side is extreme
        if up_mid_240 >= EXTREME_THRESHOLD:
            extreme_side = "up"
            buy_side = "down"
        elif down_mid_240 >= EXTREME_THRESHOLD:
            extreme_side = "down"
            buy_side = "up"
        else:
            continue  # neither side extreme

        buy_ask_col = f"{buy_side}_best_ask"
        buy_bid_col = f"{buy_side}_best_bid"

        # STEP 3: Look for entry in window [240, 255] with ask <= 0.15
        entry_window = df[(df["elapsed"] >= ENTRY_WINDOW_START) &
                          (df["elapsed"] <= ENTRY_WINDOW_END)]
        cheap_rows = entry_window[
            (entry_window[buy_ask_col].notna()) &
            (entry_window[buy_ask_col] > 0) &
            (entry_window[buy_ask_col] <= MAX_ENTRY_ASK)
        ]
        if len(cheap_rows) == 0:
            continue

        # Take first available entry
        entry_row = cheap_rows.iloc[0]
        entry_price = float(entry_row[buy_ask_col])
        entry_time = float(entry_row["elapsed"])
        entry_event = entry_row.get("event_type", "unknown")

        # STEP 4: Look for take-profit after entry
        tp_target = entry_price + TP_OFFSET
        post_entry = df[df["elapsed"] > entry_time]

        tp_rows = post_entry[
            (post_entry[buy_bid_col].notna()) &
            (post_entry[buy_bid_col] >= tp_target)
        ]

        if len(tp_rows) > 0:
            # Take profit hit
            tp_row = tp_rows.iloc[0]
            exit_price = float(tp_row[buy_bid_col])
            exit_time = float(tp_row["elapsed"])
            exit_type = "take_profit"
            pnl = (tp_target - entry_price) * SHARES  # sell at tp_target, not market
        else:
            # Hold to settlement
            exit_price = 1.0 if settlement == buy_side else 0.0
            exit_time = df["elapsed"].iloc[-1]
            exit_type = "settle_win" if exit_price > 0.5 else "settle_lose"
            pnl = (exit_price - entry_price) * SHARES

        trades.append({
            "file": os.path.basename(fpath),
            "round_num": round_count,
            "early_up_avg": round(early_up_avg, 3),
            "up_mid_240": round(up_mid_240, 3) if not np.isnan(up_mid_240) else None,
            "down_mid_240": round(down_mid_240, 3) if not np.isnan(down_mid_240) else None,
            "extreme_side": extreme_side,
            "buy_side": buy_side,
            "entry_price": round(entry_price, 3),
            "entry_time": round(entry_time, 1),
            "entry_event": entry_event,
            "tp_target": round(tp_target, 3),
            "exit_price": round(exit_price, 3),
            "exit_time": round(exit_time, 1),
            "exit_type": exit_type,
            "pnl": round(pnl, 2),
            "settlement": settlement,
            "btc_diff": round(final_diff, 1),
        })

    except Exception as e:
        skipped += 1
        continue

    if (i + 1) % 1000 == 0:
        print(f"  Processed {i+1}/{len(files)}, trades so far: {len(trades)}")

# === RESULTS ===
tdf = pd.DataFrame(trades)
tdf.to_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\frontrun_clean.csv", index=False)

print(f"\n{'='*70}")
print(f"BACKTEST RESULTS")
print(f"{'='*70}")
print(f"Total rounds parsed: {round_count}")
print(f"Skipped files: {skipped}")
print(f"Total trades: {len(tdf)}")
print()

if len(tdf) == 0:
    print("NO TRADES!")
    exit()

wins = tdf[tdf["pnl"] > 0]
losses = tdf[tdf["pnl"] <= 0]
print(f"Win rate: {len(wins)}/{len(tdf)} = {len(wins)/len(tdf)*100:.1f}%")
print(f"Total PnL: {tdf['pnl'].sum():+.2f}")
print(f"Avg PnL per trade: {tdf['pnl'].mean():+.2f}")
print(f"Avg entry price: {tdf['entry_price'].mean():.3f}")
print(f"Median entry price: {tdf['entry_price'].median():.3f}")
print()

# Exit type breakdown
print("Exit type breakdown:")
for et in ["take_profit", "settle_win", "settle_lose"]:
    sub = tdf[tdf["exit_type"] == et]
    if len(sub) > 0:
        print(f"  {et:15s}: {len(sub):4d} trades ({len(sub)/len(tdf)*100:5.1f}%), "
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

# Entry price distribution
print("Entry price distribution:")
for lo, hi in [(0, 0.03), (0.03, 0.05), (0.05, 0.08), (0.08, 0.10), (0.10, 0.15)]:
    sub = tdf[(tdf["entry_price"] >= lo) & (tdf["entry_price"] < hi)]
    if len(sub) > 0:
        wr = sub["pnl"].gt(0).mean() * 100
        print(f"  [{lo:.2f}-{hi:.2f}): {len(sub):4d} trades, WR={wr:.1f}%, "
              f"avgPnL={sub['pnl'].mean():+.2f}")

print()

# Cumulative PnL over time
print("Cumulative PnL (every 100 trades):")
cum_pnl = tdf["pnl"].cumsum()
for j in range(0, len(tdf), 100):
    print(f"  Trade {j:4d}-{min(j+99,len(tdf)-1):4d}: cumPnL={cum_pnl.iloc[min(j+99,len(tdf)-1)]:+.1f}")

print()

# Print first 10 trades for manual verification
print("First 10 trades (for verification):")
print("-" * 120)
cols = ["file", "early_up_avg", "up_mid_240", "down_mid_240", "extreme_side",
        "buy_side", "entry_price", "entry_time", "tp_target", "exit_type", "exit_price", "pnl", "btc_diff"]
for _, row in tdf.head(10).iterrows():
    print(f"  {row['file']:30s} early={row['early_up_avg']:.2f} "
          f"UP@240={row['up_mid_240']} DN@240={row['down_mid_240']} "
          f"extreme={row['extreme_side']} buy={row['buy_side']} "
          f"entry={row['entry_price']:.3f}@{row['entry_time']:.0f}s "
          f"tp={row['tp_target']:.3f} → {row['exit_type']:13s} exit={row['exit_price']:.3f} "
          f"PnL={row['pnl']:+.2f} diff={row['btc_diff']}")

print(f"\n{'='*70}")
print("DONE - saved to results/frontrun_clean.csv")
