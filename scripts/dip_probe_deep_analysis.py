"""
═══════════════════════════════════════════════════════════════
 DipBuy反向探针 —— 深度分析
═══════════════════════════════════════════════════════════════

最佳发现: 当DipBuy(16%WR)滚动胜率突然升高(≥25%)时,
说明市场频繁大逆转 → 庄家活跃 → fade动量策略

本脚本深入分析:
  1. Fade胜率/赔率分析
  2. 累计PnL曲线（平滑度、最大回撤）
  3. 时间分布（探针何时触发）
  4. 与时间过滤组合
  5. 不同时间段稳健性检验
  6. DipBuy WR连续性分析（是集中爆发还是均匀分布）
  7. 最优参数精调
  8. 实盘可行性评估
"""

import pandas as pd, numpy as np, os, warnings
warnings.filterwarnings('ignore')

INPUT = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies_fixed\three_strategies_fixed.csv'
OUT_DIR = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\dip_probe_deep'
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(INPUT)
N = len(df)

# Extract time info
df['hour'] = df['round_id'].str.extract(r'_(\d{2})-\d{2}-\d{2}').astype(int)
df['date'] = df['round_id'].str.extract(r'^(\d{4}-\d{2}-\d{2})')

b_traded = df['B_traded'].values.astype(bool)
settlement = df['f_settlement'].values
mom_side = df['B_side'].values
mom_entry = df['B_entry'].values
mom_pnl = df['B_pnl'].values

# Pre-compute: dip_buy win/loss per round
dip_won = np.where(df['C_traded']==1, (df['C_pnl']>0).astype(float), np.nan)

# Pre-compute: fade PnL
fade_pnl = np.zeros(N)
mom_won = np.zeros(N, dtype=int)
for i in range(N):
    if not b_traded[i]:
        continue
    fade_entry = 1.0 - mom_entry[i]
    if settlement[i] != mom_side[i]:
        fade_pnl[i] = (1.0 - fade_entry) * 10
        mom_won[i] = 0
    else:
        fade_pnl[i] = -fade_entry * 10
        mom_won[i] = 1

# Rolling DipBuy WR
def compute_dip_wr_rolling(window):
    result = np.full(N, np.nan)
    for i in range(N):
        count = 0; wins = 0
        for j in range(i-1, -1, -1):
            if np.isnan(dip_won[j]): continue
            count += 1; wins += dip_won[j]
            if count >= window: break
        if count >= window:
            result[i] = wins / count
    return result

# ═══════════════════════════════════════════════════════════
# SECTION 1: 最佳配置详细统计
# ═══════════════════════════════════════════════════════════
print('='*70)
print('SECTION 1: 最佳配置 W=10, DipWR≥0.25 详细分析')
print('='*70)

W = 10
T = 0.25
dip_wr_10 = compute_dip_wr_rolling(W)

whale_mask = np.zeros(N, dtype=bool)
for i in range(N):
    if b_traded[i] and not np.isnan(dip_wr_10[i]) and dip_wr_10[i] >= T:
        whale_mask[i] = True

n_whale = whale_mask.sum()
n_normal = b_traded.sum() - n_whale - (~b_traded & ~whale_mask).sum()

# Whale rounds stats
whale_idx = np.where(whale_mask)[0]
normal_idx = np.where(b_traded & ~whale_mask)[0]

print(f'\nWhale rounds: {len(whale_idx)}')
print(f'Normal rounds: {len(normal_idx)}')
print(f'Total B trades: {b_traded.sum()}')

# Momentum in whale vs normal
whale_mom_wr = mom_won[whale_idx].mean() * 100
normal_mom_wr = mom_won[normal_idx].mean() * 100
print(f'\nMomentum WR:')
print(f'  Normal: {normal_mom_wr:.1f}%')
print(f'  Whale:  {whale_mom_wr:.1f}%  ← 动量在whale里明显变差?')
print(f'  Total:  {mom_won[b_traded].mean()*100:.1f}%')

# Fade stats in whale mode
fade_in_whale = fade_pnl[whale_idx]
fade_wins = (fade_in_whale > 0).sum()
fade_losses = (fade_in_whale < 0).sum()
fade_wr = fade_wins / len(whale_idx) * 100 if len(whale_idx) > 0 else 0
avg_fade_win = fade_in_whale[fade_in_whale > 0].mean() if fade_wins > 0 else 0
avg_fade_loss = fade_in_whale[fade_in_whale < 0].mean() if fade_losses > 0 else 0

