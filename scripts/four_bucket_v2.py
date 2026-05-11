"""
═══════════════════════════════════════════════════════════════
 四桶重测 V2 - 新配置 + 阈值优化 + 图表
═══════════════════════════════════════════════════════════════
Grid: buy≤$0.18, sell≥$0.26, 买入窗口0-94s, 卖出窗口0-190s
动量/Fade: 测试9个阈值 δ=0.05,0.10,...,0.45
Whale信号: DipBuy探针 W=7, T≥0.30
统一50 shares
"""

import pandas as pd, numpy as np, glob, os, time, warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
matplotlib.rcParams['font.family'] = ['SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings('ignore')

DATA_DIR  = r'C:\Users\ZHAOKAI\data'
EXIST_CSV = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies_fixed\three_strategies_fixed.csv'
OUT_DIR   = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\four_bucket_v2'
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50

# ═══════════════════════════════════════════════════════════
# PART 1: 加载现有结果（settlement, features, DipBuy）
# ═══════════════════════════════════════════════════════════
existing = pd.read_csv(EXIST_CSV)
N = len(existing)
print(f'Existing: {N} rounds')

settlement    = existing['f_settlement'].values
up_mid_250    = existing['f_up_mid_250'].values.astype(float)
round_ids     = existing['round_id'].values
hours         = existing['round_id'].str.extract(r'_(\d{2})-\d{2}-\d{2}')[0].astype(int).values
dates         = existing['round_id'].str.extract(r'^(\d{4}-\d{2}-\d{2})')[0].values

# 原始B策略数据（用于获取ask价格）
b_traded_orig = existing['B_traded'].values.astype(bool)
b_side_orig   = existing['B_side'].values
b_entry_orig  = existing['B_entry'].values.astype(float)

# DipBuy探针数据
c_traded = existing['C_traded'].values.astype(bool)
c_pnl    = existing['C_pnl'].values.astype(float)
dip_won  = np.where(c_traded, (c_pnl > 0).astype(float), np.nan)

# ═══════════════════════════════════════════════════════════
# PART 2: 重新跑网格（新配置: buy≤0.18, sell≥0.26, 0-94/0-190）
# ═══════════════════════════════════════════════════════════
print('\n重新计算网格 (buy≤0.18, sell≥0.26, 0-94/0-190, 50shares)...')

files = sorted(glob.glob(os.path.join(DATA_DIR, '*.csv')))
settlement_map = dict(zip(round_ids, settlement))

def load_raw(fpath):
    try:
        df = pd.read_csv(fpath)
        if len(df) < 20: return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        df['elapsed'] = (ts - ts.iloc[0]).dt.total_seconds()
        if df['elapsed'].max() < 200: return None
        for c in ['up_best_bid','up_best_ask','down_best_bid','down_best_ask']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce').ffill()
        return os.path.basename(fpath).replace('.csv',''), df
    except:
        return None

def grid_v2(df, settle_side):
    result = {'traded': False, 'pnl': 0.0, 'entry': np.nan, 'exit': np.nan, 'type': None}
    entry_df = df[(df['elapsed'] >= 0) & (df['elapsed'] <= 94)]

    for side in ['up', 'down']:
        ask_c = f'{side}_best_ask'
        bid_c = f'{side}_best_bid'
        cheap = entry_df[(entry_df[ask_c].fillna(999) > 0) & (entry_df[ask_c].fillna(999) <= 0.18)]
        if len(cheap) == 0: continue

        ep = float(cheap.iloc[0][ask_c])
        et = cheap.iloc[0]['elapsed']

        sell_df = df[(df['elapsed'] > et) & (df['elapsed'] <= 190)]
        tp = sell_df[sell_df[bid_c].fillna(0) >= 0.26]
        if len(tp) > 0:
            pnl = (0.26 - ep) * SHARES
            return {'traded': True, 'pnl': pnl, 'entry': ep, 'exit': 0.26, 'type': 'profit'}

        if len(sell_df) > 0:
            lb = sell_df[bid_c].dropna()
            if len(lb) > 0:
                xp = float(lb.iloc[-1])
                return {'traded': True, 'pnl': (xp - ep)*SHARES, 'entry': ep, 'exit': xp, 'type': 'timeout'}

        xp = 1.0 if settle_side == side else 0.0
        return {'traded': True, 'pnl': (xp - ep)*SHARES, 'entry': ep, 'exit': xp, 'type': 'settle'}

    return result

t0 = time.time()
grid_map = {}
for i, fpath in enumerate(files):
    res = load_raw(fpath)
    if res is None: continue
    rid, df_raw = res
    if rid not in settlement_map: continue
    grid_map[rid] = grid_v2(df_raw, settlement_map[rid])
    if (i+1) % 1000 == 0:
        print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')

print(f'Grid done: {len(grid_map)} rounds in {time.time()-t0:.0f}s')

grid_traded_arr = np.zeros(N, dtype=bool)
grid_pnl_arr    = np.zeros(N)
for i, rid in enumerate(round_ids):
    if rid in grid_map and grid_map[rid]['traded']:
        grid_traded_arr[i] = True
        grid_pnl_arr[i] = grid_map[rid]['pnl']

gt_count = grid_traded_arr.sum()
print(f'New grid: {gt_count} trades ({gt_count/N*100:.1f}%), '
      f'PnL={grid_pnl_arr.sum():.1f}, '
      f'WR={(grid_pnl_arr[grid_traded_arr]>0).mean()*100:.1f}%')

# ═══════════════════════════════════════════════════════════
# PART 3: 动量/Fade 9个阈值测试
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 动量/Fade 阈值测试 (δ=0.05 to 0.45)')
print('='*70)

deltas = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45]

