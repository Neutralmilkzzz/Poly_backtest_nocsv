"""Deep microstructure analysis of individual rounds.
Look at: bid-ask spread, price dynamics, volatility patterns,
whale-like behavior, tail-end manipulation signals."""
import pandas as pd, numpy as np, glob, os
import warnings
warnings.filterwarnings('ignore')

data_dir = r'C:\Users\ZHAOKAI\data'
files = sorted(glob.glob(os.path.join(data_dir, '*.csv')))

def analyze_microstructure(fpath, label=""):
    """Detailed tick-by-tick microstructure of one round."""
    round_id = os.path.basename(fpath).replace('.csv','')
    df = pd.read_csv(fpath)
    ts = pd.to_datetime(df['timestamp'], format='ISO8601')
    elapsed = (ts - ts.iloc[0]).dt.total_seconds().values
    df['elapsed'] = elapsed
    
    print(f'\n{"="*70}')
    print(f'ROUND: {round_id} {label}')
    print(f'{"="*70}')
    print(f'Ticks: {len(df)}, Duration: {elapsed[-1]:.1f}s')
    
    # BTC info
    btc_diff = df['btc_diff'].dropna()
    if len(btc_diff) > 0:
        last_diff = btc_diff.iloc[-1]
        settlement = 'UP wins' if last_diff > 0 else 'DOWN wins'
        print(f'BTC diff: first={btc_diff.iloc[0]:.1f}, last={last_diff:.1f} → {settlement}')
    
    # Price summary at key timepoints
    print(f'\n--- Price Snapshots ---')
    for t_target in [0, 10, 30, 60, 90, 120, 180, 240, 260, 280, 290, 295, 299]:
        mask = (elapsed >= t_target) & (elapsed <= t_target + 2)
        if mask.any():
            row = df[mask].iloc[0]
            up_bid = row.get('up_best_bid', np.nan)
            up_ask = row.get('up_best_ask', np.nan)
            dn_bid = row.get('down_best_bid', np.nan)
            dn_ask = row.get('down_best_ask', np.nan)
            spread_up = (up_ask - up_bid) if pd.notna(up_ask) and pd.notna(up_bid) else np.nan
            spread_dn = (dn_ask - dn_bid) if pd.notna(dn_ask) and pd.notna(dn_bid) else np.nan
            diff = row.get('btc_diff', np.nan)
            print(f'  t={t_target:3d}s  UP[{up_bid:.2f}/{up_ask:.2f}] spread={spread_up:.2f}'
                  f'  DN[{dn_bid:.2f}/{dn_ask:.2f}] spread={spread_dn:.2f}'
                  f'  btc_diff={diff:+.1f}' if pd.notna(diff) else 
                  f'  t={t_target:3d}s  UP[{up_bid:.2f}/{up_ask:.2f}]  DN[{dn_bid:.2f}/{dn_ask:.2f}]')
    
    # Spread analysis
    df['up_spread'] = df['up_best_ask'] - df['up_best_bid']
    df['dn_spread'] = df['down_best_ask'] - df['down_best_bid']
    
    print(f'\n--- Spread Stats ---')
    for period_name, t_start, t_end in [('Early 0-90s', 0, 90), ('Mid 90-240s', 90, 240), ('Tail 240-300s', 240, 300)]:
        pmask = (elapsed >= t_start) & (elapsed <= t_end)
        if pmask.any():
            up_sp = df.loc[pmask, 'up_spread'].dropna()
            dn_sp = df.loc[pmask, 'dn_spread'].dropna()
            print(f'  {period_name}:')
            if len(up_sp) > 0:
                print(f'    UP spread: mean={up_sp.mean():.3f} min={up_sp.min():.3f} max={up_sp.max():.3f}')
            if len(dn_sp) > 0:
                print(f'    DN spread: mean={dn_sp.mean():.3f} min={dn_sp.min():.3f} max={dn_sp.max():.3f}')
    
    # Volatility in tail end
    print(f'\n--- Tail-End Analysis (240-300s) ---')
    tail = df[elapsed >= 240].copy()
    if len(tail) > 0:
        up_mid = tail['up_midpoint'].dropna()
        dn_mid = tail['down_midpoint'].dropna()
        if len(up_mid) > 1:
            up_vol = up_mid.diff().abs().mean()
            up_range = up_mid.max() - up_mid.min()
            print(f'  UP mid: range={up_range:.3f}, mean_tick_move={up_vol:.4f}')
            print(f'  UP mid: min={up_mid.min():.3f} max={up_mid.max():.3f}')
        if len(dn_mid) > 1:
            dn_vol = dn_mid.diff().abs().mean()
            dn_range = dn_mid.max() - dn_mid.min()
            print(f'  DN mid: range={dn_range:.3f}, mean_tick_move={dn_vol:.4f}')
            print(f'  DN mid: min={dn_mid.min():.3f} max={dn_mid.max():.3f}')
        
        # Look for extreme moves (potential whale manipulation)
        if len(up_mid) > 5:
            up_changes = up_mid.diff().abs()
            big_moves = up_changes[up_changes >= 0.05]
            if len(big_moves) > 0:
                print(f'  ** UP big moves (>=0.05): {len(big_moves)} times')
                for idx in big_moves.index[:5]:
                    t = elapsed[idx]
                    print(f'     t={t:.1f}s  move={up_mid.loc[idx] - up_mid.shift(1).loc[idx]:.3f}')
    
    # Grid probe simulation
    print(f'\n--- Grid Probe Outcome ---')
    entry_found = False
    for i in range(len(df)):
        if elapsed[i] > 90: break
        up_ask = df['up_best_ask'].iloc[i]
        dn_ask = df['down_best_ask'].iloc[i]
        if pd.notna(up_ask) and up_ask <= 0.25:
            entry_found = True
            side = 'up'
            entry_px = up_ask
            entry_t = elapsed[i]
            break
        if pd.notna(dn_ask) and dn_ask <= 0.25:
            entry_found = True
            side = 'down'
            entry_px = dn_ask
            entry_t = elapsed[i]
            break
    
    if entry_found:
        bid_col = f'{side}_best_bid'
        sell_start = entry_t + 8
        profit_hit = False
        for j in range(i, len(df)):
            if elapsed[j] < sell_start: continue
            bid = df[bid_col].iloc[j]
            if pd.notna(bid) and bid >= 0.26:
                profit_hit = True
                print(f'  Entry: t={entry_t:.1f}s {side} ask={entry_px:.2f}')
                print(f'  Exit:  t={elapsed[j]:.1f}s bid={bid:.2f} (PROFIT after {elapsed[j]-entry_t:.1f}s)')
                break
        if not profit_hit:
            last_bid = df[elapsed >= sell_start][bid_col].dropna()
            exit_px = last_bid.iloc[-1] if len(last_bid) > 0 else 0
            pnl = (exit_px - entry_px) * 10
            print(f'  Entry: t={entry_t:.1f}s {side} ask={entry_px:.2f}')
            print(f'  Exit:  TIMEOUT at bid={exit_px:.2f} PnL={pnl:.2f}')
    else:
        print(f'  No entry (ask never <= 0.25 in 0-90s)')
    
    return df