print(f'\nFade in WHALE mode:')
print(f'  WR: {fade_wr:.1f}% ({fade_wins}W / {fade_losses}L)')
print(f'  Avg win:  {avg_fade_win:+.2f}')
print(f'  Avg loss: {avg_fade_loss:+.2f}')
print(f'  PnL: {fade_in_whale.sum():+.1f}')
print(f'  EV per trade: {fade_in_whale.mean():+.3f}')

# Momentum stats in normal mode
mom_in_normal = mom_pnl[normal_idx]
mom_wins_n = (mom_in_normal > 0).sum()
mom_losses_n = (mom_in_normal < 0).sum()
print(f'\nMomentum in NORMAL mode:')
print(f'  WR: {mom_wins_n/len(normal_idx)*100:.1f}%')
print(f'  PnL: {mom_in_normal.sum():+.1f}')
print(f'  EV per trade: {mom_in_normal.mean():+.3f}')

# ═══════════════════════════════════════════════════════════
# SECTION 2: 精调参数
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 2: 精调参数 (fine-grained sweep)')
print('='*70)

fine_windows = [5, 7, 8, 10, 12, 15]
fine_thresholds = [0.20, 0.22, 0.25, 0.28, 0.30, 0.33, 0.35, 0.40]

print(f'\n{"W":>3} {"T":>5} {"N_wh":>5} {"Wh%":>5} {"MomWR_wh":>9} {"FadeWR_wh":>10} '
      f'{"FadePnL":>9} {"StopPnL":>9} {"SysPnL":>8} {"vs_base":>8}')
print('-'*85)

baseline = mom_pnl[b_traded].sum()
best_config = None
best_fade = -9999

for W in fine_windows:
    dwr = compute_dip_wr_rolling(W)
    for T in fine_thresholds:
        wh_idx = []
        nm_idx = []
        for i in range(N):
            if not b_traded[i]: continue
            if not np.isnan(dwr[i]) and dwr[i] >= T:
                wh_idx.append(i)
            else:
                nm_idx.append(i)
        wh_idx = np.array(wh_idx)
        nm_idx = np.array(nm_idx)
        if len(wh_idx) == 0:
            continue

        wh_pct = len(wh_idx) / b_traded.sum() * 100
        mom_wr_wh = mom_won[wh_idx].mean() * 100
        fade_wr_wh = (1 - mom_won[wh_idx].mean()) * 100
        fade_total = fade_pnl[wh_idx].sum()
        stop_total = mom_pnl[nm_idx].sum()
        sys_pnl = stop_total + fade_total  # fade in whale, momentum in normal
        vs = sys_pnl - baseline

        if sys_pnl > best_fade:
            best_fade = sys_pnl
            best_config = (W, T)

        marker = ' ★' if vs > 100 else ''
        print(f'{W:>3} {T:>5.2f} {len(wh_idx):>5} {wh_pct:>4.1f}% {mom_wr_wh:>8.1f}% {fade_wr_wh:>9.1f}% '
              f'{fade_total:>+8.1f} {stop_total:>+8.1f} {sys_pnl:>+7.1f} {vs:>+7.1f}{marker}')

print(f'\nBest config: W={best_config[0]}, T={best_config[1]:.2f}, Fade PnL={best_fade:+.1f}')

# ═══════════════════════════════════════════════════════════
# SECTION 3: 累计PnL曲线 + 最大回撤
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 3: Cumulative PnL Curve & Max Drawdown (best config)')
print('='*70)

W_best, T_best = best_config
dwr_best = compute_dip_wr_rolling(W_best)

cum_base = []
cum_sys = []
regime_labels = []
running_base = 0
running_sys = 0

trade_log = []  # detailed log for each B trade

for i in range(N):
    if not b_traded[i]:
        continue
    running_base += mom_pnl[i]
    is_whale = not np.isnan(dwr_best[i]) and dwr_best[i] >= T_best
    if is_whale:
        running_sys += fade_pnl[i]
        regime_labels.append('whale')
    else:
        running_sys += mom_pnl[i]
        regime_labels.append('normal')
    cum_base.append(running_base)
    cum_sys.append(running_sys)
    trade_log.append({
        'round': df['round_id'].iloc[i],
        'date': df['date'].iloc[i],
        'hour': df['hour'].iloc[i],
        'regime': regime_labels[-1],
        'dip_wr': dwr_best[i] if not np.isnan(dwr_best[i]) else None,
        'mom_pnl': mom_pnl[i],
        'fade_pnl': fade_pnl[i],
        'sys_pnl': fade_pnl[i] if is_whale else mom_pnl[i],
        'cum_base': running_base,
        'cum_sys': running_sys,
    })

