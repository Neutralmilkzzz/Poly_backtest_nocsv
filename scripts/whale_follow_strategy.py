"""
Whale-Follow Strategy: Detect whale manipulation in tail and trade WITH the whale.

The whale's playbook:
1. Round is competitive (mid ~0.50) for first 3 minutes
2. Price drifts to extreme (UP=0.90 or DOWN=0.90) as bots pile in
3. At ~240s, whale SLAMS price the OTHER way (90→60 or 10→40)
4. This triggers stop-losses of tail-chasing bots, whale eats their shares cheap
5. Price recovers back toward where it was (60→85-90)
6. Settlement happens at the original direction

Strategy 1 ("Follow the Slam"):
- Detect a competitive round that has gone extreme
- Wait for the whale slam (sudden reversal of 20+ cents)
- Buy AFTER the slam at the depressed price
- Sell when price recovers or hold to settlement

Strategy 2 ("Front-run the Slam"):
- When price is at extreme (>=0.85) in a previously competitive round
- Buy the OTHER side cheap (at 0.10-0.15)
- Wait for whale to slam, the other side jumps to 0.30-0.40
- Sell for quick profit
"""
import pandas as pd
import numpy as np
import os
import glob
import warnings
warnings.filterwarnings("ignore")

data_dir = r"C:\Users\ZHAOKAI\data"
files = sorted(glob.glob(os.path.join(data_dir, "*.csv")))
print(f"Total files: {len(files)}")

results = []