# 对于每个delta，需要知道entry price
# 当 up_mid > 0.5+δ → buy UP → entry = up_best_ask
# 当 up_mid < 0.5-δ → buy DOWN → entry = down_best_ask
# 现有CSV只存了 threshold=0.55 (δ=0.05) 的 B_entry
# 对于更大的δ，是B_traded子集 → B_entry仍然有效
# 但对于中间区域(0.45-0.55)的round，原始没有entry数据
# 不过δ≥0.05时总是B_traded子集，所以可以直接用

def compute_threshold_results(delta):
    upper = 0.5 + delta
    lower = 0.5 - delta

    traded = np.zeros(N, dtype=bool)
    side = np.empty(N, dtype=object)
    entry = np.full(N, np.nan)
    mom_pnl = np.full(N, np.nan)
    fade_pnl_arr = np.full(N, np.nan)
    mom_won = np.full(N, np.nan)

    for i in range(N):
        m = up_mid_250[i]
        if np.isnan(m): continue

        if m > upper:
            s = 'up'
        elif m < lower:
            s = 'down'
        else:
            continue

        # entry price: use original B_entry if same side was chosen
        if b_traded_orig[i] and b_side_orig[i] == s:
            ep = b_entry_orig[i]
        else:
            # 对于δ>0.05且原始没交易的round，不应存在
            # (因为更严格的delta是子集)
            # 但safety check
            continue

        if np.isnan(ep) or ep <= 0 or ep >= 0.95:
            continue

        traded[i] = True
        side[i] = s
        entry[i] = ep

        # settlement
        if settlement[i] == s:
            mom_pnl[i] = (1.0 - ep) * SHARES
            fade_pnl_arr[i] = -(1.0 - ep) * SHARES
            mom_won[i] = 1
        else:
            mom_pnl[i] = -ep * SHARES
            fade_pnl_arr[i] = (ep) * SHARES  # fade entry = 1-ep, fade win = 1-(1-ep) = ep
            mom_won[i] = 0

    # Fix fade PnL: fade buys opposite side at (1-ep)
    # fade wins when momentum loses: payout = (1 - (1-ep)) * SHARES = ep * SHARES
    # fade loses when momentum wins: cost = (1-ep) * SHARES

    return {
        'traded': traded,
        'side': side,
        'entry': entry,
        'mom_pnl': mom_pnl,
        'fade_pnl': fade_pnl_arr,
        'mom_won': mom_won,
    }

