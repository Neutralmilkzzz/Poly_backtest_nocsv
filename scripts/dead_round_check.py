"""Check dip buy performance by side and entry price, especially 'dead rounds'."""
import pandas as pd
import numpy as np

df = pd.read_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies\three_strategies_raw.csv")
traded = df[df["C_traded"] == True].copy()
traded["C_won"] = traded["C_pnl"] > 0
# Rename columns to match
traded.rename(columns={"C_entry": "C_entry_price", "C_exit": "C_exit_price",
                        "C_type": "C_exit_type"}, inplace=True)

print(f"Total dip buy trades: {len(traded)}")
print()

# Side distribution
print("=== Side Distribution ===")
print(traded["C_side"].value_counts())
print()

# Per-side stats
print("=== Per-Side Stats ===")
for side in ["up", "down"]:
    s = traded[traded["C_side"] == side]
    if len(s) == 0:
        continue
    wr = s["C_won"].mean() * 100
    avg_e = s["C_entry_price"].mean()
    avg_p = s["C_pnl"].mean()
    tot_p = s["C_pnl"].sum()
    print(f"  {side.upper():5s}: {len(s):4d} trades, WR={wr:5.1f}%, "
          f"avg_entry={avg_e:.3f}, avg_PnL={avg_p:+.2f}, total={tot_p:+.0f}")
print()

# Entry price buckets
print("=== Entry Price Buckets ===")
for lo, hi in [(0, 0.03), (0.03, 0.05), (0.05, 0.10), (0.10, 0.15), (0.15, 0.20)]:
    b = traded[(traded["C_entry_price"] >= lo) & (traded["C_entry_price"] < hi)]
    if len(b) > 0:
        wr = b["C_won"].mean() * 100
        print(f"  [{lo:.2f}-{hi:.2f}): {len(b):4d} trades, WR={wr:5.1f}%, "
              f"avgPnL={b['C_pnl'].mean():+.2f}, total={b['C_pnl'].sum():+.0f}")
print()

# KEY QUESTION: when entry_price is very low (< 0.05), it means the market
# is extremely one-sided ("dead round"). What's the win rate?
print("=== Dead Round Analysis (entry < 0.05 = extreme one-sided) ===")
dead = traded[traded["C_entry_price"] < 0.05]
alive = traded[traded["C_entry_price"] >= 0.05]
print(f"  Dead rounds (entry<0.05):  {len(dead):4d} trades, "
      f"WR={dead['C_won'].mean()*100:.1f}%, avgPnL={dead['C_pnl'].mean():+.2f}, "
      f"total={dead['C_pnl'].sum():+.0f}")
print(f"  Normal rounds (entry>=0.05): {len(alive):4d} trades, "
      f"WR={alive['C_won'].mean()*100:.1f}%, avgPnL={alive['C_pnl'].mean():+.2f}, "
      f"total={alive['C_pnl'].sum():+.0f}")
print()

# Per-side in dead rounds
print("=== Dead Rounds by Side ===")
for side in ["up", "down"]:
    s = dead[dead["C_side"] == side]
    if len(s) == 0:
        continue
    wr = s["C_won"].mean() * 100
    print(f"  {side.upper():5s}: {len(s):4d} trades, WR={wr:5.1f}%, "
          f"avgPnL={s['C_pnl'].mean():+.2f}, total={s['C_pnl'].sum():+.0f}")
print()

# Exit type breakdown by entry price bucket
print("=== Exit Type by Entry Price ===")
for lo, hi in [(0, 0.05), (0.05, 0.10), (0.10, 0.20)]:
    b = traded[(traded["C_entry_price"] >= lo) & (traded["C_entry_price"] < hi)]
    if len(b) > 0:
        print(f"  [{lo:.2f}-{hi:.2f}):")
        for et in b["C_exit_type"].value_counts().index:
            sub = b[b["C_exit_type"] == et]
            print(f"    {et:15s}: {len(sub):4d} trades, avgPnL={sub['C_pnl'].mean():+.2f}")
print()

# The real question: among very cheap entries, how often does the "dead" side
# actually win? (i.e., BTC reverses in last seconds)
print("=== Settle Win vs Lose in Dead Rounds ===")
for et in ["settle_win", "settle_lose", "take_profit"]:
    s = dead[dead["C_exit_type"] == et]
    if len(s) > 0:
        print(f"  {et:15s}: {len(s):4d} ({len(s)/len(dead)*100:.1f}%), "
              f"avgPnL={s['C_pnl'].mean():+.2f}")