for i, fpath in enumerate(files):
    try:
        df = pd.read_csv(fpath, low_memory=False)
        if len(df) < 20:
            continue

        # Parse timestamps
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        t0 = df["ts"].iloc[0]
        df["elapsed"] = (df["ts"] - t0).dt.total_seconds()

        # Numeric conversion and forward-fill
        for c in ["up_best_bid", "up_best_ask", "up_midpoint",
                   "down_best_bid", "down_best_ask", "down_midpoint", "btc_diff"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
                df[c] = df[c].ffill()

        # Settlement
        last_diff = df["btc_diff"].dropna()
        if len(last_diff) == 0:
            continue
        settlement = "up" if float(last_diff.iloc[-1]) > 0 else "down"

        # === Phase 1: Was the round competitive in the first 3 minutes? ===
        early = df[(df["elapsed"] >= 30) & (df["elapsed"] <= 180)]
        if len(early) == 0:
            continue
        early_up_mid_avg = early["up_midpoint"].mean()
        # "Competitive" = mid was between 0.30 and 0.70 on average
        is_competitive = 0.30 <= early_up_mid_avg <= 0.70 if not np.isnan(early_up_mid_avg) else False

        # === Phase 2: Did the price go extreme before the tail? ===
        pre_tail = df[(df["elapsed"] >= 180) & (df["elapsed"] <= 250)]
        if len(pre_tail) == 0:
            continue
        # Peak extreme: max UP mid or max DOWN mid in 180-250s
        up_peak = pre_tail["up_midpoint"].max()
        down_peak = pre_tail["down_midpoint"].max()
        # Which side went extreme?
        up_extreme = up_peak >= 0.80 if not np.isnan(up_peak) else False
        down_extreme = down_peak >= 0.80 if not np.isnan(down_peak) else False

        # === Phase 3: The "slam" - sudden reversal in tail ===
        tail = df[(df["elapsed"] >= 240) & (df["elapsed"] <= 300)]
        if len(tail) == 0:
            continue

        # For each potential extreme side, look for the slam
        up_mid_at_240 = tail["up_midpoint"].iloc[0] if not np.isnan(tail["up_midpoint"].iloc[0]) else np.nan
        down_mid_at_240 = tail["down_midpoint"].iloc[0] if not np.isnan(tail["down_midpoint"].iloc[0]) else np.nan
        up_mid_min_tail = tail["up_midpoint"].min()
        up_mid_max_tail = tail["up_midpoint"].max()
        down_mid_min_tail = tail["down_midpoint"].min()
        down_mid_max_tail = tail["down_midpoint"].max()

        # Slam detection: price drops 20+ cents from its level at 240s
        up_slam_size = up_mid_at_240 - up_mid_min_tail if not np.isnan(up_mid_at_240) else 0
        down_slam_size = down_mid_at_240 - down_mid_min_tail if not np.isnan(down_mid_at_240) else 0

        # === STRATEGY 1: Follow the Slam ===
        # If UP was extreme (>=0.80) and gets slammed down 20+ cents
        # → buy UP at the bottom of the slam, ride recovery
        for slam_side, extreme_flag, mid_at_240, slam_sz, ask_col, bid_col in [
            ("up", up_extreme, up_mid_at_240, up_slam_size, "up_best_ask", "up_best_bid"),
            ("down", down_extreme, down_mid_at_240, down_slam_size, "down_best_ask", "down_best_bid"),
        ]:
            s1_result = {
                "file": os.path.basename(fpath),
                "strategy": "follow_slam",
                "is_competitive": is_competitive,
                "slam_side": slam_side,
                "extreme": extreme_flag,
                "mid_at_240": mid_at_240,
                "slam_size": slam_sz,
                "early_mid_avg": early_up_mid_avg if slam_side == "up" else (1 - early_up_mid_avg if not np.isnan(early_up_mid_avg) else np.nan),
                "traded": False,
                "entry_price": np.nan,
                "entry_time": np.nan,
                "exit_price": np.nan,
                "exit_type": None,
                "pnl": 0.0,
                "settlement": settlement,
            }

            # Conditions: competitive round + extreme price + slam >= 0.20
            if not is_competitive or not extreme_flag or slam_sz < 0.20:
                results.append(s1_result)
                continue

            # Find the bottom of the slam: first row where ask is at least 20c below mid_at_240
            slam_threshold = mid_at_240 - 0.20
            entry_window = tail[(tail["elapsed"] >= 245) & (tail["elapsed"] <= 285)]
            slam_rows = entry_window[
                (entry_window[ask_col].notna()) &
                (entry_window[ask_col] > 0) &
                (entry_window[ask_col] <= slam_threshold)
            ]
            if len(slam_rows) == 0:
                results.append(s1_result)
                continue

            # Buy at the slam price
            entry_row = slam_rows.iloc[0]
            entry_price = float(entry_row[ask_col])
            entry_time = float(entry_row["elapsed"])

            # Try to sell when price recovers (bid >= entry + 0.10, or bid >= mid_at_240 - 0.05)
            recovery_target = min(entry_price + 0.15, mid_at_240 - 0.05)
            recovery_target = max(recovery_target, entry_price + 0.05)  # at least 5c profit
            post_entry = tail[tail["elapsed"] > entry_time]
            tp_rows = post_entry[
                (post_entry[bid_col].notna()) &
                (post_entry[bid_col] >= recovery_target)
            ]
            if len(tp_rows) > 0:
                exit_price = float(tp_rows.iloc[0][bid_col])
                pnl = (exit_price - entry_price) * 10
                s1_result.update({
                    "traded": True, "entry_price": entry_price,
                    "entry_time": entry_time, "exit_price": exit_price,
                    "exit_type": "recovery_tp", "pnl": pnl,
                })
            else:
                # Hold to settlement
                exit_price = 1.0 if settlement == slam_side else 0.0
                pnl = (exit_price - entry_price) * 10
                s1_result.update({
                    "traded": True, "entry_price": entry_price,
                    "entry_time": entry_time, "exit_price": exit_price,
                    "exit_type": "settle_win" if exit_price > 0.5 else "settle_lose",
                    "pnl": pnl,
                })
            results.append(s1_result)

        # === STRATEGY 2: Front-run the Slam ===
        # If UP is extreme (>=0.85 at 240s), buy DOWN cheap (ask <= 0.20)
        # Wait for whale slam → DOWN jumps from 0.10 to 0.30-0.40 → sell
        for extreme_side, other_side in [("up", "down"), ("down", "up")]:
            ext_mid = up_mid_at_240 if extreme_side == "up" else down_mid_at_240
            other_ask_col = f"{other_side}_best_ask"
            other_bid_col = f"{other_side}_best_bid"

            s2_result = {
                "file": os.path.basename(fpath),
                "strategy": "frontrun_slam",
                "is_competitive": is_competitive,
                "slam_side": other_side,
                "extreme": ext_mid >= 0.85 if not np.isnan(ext_mid) else False,
                "mid_at_240": ext_mid,
                "slam_size": 0,
                "early_mid_avg": early_up_mid_avg if extreme_side == "up" else (1 - early_up_mid_avg if not np.isnan(early_up_mid_avg) else np.nan),
                "traded": False,
                "entry_price": np.nan,
                "entry_time": np.nan,
                "exit_price": np.nan,
                "exit_type": None,
                "pnl": 0.0,
                "settlement": settlement,
            }

            if not is_competitive or np.isnan(ext_mid) or ext_mid < 0.85:
                results.append(s2_result)
                continue

            # Buy the OTHER side cheap at the START of tail (240-255s, before slam)
            early_tail = tail[(tail["elapsed"] >= 240) & (tail["elapsed"] <= 255)]
            cheap_rows = early_tail[
                (early_tail[other_ask_col].notna()) &
                (early_tail[other_ask_col] > 0) &
                (early_tail[other_ask_col] <= 0.20)
            ]
            if len(cheap_rows) == 0:
                results.append(s2_result)
                continue

            entry_row = cheap_rows.iloc[0]
            entry_price = float(entry_row[other_ask_col])
            entry_time = float(entry_row["elapsed"])

            # Sell when the other side pumps (bid >= entry + 0.10)
            tp_target = entry_price + 0.10
            post_entry = tail[tail["elapsed"] > entry_time]
            tp_rows = post_entry[
                (post_entry[other_bid_col].notna()) &
                (post_entry[other_bid_col] >= tp_target)
            ]
            if len(tp_rows) > 0:
                exit_price = float(tp_rows.iloc[0][other_bid_col])
                pnl = (exit_price - entry_price) * 10
                s2_result.update({
                    "traded": True, "entry_price": entry_price,
                    "entry_time": entry_time, "exit_price": exit_price,
                    "exit_type": "slam_tp", "pnl": pnl,
                })
            else:
                # Hold to settlement
                exit_price = 1.0 if settlement == other_side else 0.0
                pnl = (exit_price - entry_price) * 10
                s2_result.update({
                    "traded": True, "entry_price": entry_price,
                    "entry_time": entry_time, "exit_price": exit_price,
                    "exit_type": "settle_win" if exit_price > 0.5 else "settle_lose",
                    "pnl": pnl,
                })
            results.append(s2_result)

    except Exception as e:
        continue

    if (i + 1) % 500 == 0:
        print(f"  Processed {i+1}/{len(files)}...")

# === RESULTS ===
rdf = pd.DataFrame(results)
rdf.to_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\whale_follow_results.csv", index=False)

print(f"\n{'='*70}")
print(f"TOTAL RECORDS: {len(rdf)}")
print(f"{'='*70}")

for strat_name in ["follow_slam", "frontrun_slam"]:
    sdf = rdf[rdf["strategy"] == strat_name]
    traded = sdf[sdf["traded"] == True]
    print(f"\n{'='*70}")
    print(f"STRATEGY: {strat_name}")
    print(f"{'='*70}")
    print(f"  Total rounds checked: {len(sdf)}")
    print(f"  Traded: {len(traded)}")
    if len(traded) == 0:
        print("  No trades!")
        continue

    wins = traded[traded["pnl"] > 0]
    losses = traded[traded["pnl"] <= 0]
    print(f"  Win rate: {len(wins)/len(traded)*100:.1f}%")
    print(f"  Total PnL: {traded['pnl'].sum():+.1f}")
    print(f"  Avg PnL per trade: {traded['pnl'].mean():+.2f}")
    print(f"  Avg entry price: {traded['entry_price'].mean():.3f}")
    print(f"  Avg exit price: {traded['exit_price'].mean():.3f}")
    print()

    # By exit type
    print(f"  Exit type breakdown:")
    for et in traded["exit_type"].unique():
        sub = traded[traded["exit_type"] == et]
        print(f"    {et:15s}: {len(sub):4d} trades, avgPnL={sub['pnl'].mean():+.2f}, "
              f"total={sub['pnl'].sum():+.1f}")

    # By slam size
    if strat_name == "follow_slam":
        print(f"\n  By slam size:")
        for lo, hi in [(0.20, 0.30), (0.30, 0.40), (0.40, 0.60), (0.60, 1.0)]:
            sub = traded[(traded["slam_size"] >= lo) & (traded["slam_size"] < hi)]
            if len(sub) > 0:
                print(f"    slam [{lo:.1f}-{hi:.1f}): {len(sub):4d} trades, "
                      f"WR={sub['pnl'].gt(0).mean()*100:.1f}%, avgPnL={sub['pnl'].mean():+.2f}")

    # Only competitive rounds
    comp = traded[traded["is_competitive"] == True]
    noncomp = traded[traded["is_competitive"] == False]
    print(f"\n  Competitive rounds only: {len(comp)} trades, "
          f"PnL={comp['pnl'].sum():+.1f}" if len(comp) > 0 else "")
    if len(noncomp) > 0:
        print(f"  Non-competitive rounds:  {len(noncomp)} trades, "
              f"PnL={noncomp['pnl'].sum():+.1f}")

print(f"\n{'='*70}")
print("DONE")
