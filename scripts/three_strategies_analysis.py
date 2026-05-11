"""
═══════════════════════════════════════════════════════════════
 三策略全量回测 + 庄控信号识别 + 跟庄策略测试
═══════════════════════════════════════════════════════════════

策略A: 早期网格 (Classic Grid)   — 0-90s 买0.25 卖0.26
策略B: 尾盘动量 (Tail Momentum)  — t=250s 跟方向持有到结算
策略C: 尾盘低吸 (Tail Dip Buy)   — 240-280s 极端低价买入持有到结算

对每个策略:
  1. 全量回测 (5000+ rounds)
  2. 提取微结构特征
  3. 识别亏损与哪些微结构特征关联 → 庄控信号
  4. 测试跟庄反击策略
"""

import pandas as pd, numpy as np, glob, os, time, warnings
from collections import defaultdict
warnings.filterwarnings('ignore')

DATA_DIR = r'C:\Users\ZHAOKAI\data'
OUT_DIR  = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies'
os.makedirs(OUT_DIR, exist_ok=True)

files = sorted(glob.glob(os.path.join(DATA_DIR, '*.csv')))
print(f'Found {len(files)} CSV files')

# ─────────────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────────────
def load_round(fpath):
    """Load and parse one round CSV. Returns df with elapsed column or None."""
    try:
        df = pd.read_csv(fpath)
        if len(df) < 20:
            return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        df['elapsed'] = (ts - ts.iloc[0]).dt.total_seconds()
        bd = df['btc_diff'].dropna()
        if len(bd) == 0:
            return None
        df.attrs['settlement'] = 'up' if bd.iloc[-1] > 0 else 'down'
        df.attrs['round_id'] = os.path.basename(fpath).replace('.csv', '')
        return df
    except:
        return None

# ─────────────────────────────────────────────────────
# MICROSTRUCTURE FEATURES (computed once per round)
# ─────────────────────────────────────────────────────
def compute_features(df):
    """Extract microstructure features for whale signal analysis."""
    feat = {}

    # Settlement
    feat['settlement'] = df.attrs.get('settlement', 'unknown')

    # ── Early phase (0-90s) ──
    early = df[df['elapsed'] <= 90]
    if len(early) > 1:
        feat['early_up_range'] = early['up_midpoint'].max() - early['up_midpoint'].min()
        feat['early_dn_range'] = early['down_midpoint'].max() - early['down_midpoint'].min()
        feat['early_up_mid_mean'] = early['up_midpoint'].mean()
    else:
        feat['early_up_range'] = 0
        feat['early_dn_range'] = 0
        feat['early_up_mid_mean'] = 0.5

    # ── Tail phase (240-300s) ──
    tail = df[df['elapsed'] >= 240]
    if len(tail) > 5:
        up_mid = tail['up_midpoint'].dropna()
        dn_mid = tail['down_midpoint'].dropna()
        feat['tail_up_range'] = (up_mid.max() - up_mid.min()) if len(up_mid) > 1 else 0
        feat['tail_dn_range'] = (dn_mid.max() - dn_mid.min()) if len(dn_mid) > 1 else 0
        feat['tail_range'] = max(feat['tail_up_range'], feat['tail_dn_range'])

        # Direction at t=240 vs settlement
        t240 = df[(df['elapsed'] >= 238) & (df['elapsed'] <= 242)]
        if len(t240) > 0:
            up240 = t240['up_midpoint'].iloc[0]
            feat['up_mid_240'] = up240
            feat['direction_240'] = 'up' if up240 > 0.5 else 'down'
            feat['reversal'] = int(feat['direction_240'] != feat['settlement'])
        else:
            feat['up_mid_240'] = np.nan
            feat['direction_240'] = None
            feat['reversal'] = np.nan

        # Direction at t=250 (for momentum strategy)
        t250 = df[(df['elapsed'] >= 248) & (df['elapsed'] <= 252)]
        if len(t250) > 0:
            feat['up_mid_250'] = t250['up_midpoint'].iloc[0]
        else:
            feat['up_mid_250'] = np.nan

        # Spread in last 30s
        last30 = df[df['elapsed'] >= 270]
        if len(last30) > 0:
            us = (last30['up_best_ask'] - last30['up_best_bid']).dropna()
            ds = (last30['down_best_ask'] - last30['down_best_bid']).dropna()
            feat['tail_spread'] = max(
                us.mean() if len(us) > 0 else 0,
                ds.mean() if len(ds) > 0 else 0
            )
        else:
            feat['tail_spread'] = 0

        # Big moves (single tick changes >= 0.05)
        if len(up_mid) > 2:
            diffs = up_mid.diff().abs()
            feat['n_big_moves'] = int((diffs >= 0.05).sum())
            feat['max_single_move'] = float(diffs.max())
        else:
            feat['n_big_moves'] = 0
            feat['max_single_move'] = 0

        # Tail min prices (for dip detection)
        feat['tail_up_min_ask'] = tail['up_best_ask'].replace(0, np.nan).dropna().min() if 'up_best_ask' in tail else 1.0
        feat['tail_dn_min_ask'] = tail['down_best_ask'].replace(0, np.nan).dropna().min() if 'down_best_ask' in tail else 1.0

        # Tail max bid (for momentum quality)
        feat['tail_up_max_bid'] = tail['up_best_bid'].dropna().max() if 'up_best_bid' in tail else 0
        feat['tail_dn_max_bid'] = tail['down_best_bid'].dropna().max() if 'down_best_bid' in tail else 0
    else:
        for k in ['tail_up_range','tail_dn_range','tail_range','up_mid_240',
                   'direction_240','reversal','up_mid_250','tail_spread',
                   'n_big_moves','max_single_move','tail_up_min_ask',
                   'tail_dn_min_ask','tail_up_max_bid','tail_dn_max_bid']:
            feat[k] = 0 if k not in ['direction_240'] else None

    return feat

