"""
Deep investigation: distinguish 'real dips' from 'already dead' rounds.
User's key insight: a REAL dip buy opportunity is when the round is COMPETITIVE
at t=240 (e.g., up_mid ~0.50) and then suddenly crashes. NOT when the round
is already decided (up=0.95, down=0.05) and nobody is trading.

Also check data quality: are the ask prices at extreme levels real or stale?
"""
import pandas as pd
import numpy as np
import os
import glob
from datetime import datetime

data_dir = r"C:\Users\ZHAOKAI\data"
files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
print(f"Total files: {len(files)}")

results = []

for fpath in files:
    try:
        df = pd.read_csv(fpath, low_memory=False)
        if len(df) < 10:
            continue

        # Parse timestamps, compute elapsed
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        t0 = df["ts"].iloc[0]
        df["elapsed"] = (df["ts"] - t0).dt.total_seconds()

        # Forward-fill prices
        for c in ["up_best_bid", "up_best_ask", "down_best_bid", "down_best_ask",
                   "up_midpoint", "down_midpoint", "btc_diff"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
                df[c] = df[c].ffill()

        # Settlement
        last_diff = df["btc_diff"].dropna()
        if len(last_diff) == 0:
            continue
        settlement = "up" if float(last_diff.iloc[-1]) > 0 else "down"

        # State at t=240 (entry window start)
        pre240 = df[df["elapsed"] <= 240]
        if len(pre240) == 0:
            continue
        up_mid_240 = pre240["up_midpoint"].iloc[-1]
        down_mid_240 = pre240["down_midpoint"].iloc[-1]

        # Tail data (240-300s)
        tail = df[(df["elapsed"] >= 240) & (df["elapsed"] <= 300)]
        if len(tail) == 0:
            continue

        # Find dip opportunities in tail
        for side in ["up", "down"]:
            ask_col = f"{side}_best_ask"
            bid_col = f"{side}_best_bid"
            mid_col = f"{side}_midpoint"

            # Entry window 240-285
            entry_window = tail[(tail["elapsed"] >= 240) & (tail["elapsed"] <= 285)]
            dip_rows = entry_window[
                (entry_window[ask_col].notna()) &
                (entry_window[ask_col] > 0) &
                (entry_window[ask_col] <= 0.20)
            ]
            if len(dip_rows) == 0:
                continue

            entry_row = dip_rows.iloc[0]
            entry_price = float(entry_row[ask_col])
            entry_time = float(entry_row["elapsed"])

            # What was the midpoint BEFORE the dip?
            mid_at_240 = float(pre240[mid_col].iloc[-1]) if pre240[mid_col].notna().any() else np.nan

            # What was the midpoint 30s before entry?
            pre_entry = df[(df["elapsed"] >= entry_time - 30) & (df["elapsed"] < entry_time)]
            mid_before_dip = float(pre_entry[mid_col].dropna().iloc[-1]) if len(pre_entry) > 0 and pre_entry[mid_col].notna().any() else np.nan

            # Check data quality: is there actual trading volume near the dip?
            dip_trades = dip_rows[dip_rows["event_type"] == "last_trade_price"]
            dip_bba = dip_rows[dip_rows["event_type"] == "best_bid_ask"]
            has_trade_at_dip = len(dip_trades) > 0
            has_bba_at_dip = len(dip_bba) > 0

            # Check spread at dip
            spread_at_dip = float(entry_row[ask_col]) - float(entry_row[bid_col]) if pd.notna(entry_row[bid_col]) else np.nan

            # Was this a "sudden crash" or "already dead"?
            # If mid_at_240 > 0.30 → was competitive, then crashed = REAL DIP
            # If mid_at_240 < 0.10 → was already dead = FAKE DIP

            # Settlement outcome
            exit_price = 1.0 if settlement == side else 0.0
            pnl = (exit_price - entry_price) * 10
            won = exit_price > 0.5

            results.append({
                "file": os.path.basename(fpath),
                "side": side,
                "mid_at_240": mid_at_240,
                "mid_before_dip": mid_before_dip,
                "entry_price": entry_price,
                "entry_time": entry_time,
                "spread_at_dip": spread_at_dip,
                "has_trade": has_trade_at_dip,
                "has_bba": has_bba_at_dip,
                "n_dip_rows": len(dip_rows),
                "settlement": settlement,
                "won": won,
                "pnl": pnl,
            })
            break  # only first side that dips

    except Exception as e:
        continue

rdf = pd.DataFrame(results)
print(f"\nTotal dip trades analyzed: {len(rdf)}")
print()

# KEY ANALYSIS: Classify rounds by competitiveness at t=240
print("=" * 70)
print("CLASSIFICATION: Was the round competitive at t=240?")
print("=" * 70)
bins = [0, 0.10, 0.20, 0.30, 0.40, 0.50, 1.0]
labels = ["<0.10 DEAD", "0.10-0.20 weak", "0.20-0.30 leaning",
          "0.30-0.40 competitive", "0.40-0.50 competitive", "0.50+ favorable"]
rdf["mid240_bucket"] = pd.cut(rdf["mid_at_240"], bins=bins, labels=labels, right=False)

for bucket in labels:
    sub = rdf[rdf["mid240_bucket"] == bucket]
    if len(sub) == 0:
        continue
    wr = sub["won"].mean() * 100
    avg_entry = sub["entry_price"].mean()
    avg_pnl = sub["pnl"].mean()
    tot_pnl = sub["pnl"].sum()
    print(f"  {bucket:25s}: {len(sub):4d} trades, WR={wr:5.1f}%, "
          f"avg_entry={avg_entry:.3f}, avgPnL={avg_pnl:+.2f}, totalPnL={tot_pnl:+.0f}")

print()
print("=" * 70)
print("REAL DIP (mid@240 >= 0.30) vs ALREADY DEAD (mid@240 < 0.10)")
print("=" * 70)
real_dip = rdf[rdf["mid_at_240"] >= 0.30]
already_dead = rdf[rdf["mid_at_240"] < 0.10]
middle = rdf[(rdf["mid_at_240"] >= 0.10) & (rdf["mid_at_240"] < 0.30)]

for label, sub in [("REAL DIP (mid>=0.30)", real_dip),
                    ("MIDDLE (0.10-0.30)", middle),
                    ("ALREADY DEAD (<0.10)", already_dead)]:
    if len(sub) == 0:
        print(f"  {label}: 0 trades")
        continue
    wr = sub["won"].mean() * 100
    print(f"  {label:25s}: {len(sub):4d} trades, WR={wr:5.1f}%, "
          f"avg_entry={sub['entry_price'].mean():.3f}, avgPnL={sub['pnl'].mean():+.2f}, "
          f"totalPnL={sub['pnl'].sum():+.0f}")

print()
print("=" * 70)
print("DATA QUALITY: Are dip prices backed by real trades?")
print("=" * 70)
print(f"  Dips with actual trades (last_trade_price): {rdf['has_trade'].sum()} / {len(rdf)} "
      f"({rdf['has_trade'].mean()*100:.1f}%)")
print(f"  Dips with BBA update only: {(rdf['has_bba'] & ~rdf['has_trade']).sum()}")
print(f"  Average spread at dip: {rdf['spread_at_dip'].mean():.3f}")
print(f"  Median spread at dip: {rdf['spread_at_dip'].median():.3f}")
print()

# Spread distribution
print("  Spread at dip distribution:")
for threshold in [0, 0.01, 0.02, 0.05, 0.10, 0.20]:
    n = (rdf["spread_at_dip"] <= threshold).sum()
    print(f"    spread <= {threshold:.2f}: {n} ({n/len(rdf)*100:.1f}%)")

print()
print("=" * 70)
print("FOCUS: Only REAL DIPS (mid@240 >= 0.30) with actual trades")
print("=" * 70)
clean = rdf[(rdf["mid_at_240"] >= 0.30) & (rdf["has_trade"] == True)]
if len(clean) > 0:
    wr = clean["won"].mean() * 100
    print(f"  Trades: {len(clean)}, WR={wr:.1f}%, avg_entry={clean['entry_price'].mean():.3f}, "
          f"avgPnL={clean['pnl'].mean():+.2f}, totalPnL={clean['pnl'].sum():+.0f}")
    print()
    # Entry price distribution for real dips
    print("  Entry price in real dips:")
    for lo, hi in [(0, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20)]:
        b = clean[(clean["entry_price"] >= lo) & (clean["entry_price"] < hi)]
        if len(b) > 0:
            print(f"    [{lo:.2f}-{hi:.2f}): {len(b):4d}, WR={b['won'].mean()*100:.1f}%, "
                  f"avgPnL={b['pnl'].mean():+.2f}")
else:
    print("  No clean real dips found!")

print()
print("=" * 70)
print("EXAMPLES: Show some real dips vs dead rounds")
print("=" * 70)
if len(real_dip) > 0:
    print("\n  REAL DIPS (competitive at 240, then crashed):")
    sample = real_dip.head(5)
    for _, r in sample.iterrows():
        print(f"    {r['file']}: side={r['side']}, mid@240={r['mid_at_240']:.2f}, "
              f"entry={r['entry_price']:.3f}@{r['entry_time']:.0f}s, "
              f"won={r['won']}, pnl={r['pnl']:+.1f}, trade={r['has_trade']}")

if len(already_dead) > 0:
    print("\n  ALREADY DEAD (one-sided from the start):")
    sample = already_dead.head(5)
    for _, r in sample.iterrows():
        print(f"    {r['file']}: side={r['side']}, mid@240={r['mid_at_240']:.2f}, "
              f"entry={r['entry_price']:.3f}@{r['entry_time']:.0f}s, "
              f"won={r['won']}, pnl={r['pnl']:+.1f}, trade={r['has_trade']}")

# Save for further analysis
rdf.to_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\dip_classification.csv", index=False)
print(f"\nSaved to results/dip_classification.csv")
