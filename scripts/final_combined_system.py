"""
═══════════════════════════════════════════════════════════════
 三合一最终系统：DipBuy探针 + 时间过滤 + 网格探针
═══════════════════════════════════════════════════════════════

决策引擎:
  Layer 1: 时间过滤 — 跳过低WR小时(整体停盘)
  Layer 2: DipBuy反向探针 — 检测逆转频率(whale regime)
  Layer 3: 网格探针 — 额外确认异常(grid开始亏=市场异常)
  
Action:
  bad_hour → SKIP (不交易)
  good_hour + whale_signal → FADE (反打动量)
  good_hour + normal → MOMENTUM (正常跟动量)

全面测试:
  1. 单信号 vs 双信号 vs 三信号
  2. 不同组合方式(OR / AND)
  3. 最优参数网格搜索
  4. 最终系统: 累计PnL, 回撤, Sharpe, 稳健性
"""

import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings('ignore')

INPUT = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies_fixed\three_strategies_fixed.csv'
OUT_DIR = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\final_system'
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(INPUT)
N = len(df)

df['hour'] = df['round_id'].str.extract(r'_(\d{2})-\d{2}-\d{2}').astype(int)
df['date'] = df['round_id'].str.extract(r'^(\d{4}-\d{2}-\d{2})')

b_traded = df['B_traded'].values.astype(bool)
settlement = df['f_settlement'].values
mom_side = df['B_side'].values
mom_entry = df['B_entry'].values
mom_pnl = df['B_pnl'].values

# Grid and DipBuy outcomes
grid_won = np.where(df['A_traded']==1, (df['A_pnl']>0).astype(float), np.nan)
dip_won  = np.where(df['C_traded']==1, (df['C_pnl']>0).astype(float), np.nan)

# Momentum outcome
mom_won = np.zeros(N)
for i in range(N):
    if b_traded[i] and settlement[i] == mom_side[i]:
        mom_won[i] = 1

# Fade PnL
fade_pnl = np.zeros(N)
for i in range(N):
    if not b_traded[i]: continue
    fe = 1.0 - mom_entry[i]
    if settlement[i] != mom_side[i]:
        fade_pnl[i] = (1.0 - fe) * 10
    else:
        fade_pnl[i] = -fe * 10

baseline = mom_pnl[b_traded].sum()
print(f'Baseline (pure momentum): {baseline:+.1f}, {b_traded.sum()} trades')

# ─── Rolling feature computation ───
def rolling_rate(won_arr, window):
    n = len(won_arr)
    result = np.full(n, np.nan)
    for i in range(n):
        count = 0; hits = 0
        for j in range(i-1, -1, -1):
            if np.isnan(won_arr[j]): continue
            count += 1; hits += won_arr[j]
            if count >= window: break
        if count >= window:
            result[i] = hits / count
    return result

print('Computing rolling features...')
# DipBuy WR (various windows)
dip_wr_5  = rolling_rate(dip_won, 5)
dip_wr_7  = rolling_rate(dip_won, 7)
dip_wr_10 = rolling_rate(dip_won, 10)

# Grid LOSS rate (various windows)
grid_lr_5  = rolling_rate(1 - grid_won, 5)  # invert: loss rate
grid_lr_10 = rolling_rate(1 - grid_won, 10)
# NaN handling: rolling_rate returns NaN for NaN inputs, need fix
# Actually grid_won has NaN for no-trade rounds, 1-NaN = NaN, so rolling_rate handles it
# But 1-grid_won when grid_won is NaN → NaN, which is what we want

# Fix: compute grid loss rate directly
grid_loss = np.where(df['A_traded']==1, (df['A_pnl']<=0).astype(float), np.nan)
grid_lr_5 = rolling_rate(grid_loss, 5)
grid_lr_10 = rolling_rate(grid_loss, 10)
grid_lr_8 = rolling_rate(grid_loss, 8)

print('Done.')

# ─── Hourly analysis for time filter ───
hourly = df[b_traded].groupby('hour')['B_pnl'].agg(['count', 'sum', lambda x: (x>0).mean()])
hourly.columns = ['n', 'pnl', 'wr']
hourly = hourly.reset_index()

# Different hour filter levels
bad_76 = set(hourly[hourly['wr'] < 0.76]['hour'])
bad_78 = set(hourly[hourly['wr'] < 0.78]['hour'])
bad_75 = set(hourly[hourly['wr'] < 0.75]['hour'])
bad_77 = set(hourly[hourly['wr'] < 0.77]['hour'])