# ═══════════════════════════════════════════════════════════
# PART 4: DipBuy探针信号
# ═══════════════════════════════════════════════════════════
def rolling_dip_wr(window=7):
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

print('\nComputing DipBuy rolling WR (W=7)...')
dip_wr_7 = rolling_dip_wr(7)
is_whale = dip_wr_7 >= 0.30
is_whale[np.isnan(dip_wr_7)] = False
print(f'Whale rounds: {is_whale.sum()} ({is_whale.mean()*100:.1f}%)')

# ═══════════════════════════════════════════════════════════
# PART 5: 每个阈值的四桶分析
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 9个阈值 × 四桶分析')
print('='*70)

def max_drawdown(cum):
    peak = np.maximum.accumulate(cum)
    return (peak - cum).max()

all_threshold_results = []

for delta in deltas:
    tr = compute_threshold_results(delta)
    t_traded = tr['traded']
    t_mom_pnl = tr['mom_pnl']
    t_fade_pnl = tr['fade_pnl']
    t_mom_won = tr['mom_won']

    n_traded = t_traded.sum()

    # ── 四桶 ──
    # B1: whale + fade
    b1_mask = is_whale & t_traded
    b1_pnl = np.nansum(t_fade_pnl[b1_mask])
    b1_n = b1_mask.sum()
    b1_wr = (t_mom_won[b1_mask]==0).mean()*100 if b1_n > 0 else 0

    # B2: normal + fade
    b2_mask = (~is_whale) & t_traded
    b2_pnl = np.nansum(t_fade_pnl[b2_mask])
    b2_n = b2_mask.sum()

    # B3: normal + regular (grid + momentum)
    nw = ~is_whale
    b3_grid_pnl = grid_pnl_arr[nw & grid_traded_arr].sum()
    b3_mom_pnl = np.nansum(t_mom_pnl[nw & t_traded])
    b3_pnl = b3_grid_pnl + b3_mom_pnl
    b3_mom_n = (nw & t_traded).sum()
    b3_mom_wr = (t_mom_won[nw & t_traded]==1).mean()*100 if b3_mom_n > 0 else 0

    # B4: whale + regular (grid + momentum)
    wh = is_whale
    b4_grid_pnl = grid_pnl_arr[wh & grid_traded_arr].sum()
    b4_mom_pnl = np.nansum(t_mom_pnl[wh & t_traded])
    b4_pnl = b4_grid_pnl + b4_mom_pnl
    b4_mom_n = (wh & t_traded).sum()
    b4_mom_wr = (t_mom_won[wh & t_traded]==1).mean()*100 if b4_mom_n > 0 else 0

    # Optimal = B1 + B3
    optimal = b1_pnl + b3_pnl
    baseline = b3_pnl + b4_pnl

    # Equity curve for optimal
    opt_round = np.zeros(N)
    base_round = np.zeros(N)
    for i in range(N):
        g = grid_pnl_arr[i]
        if is_whale[i]:
            # whale: fade + no grid
            opt_round[i] = (t_fade_pnl[i] if t_traded[i] else 0)
            base_round[i] = g + (t_mom_pnl[i] if t_traded[i] else 0)
        else:
            # normal: momentum + grid
            opt_round[i] = g + (t_mom_pnl[i] if t_traded[i] else 0)
            base_round[i] = opt_round[i]

    opt_cum = np.cumsum(opt_round)
    base_cum = np.cumsum(base_round)
    opt_mdd = max_drawdown(opt_cum)
    base_mdd = max_drawdown(base_cum)
    opt_sharpe = opt_round.mean() / (opt_round.std()+1e-8) * np.sqrt(252*24*12)

    print(f'\nδ={delta:.2f} (>{0.5+delta:.2f} buy UP, <{0.5-delta:.2f} buy DOWN)')
    print(f'  Trades: {n_traded} ({n_traded/N*100:.1f}%)')
    print(f'  B1(whale+fade):  n={b1_n}, WR={b1_wr:.1f}%, PnL={b1_pnl:+.1f}')
    print(f'  B2(normal+fade): n={b2_n}, PnL={b2_pnl:+.1f}')
    print(f'  B3(normal+常规):  mom_n={b3_mom_n}, WR={b3_mom_wr:.1f}%, PnL={b3_pnl:+.1f}')
    print(f'  B4(whale+常规):  mom_n={b4_mom_n}, WR={b4_mom_wr:.1f}%, PnL={b4_pnl:+.1f}')
    print(f'  ★ Optimal(B1+B3)={optimal:+.1f} | Baseline(B3+B4)={baseline:+.1f} | Δ={optimal-baseline:+.1f}')
    print(f'  MDD: opt={opt_mdd:.1f} base={base_mdd:.1f} | Sharpe: {opt_sharpe:.3f}')

    all_threshold_results.append({
        'delta': delta,
        'upper': 0.5+delta, 'lower': 0.5-delta,
        'n_traded': n_traded,
        'B1_n': b1_n, 'B1_pnl': round(b1_pnl,1), 'B1_wr': round(b1_wr,1),
        'B2_pnl': round(b2_pnl,1),
        'B3_pnl': round(b3_pnl,1), 'B3_mom_wr': round(b3_mom_wr,1),
        'B4_pnl': round(b4_pnl,1), 'B4_mom_wr': round(b4_mom_wr,1),
        'optimal': round(optimal,1), 'baseline': round(baseline,1),
        'improvement': round(optimal-baseline,1),
        'opt_mdd': round(opt_mdd,1), 'opt_sharpe': round(opt_sharpe,3),
        'opt_cum': opt_cum.copy(),
        'base_cum': base_cum.copy(),
        'opt_round': opt_round.copy(),
    })