# ─────────────────────────────────────────────────────
# STRATEGY A: CLASSIC GRID (早期网格)
# ─────────────────────────────────────────────────────
def strategy_grid(df, entry_price=0.25, profit_price=0.26, cooldown=8,
                  entry_start=0, entry_end=90, sell_end=180, shares=10):
    """Grid: buy at ask <= entry_price in [entry_start, entry_end],
    sell at bid >= profit_price after cooldown, timeout at sell_end, or settle."""
    result = {'traded': False, 'side': None, 'pnl': 0.0,
              'entry_price': np.nan, 'exit_price': np.nan, 'exit_type': None}

    for side in ['up', 'down']:
        ask_col = f'{side}_best_ask'
        bid_col = f'{side}_best_bid'

        # Entry scan
        entry_df = df[(df['elapsed'] >= entry_start) & (df['elapsed'] <= entry_end)]
        entry_rows = entry_df[entry_df[ask_col].fillna(999) <= entry_price]
        if len(entry_rows) == 0:
            continue

        entry_time = entry_rows.iloc[0]['elapsed']

        # Sell scan (after cooldown)
        sell_start = entry_time + cooldown
        sell_df = df[(df['elapsed'] >= sell_start) & (df['elapsed'] <= sell_end)]

        # Take profit
        profit_rows = sell_df[sell_df[bid_col].fillna(0) >= profit_price]
        if len(profit_rows) > 0:
            pnl = (profit_price - entry_price) * shares  # maker fill at limit
            result.update({'traded': True, 'side': side, 'pnl': pnl,
                           'entry_price': entry_price, 'exit_price': profit_price,
                           'exit_type': 'profit'})
            return result

        # Timeout: market sell at last bid
        if len(sell_df) > 0:
            last_bids = sell_df[bid_col].dropna()
            if len(last_bids) > 0:
                exit_p = float(last_bids.iloc[-1])
                pnl = (exit_p - entry_price) * shares
                result.update({'traded': True, 'side': side, 'pnl': pnl,
                               'entry_price': entry_price, 'exit_price': exit_p,
                               'exit_type': 'timeout'})
                return result

        # Settlement
        settlement = df.attrs.get('settlement', 'down')
        exit_p = 1.0 if settlement == side else 0.0
        pnl = (exit_p - entry_price) * shares
        result.update({'traded': True, 'side': side, 'pnl': pnl,
                       'entry_price': entry_price, 'exit_price': exit_p,
                       'exit_type': 'settle'})
        return result

    return result