# Pick interesting rounds: 
# 1. A "normal" profitable round
# 2. A losing round (timeout)
# 3. A round from the worst window (Apr 6 area)
# 4. A high-volatility tail-end round
# 5. A round with extreme btc_diff

print("PART 1: Sample diverse rounds")
print("="*70)

# Find specific rounds
interesting = {
    'worst_window_1': '2026-04-06_09-25-00',
    'worst_window_2': '2026-04-06_09-15-00',
    'worst_window_3': '2026-04-02_23-10-00',
}

# Also find a "normal" round and a high-vol round
round_data = {}
for f in files[:200]:
    name = os.path.basename(f).replace('.csv','')
    try:
        df = pd.read_csv(f)
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        elapsed = (ts - ts.iloc[0]).dt.total_seconds().values
        
        # Check if grid trades
        entry = False
        for i in range(len(df)):
            if elapsed[i] > 90: break
            if pd.notna(df['up_best_ask'].iloc[i]) and df['up_best_ask'].iloc[i] <= 0.25:
                entry = True; break
            if pd.notna(df['down_best_ask'].iloc[i]) and df['down_best_ask'].iloc[i] <= 0.25:
                entry = True; break
        
        tail = df[elapsed >= 240]
        if len(tail) > 0:
            up_range = tail['up_midpoint'].dropna()
            if len(up_range) > 1:
                vol = up_range.max() - up_range.min()
                round_data[name] = {'vol': vol, 'entry': entry, 'path': f}
    except:
        pass