# ═══════════════════════════════════════════════════════════
# PART 6: 找最优delta
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 阈值汇总')
print('='*70)

summary = pd.DataFrame([{k:v for k,v in r.items() if k not in ('opt_cum','base_cum','opt_round')}
                         for r in all_threshold_results])
print(summary[['delta','upper','lower','n_traded','B1_pnl','B1_wr',
               'B3_pnl','B4_pnl','optimal','baseline','improvement',
               'opt_mdd','opt_sharpe']].to_string(index=False))

best_idx = summary['optimal'].idxmax()
best = all_threshold_results[best_idx]
best_delta = best['delta']
print(f'\n★ 最优阈值: δ={best_delta:.2f} '
      f'(>{0.5+best_delta:.2f} / <{0.5-best_delta:.2f}), '
      f'Optimal PnL={best["optimal"]:+.1f}, Sharpe={best["opt_sharpe"]:.3f}')

summary.to_csv(os.path.join(OUT_DIR, 'threshold_comparison.csv'), index=False)

# ═══════════════════════════════════════════════════════════
# PART 7: 最优配置的详细四桶
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(f' 最优配置 δ={best_delta:.2f} 四桶详细')
print('='*70)

best_tr = compute_threshold_results(best_delta)

# 逐日
unique_dates = sorted(set(dates))
print(f'\n逐日明细:')
print(f'{"Date":>12} {"Total":>6} {"Whale":>6} {"B1fade":>8} {"B3norm":>8} {"B4wh_norm":>10} {"Optimal":>8} {"Base":>8}')