cum_base = np.array(cum_base)
cum_sys = np.array(cum_sys)

# Max drawdown
def max_drawdown(curve):
    peak = curve[0]
    mdd = 0
    for v in curve:
        if v > peak: peak = v
        dd = peak - v
        if dd > mdd: mdd = dd
    return mdd

mdd_base = max_drawdown(cum_base)
mdd_sys = max_drawdown(cum_sys)

print(f'Baseline:  Final={cum_base[-1]:+.1f}, MaxDD={mdd_base:.1f}')
print(f'System:    Final={cum_sys[-1]:+.1f}, MaxDD={mdd_sys:.1f}')
print(f'Improvement: PnL {cum_sys[-1]-cum_base[-1]:+.1f}, DD reduction {mdd_base-mdd_sys:.1f}')

# Sharpe-like (PnL per trade / std)
tlog = pd.DataFrame(trade_log)
sharpe_base = tlog['mom_pnl'].mean() / tlog['mom_pnl'].std() if tlog['mom_pnl'].std() > 0 else 0
sharpe_sys = tlog['sys_pnl'].mean() / tlog['sys_pnl'].std() if tlog['sys_pnl'].std() > 0 else 0
print(f'Per-trade Sharpe: Base={sharpe_base:.4f}, System={sharpe_sys:.4f}')

# Print every 200 trades
print(f'\n{"Trade#":>7} {"Baseline":>10} {"System":>10} {"Diff":>8} {"Regime":>8}')
for j in range(0, len(cum_base), 200):
    print(f'{j:>7} {cum_base[j]:>+9.1f} {cum_sys[j]:>+9.1f} {cum_sys[j]-cum_base[j]:>+7.1f} {regime_labels[j]:>8}')
j = len(cum_base) - 1
print(f'{j:>7} {cum_base[j]:>+9.1f} {cum_sys[j]:>+9.1f} {cum_sys[j]-cum_base[j]:>+7.1f} {regime_labels[j]:>8}')

# ═══════════════════════════════════════════════════════════
# SECTION 4: 时间分布 — whale regime何时出现
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 4: Whale Regime 时间分布')
print('='*70)

whale_trades = tlog[tlog['regime'] == 'whale']
normal_trades = tlog[tlog['regime'] == 'normal']

# By hour
print(f'\n按小时分布:')
print(f'{"Hour":>5} {"NWhale":>7} {"NNorm":>7} {"Wh%":>6} {"MomPnL_wh":>10} {"FadePnL_wh":>11}')
for h in range(24):
    wh = whale_trades[whale_trades['hour'] == h]
    nm = normal_trades[normal_trades['hour'] == h]
    total = len(wh) + len(nm)
    if total == 0: continue
    print(f'{h:>5} {len(wh):>7} {len(nm):>7} {len(wh)/total*100:>5.1f}% '
          f'{wh["mom_pnl"].sum():>+9.1f} {wh["fade_pnl"].sum():>+10.1f}')

# By date
print(f'\n按日期分布 (whale rounds per day):')
daily = whale_trades.groupby('date').size()
print(f'Days with whale: {len(daily)}/{tlog["date"].nunique()}')
print(f'Avg whale rounds/day: {daily.mean():.1f}')
print(f'Max whale rounds/day: {daily.max()}, date: {daily.idxmax()}')
print(f'Min whale rounds/day: {daily.min()}')

# ═══════════════════════════════════════════════════════════
# SECTION 5: 时间段稳健性检验
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 5: 稳健性检验 — 分时间段表现')
print('='*70)

# Split into 3 periods
dates = sorted(tlog['date'].unique())
n_dates = len(dates)
period_size = n_dates // 3
periods = [
    ('Period1', dates[:period_size]),
    ('Period2', dates[period_size:2*period_size]),
    ('Period3', dates[2*period_size:]),
]

print(f'\n{"Period":>10} {"Dates":>25} {"N_trades":>9} {"N_whale":>8} '
      f'{"Base_PnL":>10} {"Sys_PnL":>9} {"Improve":>9}')
print('-'*90)

