"""Quantify tail-end signals to calibrate the new regime detector.
Compute per-round: tail_range, tail_spread, tail_reversal, settlement direction."""
import pandas as pd, numpy as np, glob, os
import warnings
warnings.filterwarnings('ignore')

data_dir = r'C:\Users\ZHAOKAI\data'
files = sorted(glob.glob(os.path.join(data_dir, '*.csv')))

def compute_tail_signals(fpath):
    round_id = os.path.basename(fpath).replace('.csv','')
    try:
        df = pd.read_csv(fpath)
        if len(df) < 10: return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        elapsed = (ts - ts.iloc[0]).dt.total_seconds().values

        # Settlement
        btc_diff = df['btc_diff'].dropna()
        if len(btc_diff) == 0: return None
        last_diff = btc_diff.iloc[-1]
        settlement = 'up' if last_diff > 0 else 'down'

        # Price at t=240s
        t240_mask = (elapsed >= 238) & (elapsed <= 242)
        if t240_mask.any():
            t240_row = df[t240_mask].iloc[0]
            up_mid_240 = t240_row.get('up_midpoint', np.nan)
            predicted_winner = 'up' if pd.notna(up_mid_240) and up_mid_240 > 0.5 else 'down'
            reversal = predicted_winner != settlement
        else:
            up_mid_240 = np.nan
            reversal = None

        # Tail range (240-300s)
        tail = df[elapsed >= 240]
        if len(tail) < 5:
            return None

        up_mid = tail['up_midpoint'].dropna()
        dn_mid = tail['down_midpoint'].dropna()
        
        tail_up_range = (up_mid.max() - up_mid.min()) if len(up_mid) > 1 else 0
        tail_dn_range = (dn_mid.max() - dn_mid.min()) if len(dn_mid) > 1 else 0
        tail_range = max(tail_up_range, tail_dn_range)

        # Tail spread (last 30s: 270-300s)
        last30 = df[(elapsed >= 270) & (elapsed <= 300)]
        if len(last30) > 0:
            up_spread = (last30['up_best_ask'] - last30['up_best_bid']).dropna()
            dn_spread = (last30['down_best_ask'] - last30['down_best_bid']).dropna()
            mean_spread = max(
                up_spread.mean() if len(up_spread) > 0 else 0,
                dn_spread.mean() if len(dn_spread) > 0 else 0
            )
            max_spread = max(
                up_spread.max() if len(up_spread) > 0 else 0,
                dn_spread.max() if len(dn_spread) > 0 else 0
            )
        else:
            mean_spread = 0
            max_spread = 0

        # Tail volatility (tick-to-tick moves in last 60s)
        if len(up_mid) > 2:
            big_moves = up_mid.diff().abs()
            n_big_moves = (big_moves >= 0.05).sum()
            max_move = big_moves.max()
        else:
            n_big_moves = 0
            max_move = 0

        # Speed of collapse: max drawdown in any 10-second window in tail
        if len(up_mid) > 10:
            up_vals = up_mid.values
            up_elapsed = elapsed[up_mid.index]
            max_dd = 0
            max_rally = 0
            for i in range(len(up_vals)):
                window = (up_elapsed >= up_elapsed[i]) & (up_elapsed <= up_elapsed[i] + 10)
                w_vals = up_vals[window[:len(up_vals)]] if i < len(up_vals) else []
                if len(w_vals) > 1:
                    dd = w_vals[0] - w_vals.min()
                    rally = w_vals.max() - w_vals[0]
                    max_dd = max(max_dd, dd)
                    max_rally = max(max_rally, rally)
        else:
            max_dd = 0
            max_rally = 0

        return {
            'round_id': round_id,
            'settlement': settlement,
            'up_mid_240': up_mid_240,
            'predicted_240': predicted_winner if reversal is not None else None,
            'reversal': reversal,
            'tail_range': tail_range,
            'mean_spread_last30': mean_spread,
            'max_spread_last30': max_spread,
            'n_big_moves': n_big_moves,
            'max_single_move': max_move,
            'max_10s_drawdown': max_dd,
            'max_10s_rally': max_rally,
            'tail_ticks': len(tail),
        }
    except:
        return None

print(f'Computing tail signals for {len(files)} rounds...')
results = []
for i, f in enumerate(files):
    r = compute_tail_signals(f)
    if r: results.append(r)
    if (i+1) % 1000 == 0: print(f'  {i+1}/{len(files)}')

df = pd.DataFrame(results)
print(f'\nTotal rounds with tail data: {len(df)}')

print(f'\n{"="*60}')
print(f'TAIL RANGE (max side range in 240-300s)')
print(f'  Mean: {df.tail_range.mean():.3f}')
print(f'  Median: {df.tail_range.median():.3f}')
print(f'  P75: {df.tail_range.quantile(0.75):.3f}')
print(f'  P90: {df.tail_range.quantile(0.90):.3f}')
print(f'  P95: {df.tail_range.quantile(0.95):.3f}')
for t in [0.10, 0.20, 0.30, 0.50, 0.70, 0.80, 0.90]:
    pct = (df.tail_range >= t).mean() * 100
    print(f'  >= {t:.2f}: {(df.tail_range >= t).sum():4d} rounds ({pct:.1f}%)')