# Pick high vol tail round
if round_data:
    sorted_vols = sorted(round_data.items(), key=lambda x: -x[1]['vol'])
    high_vol = sorted_vols[0]
    interesting['high_vol_tail'] = high_vol[0]
    
    # Pick a normal entry round
    for name, info in sorted(round_data.items()):
        if info['entry']:
            interesting['normal_entry'] = name
            break

# Run detailed analysis
for label, round_name in interesting.items():
    fpath = os.path.join(data_dir, round_name + '.csv')
    if os.path.exists(fpath):
        analyze_microstructure(fpath, f'[{label}]')
    else:
        print(f'\n{round_name}: FILE NOT FOUND')

# PART 2: Aggregate microstructure stats
print(f'\n\n{"="*70}')
print('PART 2: Aggregate Microstructure (sampling 500 rounds)')
print("="*70)

spread_data = []
vol_data = []
sample_files = files[::10][:500]  # every 10th, up to 500

for f in sample_files:
    try:
        df = pd.read_csv(f)
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        elapsed = (ts - ts.iloc[0]).dt.total_seconds().values
        name = os.path.basename(f).replace('.csv','')
        
        for period, t0, t1 in [('early', 0, 90), ('mid', 90, 240), ('tail', 240, 300)]:
            mask = (elapsed >= t0) & (elapsed <= t1)
            if mask.any():
                chunk = df[mask]
                up_sp = (chunk['up_best_ask'] - chunk['up_best_bid']).dropna()
                dn_sp = (chunk['down_best_ask'] - chunk['down_best_bid']).dropna()
                if len(up_sp) > 0:
                    spread_data.append({'round': name, 'period': period, 'side': 'up', 'mean_spread': up_sp.mean(), 'max_spread': up_sp.max()})
                if len(dn_sp) > 0:
                    spread_data.append({'round': name, 'period': period, 'side': 'down', 'mean_spread': dn_sp.mean(), 'max_spread': dn_sp.max()})
        
        # Tail volatility
        tail = df[elapsed >= 240]
        if len(tail) > 5:
            up_mid = tail['up_midpoint'].dropna()
            if len(up_mid) > 1:
                vol_data.append({
                    'round': name,
                    'up_range': up_mid.max() - up_mid.min(),
                    'up_vol': up_mid.diff().abs().mean(),
                    'ticks': len(tail)
                })
    except:
        pass

if spread_data:
    sdf = pd.DataFrame(spread_data)
    print('\n--- Average Spread by Period ---')
    pivot = sdf.groupby(['period','side'])['mean_spread'].agg(['mean','median','quantile']).round(4)
    # Manual percentiles
    for (period, side), grp in sdf.groupby(['period','side']):
        vals = grp['mean_spread']
        print(f'  {period:5s} {side:4s}: mean={vals.mean():.4f}  median={vals.median():.4f}  p95={vals.quantile(0.95):.4f}  max={vals.max():.4f}')

if vol_data:
    vdf = pd.DataFrame(vol_data)
    print('\n--- Tail-End (240-300s) Volatility ---')
    print(f'  UP range: mean={vdf.up_range.mean():.4f}  median={vdf.up_range.median():.4f}  p95={vdf.up_range.quantile(0.95):.4f}  max={vdf.up_range.max():.4f}')
    print(f'  Rounds with range > 0.20: {(vdf.up_range > 0.20).sum()} ({(vdf.up_range > 0.20).mean()*100:.1f}%)')
    print(f'  Rounds with range > 0.50: {(vdf.up_range > 0.50).sum()} ({(vdf.up_range > 0.50).mean()*100:.1f}%)')
    print(f'  Rounds with range > 0.80: {(vdf.up_range > 0.80).sum()} ({(vdf.up_range > 0.80).mean()*100:.1f}%)')