for name, date_list in periods:
    mask = tlog['date'].isin(date_list)
    subset = tlog[mask]
    n_trades = len(subset)
    n_wh = (subset['regime'] == 'whale').sum()
    base_pnl = subset['mom_pnl'].sum()
    sys_pnl = subset['sys_pnl'].sum()
    print(f'{name:>10} {date_list[0]}~{date_list[-1]} {n_trades:>9} {n_wh:>8} '
          f'{base_pnl:>+9.1f} {sys_pnl:>+8.1f} {sys_pnl-base_pnl:>+8.1f}')

# ═══════════════════════════════════════════════════════════
# SECTION 6: DipBuy WR 连续性分析
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 6: DipBuy WR 连续性 — 是集中爆发还是均匀分布')
print('='*70)

# How long do whale streaks last?
whale_streak_lengths = []
current_streak = 0
for i in range(len(tlog)):
    if tlog.iloc[i]['regime'] == 'whale':
        current_streak += 1
    else:
        if current_streak > 0:
            whale_streak_lengths.append(current_streak)
        current_streak = 0
if current_streak > 0:
    whale_streak_lengths.append(current_streak)

wsl = np.array(whale_streak_lengths)
print(f'Number of whale streaks: {len(wsl)}')
print(f'Streak lengths: min={wsl.min()}, mean={wsl.mean():.1f}, max={wsl.max()}, median={np.median(wsl):.0f}')
print(f'Distribution: 1-5={sum(wsl<=5)}, 6-15={sum((wsl>5)&(wsl<=15))}, 16-30={sum((wsl>15)&(wsl<=30))}, 30+={sum(wsl>30)}')

# PnL by streak
print(f'\nFade PnL by whale streak position:')
print(f'  (Is fade profitable at start vs end of whale regime?)')
pos_pnl = {}
pos_in_streak = 0
for i in range(len(tlog)):
    if tlog.iloc[i]['regime'] == 'whale':
        pos_in_streak += 1
        bucket = min(pos_in_streak, 20)
        if bucket not in pos_pnl:
            pos_pnl[bucket] = []
        pos_pnl[bucket].append(tlog.iloc[i]['sys_pnl'])
    else:
        pos_in_streak = 0

print(f'{"Pos":>5} {"N":>5} {"AvgPnL":>8} {"WR":>6}')
for pos in sorted(pos_pnl.keys())[:15]:
    pnls = np.array(pos_pnl[pos])
    print(f'{pos:>5} {len(pnls):>5} {pnls.mean():>+7.2f} {(pnls>0).mean()*100:>5.1f}%')

# ═══════════════════════════════════════════════════════════
# SECTION 7: 与时间过滤组合
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 7: DipBuy探针 + 时间过滤 组合')
print('='*70)

# Hours with momentum WR < 76%
hourly_mom = tlog.groupby('hour').agg(
    n=('mom_pnl', 'count'),
    wr=('mom_pnl', lambda x: (x>0).mean()),
    pnl=('mom_pnl', 'sum')
).reset_index()

bad_hours_76 = hourly_mom[hourly_mom['wr'] < 0.76]['hour'].tolist()
bad_hours_78 = hourly_mom[hourly_mom['wr'] < 0.78]['hour'].tolist()

for label, bad_hours in [('WR<76%', bad_hours_76), ('WR<78%', bad_hours_78)]:
    # Strategy: skip bad hours entirely + fade when dip probe fires in good hours
    sys_pnl_combo = 0
    n_skip = 0
    n_fade = 0
    n_mom = 0
    for i in range(len(tlog)):
        row = tlog.iloc[i]
        if row['hour'] in bad_hours:
            n_skip += 1
            continue  # skip
        if row['regime'] == 'whale':
            sys_pnl_combo += row['fade_pnl']
            n_fade += 1
        else:
            sys_pnl_combo += row['mom_pnl']
            n_mom += 1
    print(f'\n{label}: bad_hours={bad_hours}')
    print(f'  Skip={n_skip}, Fade={n_fade}, Mom={n_mom}')
    print(f'  Combo PnL: {sys_pnl_combo:+.1f} (base={baseline:+.1f}, improve={sys_pnl_combo-baseline:+.1f})')

# ═══════════════════════════════════════════════════════════
# SECTION 8: Entry price analysis for fade trades
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 8: Fade交易的入场价分析')
print('='*70)