# ─────────────────────────────────────────────────────
# STRATEGY B: TAIL MOMENTUM (尾盘动量)
# ─────────────────────────────────────────────────────
def strategy_tail_momentum(df, check_time=250, threshold=0.55,
                           shares=10):
    """At check_time, if UP mid > threshold → buy UP; if < (1-threshold) → buy DOWN.
    Buy at market (ask). Hold to settlement."""
    result = {'traded': False, 'side': None, 'pnl': 0.0,
              'entry_price': np.nan, 'exit_price': np.nan, 'exit_type': None}

    # Find the tick nearest to check_time
    check = df[(df['elapsed'] >= check_time - 2) & (df['elapsed'] <= check_time + 2)]
    if len(check) == 0:
        return result

    row = check.iloc[0]
    up_mid = row.get('up_midpoint', np.nan)
    if pd.isna(up_mid):
        return result

    if up_mid > threshold:
        side = 'up'
    elif up_mid < (1 - threshold):
        side = 'down'
    else:
        return result  # no signal

    ask_col = f'{side}_best_ask'
    ask_val = row.get(ask_col, np.nan)
    if pd.isna(ask_val) or ask_val <= 0 or ask_val >= 0.95:
        return result  # too expensive or invalid

    entry_price = float(ask_val)
    settlement = df.attrs.get('settlement', 'down')
    exit_price = 1.0 if settlement == side else 0.0
    pnl = (exit_price - entry_price) * shares

    result.update({'traded': True, 'side': side, 'pnl': pnl,
                   'entry_price': entry_price, 'exit_price': exit_price,
                   'exit_type': 'settle_win' if exit_price > entry_price else 'settle_lose'})
    return result

# ─────────────────────────────────────────────────────
# STRATEGY C: TAIL DIP BUY (尾盘低吸)
# ─────────────────────────────────────────────────────
def strategy_tail_dip(df, dip_threshold=0.20, entry_start=240,
                      entry_end=285, take_profit=0.50, shares=10):
    """In tail end, buy whichever side has ask <= dip_threshold.
    Exit: take profit at 0.50 (2.5x), or hold to settlement."""
    result = {'traded': False, 'side': None, 'pnl': 0.0,
              'entry_price': np.nan, 'exit_price': np.nan, 'exit_type': None}

    entry_df = df[(df['elapsed'] >= entry_start) & (df['elapsed'] <= entry_end)]
    if len(entry_df) == 0:
        return result

    settlement = df.attrs.get('settlement', 'down')

    for side in ['up', 'down']:
        ask_col = f'{side}_best_ask'
        bid_col = f'{side}_best_bid'

        dip_rows = entry_df[(entry_df[ask_col].fillna(999) > 0) &
                            (entry_df[ask_col].fillna(999) <= dip_threshold)]
        if len(dip_rows) == 0:
            continue

        entry_row = dip_rows.iloc[0]
        entry_price = float(entry_row[ask_col])
        entry_time = entry_row['elapsed']

        # Try take profit
        post_entry = df[df['elapsed'] > entry_time]
        tp_rows = post_entry[post_entry[bid_col].fillna(0) >= take_profit]
        if len(tp_rows) > 0:
            pnl = (take_profit - entry_price) * shares
            result.update({'traded': True, 'side': side, 'pnl': pnl,
                           'entry_price': entry_price, 'exit_price': take_profit,
                           'exit_type': 'take_profit'})
            return result

        # Hold to settlement
        exit_price = 1.0 if settlement == side else 0.0
        pnl = (exit_price - entry_price) * shares
        result.update({'traded': True, 'side': side, 'pnl': pnl,
                       'entry_price': entry_price, 'exit_price': exit_price,
                       'exit_type': 'settle_win' if exit_price > 0.5 else 'settle_lose'})
        return result

    return result

# ─────────────────────────────────────────────────────
# MAIN LOOP
# ─────────────────────────────────────────────────────
print(f'\n{"="*60}')
print(f'Running 3 strategies on {len(files)} rounds...')
print(f'{"="*60}\n')

t0 = time.time()
all_results = []

for i, fpath in enumerate(files):
    df = load_round(fpath)
    if df is None:
        continue

    rid = df.attrs['round_id']

    # Compute features once
    feat = compute_features(df)
    feat['round_id'] = rid

    # Run 3 strategies
    rA = strategy_grid(df)
    rB = strategy_tail_momentum(df)
    rC = strategy_tail_dip(df)

    row = {
        'round_id': rid,
        # Features
        **{f'f_{k}': v for k, v in feat.items() if k != 'round_id'},
        # Strategy A
        'A_traded': rA['traded'], 'A_side': rA['side'], 'A_pnl': rA['pnl'],
        'A_entry': rA['entry_price'], 'A_exit': rA['exit_price'], 'A_type': rA['exit_type'],
        # Strategy B
        'B_traded': rB['traded'], 'B_side': rB['side'], 'B_pnl': rB['pnl'],
        'B_entry': rB['entry_price'], 'B_exit': rB['exit_price'], 'B_type': rB['exit_type'],
        # Strategy C
        'C_traded': rC['traded'], 'C_side': rC['side'], 'C_pnl': rC['pnl'],
        'C_entry': rC['entry_price'], 'C_exit': rC['exit_price'], 'C_type': rC['exit_type'],
    }
    all_results.append(row)

    if (i + 1) % 500 == 0:
        print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')

