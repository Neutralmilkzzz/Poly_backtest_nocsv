"""Fast Python simulation of the full probe-whale system with microstructure regime detection.
Matches the R logic: grid probe + tail probe + microstructure regime + whale-follow."""
import pandas as pd, numpy as np, glob, os, sys, time
import warnings
warnings.filterwarnings('ignore')

data_dir = r'C:\Users\ZHAOKAI\data'
files = sorted(glob.glob(os.path.join(data_dir, '*.csv')))

# Config (matching probe_whale.yaml)
CFG = {
    'cooldown': 8,
    'grid': {'entry_price': 0.25, 'profit_price': 0.26,
             'entry_start': 0, 'entry_end': 90, 'shares': 10},
    'tail': {'entry_price': 0.60, 'profit_price': 0.80, 'stop_price': 0.50,
             'entry_start': 240, 'entry_end': 280, 'shares': 10},
    'regime': {'window': 20, 'range_threshold': 0.50,
               'whale_count': 7, 'normal_count': 3},
    'whale': {'cheap_threshold': 0.15, 'cheap_tp': 0.40, 'cheap_budget': 10,
              'exp_threshold': 0.80, 'dip_price': 0.65, 'dip_tp': 0.85, 'exp_budget': 10},
}

def parse_round(fpath):
    try:
        df = pd.read_csv(fpath)
        if len(df) < 10: return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        df['elapsed'] = (ts - ts.iloc[0]).dt.total_seconds()
        btc_diff = df['btc_diff'].dropna()
        if len(btc_diff) == 0: return None
        df['_settlement'] = 'up' if btc_diff.iloc[-1] > 0 else 'down'
        return df
    except:
        return None

def run_grid_probe(df, cfg):
    """Grid probe: buy at entry_price, sell at profit_price, entry 0-90s"""
    gc = cfg['grid']
    entry_price = gc['entry_price']
    profit_price = gc['profit_price']
    cooldown = cfg['cooldown']
    shares = gc['shares']
    
    entry_mask = (df['elapsed'] >= gc['entry_start']) & (df['elapsed'] <= gc['entry_end'])
    entry_df = df[entry_mask]
    
    # Try UP side entry
    for side in ['up', 'down']:
        ask_col = f'{side}_best_ask'
        bid_col = f'{side}_best_bid'
        
        # Find entry: ask <= entry_price
        entry_rows = entry_df[entry_df[ask_col].fillna(999) <= entry_price]
        if len(entry_rows) == 0:
            continue
        
        entry_row = entry_rows.iloc[0]
        entry_time = entry_row['elapsed']
        
        # Sell phase: after cooldown
        sell_start = entry_time + cooldown
        sell_df = df[(df['elapsed'] >= sell_start) & (df['elapsed'] <= 180)]
        
        # Look for bid >= profit_price
        profit_rows = sell_df[sell_df[bid_col].fillna(0) >= profit_price]
        if len(profit_rows) > 0:
            # Sell at target_price (maker, CLOB fills at limit)
            pnl = (profit_price - entry_price) * shares
            return {'traded': True, 'side': side, 'pnl': pnl, 'exit': 'profit'}
        
        # Timeout: sell at last available bid
        if len(sell_df) > 0:
            last_bid = sell_df[bid_col].dropna()
            if len(last_bid) > 0:
                exit_price = last_bid.iloc[-1]
                pnl = (exit_price - entry_price) * shares
                return {'traded': True, 'side': side, 'pnl': pnl, 'exit': 'timeout'}
        
        # Worst case: hold to settlement
        settlement = df['_settlement'].iloc[0]
        exit_price = 1.0 if settlement == side else 0.0
        pnl = (exit_price - entry_price) * shares
        return {'traded': True, 'side': side, 'pnl': pnl, 'exit': 'settle'}
    
    return {'traded': False, 'side': None, 'pnl': 0, 'exit': None}

def compute_tail_signal(df, cfg):
    """Compute tail-end microstructure signal"""
    rc = cfg['regime']
    tail = df[df['elapsed'] >= 240]
    if len(tail) < 5:
        return 0, False
    
    up_mid = tail['up_midpoint'].dropna()
    dn_mid = tail['down_midpoint'].dropna()
    up_range = (up_mid.max() - up_mid.min()) if len(up_mid) > 1 else 0
    dn_range = (dn_mid.max() - dn_mid.min()) if len(dn_mid) > 1 else 0
    tail_range = max(up_range, dn_range)
    is_whale = tail_range >= rc['range_threshold']
    return tail_range, is_whale