whale_entries = 1.0 - mom_entry[np.array(whale_trades.index)]
print(f'Fade entry price (opposite side ask):')
print(f'  Mean: {whale_entries.mean():.3f}')
print(f'  Median: {np.median(whale_entries):.3f}')
print(f'  Range: {whale_entries.min():.3f} - {whale_entries.max():.3f}')

# Break down by entry price bucket
for lo, hi in [(0, 0.15), (0.15, 0.25), (0.25, 0.35), (0.35, 0.50)]:
    mask_price = (whale_entries >= lo) & (whale_entries < hi)
    if mask_price.sum() == 0: continue
    pnls = fade_pnl[np.array(whale_trades.index)][mask_price]
    print(f'  [{lo:.2f}-{hi:.2f}): N={mask_price.sum()}, WR={(pnls>0).mean()*100:.1f}%, '
          f'PnL={pnls.sum():+.1f}, Avg={pnls.mean():+.2f}')

# ═══════════════════════════════════════════════════════════
# SECTION 9: 实盘可行性评估
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('SECTION 9: 实盘可行性评估')
print('='*70)

total_trades = b_traded.sum()
whale_count = len(whale_trades)
fade_count = whale_count
mom_count = total_trades - whale_count

# Fee impact (2% taker fee on Polymarket)
fee_rate = 0.02
shares_per_trade = 10
avg_entry_fade = whale_entries.mean()
avg_entry_mom = mom_entry[b_traded].mean()

# Fees: entry + exit (if win, exit at 1.0; if lose, held to settlement)
fee_per_fade = avg_entry_fade * shares_per_trade * fee_rate  # entry fee only
fee_per_mom = avg_entry_mom * shares_per_trade * fee_rate

total_fees_nofade = total_trades * fee_per_mom
total_fees_withfade = mom_count * fee_per_mom + fade_count * fee_per_fade

print(f'Fee analysis (2% taker, {shares_per_trade} shares/trade):')
print(f'  Baseline fees: {total_fees_nofade:.1f} ({total_trades} trades × ~{fee_per_mom:.2f})')
print(f'  System fees:   {total_fees_withfade:.1f} ({mom_count} mom × {fee_per_mom:.2f} + {fade_count} fade × {fee_per_fade:.2f})')
print(f'  Baseline PnL after fees: {baseline - total_fees_nofade:+.1f}')
print(f'  System PnL after fees:   {cum_sys[-1] - total_fees_withfade:+.1f}')

# Trades per day
n_days = tlog['date'].nunique()
print(f'\nTrade frequency:')
print(f'  Days: {n_days}')
print(f'  Trades/day: {total_trades/n_days:.1f}')
print(f'  Whale rounds/day: {whale_count/n_days:.1f}')

# Capital requirement
print(f'\nCapital: {shares_per_trade} shares × ~$0.78 entry = ~${shares_per_trade * 0.78:.1f} per trade')
print(f'  (plus DipBuy probe cost: ~1 share × $0.15 × {df["C_traded"].sum()} trades = ~${df["C_traded"].sum() * 0.15:.0f})')

# Save detailed trade log
tlog.to_csv(os.path.join(OUT_DIR, 'trade_log.csv'), index=False)
print(f'\nTrade log saved to {OUT_DIR}/trade_log.csv')

# ═══════════════════════════════════════════════════════════
# SECTION 10: 关键结论
# ═══════════════════════════════════════════════════════════
print(f'\n{"="*70}')
print('CONCLUSIONS')
print('='*70)
print(f'''
1. DipBuy反向探针 W={W_best}, T={T_best}:
   - 检测到 {whale_count} 轮 whale ({whale_count/total_trades*100:.1f}% of trades)
   - Whale时动量WR: {whale_mom_wr:.1f}% (正常: {normal_mom_wr:.1f}%)
   - Fade WR: {fade_wr:.1f}%
   - System PnL: {cum_sys[-1]:+.1f} (baseline {baseline:+.1f})

2. 机制解释:
   DipBuy买便宜端(~$0.15), 正常16%WR
   当DipBuy连赢 → 市场频繁大逆转 → 便宜端翻盘
   此时动量(买贵端)会被反杀 → fade动量更合理

3. 稳健性: [见Section 5时间段分析]

4. 实盘考虑:
   - DipBuy探针成本: 每盘1 share × $0.15 (探测费)
   - 动量主策略: 10 shares × ~$0.78
   - 需考虑2%手续费影响
''')