elapsed = time.time() - t0
print(f'\nDone in {elapsed:.0f}s, {len(all_results)} rounds processed')

df_all = pd.DataFrame(all_results)
df_all.to_csv(os.path.join(OUT_DIR, 'three_strategies_raw.csv'), index=False)

# ═══════════════════════════════════════════════════════
# ANALYSIS
# ═══════════════════════════════════════════════════════

def analyze_strategy(df_all, prefix, name):
    """Analyze one strategy's performance and correlations."""
    print(f'\n{"="*60}')
    print(f'  STRATEGY {prefix}: {name}')
    print(f'{"="*60}')

    traded = df_all[df_all[f'{prefix}_traded'] == True].copy()
    n_total = len(df_all)
    n_traded = len(traded)
    print(f'Rounds: {n_total} | Traded: {n_traded} ({n_traded/n_total*100:.1f}%)')

    if n_traded == 0:
        print('  No trades!')
        return None

    pnl = traded[f'{prefix}_pnl']
    wins = (pnl > 0).sum()
    losses = (pnl <= 0).sum()
    win_rate = wins / n_traded * 100
    total_pnl = pnl.sum()
    avg_pnl = pnl.mean()
    avg_win = pnl[pnl > 0].mean() if wins > 0 else 0
    avg_loss = pnl[pnl <= 0].mean() if losses > 0 else 0

    print(f'Win rate: {win_rate:.1f}% ({wins}W / {losses}L)')
    print(f'Total PnL: {total_pnl:.2f} | Avg PnL: {avg_pnl:.4f}')
    print(f'Avg win: {avg_win:.4f} | Avg loss: {avg_loss:.4f}')

    # Exit type distribution
    types = traded[f'{prefix}_type'].value_counts()
    print(f'Exit types: {dict(types)}')

    # Entry price stats
    entries = traded[f'{prefix}_entry'].dropna()
    if len(entries) > 0:
        print(f'Entry price: mean={entries.mean():.3f}, min={entries.min():.3f}, max={entries.max():.3f}')

    # Rolling 20-round loss rate
    traded_pnl = traded[f'{prefix}_pnl'].values
    if len(traded_pnl) >= 20:
        loss_flags = (traded_pnl <= 0).astype(float)
        rolling_loss = pd.Series(loss_flags).rolling(20).mean().dropna()
        print(f'\nRolling 20-round loss rate:')
        print(f'  Mean: {rolling_loss.mean():.1%}')
        print(f'  Max:  {rolling_loss.max():.1%}')
        print(f'  P90:  {rolling_loss.quantile(0.90):.1%}')
        print(f'  P95:  {rolling_loss.quantile(0.95):.1%}')
        for t in [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]:
            cnt = (rolling_loss >= t).sum()
            pct = cnt / len(rolling_loss) * 100
            print(f'  >= {t:.0%}: {cnt} windows ({pct:.1f}%)')

    # ── Whale Signal Detection ──
    # Correlate strategy losses with microstructure features
    print(f'\n--- Whale Signal Detection ---')
    traded['_loss'] = (traded[f'{prefix}_pnl'] <= 0).astype(int)

    # Features to test
    feature_cols = ['f_tail_range', 'f_tail_spread', 'f_reversal',
                    'f_n_big_moves', 'f_max_single_move',
                    'f_early_up_range', 'f_tail_up_range', 'f_tail_dn_range']

    print(f'\n  Loss rate by feature buckets:')
    for fcol in feature_cols:
        if fcol not in traded.columns:
            continue
        vals = traded[fcol].dropna()
        if len(vals) < 50:
            continue

        # Split into quartiles
        try:
            traded['_bucket'] = pd.qcut(vals, 4, duplicates='drop')
            grouped = traded.groupby('_bucket')['_loss'].agg(['mean', 'count'])
            if len(grouped) >= 2:
                lo = grouped.iloc[0]['mean']
                hi = grouped.iloc[-1]['mean']
                delta = hi - lo
                if abs(delta) > 0.05:
                    print(f'\n  {fcol}: (Q1 loss={lo:.1%}, Q4 loss={hi:.1%}, Δ={delta:+.1%})')
                    for idx, row in grouped.iterrows():
                        print(f'    {idx}: loss={row["mean"]:.1%} (n={int(row["count"])})')
        except:
            pass

    # Best feature: tail_range (from prior analysis)
    if 'f_tail_range' in traded.columns:
        for threshold in [0.30, 0.50, 0.70]:
            hi = traded[traded['f_tail_range'] >= threshold]
            lo = traded[traded['f_tail_range'] < threshold]
            if len(hi) > 10 and len(lo) > 10:
                hi_loss = hi['_loss'].mean()
                lo_loss = lo['_loss'].mean()
                hi_pnl = hi[f'{prefix}_pnl'].mean()
                lo_pnl = lo[f'{prefix}_pnl'].mean()
                print(f'\n  tail_range >= {threshold:.2f}: n={len(hi)}, loss={hi_loss:.1%}, avg_pnl={hi_pnl:.4f}')
                print(f'  tail_range <  {threshold:.2f}: n={len(lo)}, loss={lo_loss:.1%}, avg_pnl={lo_pnl:.4f}')

    return traded

