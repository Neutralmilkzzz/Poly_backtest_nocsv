"""Analyze buy-sell timing gap in grid probe to check if instant flips inflate win rate."""
import pandas as pd, numpy as np, glob, os
import warnings
warnings.filterwarnings('ignore')

data_dir = r'C:\Users\ZHAOKAI\data'
files = sorted(glob.glob(os.path.join(data_dir, '*.csv')))

ENTRY_PRICE = 0.25
PROFIT_PRICE = 0.26
COOLDOWN = 8  # seconds - real trading requires this wait

def simulate_grid_with_timing(fpath):
    """Simulate grid round, tracking exact entry/exit times."""
    round_id = os.path.basename(fpath).replace('.csv','')
    try:
        df = pd.read_csv(fpath)
        if len(df) < 5:
            return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        elapsed = (ts - ts.iloc[0]).dt.total_seconds().values

        # Entry: ask <= 0.25 within 0-90s
        entry_idx = None
        entry_side = None
        entry_px = None
        for i in range(len(df)):
            if elapsed[i] > 90:
                break
            up_ask = df['up_best_ask'].iloc[i]
            dn_ask = df['down_best_ask'].iloc[i]
            if pd.notna(up_ask) and up_ask <= ENTRY_PRICE:
                entry_idx = i; entry_side = 'up'; entry_px = up_ask; break
            if pd.notna(dn_ask) and dn_ask <= ENTRY_PRICE:
                entry_idx = i; entry_side = 'down'; entry_px = dn_ask; break

        if entry_idx is None:
            return None

        entry_time = elapsed[entry_idx]
        bid_col = f'{entry_side}_best_bid'

        # === WITHOUT cooldown: sell immediately ===
        exit_px_no_cd = None
        exit_time_no_cd = None
        for j in range(entry_idx, len(df)):
            bid = df[bid_col].iloc[j]
            if pd.notna(bid) and bid >= PROFIT_PRICE:
                exit_px_no_cd = bid
                exit_time_no_cd = elapsed[j]
                break
        if exit_px_no_cd is None:
            last_bids = df[bid_col].dropna()
            exit_px_no_cd = last_bids.iloc[-1] if len(last_bids) > 0 else 0.0
            exit_time_no_cd = elapsed[-1]

        # === WITH cooldown: sell only after entry_time + 8s ===
        sell_start = entry_time + COOLDOWN
        exit_px_cd = None
        exit_time_cd = None
        for j in range(entry_idx, len(df)):
            if elapsed[j] < sell_start:
                continue
            bid = df[bid_col].iloc[j]
            if pd.notna(bid) and bid >= PROFIT_PRICE:
                exit_px_cd = bid
                exit_time_cd = elapsed[j]
                break
        if exit_px_cd is None:
            # Timeout: last bid
            post_cd = df[elapsed >= sell_start]
            if len(post_cd) > 0:
                last_bids_cd = post_cd[bid_col].dropna()
                exit_px_cd = last_bids_cd.iloc[-1] if len(last_bids_cd) > 0 else 0.0
                exit_time_cd = elapsed[-1]
            else:
                exit_px_cd = 0.0
                exit_time_cd = elapsed[-1]

        pnl_no_cd = (exit_px_no_cd - entry_px) * 10
        pnl_cd = (exit_px_cd - entry_px) * 10
        sell_delay = (exit_time_no_cd - entry_time) if exit_time_no_cd else 0

        return {
            'round_id': round_id,
            'entry_time': entry_time,
            'entry_px': entry_px,
            'side': entry_side,
            'exit_px_no_cd': exit_px_no_cd,
            'exit_time_no_cd': exit_time_no_cd,
            'pnl_no_cd': pnl_no_cd,
            'exit_px_cd': exit_px_cd,
            'exit_time_cd': exit_time_cd,
            'pnl_cd': pnl_cd,
            'sell_delay_no_cd': sell_delay,
            'instant_flip': sell_delay < COOLDOWN,
        }
    except:
        return None

print(f'Analyzing {len(files)} rounds...')
results = []
for i, f in enumerate(files):
    r = simulate_grid_with_timing(f)
    if r: results.append(r)
    if (i+1) % 1000 == 0:
        print(f'  {i+1}/{len(files)}')

