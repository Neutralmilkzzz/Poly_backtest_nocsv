"""Deep dive into fill model accuracy: entry/exit price vs actual market prices."""
import pandas as pd, numpy as np, glob, os
import warnings
warnings.filterwarnings('ignore')

data_dir = r'C:\Users\ZHAOKAI\data'
files = sorted(glob.glob(os.path.join(data_dir, '*.csv')))

ENTRY_PRICE = 0.25
PROFIT_PRICE = 0.26
COOLDOWN = 8

def analyze_round_detail(fpath):
    """Detailed tick-by-tick analysis of one round's grid trade."""
    round_id = os.path.basename(fpath).replace('.csv','')
    try:
        df = pd.read_csv(fpath)
        if len(df) < 5: return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        elapsed = (ts - ts.iloc[0]).dt.total_seconds().values

        # Entry
        entry_idx = None
        entry_side = None
        actual_ask = None
        for i in range(len(df)):
            if elapsed[i] > 90: break
            up_ask = df['up_best_ask'].iloc[i]
            dn_ask = df['down_best_ask'].iloc[i]
            if pd.notna(up_ask) and up_ask <= ENTRY_PRICE:
                entry_idx = i; entry_side = 'up'; actual_ask = up_ask; break
            if pd.notna(dn_ask) and dn_ask <= ENTRY_PRICE:
                entry_idx = i; entry_side = 'down'; actual_ask = dn_ask; break
        
        if entry_idx is None: return None

        entry_time = elapsed[entry_idx]
        bid_col = f'{entry_side}_best_bid'
        ask_col = f'{entry_side}_best_ask'

        # R fill model: entry fills at entry_price (0.25), NOT actual ask
        # Reality: on CLOB, limit buy at 0.25 when ask=0.01 fills at 0.01
        r_entry_px = ENTRY_PRICE  # R model always fills at limit price
        real_entry_px = actual_ask  # Real: fills at ask price

        # Sell with cooldown
        sell_start = entry_time + COOLDOWN
        
        exit_type = 'timeout'
        r_exit_px = None  # R model: observed_bid (price improvement)
        real_exit_px = None  # Real: fills at limit price 0.26
        exit_bid = None
        exit_time = None
        
        for j in range(entry_idx, len(df)):
            if elapsed[j] < sell_start: continue
            bid = df[bid_col].iloc[j]
            if pd.notna(bid) and bid >= PROFIT_PRICE:
                exit_type = 'profit'
                exit_bid = bid
                exit_time = elapsed[j]
                r_exit_px = bid  # R model: observed_bid (often > 0.26)
                real_exit_px = PROFIT_PRICE  # Real CLOB: fills at limit (0.26)
                break
        
        if exit_type == 'timeout':
            # Find last bid before round end
            valid = df[elapsed >= sell_start]
            if len(valid) > 0:
                last_bid = valid[bid_col].dropna()
                if len(last_bid) > 0:
                    exit_bid = last_bid.iloc[-1]
                    r_exit_px = exit_bid
                    real_exit_px = exit_bid  # Both models agree on timeout
                    exit_time = elapsed[-1]
                else:
                    r_exit_px = 0; real_exit_px = 0; exit_time = elapsed[-1]
            else:
                r_exit_px = 0; real_exit_px = 0; exit_time = elapsed[-1]

        # PnL calculations (10 shares)
        r_pnl = (r_exit_px - r_entry_px) * 10           # R model PnL
        real_pnl = (real_exit_px - real_entry_px) * 10   # Realistic PnL

        return {
            'round_id': round_id,
            'entry_time': entry_time,
            'side': entry_side,
            'actual_ask': actual_ask,
            'r_entry_px': r_entry_px,
            'real_entry_px': real_entry_px,
            'entry_overpay': r_entry_px - real_entry_px,
            'exit_type': exit_type,
            'exit_bid': exit_bid,
            'exit_time': exit_time,
            'r_exit_px': r_exit_px,
            'real_exit_px': real_exit_px,
            'exit_overget': r_exit_px - real_exit_px if r_exit_px and real_exit_px else 0,
            'r_pnl': r_pnl,
            'real_pnl': real_pnl,
            'pnl_diff': r_pnl - real_pnl,
        }
    except:
        return None

print(f'Deep-diving {len(files)} rounds...')
results = []
for i, f in enumerate(files):
    r = analyze_round_detail(f)
    if r: results.append(r)
    if (i+1) % 1000 == 0: print(f'  {i+1}/{len(files)}')

df = pd.DataFrame(results)
print(f'\nTotal analyzed: {len(df)}')