# Run analysis for each strategy
print(f'\n{"#"*60}')
print(f'#  THREE-STRATEGY ANALYSIS RESULTS')
print(f'{"#"*60}')

tA = analyze_strategy(df_all, 'A', 'Classic Grid (早期网格) — Buy 0.25, Sell 0.26, 0-90s')
tB = analyze_strategy(df_all, 'B', 'Tail Momentum (尾盘动量) — Follow direction at t=250')
tC = analyze_strategy(df_all, 'C', 'Tail Dip Buy (尾盘低吸) — Buy ask<0.20 in tail')

# ═══════════════════════════════════════════════════════
# CROSS-STRATEGY COMPARISON
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  CROSS-STRATEGY COMPARISON')
print(f'{"="*60}')

for prefix, name in [('A','Grid'), ('B','Momentum'), ('C','Dip')]:
    traded = df_all[df_all[f'{prefix}_traded']]
    pnl = traded[f'{prefix}_pnl']
    n = len(traded)
    wr = (pnl > 0).mean() * 100 if n > 0 else 0
    total = pnl.sum() if n > 0 else 0
    print(f'{name:>10}: {n:5d} trades, WR={wr:5.1f}%, PnL={total:+8.2f}')

# ═══════════════════════════════════════════════════════
# REGIME DETECTION CANDIDATES PER STRATEGY
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  REGIME DETECTION: Best Signals Per Strategy')
print(f'{"="*60}')

def find_best_signal(df_all, prefix, name):
    """Find which microstructure feature best predicts losses for this strategy."""
    traded = df_all[df_all[f'{prefix}_traded']].copy()
    if len(traded) < 50:
        print(f'\n{name}: Too few trades for signal analysis')
        return

    traded['_loss'] = (traded[f'{prefix}_pnl'] <= 0).astype(int)
    base_loss = traded['_loss'].mean()

    print(f'\n{name}: Base loss rate = {base_loss:.1%}')
    print(f'{"Signal":<35} {"Threshold":>10} {"N_hi":>6} {"LR_hi":>7} {"N_lo":>6} {"LR_lo":>7} {"Lift":>7}')
    print('-' * 85)

    best_lift = 0
    best_signal = None

    signals = [
        ('f_tail_range', [0.20, 0.30, 0.50, 0.70]),
        ('f_tail_spread', [0.015, 0.02, 0.03, 0.05]),
        ('f_reversal', [0.5]),
        ('f_n_big_moves', [5, 10, 20, 50]),
        ('f_max_single_move', [0.05, 0.10, 0.20, 0.30]),
        ('f_early_up_range', [0.03, 0.05, 0.10]),
        ('f_tail_up_range', [0.20, 0.30, 0.50]),
    ]

    for feat, thresholds in signals:
        if feat not in traded.columns:
            continue
        for t in thresholds:
            hi = traded[traded[feat].fillna(0) >= t]
            lo = traded[traded[feat].fillna(0) < t]
            if len(hi) < 20 or len(lo) < 20:
                continue
            lr_hi = hi['_loss'].mean()
            lr_lo = lo['_loss'].mean()
            lift = lr_hi - lr_lo
            marker = ' ★' if lift > best_lift else ''
            print(f'{feat:<35} {t:>10.3f} {len(hi):>6} {lr_hi:>6.1%} {len(lo):>6} {lr_lo:>6.1%} {lift:>+6.1%}{marker}')
            if lift > best_lift:
                best_lift = lift
                best_signal = (feat, t, lr_hi, lr_lo, len(hi), len(lo))

    if best_signal:
        f, t, lr_hi, lr_lo, n_hi, n_lo = best_signal
        print(f'\n★ Best signal for {name}: {f} >= {t}')
        print(f'  When signal ON:  loss rate = {lr_hi:.1%} (n={n_hi})')
        print(f'  When signal OFF: loss rate = {lr_lo:.1%} (n={n_lo})')
        print(f'  Lift: {best_lift:+.1%}')
        return (f, t)
    return None

