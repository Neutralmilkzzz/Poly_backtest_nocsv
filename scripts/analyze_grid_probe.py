"""Fast Python simulation of grid probe across all rounds."""
import pandas as pd, numpy as np, glob, os, sys
import warnings
warnings.filterwarnings('ignore')

data_dir = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\ZHAOKAI\data'
files = sorted(glob.glob(os.path.join(data_dir, '*.csv')))
print(f'Total files: {len(files)}')

ENTRY_PRICE = 0.25
PROFIT_PRICE = 0.26
ENTRY_WINDOW_END = 90
SHARES = 10

def simulate_grid_round(fpath):
    round_id = os.path.basename(fpath).replace('.csv','')
    try:
        df = pd.read_csv(fpath)
        if len(df) < 5:
            return (round_id, False, 0.0, None, None, None, 'too_few_rows')
        
        ts = pd.to_datetime(df['timestamp'])
        elapsed = (ts - ts.iloc[0]).dt.total_seconds().values
        
        entry_mask = elapsed <= ENTRY_WINDOW_END
        entry_idx = None
        entry_side = None
        entry_px = None
        
        for i in np.where(entry_mask)[0]:
            up_ask = df['up_best_ask'].iloc[i]
            dn_ask = df['down_best_ask'].iloc[i]
            if pd.notna(up_ask) and up_ask <= ENTRY_PRICE:
                entry_idx = i; entry_side = 'up'; entry_px = up_ask; break
            if pd.notna(dn_ask) and dn_ask <= ENTRY_PRICE:
                entry_idx = i; entry_side = 'down'; entry_px = dn_ask; break
        
        if entry_idx is None:
            return (round_id, False, 0.0, None, None, None, 'no_entry')
        
        bid_col = f'{entry_side}_best_bid'
        exit_px = None
        
        for j in range(entry_idx, len(df)):
            bid_val = df[bid_col].iloc[j]
            if pd.notna(bid_val) and bid_val >= PROFIT_PRICE:
                exit_px = bid_val
                break
        
        if exit_px is None:
            last_bids = df[bid_col].dropna()
            exit_px = last_bids.iloc[-1] if len(last_bids) > 0 else 0.0
        
        pnl = (exit_px - entry_px) * SHARES
        return (round_id, True, pnl, entry_px, exit_px, entry_side, 'traded')
    except Exception as e:
        return (round_id, False, 0.0, None, None, None, str(e))

print('Simulating grid probe...')
results = []
for i, f in enumerate(files):
    results.append(simulate_grid_round(f))
    if (i+1) % 1000 == 0:
        print(f'  {i+1}/{len(files)}')

print(f'Done! {len(results)} rounds')

rdf = pd.DataFrame(results, columns=['round_id','traded','pnl','entry_px','exit_px','side','reason'])
traded = rdf[rdf['traded']].copy().reset_index(drop=True)
not_traded = rdf[~rdf['traded']]

print(f'\n=== Summary ===')
print(f'Traded: {len(traded)} / {len(rdf)} ({len(traded)/len(rdf)*100:.1f}%)')
print(f'Skip reasons: {not_traded.reason.value_counts().to_dict()}')
print(f'Win rate: {(traded.pnl > 0).mean()*100:.1f}%')
print(f'Total PnL: {traded.pnl.sum():.2f}')
print(f'Mean PnL/trade: {traded.pnl.mean():.4f}')

# Rolling 20-round loss rate
traded['loss'] = (traded['pnl'] <= 0).astype(int)
traded['loss_rate_20'] = traded['loss'].rolling(20, min_periods=20).mean()

lr = traded['loss_rate_20'].dropna()
print(f'\n=== Rolling 20-round loss rate ===')
print(f'Max: {lr.max()*100:.1f}%')
print(f'Mean: {lr.mean()*100:.1f}%')
print(f'P95: {lr.quantile(0.95)*100:.1f}%')
print(f'P99: {lr.quantile(0.99)*100:.1f}%')
print(f'Rounds > 70%: {(lr > 0.7).sum()}')
print(f'Rounds > 60%: {(lr > 0.6).sum()}')
print(f'Rounds > 50%: {(lr > 0.5).sum()}')
print(f'Rounds > 40%: {(lr > 0.4).sum()}')
print(f'Rounds > 30%: {(lr > 0.3).sum()}')

print(f'\n=== Top 10 worst 20-round windows ===')
top = traded.nlargest(10, 'loss_rate_20')[['round_id','pnl','loss_rate_20','entry_px','exit_px','side']]
for _, row in top.iterrows():
    print(f'  {row.round_id}  loss_rate={row.loss_rate_20*100:.0f}%  pnl={row.pnl:.2f}  entry={row.entry_px}  exit={row.exit_px}  {row.side}')

# Consecutive loss streaks
streaks = []
streak = 0
for i, row in traded.iterrows():
    if row['pnl'] <= 0:
        streak += 1
    else:
        if streak > 0:
            streaks.append((i - streak, i - 1, streak))
        streak = 0
if streak > 0:
    streaks.append((len(traded) - streak, len(traded) - 1, streak))

streaks.sort(key=lambda x: -x[2])
print(f'\n=== Top 10 consecutive loss streaks ===')
for s, e, l in streaks[:10]:
    rids = f"{traded.iloc[s]['round_id']} to {traded.iloc[min(e, len(traded)-1)]['round_id']}"
    total_loss = traded.iloc[s:e+1]['pnl'].sum()
    print(f'  Streak={l}  PnL={total_loss:.2f}  {rids}')

# Rolling cumulative PnL
traded['cum_pnl'] = traded['pnl'].cumsum()
traded['rolling_pnl_20'] = traded['pnl'].rolling(20).sum()
print(f'\n=== Rolling 20-round PnL ===')
rp = traded['rolling_pnl_20'].dropna()
print(f'Min (worst 20-round): {rp.min():.2f}')
print(f'Max (best 20-round): {rp.max():.2f}')
print(f'Mean: {rp.mean():.2f}')

# Show worst PnL windows
print(f'\n=== Top 10 worst 20-round PnL windows ===')
worst_pnl = traded.nsmallest(10, 'rolling_pnl_20')[['round_id','pnl','rolling_pnl_20','loss_rate_20']]
for _, row in worst_pnl.iterrows():
    print(f'  {row.round_id}  20r_pnl={row.rolling_pnl_20:.2f}  loss_rate={row.loss_rate_20*100:.0f}%')

os.makedirs('results', exist_ok=True)
traded.to_csv('results/grid_probe_full_analysis.csv', index=False)
print('\nSaved: results/grid_probe_full_analysis.csv')