print(f'\nHour filters:')
print(f'  WR<75%: skip hours {sorted(bad_75)} ({sum(b_traded[i] and df["hour"].iloc[i] in bad_75 for i in range(N))} trades)')
print(f'  WR<76%: skip hours {sorted(bad_76)} ({sum(b_traded[i] and df["hour"].iloc[i] in bad_76 for i in range(N))} trades)')
print(f'  WR<77%: skip hours {sorted(bad_77)} ({sum(b_traded[i] and df["hour"].iloc[i] in bad_77 for i in range(N))} trades)')
print(f'  WR<78%: skip hours {sorted(bad_78)} ({sum(b_traded[i] and df["hour"].iloc[i] in bad_78 for i in range(N))} trades)')

# ─── Helper: evaluate a system config ───
def max_drawdown(curve):
    if len(curve) == 0: return 0
    peak = curve[0]; mdd = 0
    for v in curve:
        if v > peak: peak = v
        dd = peak - v
        if dd > mdd: mdd = dd
    return mdd

def evaluate_system(time_filter, dip_wr_arr, dip_thresh, grid_lr_arr, grid_thresh,
                    combine_mode='dip_only'):
    """
    combine_mode:
      'dip_only': whale if dip_wr >= dip_thresh
      'grid_only': whale if grid_lr >= grid_thresh
      'dip_or_grid': whale if dip OR grid
      'dip_and_grid': whale if dip AND grid
    """
    sys_pnl = 0
    n_skip = 0
    n_fade = 0
    n_mom = 0
    cum = []

    for i in range(N):
        if not b_traded[i]:
            continue
        h = df['hour'].iloc[i]
        if h in time_filter:
            n_skip += 1
            continue

        dip_whale = not np.isnan(dip_wr_arr[i]) and dip_wr_arr[i] >= dip_thresh
        grid_whale = not np.isnan(grid_lr_arr[i]) and grid_lr_arr[i] >= grid_thresh

        if combine_mode == 'dip_only':
            is_whale = dip_whale
        elif combine_mode == 'grid_only':
            is_whale = grid_whale
        elif combine_mode == 'dip_or_grid':
            is_whale = dip_whale or grid_whale
        elif combine_mode == 'dip_and_grid':
            is_whale = dip_whale and grid_whale
        else:
            is_whale = False

        if is_whale:
            sys_pnl += fade_pnl[i]
            n_fade += 1
        else:
            sys_pnl += mom_pnl[i]
            n_mom += 1
        cum.append(sys_pnl)

    mdd = max_drawdown(np.array(cum)) if cum else 0
    n_total = n_fade + n_mom
    sharpe = (np.mean(np.diff([0] + cum)) / np.std(np.diff([0] + cum))) if len(cum) > 1 else 0
    return {
        'sys_pnl': round(sys_pnl, 1),
        'n_skip': n_skip,
        'n_fade': n_fade,
        'n_mom': n_mom,
        'n_total': n_total,
        'mdd': round(mdd, 1),
        'sharpe': round(sharpe, 4),
        'improve': round(sys_pnl - baseline, 1),
    }

# ═══════════════════════════════════════════════════════════
# Phase 1: Systematic grid search
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('Phase 1: Full Grid Search — all combinations')
print('='*70)

configs = []

time_filters = [
    ('none', set()),
    ('WR<75%', bad_75),
    ('WR<76%', bad_76),
    ('WR<77%', bad_77),
    ('WR<78%', bad_78),
]

dip_configs = [
    ('dip_off', dip_wr_7, 999),  # never triggers
    ('dip7_25', dip_wr_7, 0.25),
    ('dip7_30', dip_wr_7, 0.30),
    ('dip7_35', dip_wr_7, 0.35),
    ('dip10_22', dip_wr_10, 0.22),
    ('dip10_25', dip_wr_10, 0.25),
    ('dip10_30', dip_wr_10, 0.30),
    ('dip5_22', dip_wr_5, 0.22),
    ('dip5_30', dip_wr_5, 0.30),
]

grid_configs = [
    ('grid_off', grid_lr_10, 999),  # never triggers
    ('g10_10', grid_lr_10, 0.10),
    ('g10_15', grid_lr_10, 0.15),
    ('g10_20', grid_lr_10, 0.20),
    ('g8_15', grid_lr_8, 0.15),
    ('g8_25', grid_lr_8, 0.25),
    ('g5_20', grid_lr_5, 0.20),
]