sigA = find_best_signal(df_all, 'A', 'Strategy A (Grid)')
sigB = find_best_signal(df_all, 'B', 'Strategy B (Momentum)')
sigC = find_best_signal(df_all, 'C', 'Strategy C (Dip Buy)')

# ═══════════════════════════════════════════════════════
# FOLLOW-THE-WHALE COUNTER-STRATEGIES
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  FOLLOW-THE-WHALE: Counter-Strategies')
print(f'{"="*60}')

# Counter A: When whale is active for grid, stop grid & do nothing (capital preservation)
# Counter B: When whale fakes momentum, FADE the direction (contrarian)
# Counter C: When whale creates dips that don't recover, DON'T buy dips (also capital preservation)
#            or: buy dips ONLY when the dip is extremely deep (< 0.10) and there's time left

def counter_strategy_B_fade(df, check_time=250, threshold=0.55, shares=10):
    """CONTRARIAN: When momentum says buy UP, buy DOWN instead.
    Only activated when whale signal is on."""
    result = {'traded': False, 'pnl': 0.0, 'exit_type': None}

    check = df[(df['elapsed'] >= check_time - 2) & (df['elapsed'] <= check_time + 2)]
    if len(check) == 0:
        return result

    row = check.iloc[0]
    up_mid = row.get('up_midpoint', np.nan)
    if pd.isna(up_mid):
        return result

    # FADE: opposite direction
    if up_mid > threshold:
        side = 'down'  # momentum says UP → we buy DOWN
    elif up_mid < (1 - threshold):
        side = 'up'    # momentum says DOWN → we buy UP
    else:
        return result

    ask_col = f'{side}_best_ask'
    ask_val = row.get(ask_col, np.nan)
    if pd.isna(ask_val) or ask_val <= 0 or ask_val >= 0.95:
        return result

    entry_price = float(ask_val)
    settlement = df.attrs.get('settlement', 'down')
    exit_price = 1.0 if settlement == side else 0.0
    pnl = (exit_price - entry_price) * shares
    result.update({'traded': True, 'pnl': pnl,
                   'exit_type': 'settle_win' if exit_price > 0.5 else 'settle_lose'})
    return result

def counter_strategy_C_deep_dip(df, dip_threshold=0.10, entry_start=240,
                                 entry_end=285, shares=10):
    """Deeper dip buy: only buy at extremely low prices (< 0.10).
    These are more likely to be genuine panic rather than whale manipulation."""
    result = {'traded': False, 'pnl': 0.0, 'exit_type': None}
    settlement = df.attrs.get('settlement', 'down')

    entry_df = df[(df['elapsed'] >= entry_start) & (df['elapsed'] <= entry_end)]
    for side in ['up', 'down']:
        ask_col = f'{side}_best_ask'
        dip_rows = entry_df[(entry_df[ask_col].fillna(999) > 0) &
                            (entry_df[ask_col].fillna(999) <= dip_threshold)]
        if len(dip_rows) == 0:
            continue

        entry_price = float(dip_rows.iloc[0][ask_col])
        exit_price = 1.0 if settlement == side else 0.0
        pnl = (exit_price - entry_price) * shares
        result.update({'traded': True, 'pnl': pnl,
                       'exit_type': 'settle_win' if exit_price > 0.5 else 'settle_lose'})
        return result
    return result