for d in unique_dates:
    dm = dates == d
    wh_d = is_whale & dm
    nw_d = (~is_whale) & dm
    
    b1 = np.nansum(best_tr['fade_pnl'][wh_d & best_tr['traded']])
    b3_g = grid_pnl_arr[nw_d & grid_traded_arr].sum()
    b3_m = np.nansum(best_tr['mom_pnl'][nw_d & best_tr['traded']])
    b3 = b3_g + b3_m
    b4_g = grid_pnl_arr[wh_d & grid_traded_arr].sum()
    b4_m = np.nansum(best_tr['mom_pnl'][wh_d & best_tr['traded']])
    b4 = b4_g + b4_m
    
    opt = b1 + b3
    base = b3 + b4
    
    print(f'{d:>12} {dm.sum():>6} {wh_d.sum():>6} {b1:>+8.1f} {b3:>+8.1f} {b4:>+10.1f} {opt:>+8.1f} {base:>+8.1f}')


# ═══════════════════════════════════════════════════════════
# PART 8: 画图
# ═══════════════════════════════════════════════════════════
print('\n生成图表...')

# ── 图1: Equity Curve (最优 vs 基准) ──
fig, axes = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={'height_ratios': [3, 1]})

ax1 = axes[0]
ax1.plot(best['opt_cum'], linewidth=1.2, label=f'Optimal (B1+B3) δ={best_delta:.2f}', color='#2196F3')
ax1.plot(best['base_cum'], linewidth=1.0, label='Baseline (all normal)', color='#999', alpha=0.7)
ax1.axhline(0, color='black', linewidth=0.5)
ax1.set_title(f'Equity Curve - 50 shares | Grid(0.18→0.26) + Momentum(δ={best_delta:.2f}) + Whale Fade',
              fontsize=13, fontweight='bold')
ax1.set_ylabel('Cumulative PnL ($)')
ax1.legend(fontsize=11)
ax1.grid(True, alpha=0.3)

# Mark whale periods
for i in range(N):
    if is_whale[i]:
        ax1.axvspan(i, i+1, alpha=0.03, color='red')

# Drawdown subplot
peak_opt = np.maximum.accumulate(best['opt_cum'])
dd_opt = peak_opt - best['opt_cum']
ax2 = axes[1]
ax2.fill_between(range(N), -dd_opt, 0, alpha=0.4, color='red', label='Drawdown')
ax2.set_ylabel('Drawdown ($)')
ax2.set_xlabel('Round #')
ax2.legend(fontsize=10)
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'equity_curve.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → equity_curve.png')

# ── 图2: 所有阈值的Equity Curve对比 ──
fig, ax = plt.subplots(figsize=(16, 8))
colors = plt.cm.viridis(np.linspace(0, 1, len(deltas)))
for idx, r in enumerate(all_threshold_results):
    lw = 2.0 if r['delta'] == best_delta else 0.8
    alpha = 1.0 if r['delta'] == best_delta else 0.5
    ax.plot(r['opt_cum'], linewidth=lw, alpha=alpha, color=colors[idx],
            label=f'δ={r["delta"]:.2f} PnL={r["optimal"]:+.0f}')
ax.axhline(0, color='black', linewidth=0.5)
ax.set_title('Equity Curves for All Thresholds (Optimal System)', fontsize=13, fontweight='bold')
ax.set_ylabel('Cumulative PnL ($)')
ax.set_xlabel('Round #')
ax.legend(fontsize=9, ncol=3)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'all_thresholds.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → all_thresholds.png')

# ── 图3: 24h 分时段柱状图 ──
fig, axes = plt.subplots(2, 2, figsize=(18, 12))

opt_round = best['opt_round']
base_round = best['base_cum']  # we need per-round
base_round_pnl = np.zeros(N)
for i in range(N):
    g = grid_pnl_arr[i]
    m = best_tr['mom_pnl'][i] if best_tr['traded'][i] else 0
    base_round_pnl[i] = g + (m if not np.isnan(m) else 0)

