"""
Trace a specific CSV file to show the settlement bug clearly.
Find a trade that was settle_win (old) but settle_lose (fixed).
"""
import pandas as pd
import numpy as np
import os

# Load old and new results
old = pd.read_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\frontrun_clean.csv")
new = pd.read_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\frontrun_fixed.csv")

# Find flipped trades: settle_win -> settle_lose
old_sw = old[old["exit_type"] == "settle_win"][["file", "buy_side", "entry_price", "exit_type", "pnl"]]
new_all = new[["file", "buy_side", "entry_price", "exit_type", "pnl", "btc_diff_final", "settlement_method"]]
merged = old_sw.merge(new_all, on="file", suffixes=("_OLD", "_NEW"))
flipped = merged[merged["exit_type_NEW"] == "settle_lose"]

print(f"Trades flipped from settle_win -> settle_lose: {len(flipped)}")
print(f"Total PnL swing: OLD={flipped['pnl_OLD'].sum():+.1f}, NEW={flipped['pnl_NEW'].sum():+.1f}")
print()

# Pick one with big PnL swing to demonstrate
example = flipped.sort_values("pnl_OLD", ascending=False).iloc[0]
fname = example["file"]
print(f"{'='*70}")
print(f"EXAMPLE: {fname}")
print(f"{'='*70}")
print(f"OLD result: buy {example['buy_side_OLD']} @{example['entry_price_OLD']:.3f}, "
      f"{example['exit_type_OLD']}, PnL={example['pnl_OLD']:+.2f}")
print(f"NEW result: buy {example['buy_side_NEW']} @{example['entry_price_NEW']:.3f}, "
      f"{example['exit_type_NEW']}, PnL={example['pnl_NEW']:+.2f}")
print(f"Fixed btc_diff: {example['btc_diff_final']:.1f}")
print()

# Now read the actual CSV
fpath = os.path.join(r"C:\Users\ZHAOKAI\data", fname)
df = pd.read_csv(fpath, low_memory=False)
df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
df = df.dropna(subset=["ts"])
t0 = df["ts"].iloc[0]
df["elapsed"] = (df["ts"] - t0).dt.total_seconds()

for c in ["up_best_bid", "up_best_ask", "up_midpoint",
          "down_best_bid", "down_best_ask", "down_midpoint", "btc_diff"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce")

print(f"Total rows: {len(df)}")
print(f"Time range: {df['elapsed'].min():.1f}s ~ {df['elapsed'].max():.1f}s")
print()

# Show btc_diff at key timestamps
print("=== btc_diff timeline ===")
for t in [0, 30, 60, 120, 180, 240, 250, 260, 270, 280, 285, 290, 293, 295, 297, 298, 299, 300]:
    rows = df[(df["elapsed"] >= t - 1) & (df["elapsed"] <= t + 1)]
    if len(rows) > 0:
        diff_vals = rows["btc_diff"].dropna()
        if len(diff_vals) > 0:
            d = diff_vals.iloc[-1]
            print(f"  t={t:3d}s: btc_diff = {d:+.1f}")
        else:
            print(f"  t={t:3d}s: btc_diff = NaN")
    else:
        print(f"  t={t:3d}s: (no data)")

print()

# Show LAST 10 rows of the CSV (where the bug lives)
print("=== LAST 10 rows of CSV ===")
last10 = df.tail(10)[["elapsed", "btc_diff", "up_midpoint", "down_midpoint", "event_type"]].copy()
last10["elapsed"] = last10["elapsed"].round(1)
for _, row in last10.iterrows():
    diff_str = f"{row['btc_diff']:+.1f}" if pd.notna(row["btc_diff"]) else "NaN"
    up_str = f"{row['up_midpoint']:.3f}" if pd.notna(row["up_midpoint"]) else "NaN"
    dn_str = f"{row['down_midpoint']:.3f}" if pd.notna(row["down_midpoint"]) else "NaN"
    print(f"  t={row['elapsed']:6.1f}s  btc_diff={diff_str:>8s}  "
          f"up_mid={up_str}  dn_mid={dn_str}  event={row['event_type']}")

print()

# The smoking gun
last_diff = df["btc_diff"].dropna().iloc[-1]
print(f"=== SMOKING GUN ===")
print(f"Last btc_diff (used by OLD code):  {last_diff:+.1f}")
print(f"  -> OLD settlement: {'UP' if last_diff > 0 else 'DOWN'} wins")
print()

# What the FIXED code uses
late = df[(df["elapsed"] >= 285) & (df["elapsed"] <= 298)]
if len(late) > 0:
    nonzero = late["btc_diff"].dropna()
    nonzero = nonzero[nonzero != 0]
    if len(nonzero) > 0:
        fixed_diff = float(nonzero.iloc[-1])
        print(f"btc_diff @285-298s (FIXED code): {fixed_diff:+.1f}")
        print(f"  -> FIXED settlement: {'UP' if fixed_diff > 0 else 'DOWN'} wins")
    else:
        print("No non-zero btc_diff in 285-298s window")

print()
buy_side = example["buy_side_OLD"]
print(f"Strategy bought: {buy_side.upper()}")
print(f"OLD said {buy_side.upper()} wins  -> settle_win  -> PnL = +(1.0 - {example['entry_price_OLD']:.3f}) * 10 = +{(1.0-example['entry_price_OLD'])*10:.2f}")
print(f"FIX says {buy_side.upper()} loses -> settle_lose -> PnL = -(0.0 + {example['entry_price_OLD']:.3f}) * 10 = -{example['entry_price_OLD']*10:.2f}")
print(f"PnL difference per trade: {(1.0-example['entry_price_OLD'])*10 + example['entry_price_OLD']*10:.2f} !!!")