# Test counter-strategies on rounds where whale signal is active
print(f'\nTesting counter-strategies on whale-signal rounds...\n')

# For each strategy, split into whale/normal and test counter
for prefix, name, sig, counter_fn, counter_name in [
    ('A', 'Grid', sigA, None, 'Stop trading (capital preservation)'),
    ('B', 'Momentum', sigB, counter_strategy_B_fade, 'Fade momentum (contrarian)'),
    ('C', 'Dip Buy', sigC, counter_strategy_C_deep_dip, 'Deeper dip (threshold 0.10)'),
]:
    print(f'\n--- {name} ---')
    if sig is None:
        print(f'  No strong whale signal found, counter = {counter_name}')
        continue

    feat_name, feat_threshold = sig
    traded = df_all[df_all[f'{prefix}_traded']].copy()

    whale_mask = traded[feat_name].fillna(0) >= feat_threshold
    normal_mask = ~whale_mask

    n_whale = whale_mask.sum()
    n_normal = normal_mask.sum()

    original_whale_pnl = traded.loc[whale_mask, f'{prefix}_pnl'].sum()
    original_normal_pnl = traded.loc[normal_mask, f'{prefix}_pnl'].sum()
    original_total = traded[f'{prefix}_pnl'].sum()

    print(f'  Signal: {feat_name} >= {feat_threshold}')
    print(f'  Whale rounds: {n_whale} | Normal rounds: {n_normal}')
    print(f'  Original PnL: whale={original_whale_pnl:.2f}, normal={original_normal_pnl:.2f}, total={original_total:.2f}')

    if counter_fn is not None and n_whale > 0:
        # Re-run counter strategy on whale rounds only
        whale_round_ids = traded.loc[whale_mask, 'round_id'].values
        counter_pnls = []

        for rid in whale_round_ids:
            # Find original file
            fpath = os.path.join(DATA_DIR, f'{rid}.csv')
            if not os.path.exists(fpath):
                continue
            df = load_round(fpath)
            if df is None:
                continue
            cr = counter_fn(df)
            counter_pnls.append(cr['pnl'])

        counter_total = sum(counter_pnls)
        counter_trades = sum(1 for p in counter_pnls if p != 0)
        counter_wins = sum(1 for p in counter_pnls if p > 0)
        counter_wr = counter_wins / max(counter_trades, 1) * 100

        # System: normal rounds use original strategy, whale rounds use counter
        system_pnl = original_normal_pnl + counter_total

        print(f'\n  Counter ({counter_name}):')
        print(f'    Trades: {counter_trades}, Wins: {counter_wins} ({counter_wr:.1f}%)')
        print(f'    Counter PnL: {counter_total:.2f}')
        print(f'    vs Original whale PnL: {original_whale_pnl:.2f}')
        print(f'    Improvement: {counter_total - original_whale_pnl:+.2f}')
        print(f'\n  Combined system PnL:')
        print(f'    Original total:  {original_total:.2f}')
        print(f'    With counter:    {system_pnl:.2f}')
        print(f'    Improvement:     {system_pnl - original_total:+.2f}')
    else:
        # Capital preservation
        system_pnl = original_normal_pnl
        print(f'\n  Counter ({counter_name}):')
        print(f'    System PnL (stop trading in whale): {system_pnl:.2f}')
        print(f'    vs Original total: {original_total:.2f}')
        print(f'    Improvement: {system_pnl - original_total:+.2f}')

# ═══════════════════════════════════════════════════════
# FINAL SUMMARY TABLE
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  FINAL SUMMARY')
print(f'{"="*60}')
print(f'\n{"Strategy":<25} {"Trades":>7} {"WinRate":>8} {"PnL":>9} {"BestSignal":<25} {"CounterPnL":>10}')
print('-' * 90)

for prefix, name, sig in [('A','Grid',sigA), ('B','Momentum',sigB), ('C','Dip Buy',sigC)]:
    traded = df_all[df_all[f'{prefix}_traded']]
    n = len(traded)
    wr = (traded[f'{prefix}_pnl'] > 0).mean() * 100 if n else 0
    pnl = traded[f'{prefix}_pnl'].sum() if n else 0
    sig_str = f'{sig[0]}≥{sig[1]}' if sig else 'N/A'
    print(f'{name:<25} {n:>7} {wr:>7.1f}% {pnl:>+8.2f}  {sig_str:<25}')

print(f'\nResults saved to: {OUT_DIR}/')