# Hourly stats
hour_data = []
for h in range(24):
    hm = hours == h
    n_rounds = hm.sum()
    if n_rounds == 0: continue
    
    opt_h = opt_round[hm].sum()
    base_h = base_round_pnl[hm].sum()
    whale_h = (is_whale & hm).sum()
    whale_pct_h = whale_h / n_rounds * 100 if n_rounds > 0 else 0
    
    # momentum WR in this hour
    mom_mask_h = hm & best_tr['traded']
    mom_wr_h = (best_tr['mom_won'][mom_mask_h]==1).mean()*100 if mom_mask_h.sum() > 0 else 0
    
    hour_data.append({
        'hour': h, 'n_rounds': n_rounds, 'opt_pnl': opt_h, 'base_pnl': base_h,
        'whale_pct': whale_pct_h, 'mom_wr': mom_wr_h
    })

hdf = pd.DataFrame(hour_data)

# 3a: Optimal PnL by hour
ax = axes[0, 0]
colors_bar = ['#4CAF50' if v >= 0 else '#F44336' for v in hdf['opt_pnl']]
ax.bar(hdf['hour'], hdf['opt_pnl'], color=colors_bar, alpha=0.8)
ax.axhline(0, color='black', linewidth=0.5)
ax.set_title('Optimal System PnL by Hour', fontsize=12, fontweight='bold')
ax.set_xlabel('Hour (UTC)')
ax.set_ylabel('PnL ($)')
ax.set_xticks(range(0, 24))
ax.grid(True, alpha=0.3, axis='y')

# 3b: Baseline PnL by hour
ax = axes[0, 1]
colors_bar2 = ['#4CAF50' if v >= 0 else '#F44336' for v in hdf['base_pnl']]
ax.bar(hdf['hour'], hdf['base_pnl'], color=colors_bar2, alpha=0.8)
ax.axhline(0, color='black', linewidth=0.5)
ax.set_title('Baseline (No Whale Detection) PnL by Hour', fontsize=12, fontweight='bold')
ax.set_xlabel('Hour (UTC)')
ax.set_ylabel('PnL ($)')
ax.set_xticks(range(0, 24))
ax.grid(True, alpha=0.3, axis='y')

# 3c: Whale detection % by hour
ax = axes[1, 0]
ax.bar(hdf['hour'], hdf['whale_pct'], color='#FF9800', alpha=0.8)
ax.set_title('Whale Detection Rate by Hour (%)', fontsize=12, fontweight='bold')
ax.set_xlabel('Hour (UTC)')
ax.set_ylabel('Whale %')
ax.set_xticks(range(0, 24))
ax.grid(True, alpha=0.3, axis='y')

# 3d: Momentum WR by hour
ax = axes[1, 1]
ax.bar(hdf['hour'], hdf['mom_wr'], color='#2196F3', alpha=0.8)
ax.axhline(80, color='red', linestyle='--', linewidth=1, label='80% WR')
ax.set_title('Momentum Win Rate by Hour (%)', fontsize=12, fontweight='bold')
ax.set_xlabel('Hour (UTC)')
ax.set_ylabel('Win Rate %')
ax.set_xticks(range(0, 24))
ax.set_ylim(50, 100)
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

plt.suptitle(f'24-Hour Analysis | δ={best_delta:.2f} | 50 shares', fontsize=14, fontweight='bold', y=1.01)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'hourly_breakdown.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → hourly_breakdown.png')

# ── 图4: 阈值 vs PnL / Sharpe ──
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

ax1.bar([f'{d:.2f}' for d in summary['delta']], summary['optimal'], color='#2196F3', alpha=0.8, label='Optimal')
ax1.bar([f'{d:.2f}' for d in summary['delta']], summary['baseline'], color='#999', alpha=0.4, label='Baseline')
ax1.set_title('Total PnL by Threshold', fontsize=12, fontweight='bold')
ax1.set_xlabel('Delta (δ)')
ax1.set_ylabel('PnL ($)')
ax1.legend()
ax1.grid(True, alpha=0.3, axis='y')