print(f'\n{"="*60}')
print(f'TAIL SPREAD (mean in last 30s)')
print(f'  Mean: {df.mean_spread_last30.mean():.4f}')
print(f'  Median: {df.mean_spread_last30.median():.4f}')
print(f'  P90: {df.mean_spread_last30.quantile(0.90):.4f}')
print(f'  P95: {df.mean_spread_last30.quantile(0.95):.4f}')
for t in [0.02, 0.03, 0.05, 0.10, 0.20]:
    pct = (df.mean_spread_last30 >= t).mean() * 100
    print(f'  >= {t:.2f}: {(df.mean_spread_last30 >= t).sum():4d} rounds ({pct:.1f}%)')

print(f'\n{"="*60}')
print(f'TAIL REVERSALS (direction at t=240 vs settlement)')
valid_rev = df[df.reversal.notna()]
n_rev = valid_rev.reversal.sum()
print(f'  Total rounds with valid data: {len(valid_rev)}')
print(f'  Reversals: {n_rev} ({n_rev/len(valid_rev)*100:.1f}%)')

# Cross-tabulation: reversal + tail_range
print(f'\n  Reversal vs Tail Range:')
rev_df = valid_rev.copy()
rev_df['range_bucket'] = pd.cut(rev_df['tail_range'], bins=[0, 0.1, 0.3, 0.5, 0.7, 1.0], labels=['<0.1','0.1-0.3','0.3-0.5','0.5-0.7','0.7+'])
ct = pd.crosstab(rev_df['range_bucket'], rev_df['reversal'], margins=True, normalize='index')
print(ct.round(3).to_string())

print(f'\n{"="*60}')
print(f'BIG MOVES (single tick >= 0.05)')
print(f'  Rounds with any: {(df.n_big_moves > 0).sum()} ({(df.n_big_moves > 0).mean()*100:.1f}%)')
print(f'  Mean count per round: {df.n_big_moves.mean():.1f}')
print(f'  Max count: {df.n_big_moves.max()}')
print(f'  Max single move: {df.max_single_move.max():.3f}')

print(f'\n{"="*60}')
print(f'10s DRAWDOWN/RALLY (max price move in any 10-second window)')
print(f'  Mean drawdown: {df.max_10s_drawdown.mean():.3f}')
print(f'  P90 drawdown: {df.max_10s_drawdown.quantile(0.90):.3f}')
print(f'  P95 drawdown: {df.max_10s_drawdown.quantile(0.95):.3f}')
print(f'  Mean rally: {df.max_10s_rally.mean():.3f}')
print(f'  P90 rally: {df.max_10s_rally.quantile(0.90):.3f}')
print(f'  P95 rally: {df.max_10s_rally.quantile(0.95):.3f}')

# Composite "whale score"
print(f'\n{"="*60}')
print(f'COMPOSITE WHALE SIGNAL THRESHOLDS')
print(f'Signal = tail_range >= 0.50 AND (reversal OR mean_spread >= 0.03)')
whale_mask = (df.tail_range >= 0.50) & ((df.reversal == True) | (df.mean_spread_last30 >= 0.03))
print(f'  Whale-like rounds: {whale_mask.sum()} ({whale_mask.mean()*100:.1f}%)')

print(f'\nSignal = tail_range >= 0.30')
s30 = df.tail_range >= 0.30
print(f'  Rounds: {s30.sum()} ({s30.mean()*100:.1f}%)')

print(f'\nSignal = reversal AND tail_range >= 0.30')
s_rev30 = (df.reversal == True) & (df.tail_range >= 0.30)
print(f'  Rounds: {s_rev30.sum()} ({s_rev30.mean()*100:.1f}%)')

# Rolling window: count of whale-like rounds in last 20
df['whale_signal'] = whale_mask.astype(int)
df['whale_count_20'] = df['whale_signal'].rolling(20, min_periods=20).sum()
wc = df['whale_count_20'].dropna()
print(f'\n{"="*60}')
print(f'ROLLING 20-ROUND WHALE SIGNAL COUNT')
print(f'  Max: {wc.max():.0f}')
print(f'  Mean: {wc.mean():.1f}')
print(f'  P95: {wc.quantile(0.95):.0f}')
print(f'  P99: {wc.quantile(0.99):.0f}')
for t in [3, 5, 7, 10]:
    pct = (wc >= t).mean() * 100
    print(f'  >= {t}: {(wc >= t).sum()} windows ({pct:.1f}%)')

# Save
df.to_csv('results/tail_signals_analysis.csv', index=False)
print(f'\nSaved: results/tail_signals_analysis.csv')