combine_modes = ['dip_only', 'grid_only', 'dip_or_grid']

total_combos = len(time_filters) * len(dip_configs) * len(grid_configs) * len(combine_modes)
print(f'Testing {total_combos} combinations...')

results = []
for tf_name, tf_set in time_filters:
    for d_name, d_arr, d_thresh in dip_configs:
        for g_name, g_arr, g_thresh in grid_configs:
            for cm in combine_modes:
                # Skip redundant combos
                if cm == 'dip_only' and g_name != 'grid_off':
                    continue  # dip_only ignores grid, only need one grid config
                if cm == 'grid_only' and d_name != 'dip_off':
                    continue
                if cm in ('dip_or_grid',) and (d_name == 'dip_off' or g_name == 'grid_off'):
                    continue

                r = evaluate_system(tf_set, d_arr, d_thresh, g_arr, g_thresh, cm)
                r['time_filter'] = tf_name
                r['dip_config'] = d_name
                r['grid_config'] = g_name
                r['combine'] = cm
                results.append(r)

res_df = pd.DataFrame(results)
print(f'Evaluated {len(res_df)} unique configs')

# Sort by system PnL
res_df = res_df.sort_values('sys_pnl', ascending=False)

print(f'\n{"="*70}')
print('TOP 20 CONFIGURATIONS')
print('='*70)
print(f'{"#":>3} {"Time":>7} {"Dip":>10} {"Grid":>8} {"Mode":>12} '
      f'{"PnL":>8} {"Impr":>8} {"MDD":>7} {"Sharpe":>7} '
      f'{"Skip":>5} {"Fade":>5} {"Mom":>5}')
print('-'*105)

for rank, (_, r) in enumerate(res_df.head(20).iterrows()):
    print(f'{rank+1:>3} {r["time_filter"]:>7} {r["dip_config"]:>10} {r["grid_config"]:>8} {r["combine"]:>12} '
          f'{r["sys_pnl"]:>+7.1f} {r["improve"]:>+7.1f} {r["mdd"]:>6.1f} {r["sharpe"]:>7.4f} '
          f'{r["n_skip"]:>5} {r["n_fade"]:>5} {r["n_mom"]:>5}')

# ═══════════════════════════════════════════════════════════
# Phase 2: Deep dive on top 5 configs
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('Phase 2: Top 5 Configs — Detailed Analysis')
print('='*70)

top5 = res_df.head(5)

for rank, (_, cfg) in enumerate(top5.iterrows()):
    # Reconstruct this config
    tf_set = dict(time_filters)[cfg['time_filter']]
    d_arr = dict([(n, (a, t)) for n, a, t in dip_configs])[cfg['dip_config']]
    g_arr = dict([(n, (a, t)) for n, a, t in grid_configs])[cfg['grid_config']]
    d_arr_vals, d_thresh = d_arr
    g_arr_vals, g_thresh = g_arr
    cm = cfg['combine']

    # Build per-trade log
    trades = []
    running = 0
    for i in range(N):
        if not b_traded[i]: continue
        h = df['hour'].iloc[i]
        if h in tf_set: continue
        dip_whale = not np.isnan(d_arr_vals[i]) and d_arr_vals[i] >= d_thresh
        grid_whale = not np.isnan(g_arr_vals[i]) and g_arr_vals[i] >= g_thresh
        if cm == 'dip_only': is_whale = dip_whale
        elif cm == 'grid_only': is_whale = grid_whale
        elif cm == 'dip_or_grid': is_whale = dip_whale or grid_whale
        else: is_whale = False

        pnl = fade_pnl[i] if is_whale else mom_pnl[i]
        running += pnl
        trades.append({
            'date': df['date'].iloc[i],
            'hour': h,
            'regime': 'whale' if is_whale else 'normal',
            'pnl': pnl,
            'cum': running,
        })

    tdf = pd.DataFrame(trades)

    # Period robustness
    dates = sorted(tdf['date'].unique())
    nd = len(dates)
    ps = nd // 3
    periods = [('P1', dates[:ps]), ('P2', dates[ps:2*ps]), ('P3', dates[2*ps:])]

    print(f'\n--- Config #{rank+1}: {cfg["time_filter"]} | {cfg["dip_config"]} | {cfg["grid_config"]} | {cfg["combine"]} ---')
    print(f'    PnL={cfg["sys_pnl"]:+.1f}, MDD={cfg["mdd"]:.1f}, Sharpe={cfg["sharpe"]:.4f}')
    print(f'    Trades: {len(tdf)} (skip={cfg["n_skip"]}, fade={cfg["n_fade"]}, mom={cfg["n_mom"]})')

    all_positive = True
    for pname, pdates in periods:
        mask = tdf['date'].isin(pdates)
        subset = tdf[mask]
        ppnl = subset['pnl'].sum()
        pfade = (subset['regime'] == 'whale').sum()
        pmom = (subset['regime'] == 'normal').sum()
        pmdd = max_drawdown(subset['cum'].values) if len(subset) > 1 else 0
        sign = '✅' if ppnl > 0 else '❌'
        if ppnl <= 0: all_positive = False
        print(f'    {pname} ({pdates[0]}~{pdates[-1]}): PnL={ppnl:+.1f}, MDD={pmdd:.1f}, '
              f'fade={pfade}, mom={pmom} {sign}')

    if all_positive:
        print(f'    → ALL PERIODS POSITIVE ✅✅✅')