# Entry price analysis
print(f'\n{"="*60}')
print(f'ENTRY FILL ANALYSIS')
print(f'  R model always fills at {ENTRY_PRICE} (limit price)')
print(f'  Real CLOB fills at actual ask price')
print(f'  Actual ask distribution:')
for threshold in [0.01, 0.05, 0.10, 0.15, 0.20, 0.24, 0.25]:
    pct = (df.actual_ask <= threshold).mean() * 100
    n = (df.actual_ask <= threshold).sum()
    print(f'    ask <= {threshold:.2f}: {n:5d} ({pct:.1f}%)')

print(f'  Mean actual ask: {df.actual_ask.mean():.4f}')
print(f'  Mean R entry: {df.r_entry_px.mean():.4f}')
print(f'  Total entry overpay (R model): {df.entry_overpay.sum()*10:.2f} (per 10 shares)')

# Exit price analysis
print(f'\n{"="*60}')
print(f'EXIT FILL ANALYSIS')
profit_exits = df[df.exit_type == 'profit']
timeout_exits = df[df.exit_type == 'timeout']
print(f'  Profit exits: {len(profit_exits)} ({len(profit_exits)/len(df)*100:.1f}%)')
print(f'  Timeout exits: {len(timeout_exits)} ({len(timeout_exits)/len(df)*100:.1f}%)')

if len(profit_exits) > 0:
    print(f'\n  PROFIT EXITS:')
    print(f'    R model: fills at observed bid (price improvement)')
    print(f'    Real CLOB: fills at limit price ({PROFIT_PRICE})')
    print(f'    Mean R exit: {profit_exits.r_exit_px.mean():.4f}')
    print(f'    Mean real exit: {profit_exits.real_exit_px.mean():.4f}')
    print(f'    Mean overget per trade: {profit_exits.exit_overget.mean():.4f}')
    print(f'    Total exit overget: {profit_exits.exit_overget.sum()*10:.2f}')
    
    print(f'\n    Exit bid distribution (when profit):')
    for px in [0.26, 0.30, 0.40, 0.50, 0.60, 0.80, 0.99]:
        pct = (profit_exits.exit_bid >= px).mean() * 100
        print(f'      bid >= {px:.2f}: {pct:.1f}%')

# Net PnL comparison
print(f'\n{"="*60}')
print(f'NET PnL COMPARISON')
print(f'  R model total PnL: {df.r_pnl.sum():.2f}')
print(f'  Realistic total PnL: {df.real_pnl.sum():.2f}')
print(f'  DIFFERENCE: {df.pnl_diff.sum():.2f}')
print(f'    From entry overpay: {-df.entry_overpay.sum()*10:.2f} (R is pessimistic)')
print(f'    From exit overget: {df[df.exit_type=="profit"].exit_overget.sum()*10:.2f} (R is optimistic)')

# Win rates
print(f'\n  R model win rate: {(df.r_pnl > 0).mean()*100:.1f}%')
print(f'  Realistic win rate: {(df.real_pnl > 0).mean()*100:.1f}%')

# Rolling loss rate with realistic PnL
df['loss_real'] = (df['real_pnl'] <= 0).astype(int)
df['loss_rate_20_real'] = df['loss_real'].rolling(20, min_periods=20).mean()
lr = df['loss_rate_20_real'].dropna()
print(f'\n{"="*60}')
print(f'REGIME DETECTION with REALISTIC PnL:')
print(f'  Max 20-round loss rate: {lr.max()*100:.1f}%')
print(f'  Mean: {lr.mean()*100:.1f}%')
print(f'  P95: {lr.quantile(0.95)*100:.1f}%')
print(f'  P99: {lr.quantile(0.99)*100:.1f}%')
print(f'  Rounds > 70%: {(lr > 0.7).sum()}')
print(f'  Rounds > 60%: {(lr > 0.6).sum()}')
print(f'  Rounds > 50%: {(lr > 0.5).sum()}')
print(f'  Rounds > 40%: {(lr > 0.4).sum()}')

# Show worst periods
print(f'\n=== Top 10 worst windows (realistic PnL) ===')
top = df.nlargest(10, 'loss_rate_20_real')[['round_id','real_pnl','loss_rate_20_real','actual_ask','exit_type']]
for _, row in top.iterrows():
    print(f'  {row.round_id}  loss={row.loss_rate_20_real*100:.0f}%  pnl={row.real_pnl:.2f}  ask={row.actual_ask}  {row.exit_type}')

df.to_csv('results/grid_fillmodel_analysis.csv', index=False)
print('\nSaved: results/grid_fillmodel_analysis.csv')