def run_whale_follow(df, cfg):
    """Whale-follow: cheap side (ask<=0.15) + expensive side (bid>=0.80, dip to 0.65)"""
    wc = cfg['whale']
    settlement = df['_settlement'].iloc[0]
    
    cheap = {'traded': False, 'pnl': 0, 'side': None, 'exit': None}
    exp = {'traded': False, 'pnl': 0, 'side': None, 'exit': None}
    
    # Cheap side: find ask <= threshold
    cheap_entered = False
    cheap_exited = False
    exp_side = None
    exp_pending = False
    exp_entered = False
    exp_exited = False
    
    for _, row in df.iterrows():
        # Cheap entry
        if not cheap_entered and not cheap_exited:
            for side in ['up', 'down']:
                ask = row.get(f'{side}_best_ask', np.nan)
                if pd.notna(ask) and 0 < ask <= wc['cheap_threshold']:
                    qty = max(1, int(wc['cheap_budget'] / ask))
                    cheap.update({'traded': True, 'side': side, 'entry': ask, 'qty': qty})
                    cheap_entered = True
                    break
        
        # Cheap take-profit
        if cheap_entered and not cheap_exited:
            bid = row.get(f"{cheap['side']}_best_bid", np.nan)
            if pd.notna(bid) and bid >= wc['cheap_tp']:
                cheap['pnl'] = (wc['cheap_tp'] - cheap['entry']) * cheap['qty']
                cheap['exit'] = 'take_profit'
                cheap_exited = True
        
        # Expensive: detect side (bid >= threshold)
        if not exp_pending and not exp_entered:
            for side in ['up', 'down']:
                bid = row.get(f'{side}_best_bid', np.nan)
                if pd.notna(bid) and bid >= wc['exp_threshold']:
                    exp_side = side
                    exp_pending = True
                    break
        
        # Expensive: limit fill (ask <= dip_price)
        if exp_pending and not exp_entered:
            ask = row.get(f'{exp_side}_best_ask', np.nan)
            if pd.notna(ask) and 0 < ask <= wc['dip_price']:
                qty = max(1, int(wc['exp_budget'] / wc['dip_price']))
                exp.update({'traded': True, 'side': exp_side, 'entry': wc['dip_price'], 'qty': qty})
                exp_entered = True
        
        # Expensive: take-profit
        if exp_entered and not exp_exited:
            bid = row.get(f"{exp['side']}_best_bid", np.nan)
            if pd.notna(bid) and bid >= wc['dip_tp']:
                exp['pnl'] = (wc['dip_tp'] - exp['entry']) * exp['qty']
                exp['exit'] = 'take_profit'
                exp_exited = True
        
        if cheap_exited and exp_exited:
            break
    
    # Settlement for unfilled
    if cheap_entered and not cheap_exited:
        if settlement == cheap['side']:
            cheap['pnl'] = (1.0 - cheap['entry']) * cheap['qty']
            cheap['exit'] = 'settle_win'
        else:
            cheap['pnl'] = (0.0 - cheap['entry']) * cheap['qty']
            cheap['exit'] = 'settle_lose'
    
    if exp_entered and not exp_exited:
        if settlement == exp['side']:
            exp['pnl'] = (1.0 - exp['entry']) * exp['qty']
            exp['exit'] = 'settle_win'
        else:
            exp['pnl'] = (0.0 - exp['entry']) * exp['qty']
            exp['exit'] = 'settle_lose'
    
    return cheap, exp

# ═══════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════
print(f'Running probe-whale system on {len(files)} files...')
t0 = time.time()

regime = 'NORMAL'
signal_window = []
results = []
switch_log = []

for i, fpath in enumerate(files):
    round_id = os.path.basename(fpath).replace('.csv', '')
    df = parse_round(fpath)
    if df is None:
        continue
    
    # 1. Grid probe
    grid = run_grid_probe(df, CFG)
    
    # 2. Microstructure signal
    tail_range, is_whale_signal = compute_tail_signal(df, CFG)
    signal_window.append(int(is_whale_signal))
    if len(signal_window) > CFG['regime']['window']:
        signal_window.pop(0)
    
    whale_count = sum(signal_window) if len(signal_window) >= CFG['regime']['window'] else None
    
    # 3. Regime transition
    prev_regime = regime
    if regime == 'NORMAL':
        if whale_count is not None and whale_count >= CFG['regime']['whale_count']:
            regime = 'WHALE_ACTIVE'
    elif regime == 'WHALE_ACTIVE':
        if whale_count is not None and whale_count <= CFG['regime']['normal_count']:
            regime = 'NORMAL'
    
    if regime != prev_regime:
        switch_log.append({'round': round_id, 'from': prev_regime, 'to': regime,
                           'whale_count': whale_count, 'tail_range': tail_range})
    
    # 4. Whale-follow (only in WHALE_ACTIVE)
    is_whale = regime == 'WHALE_ACTIVE'
    if is_whale:
        cheap, exp = run_whale_follow(df, CFG)
    else:
        cheap = {'traded': False, 'pnl': 0, 'side': None, 'exit': None}
        exp = {'traded': False, 'pnl': 0, 'side': None, 'exit': None}
    
    # 5. PnL accounting
    grid_real_pnl = grid['pnl'] if (not is_whale and grid['traded']) else 0
    total_real = grid_real_pnl + cheap['pnl'] + exp['pnl']
    
    results.append({
        'round_id': round_id,
        'regime': regime,
        'whale_count': whale_count,
        'tail_range': tail_range,
        'is_whale_signal': is_whale_signal,
        'grid_traded': grid['traded'],
        'grid_pnl': grid['pnl'],
        'grid_is_paper': is_whale,
        'whale_cheap_traded': cheap['traded'],
        'whale_cheap_pnl': cheap['pnl'],
        'whale_cheap_exit': cheap.get('exit'),
        'whale_exp_traded': exp['traded'],
        'whale_exp_pnl': exp['pnl'],
        'whale_exp_exit': exp.get('exit'),
        'total_real_pnl': total_real,
    })
    
    if (i+1) % 500 == 0:
        elapsed = time.time() - t0
        print(f'  {i+1}/{len(files)} ({elapsed:.0f}s) [regime: {regime}]')