# ═══════════════════════════════════════════════════════════
# Phase 3: THE FINAL SYSTEM — best robust config
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('Phase 3: FINAL SYSTEM — Complete Analysis')
print('='*70)

# Pick the best config that's positive in all 3 periods
# Re-check top configs for period robustness
print('\nSearching for best config positive in all 3 periods...')

best_robust = None
for _, cfg in res_df.iterrows():
    tf_set = dict(time_filters)[cfg['time_filter']]
    d_arr_vals, d_thresh = dict([(n, (a, t)) for n, a, t in dip_configs])[cfg['dip_config']]
    g_arr_vals, g_thresh = dict([(n, (a, t)) for n, a, t in grid_configs])[cfg['grid_config']]
    cm = cfg['combine']

    trades = []
    running = 0
    for i in range(N):
        if not b_traded[i]: continue
        h = df['hour'].iloc[i]
        if h in tf_set: continue
        dip_whale = not np.isnan(d_arr_vals[i]) and d_arr_vals[i] >= d_thresh
        grid_whale = not np.isnan(g_arr_vals[i]) and g_arr_vals[i] >= g_thresh
        if cm == 'dip_only': is_whale = dip_whale
        elif cm == 'grid_only': is_whale = grid_whale
        elif cm == 'dip_or_grid': is_whale = dip_whale or grid_whale
        else: is_whale = False
        pnl = fade_pnl[i] if is_whale else mom_pnl[i]
        running += pnl
        trades.append({'date': df['date'].iloc[i], 'pnl': pnl, 'cum': running,
                       'regime': 'whale' if is_whale else 'normal'})
    tdf = pd.DataFrame(trades)
    dates = sorted(tdf['date'].unique())
    nd = len(dates); ps = nd // 3
    all_pos = True
    for pdates in [dates[:ps], dates[ps:2*ps], dates[2*ps:]]:
        if tdf[tdf['date'].isin(pdates)]['pnl'].sum() <= 0:
            all_pos = False; break
    if all_pos:
        best_robust = cfg
        best_robust_trades = tdf
        break