df = pd.DataFrame(results)
print(f'\nTotal traded: {len(df)}')

print(f'\n{"="*60}')
print(f'WITHOUT cooldown (instant sell allowed):')
print(f'  Win rate: {(df.pnl_no_cd > 0).mean()*100:.1f}%')
print(f'  Total PnL: {df.pnl_no_cd.sum():.2f}')
print(f'  Mean PnL: {df.pnl_no_cd.mean():.4f}')

print(f'\nWITH 8s cooldown (realistic):')
print(f'  Win rate: {(df.pnl_cd > 0).mean()*100:.1f}%')
print(f'  Total PnL: {df.pnl_cd.sum():.2f}')
print(f'  Mean PnL: {df.pnl_cd.mean():.4f}')

print(f'\n{"="*60}')
print(f'INSTANT FLIP analysis (sell < 8s after buy):')
instant = df[df['instant_flip']]
not_instant = df[~df['instant_flip']]
print(f'  Instant flips: {len(instant)} ({len(instant)/len(df)*100:.1f}%)')
print(f'  Non-instant:   {len(not_instant)} ({len(not_instant)/len(df)*100:.1f}%)')

if len(instant) > 0:
    print(f'\n  Instant flips WITHOUT cooldown:')
    print(f'    Win rate: {(instant.pnl_no_cd > 0).mean()*100:.1f}%')
    print(f'    Total PnL: {instant.pnl_no_cd.sum():.2f}')
    print(f'  Instant flips WITH cooldown:')
    print(f'    Win rate: {(instant.pnl_cd > 0).mean()*100:.1f}%')
    print(f'    Total PnL: {instant.pnl_cd.sum():.2f}')
    print(f'  PnL DIFFERENCE (inflated profit): {instant.pnl_no_cd.sum() - instant.pnl_cd.sum():.2f}')

print(f'\n{"="*60}')
print(f'Sell delay distribution (without cooldown):')
for t in [0.5, 1, 2, 3, 5, 8, 15, 30, 60]:
    pct = (df.sell_delay_no_cd <= t).mean() * 100
    print(f'  Sold within {t:5.1f}s: {pct:.1f}%')

# Rolling loss rate WITH cooldown
df_cd = df.copy()
df_cd['loss'] = (df_cd['pnl_cd'] <= 0).astype(int)
df_cd['loss_rate_20'] = df_cd['loss'].rolling(20, min_periods=20).mean()
lr = df_cd['loss_rate_20'].dropna()
print(f'\n{"="*60}')
print(f'Rolling 20-round loss rate WITH cooldown:')
print(f'  Max: {lr.max()*100:.1f}%')
print(f'  Mean: {lr.mean()*100:.1f}%')
print(f'  P95: {lr.quantile(0.95)*100:.1f}%')
print(f'  P99: {lr.quantile(0.99)*100:.1f}%')
print(f'  Rounds > 70%: {(lr > 0.7).sum()}')
print(f'  Rounds > 60%: {(lr > 0.6).sum()}')
print(f'  Rounds > 50%: {(lr > 0.5).sum()}')
print(f'  Rounds > 40%: {(lr > 0.4).sum()}')

print(f'\n=== Top 10 worst windows WITH cooldown ===')
top = df_cd.nlargest(10, 'loss_rate_20')[['round_id','pnl_cd','loss_rate_20']]
for _, row in top.iterrows():
    print(f'  {row.round_id}  loss_rate={row.loss_rate_20*100:.0f}%  pnl={row.pnl_cd:.2f}')

# Show some example instant flips
if len(instant) > 0:
    print(f'\n=== Sample instant flips (sell < 8s) ===')
    samples = instant.head(10)
    for _, row in samples.iterrows():
        print(f'  {row.round_id}: entry@{row.entry_time:.1f}s px={row.entry_px:.2f} {row.side}'
              f' → sell@{row.exit_time_no_cd:.1f}s px={row.exit_px_no_cd:.2f}'
              f' delay={row.sell_delay_no_cd:.1f}s pnl_noCD={row.pnl_no_cd:.2f} pnl_CD={row.pnl_cd:.2f}')

df.to_csv('results/grid_timing_analysis.csv', index=False)
print('\nSaved: results/grid_timing_analysis.csv')