ax2.plot(summary['delta'], summary['opt_sharpe'], 'o-', color='#4CAF50', linewidth=2, markersize=8)
ax2.set_title('Sharpe Ratio by Threshold', fontsize=12, fontweight='bold')
ax2.set_xlabel('Delta (δ)')
ax2.set_ylabel('Sharpe')
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'threshold_metrics.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → threshold_metrics.png')

# ── 图5: 逐日PnL对比 ──
fig, ax = plt.subplots(figsize=(16, 6))
daily_opt = []
daily_base = []
for d in unique_dates:
    dm = dates == d
    daily_opt.append(opt_round[dm].sum())
    daily_base.append(base_round_pnl[dm].sum())

x = np.arange(len(unique_dates))
w = 0.35
ax.bar(x - w/2, daily_opt, w, color='#2196F3', alpha=0.8, label='Optimal')
ax.bar(x + w/2, daily_base, w, color='#999', alpha=0.6, label='Baseline')
ax.axhline(0, color='black', linewidth=0.5)
ax.set_xticks(x)
ax.set_xticklabels([d[5:] for d in unique_dates], rotation=45, fontsize=9)
ax.set_title(f'Daily PnL: Optimal vs Baseline | δ={best_delta:.2f}', fontsize=13, fontweight='bold')
ax.set_ylabel('PnL ($)')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'daily_pnl.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → daily_pnl.png')

# ═══════════════════════════════════════════════════════════
# PART 9: 三阶段稳健性
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(f' 三阶段稳健性 (δ={best_delta:.2f})')
print('='*70)

n3 = N // 3
for pn, s, e in [('P1', 0, n3), ('P2', n3, 2*n3), ('P3', 2*n3, N)]:
    p_opt = opt_round[s:e].sum()
    p_base = base_round_pnl[s:e].sum()
    p_mdd = max_drawdown(np.cumsum(opt_round[s:e]))
    p_whale = is_whale[s:e].sum()
    flag = '✅' if p_opt > p_base else '⚠️'
    print(f'  {pn}: rounds={e-s}, whale={p_whale}, '
          f'Optimal={p_opt:+.1f}, Baseline={p_base:+.1f}, '
          f'Δ={p_opt-p_base:+.1f} {flag}, MDD={p_mdd:.1f}')


# ═══════════════════════════════════════════════════════════
# FINAL
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' FINAL SUMMARY')
print('='*70)
print(f'''
配置:
  Grid:     buy≤$0.18 → sell≥$0.26 | 窗口 0-94s/0-190s | 50 shares
  动量:     δ={best_delta:.2f} | >{0.5+best_delta:.2f} buy UP, <{0.5-best_delta:.2f} buy DOWN | 50 shares
  Whale信号: DipBuy探针 W=7, T≥0.30
  Fade:     whale时反打 | 50 shares

Grid统计:
  Trades: {grid_traded_arr.sum()}, WR={(grid_pnl_arr[grid_traded_arr]>0).mean()*100:.1f}%
  Total PnL: {grid_pnl_arr.sum():+.1f}
  Avg PnL/trade: {grid_pnl_arr[grid_traded_arr].mean():+.2f}

最优系统(B1+B3):
  PnL:    {best['optimal']:+.1f}
  MDD:    {best['opt_mdd']:.1f}
  Sharpe: {best['opt_sharpe']:.3f}

对照(全常规 B3+B4):
  PnL:    {best['baseline']:+.1f}

提升: {best['improvement']:+.1f} ({best['improvement']/abs(best['baseline'])*100:+.1f}%)

图表已保存: {OUT_DIR}/
''')

hdf.to_csv(os.path.join(OUT_DIR, 'hourly_stats.csv'), index=False)
print('Done!')
