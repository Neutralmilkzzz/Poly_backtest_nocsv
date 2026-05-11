"""Debug: Why do settle_win trades NOT hit take-profit?
If settlement = 1.0, price went from 0.10 to 1.00, so bid MUST have crossed TP target."""
import pandas as pd
import numpy as np
import os
import warnings
warnings.filterwarnings("ignore")

# Load results
res = pd.read_csv(r"C:\Users\ZHAOKAI\Poly_backtest_Final\results\frontrun_clean.csv")
settle_wins = res[res["exit_type"] == "settle_win"].head(5)

data_dir = r"C:\Users\ZHAOKAI\data"

for _, trade in settle_wins.iterrows():
    fname = trade["file"]
    fpath = os.path.join(data_dir, fname)
    print(f"\n{'='*80}")
    print(f"FILE: {fname}")
    print(f"Buy {trade['buy_side']} @ {trade['entry_price']:.3f} at {trade['entry_time']:.0f}s")
    print(f"TP target: {trade['tp_target']:.3f}")
    print(f"Settlement: {trade['settlement']}, btc_diff={trade['btc_diff']}")
    print(f"Exit: settle_win @ 1.000, PnL={trade['pnl']:+.2f}")
    print(f"{'='*80}")

    df = pd.read_csv(fpath, low_memory=False)
    df["ts"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df.dropna(subset=["ts"])
    t0 = df["ts"].iloc[0]
    df["elapsed"] = (df["ts"] - t0).dt.total_seconds()

    bid_col = f"{trade['buy_side']}_best_bid"
    ask_col = f"{trade['buy_side']}_best_ask"
    mid_col = f"{trade['buy_side']}_midpoint"

    for c in [bid_col, ask_col, mid_col]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
        df[c] = df[c].ffill()

    # Show bid evolution AFTER entry
    post = df[df["elapsed"] > trade["entry_time"]]
    tp = trade["tp_target"]

    # Find max bid after entry
    max_bid = post[bid_col].max()
    max_bid_time = post.loc[post[bid_col].idxmax(), "elapsed"] if not np.isnan(max_bid) else None
    print(f"\nMax {bid_col} after entry: {max_bid:.3f} at {max_bid_time:.1f}s")
    print(f"TP target: {tp:.3f}")
    print(f"Max bid >= TP? {max_bid >= tp}")

    # Show bid at key timestamps after entry
    print(f"\nBid timeline after entry ({trade['entry_time']:.0f}s):")
    for t in range(int(trade["entry_time"]), 305, 5):
        rows_at_t = df[(df["elapsed"] >= t) & (df["elapsed"] < t + 1)]
        if len(rows_at_t) > 0:
            b = rows_at_t[bid_col].iloc[-1]
            a = rows_at_t[ask_col].iloc[-1]
            m = rows_at_t[mid_col].iloc[-1]
            flag = " <<< TP should trigger!" if b >= tp else ""
            print(f"  @{t:3d}s: bid={b:.3f}, ask={a:.3f}, mid={m:.3f}{flag}")

    # Check: are there ANY rows with bid >= tp?
    tp_hits = post[post[bid_col] >= tp]
    print(f"\nRows where bid >= {tp:.3f}: {len(tp_hits)}")
    if len(tp_hits) > 0:
        first_hit = tp_hits.iloc[0]
        print(f"  FIRST HIT: @{first_hit['elapsed']:.1f}s, bid={first_hit[bid_col]:.3f}")
        print(f"  >>> BUG CONFIRMED: TP should have triggered here!")
    else:
        print(f"  NO HITS - bid never reached {tp:.3f}")
        # Show the last 20 rows to see what happens at settlement
        print(f"\n  Last 10 rows of data:")
        for _, row in df.tail(10).iterrows():
            print(f"    @{row['elapsed']:.1f}s: bid={row[bid_col]:.3f}, "
                  f"ask={row[ask_col]:.3f}, event={row['event_type']}")