if best_robust is not None:
    print(f'\n★ BEST ROBUST CONFIG:')
    print(f'  Time: {best_robust["time_filter"]}')
    print(f'  DipBuy: {best_robust["dip_config"]}')
    print(f'  Grid: {best_robust["grid_config"]}')
    print(f'  Combine: {best_robust["combine"]}')
    print(f'  PnL: {best_robust["sys_pnl"]:+.1f} (improve: {best_robust["improve"]:+.1f})')
    print(f'  MDD: {best_robust["mdd"]:.1f}')
    print(f'  Sharpe: {best_robust["sharpe"]:.4f}')
    print(f'  Trades: skip={best_robust["n_skip"]}, fade={best_robust["n_fade"]}, mom={best_robust["n_mom"]}')

    tdf = best_robust_trades
    dates = sorted(tdf['date'].unique())
    nd = len(dates); ps = nd // 3

    print(f'\n  Period robustness:')
    for pname, pdates in [('P1', dates[:ps]), ('P2', dates[ps:2*ps]), ('P3', dates[2*ps:])]:
        sub = tdf[tdf['date'].isin(pdates)]
        print(f'    {pname}: PnL={sub["pnl"].sum():+.1f}, '
              f'fade={sum(sub["regime"]=="whale")}, mom={sum(sub["regime"]=="normal")}')

    # Cumulative curve summary
    print(f'\n  Cumulative PnL (every 200 trades):')
    cvals = tdf['cum'].values
    for j in range(0, len(cvals), 200):
        print(f'    Trade {j:>5}: {cvals[j]:>+8.1f}')
    print(f'    Trade {len(cvals)-1:>5}: {cvals[-1]:>+8.1f}')

    # Daily PnL distribution
    daily_pnl = tdf.groupby('date')['pnl'].sum()
    win_days = (daily_pnl > 0).sum()
    lose_days = (daily_pnl <= 0).sum()
    print(f'\n  Daily stats:')
    print(f'    Win days: {win_days}/{len(daily_pnl)} ({win_days/len(daily_pnl)*100:.0f}%)')
    print(f'    Avg daily PnL: {daily_pnl.mean():+.1f}')
    print(f'    Best day: {daily_pnl.max():+.1f} ({daily_pnl.idxmax()})')
    print(f'    Worst day: {daily_pnl.min():+.1f} ({daily_pnl.idxmin()})')

    # Fee-adjusted
    avg_entry = mom_entry[b_traded].mean()
    fee_per_trade = avg_entry * 10 * 0.02  # 2% taker fee, 10 shares
    total_active = best_robust['n_fade'] + best_robust['n_mom']
    total_fees = total_active * fee_per_trade
    # DipBuy probe cost: 1 share per round at ~$0.15
    dip_probe_cost = df['C_traded'].sum() * 0.15  # cost of running probe
    print(f'\n  Fee-adjusted PnL:')
    print(f'    Trading fees (2% × {total_active} trades): -{total_fees:.1f}')
    print(f'    DipBuy probe cost ({df["C_traded"].sum()} × $0.15): -{dip_probe_cost:.1f}')
    print(f'    Net PnL: {best_robust["sys_pnl"] - total_fees:+.1f} (excl probe cost)')
    print(f'    Net PnL: {best_robust["sys_pnl"] - total_fees - dip_probe_cost:+.1f} (incl probe cost)')

    # Save
    tdf.to_csv(os.path.join(OUT_DIR, 'final_trade_log.csv'), index=False)

# ═══════════════════════════════════════════════════════════
# Also evaluate: baseline with same time filter only
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('COMPARISON TABLE')
print('='*70)

print(f'\n{"Strategy":>35} {"PnL":>8} {"MDD":>7} {"Sharpe":>7} {"Trades":>7}')
print('-'*70)

# Pure momentum
print(f'{"Pure Momentum":>35} {baseline:>+7.1f} {"166.1":>7} {"0.0274":>7} {b_traded.sum():>7}')

# Time filter only (best)
for tf_name, tf_set in time_filters:
    if tf_name == 'none': continue
    r = evaluate_system(tf_set, dip_wr_7, 999, grid_lr_10, 999, 'dip_only')
    print(f'{"Time Filter ("+tf_name+")":>35} {r["sys_pnl"]:>+7.1f} {r["mdd"]:>6.1f} {r["sharpe"]:>7.4f} {r["n_total"]:>7}')

# DipBuy probe only (best)
r_dip = evaluate_system(set(), dip_wr_7, 0.30, grid_lr_10, 999, 'dip_only')
print(f'{"DipBuy Probe W7 T0.30":>35} {r_dip["sys_pnl"]:>+7.1f} {r_dip["mdd"]:>6.1f} {r_dip["sharpe"]:>7.4f} {r_dip["n_total"]:>7}')

# Grid probe only (best)
r_grid = evaluate_system(set(), dip_wr_7, 999, grid_lr_10, 0.10, 'grid_only')
print(f'{"Grid Probe W10 T0.10":>35} {r_grid["sys_pnl"]:>+7.1f} {r_grid["mdd"]:>6.1f} {r_grid["sharpe"]:>7.4f} {r_grid["n_total"]:>7}')

# Best robust
if best_robust is not None:
    print(f'{"★ FINAL SYSTEM":>35} {best_robust["sys_pnl"]:>+7.1f} {best_robust["mdd"]:>6.1f} {best_robust["sharpe"]:>7.4f} {best_robust["n_total"]:>7}')

# Save all results
res_df.to_csv(os.path.join(OUT_DIR, 'all_configs.csv'), index=False)
print(f'\nAll {len(res_df)} configs saved to {OUT_DIR}/all_configs.csv')