df_res = pd.DataFrame(results)
df_res['cum_pnl'] = df_res['total_real_pnl'].cumsum()

# Save
os.makedirs('results/probe_whale_v2', exist_ok=True)
df_res.to_csv('results/probe_whale_v2/py_probe_whale_results.csv', index=False)

# ═══════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════
elapsed = time.time() - t0
n = len(df_res)
n_whale = (df_res.regime == 'WHALE_ACTIVE').sum()
n_signals = df_res.is_whale_signal.sum()

print(f'\n{"="*60}')
print(f'PROBE-WHALE SYSTEM v2 (Microstructure Regime)')
print(f'{"="*60}')
print(f'Total rounds: {n} | Time: {elapsed:.0f}s')
print(f'Whale signals: {n_signals} ({n_signals/n*100:.1f}%)')
print(f'WHALE_ACTIVE rounds: {n_whale} ({n_whale/n*100:.1f}%)')
print(f'Regime switches: {len(switch_log)}')

if switch_log:
    print(f'\nSwitch log:')
    for s in switch_log[:30]:  # first 30
        print(f"  {s['round']}: {s['from']} → {s['to']} (whale_count={s['whale_count']}, range={s['tail_range']:.3f})")
    if len(switch_log) > 30:
        print(f'  ... and {len(switch_log)-30} more')

# Grid probe stats
grid_traded = df_res[df_res.grid_traded]
grid_real = df_res[(~df_res.grid_is_paper) & df_res.grid_traded]
grid_paper = df_res[df_res.grid_is_paper & df_res.grid_traded]

print(f'\n--- GRID PROBE ---')
print(f'Total traded: {len(grid_traded)}')
print(f'  Real (NORMAL): {len(grid_real)}, PnL: {grid_real.grid_pnl.sum():.2f}')
print(f'  Paper (WHALE): {len(grid_paper)}, PnL: {grid_paper.grid_pnl.sum():.2f} (not counted)')
if len(grid_real) > 0:
    wr = (grid_real.grid_pnl > 0).mean() * 100
    print(f'  Real win rate: {wr:.1f}%')

# Whale-follow stats
whale_rounds = df_res[df_res.regime == 'WHALE_ACTIVE']
print(f'\n--- WHALE-FOLLOW ---')
print(f'Rounds active: {len(whale_rounds)}')

cheap_trades = whale_rounds[whale_rounds.whale_cheap_traded]
exp_trades = whale_rounds[whale_rounds.whale_exp_traded]
print(f'Cheap trades: {len(cheap_trades)}, PnL: {cheap_trades.whale_cheap_pnl.sum():.2f}')
if len(cheap_trades) > 0:
    vc = cheap_trades.whale_cheap_exit.value_counts()
    print(f'  Exit types: {dict(vc)}')
    print(f'  Win rate: {(cheap_trades.whale_cheap_pnl > 0).mean()*100:.1f}%')
    print(f'  Avg PnL: {cheap_trades.whale_cheap_pnl.mean():.3f}')

print(f'Expensive trades: {len(exp_trades)}, PnL: {exp_trades.whale_exp_pnl.sum():.2f}')
if len(exp_trades) > 0:
    ve = exp_trades.whale_exp_exit.value_counts()
    print(f'  Exit types: {dict(ve)}')
    print(f'  Win rate: {(exp_trades.whale_exp_pnl > 0).mean()*100:.1f}%')
    print(f'  Avg PnL: {exp_trades.whale_exp_pnl.mean():.3f}')

# Total system
total_pnl = df_res.total_real_pnl.sum()
print(f'\n--- TOTAL SYSTEM ---')
print(f'Grid real PnL:  {grid_real.grid_pnl.sum():.2f}')
print(f'Whale cheap PnL: {cheap_trades.whale_cheap_pnl.sum() if len(cheap_trades) else 0:.2f}')
print(f'Whale exp PnL:   {exp_trades.whale_exp_pnl.sum() if len(exp_trades) else 0:.2f}')
print(f'Total system PnL: {total_pnl:.2f}')
print(f'Max drawdown: {df_res.cum_pnl.min():.2f}')
print(f'Final cum PnL: {df_res.cum_pnl.iloc[-1]:.2f}')
