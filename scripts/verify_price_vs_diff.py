"""Verify: are up/down midpoint prices correct even when btc_diff resets?"""
import pandas as pd
import numpy as np
import os, glob

all_files = sorted(glob.glob(r"C:\Users\ZHAOKAI\data\*.csv"))
# Sample 5 files spread across dataset
samples = [all_files[i] for i in [0, 1000, 2000, 3000, 4000]]

for fpath in samples:
    df = pd.read_csv(fpath, low_memory=False)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"])
    if len(df) < 10:
        print(f"{os.path.basename(fpath)}: TOO SHORT ({len(df)} rows)")
        continue
    t0 = df["ts"].iloc[0]
    df["elapsed"] = (df["ts"] - t0).dt.total_seconds()
    for c in ["up_midpoint", "down_midpoint", "btc_diff"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    fname = os.path.basename(fpath)
    print(f"{'='*60}")
    print(f"FILE: {fname}  (rows={len(df)}, max_t={df['elapsed'].max():.1f}s)")

    # Last 5 rows with up/down data
    has_up = df[df["up_midpoint"].notna()].tail(5)
    print(f"  Last 5 rows with up_midpoint:")
    for _, r in has_up.iterrows():
        diff_str = f"{r['btc_diff']:+.1f}" if pd.notna(r["btc_diff"]) else "NaN"
        print(f"    t={r['elapsed']:6.1f}s  up={r['up_midpoint']:.3f}  dn={r['down_midpoint']:.3f}  btc_diff={diff_str}")

    # Determine settlement both ways
    late = df[(df["elapsed"] >= 285) & (df["elapsed"] <= 299)]
    up_vals = late["up_midpoint"].dropna()
    if len(up_vals) > 0:
        last_up = up_vals.iloc[-1]
        settle_by_price = "UP" if last_up > 0.5 else "DOWN"
    else:
        settle_by_price = "???"

    diff_vals = late["btc_diff"].dropna()
    diff_nz = diff_vals[diff_vals != 0]
    if len(diff_nz) > 0:
        settle_by_diff = "UP" if diff_nz.iloc[-1] > 0 else "DOWN"
    else:
        settle_by_diff = "???"

    match = "✅" if settle_by_price == settle_by_diff else "❌ MISMATCH"
    print(f"  Settlement: by_price={settle_by_price}, by_diff={settle_by_diff}  {match}")
    print()

# Now do a mass check across ALL files
print("="*60)
print("MASS CHECK: all files")
agree = 0
disagree = 0
price_only = 0
diff_only = 0
neither = 0

for fpath in all_files:
    try:
        df = pd.read_csv(fpath, low_memory=False)
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        if len(df) < 20:
            neither += 1
            continue
        t0 = df["ts"].iloc[0]
        df["elapsed"] = (df["ts"] - t0).dt.total_seconds()
        if df["elapsed"].max() < 200:
            neither += 1
            continue
        for c in ["up_midpoint", "btc_diff"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        late = df[(df["elapsed"] >= 285) & (df["elapsed"] <= 299)]

        # By price
        up_vals = late["up_midpoint"].dropna()
        if len(up_vals) > 0:
            s_price = "UP" if up_vals.iloc[-1] > 0.5 else "DOWN"
        else:
            s_price = None

        # By diff (nonzero)
        diff_vals = late["btc_diff"].dropna()
        diff_nz = diff_vals[diff_vals != 0]
        if len(diff_nz) > 0:
            s_diff = "UP" if diff_nz.iloc[-1] > 0 else "DOWN"
        else:
            s_diff = None

        if s_price and s_diff:
            if s_price == s_diff:
                agree += 1
            else:
                disagree += 1
        elif s_price:
            price_only += 1
        elif s_diff:
            diff_only += 1
        else:
            neither += 1
    except:
        neither += 1

total = agree + disagree + price_only + diff_only + neither
print(f"  Both agree:    {agree}")
print(f"  DISAGREE:      {disagree}")
print(f"  Price only:    {price_only}")
print(f"  Diff only:     {diff_only}")
print(f"  Neither:       {neither}")
print(f"  Total:         {total}")
if agree + disagree > 0:
    print(f"  Agreement rate: {agree/(agree+disagree)*100:.1f}%")
