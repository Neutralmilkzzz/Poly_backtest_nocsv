"""Check: is btc_diff always 0 at the last row? That would be a data bug."""
import pandas as pd
import glob
import os
import numpy as np

files = sorted(glob.glob(r"C:\Users\ZHAOKAI\data\*.csv"))
diffs = []

for f in files:
    try:
        df = pd.read_csv(f, low_memory=False)
        df["btc_diff"] = pd.to_numeric(df["btc_diff"], errors="coerce")
        df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        t0 = df["ts"].iloc[0]
        df["elapsed"] = (df["ts"] - t0).dt.total_seconds()

        # Last row's btc_diff
        last_diff = df["btc_diff"].dropna()
        if len(last_diff) == 0:
            continue
        final_val = float(last_diff.iloc[-1])
        final_time = float(df.loc[last_diff.index[-1], "elapsed"])

        # Also get the btc_diff at different times
        at_290 = df[df["elapsed"] <= 290]["btc_diff"].dropna()
        at_280 = df[df["elapsed"] <= 280]["btc_diff"].dropna()
        at_240 = df[df["elapsed"] <= 240]["btc_diff"].dropna()

        diffs.append({
            "file": os.path.basename(f),
            "diff_last": final_val,
            "diff_last_time": final_time,
            "diff_at_290": float(at_290.iloc[-1]) if len(at_290) > 0 else np.nan,
            "diff_at_280": float(at_280.iloc[-1]) if len(at_280) > 0 else np.nan,
            "diff_at_240": float(at_240.iloc[-1]) if len(at_240) > 0 else np.nan,
        })
    except:
        continue

ddf = pd.DataFrame(diffs)
print(f"Total rounds: {len(ddf)}")
print()

# Is last_diff always 0?
n_zero = (ddf["diff_last"] == 0).sum()
print(f"last_diff == 0.0: {n_zero} / {len(ddf)} ({n_zero/len(ddf)*100:.1f}%)")
print(f"last_diff == 0.0 exactly: {(ddf['diff_last'] == 0.0).sum()}")
print()

# Distribution of last_diff
print("last_diff distribution:")
print(ddf["diff_last"].describe())
print()

# Most common values
print("Top 10 most common last_diff values:")
print(ddf["diff_last"].value_counts().head(10))
print()

# Check: does btc_diff RESET to 0 at 300s?
# Compare diff at 240s vs diff at last
print("Comparison: diff@240 vs diff@last")
print(f"  Mean diff@240: {ddf['diff_at_240'].mean():.2f}")
print(f"  Mean diff@290: {ddf['diff_at_290'].mean():.2f}")
print(f"  Mean diff@last: {ddf['diff_last'].mean():.2f}")
print()

# How many rounds: diff@240 is large but diff@last is 0?
big_then_zero = ddf[(ddf["diff_at_240"].abs() > 50) & (ddf["diff_last"] == 0)]
print(f"Rounds where |diff@240| > 50 but diff_last == 0: {len(big_then_zero)}")
print()

# Show some examples where last diff is NOT 0
nonzero = ddf[ddf["diff_last"] != 0].head(10)
print("Examples where diff_last != 0:")
for _, r in nonzero.iterrows():
    print(f"  {r['file']}: diff@240={r['diff_at_240']:.1f}, diff@290={r['diff_at_290']:.1f}, "
          f"diff_last={r['diff_last']:.1f} @{r['diff_last_time']:.0f}s")

print()
# Show examples where last diff IS 0
zero_ex = ddf[ddf["diff_last"] == 0].head(10)
print("Examples where diff_last == 0:")
for _, r in zero_ex.iterrows():
    print(f"  {r['file']}: diff@240={r['diff_at_240']:.1f}, diff@290={r['diff_at_290']:.1f}, "
          f"diff_last={r['diff_last']:.1f} @{r['diff_last_time']:.0f}s")
