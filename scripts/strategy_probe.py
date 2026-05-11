"""
═══════════════════════════════════════════════════════════════
 策略探针信号 V3 —— 用实盘策略表现作为 whale 信号
═══════════════════════════════════════════════════════════════

核心思想: 不需要额外DipBuy探针。直接用正在跑的策略(网格+动量)的
         实时盈亏表现偏离基线 → 判定庄家在操纵

信号候选:
  S1: 动量滚动胜率 — 最近W盘WR低于阈值
  S2: 网格+动量联合滚动PnL — 最近W盘PnL为负
  S3: 动量连续亏损 — 连续K盘亏损
  S4: 网格滚动损失率 — 最近W盘网格亏损率异常
  S5: 联合偏差 — 动量WR + 网格损失同时恶化

配置: Grid buy≤0.18 sell≥0.26 (0-94/0-190), 动量δ=0.05, 50 shares
"""

import pandas as pd, numpy as np, os, warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
matplotlib.rcParams['font.family'] = ['SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings('ignore')

EXIST_CSV = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies_fixed\three_strategies_fixed.csv'
DETAIL_V2 = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\four_bucket_v2\round_detail.csv'
OUT_DIR   = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\strategy_probe'
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50

# ─── 加载数据 ───
df = pd.read_csv(EXIST_CSV)
N = len(df)
print(f'Loaded {N} rounds')

# 从v2拿新网格数据
try:
    v2 = pd.read_csv(DETAIL_V2)
    # v2 has: is_whale (old DipBuy signal), system_pnl, etc.
except:
    v2 = None

settlement = df['f_settlement'].values
up_mid_250 = df['f_up_mid_250'].values.astype(float)
hours = df['round_id'].str.extract(r'_(\d{2})-\d{2}-\d{2}')[0].astype(int).values
dates = df['round_id'].str.extract(r'^(\d{4}-\d{2}-\d{2})')[0].values

# ─── 动量策略 (δ=0.05, 50shares) ───
b_traded = df['B_traded'].values.astype(bool)
b_side   = df['B_side'].values
b_entry  = df['B_entry'].values.astype(float)

mom_pnl  = np.zeros(N)
mom_won  = np.full(N, np.nan)
fade_pnl = np.full(N, np.nan)

for i in range(N):
    if not b_traded[i]: continue
    ep = b_entry[i]
    if settlement[i] == b_side[i]:
        mom_pnl[i] = (1.0 - ep) * SHARES
        fade_pnl[i] = -(1.0 - ep) * SHARES
        mom_won[i] = 1
    else:
        mom_pnl[i] = -ep * SHARES
        fade_pnl[i] = ep * SHARES
        mom_won[i] = 0

# ─── 新网格 (从v2重建: buy≤0.18, sell≥0.26, 50 shares) ───
# 需要从raw CSV重新跑。但v2已经跑过了，我们从round_detail获取。
# round_detail.csv里有 normal_pnl 和 system_pnl。
# normal_pnl = grid_pnl + mom_pnl (在v2中)。
# 但v2的normal_pnl是用旧10shares计算的... 需要重新计算。

# 实际上v2脚本已经计算了新网格。让我重新加载。
import glob
DATA_DIR = r'C:\Users\ZHAOKAI\data'

# 为了效率，重新跑网格
print('重新计算网格...')
files = sorted(glob.glob(os.path.join(DATA_DIR, '*.csv')))
settlement_map = dict(zip(df['round_id'], settlement))

def load_raw(fpath):
    try:
        raw = pd.read_csv(fpath)
        if len(raw) < 20: return None
        ts = pd.to_datetime(raw['timestamp'], format='ISO8601')
        raw['elapsed'] = (ts - ts.iloc[0]).dt.total_seconds()
        if raw['elapsed'].max() < 200: return None
        for c in ['up_best_bid','up_best_ask','down_best_bid','down_best_ask']:
            if c in raw.columns:
                raw[c] = pd.to_numeric(raw[c], errors='coerce').ffill()
        return os.path.basename(fpath).replace('.csv',''), raw
    except:
        return None

def grid_v2(raw_df, settle_side):
    for side in ['up', 'down']:
        ac = f'{side}_best_ask'
        bc = f'{side}_best_bid'
        entry_df = raw_df[(raw_df['elapsed'] >= 0) & (raw_df['elapsed'] <= 94)]
        cheap = entry_df[(entry_df[ac].fillna(999) > 0) & (entry_df[ac].fillna(999) <= 0.18)]
        if len(cheap) == 0: continue
        ep = float(cheap.iloc[0][ac])
        et = cheap.iloc[0]['elapsed']
        sell_df = raw_df[(raw_df['elapsed'] > et) & (raw_df['elapsed'] <= 190)]
        tp = sell_df[sell_df[bc].fillna(0) >= 0.26]
        if len(tp) > 0:
            return True, (0.26 - ep) * SHARES, 'profit'
        if len(sell_df) > 0:
            lb = sell_df[bc].dropna()
            if len(lb) > 0:
                xp = float(lb.iloc[-1])
                return True, (xp - ep) * SHARES, 'timeout'
        xp = 1.0 if settle_side == side else 0.0
        return True, (xp - ep) * SHARES, 'settle'
    return False, 0.0, None

import time
t0 = time.time()
grid_map = {}
for i, fpath in enumerate(files):
    res = load_raw(fpath)
    if res is None: continue
    rid, raw_df = res
    if rid not in settlement_map: continue
    traded, pnl, typ = grid_v2(raw_df, settlement_map[rid])
    grid_map[rid] = (traded, pnl, typ)
    if (i+1) % 2000 == 0:
        print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')

print(f'Grid done: {len(grid_map)} rounds in {time.time()-t0:.0f}s')

grid_traded = np.zeros(N, dtype=bool)
grid_pnl = np.zeros(N)
grid_won = np.full(N, np.nan)
for i, rid in enumerate(df['round_id']):
    if rid in grid_map:
        t, p, typ = grid_map[rid]
        grid_traded[i] = t
        grid_pnl[i] = p
        if t:
            grid_won[i] = 1.0 if p > 0 else 0.0

gt = grid_traded.sum()
print(f'Grid: {gt} trades ({gt/N*100:.1f}%), PnL={grid_pnl.sum():.1f}, '
      f'WR={(grid_pnl[grid_traded]>0).mean()*100:.1f}%')

# ─── 合并常规策略PnL ───
normal_pnl = grid_pnl.copy()
for i in range(N):
    if b_traded[i]:
        normal_pnl[i] += mom_pnl[i]

print(f'Normal strategy total PnL: {normal_pnl.sum():.1f}')

# ═══════════════════════════════════════════════════════════
# DipBuy探针 (对照组)
# ═══════════════════════════════════════════════════════════
c_traded = df['C_traded'].values.astype(bool)
c_pnl = df['C_pnl'].values.astype(float)
dip_won = np.where(c_traded, (c_pnl > 0).astype(float), np.nan)

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

print('\nComputing DipBuy probe (baseline)...')
dip_wr_7 = rolling_dip_wr(7)
dipbuy_whale = (dip_wr_7 >= 0.30)
dipbuy_whale[np.isnan(dip_wr_7)] = False
print(f'DipBuy whale: {dipbuy_whale.sum()} ({dipbuy_whale.mean()*100:.1f}%)')

# ═══════════════════════════════════════════════════════════
# 策略探针信号生成
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 策略探针信号测试')
print('='*70)

def rolling_wr(won_arr, traded_arr, window):
    """滚动胜率: 最近window个traded rounds的WR"""
    result = np.full(N, np.nan)
    for i in range(N):
        count = 0; wins = 0
        for j in range(i-1, -1, -1):
            if not traded_arr[j]: continue
            count += 1
            wins += (won_arr[j] == 1)
            if count >= window: break
        if count >= window:
            result[i] = wins / count
    return result

def rolling_pnl_sum(pnl_arr, window):
    """滚动PnL总和: 最近window个round的PnL (含不交易round=0)"""
    result = np.full(N, np.nan)
    for i in range(window, N):
        result[i] = pnl_arr[i-window:i].sum()
    return result

def consecutive_losses(won_arr, traded_arr):
    """连续亏损计数: 当前连续多少盘traded且亏损"""
    result = np.zeros(N)
    streak = 0
    for i in range(N):
        if traded_arr[i] and won_arr[i] == 0:
            streak += 1
        elif traded_arr[i] and won_arr[i] == 1:
            streak = 0
        result[i] = streak
    return result

# Pre-compute building blocks
print('Computing rolling metrics...')
mom_won_bool = np.where(b_traded, (mom_pnl > 0).astype(float), np.nan)

# S1: 动量滚动WR
mom_wr_configs = []
for W in [5, 7, 10, 15, 20]:
    rwr = rolling_wr(mom_won_bool, b_traded, W)
    for thr in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
        whale = rwr < thr
        whale[np.isnan(rwr)] = False
        mom_wr_configs.append({
            'signal': f'S1_momWR', 'params': f'W={W},T<{thr:.2f}',
            'W': W, 'T': thr,
            'whale': whale.copy(),
            'n_whale': whale.sum(),
        })

# S2: 联合滚动PnL
combo_pnl_configs = []
for W in [5, 7, 10, 15, 20]:
    rpnl = rolling_pnl_sum(normal_pnl, W)
    for thr in [0, -5, -10, -20, -30, -50]:
        whale = rpnl < thr * SHARES / 10  # scale threshold to 50 shares
        whale[np.isnan(rpnl)] = False
        combo_pnl_configs.append({
            'signal': f'S2_comboPnL', 'params': f'W={W},PnL<{thr}',
            'W': W, 'T': thr,
            'whale': whale.copy(),
            'n_whale': whale.sum(),
        })

# S3: 动量连续亏损
consec = consecutive_losses(mom_won_bool, b_traded)
consec_configs = []
for K in [2, 3, 4, 5]:
    whale = consec >= K
    # Use lagged signal (look at streak BEFORE current round)
    whale_lag = np.zeros(N, dtype=bool)
    whale_lag[1:] = whale[:-1]
    consec_configs.append({
        'signal': 'S3_consec', 'params': f'K>={K}',
        'K': K,
        'whale': whale_lag.copy(),
        'n_whale': whale_lag.sum(),
    })

# S4: 网格滚动损失率
grid_wr_configs = []
for W in [5, 7, 10, 15]:
    rwr = rolling_wr(grid_won, grid_traded, W)
    for thr in [0.40, 0.45, 0.50, 0.55, 0.60]:
        whale = rwr < thr
        whale[np.isnan(rwr)] = False
        grid_wr_configs.append({
            'signal': 'S4_gridWR', 'params': f'W={W},T<{thr:.2f}',
            'W': W, 'T': thr,
            'whale': whale.copy(),
            'n_whale': whale.sum(),
        })

# S5: 联合 — 动量WR低 AND 网格WR低 (同时恶化)
combo_signal_configs = []
for mW in [7, 10]:
    m_wr = rolling_wr(mom_won_bool, b_traded, mW)
    for gW in [5, 7]:
        g_wr = rolling_wr(grid_won, grid_traded, gW)
        for mT in [0.65, 0.70, 0.75]:
            for gT in [0.50, 0.55, 0.60]:
                whale = (m_wr < mT) & (g_wr < gT)
                whale[np.isnan(m_wr) | np.isnan(g_wr)] = False
                combo_signal_configs.append({
                    'signal': 'S5_combo', 
                    'params': f'mW={mW},mT<{mT:.2f},gW={gW},gT<{gT:.2f}',
                    'whale': whale.copy(),
                    'n_whale': whale.sum(),
                })

all_configs = mom_wr_configs + combo_pnl_configs + consec_configs + grid_wr_configs + combo_signal_configs

# DipBuy对照
all_configs.append({
    'signal': 'BASELINE_DipBuy', 'params': 'W=7,T>=0.30',
    'whale': dipbuy_whale.copy(),
    'n_whale': dipbuy_whale.sum(),
})

print(f'Total configs to test: {len(all_configs)}')

# ═══════════════════════════════════════════════════════════
# 四桶计算
# ═══════════════════════════════════════════════════════════
def max_drawdown(cum):
    peak = np.maximum.accumulate(cum)
    return (peak - cum).max()

def evaluate_signal(whale_mask):
    """Given a whale mask, compute the 4-bucket system metrics."""
    wh = whale_mask
    nw = ~whale_mask
    
    # B1: whale + fade
    b1_mask = wh & b_traded
    b1_pnl = np.nansum(fade_pnl[b1_mask])
    b1_n = b1_mask.sum()
    b1_wr = (mom_won[b1_mask]==0).mean()*100 if b1_n > 0 else 0
    
    # B3: normal + regular (grid + momentum)
    b3_pnl = grid_pnl[nw].sum() + np.nansum(np.where(b_traded & nw, mom_pnl, 0))
    
    # B4: whale + regular
    b4_pnl = grid_pnl[wh].sum() + np.nansum(np.where(b_traded & wh, mom_pnl, 0))
    
    # Optimal = B1 + B3
    optimal = b1_pnl + b3_pnl
    baseline = b3_pnl + b4_pnl
    
    # Per-round equity
    opt_round = np.zeros(N)
    for i in range(N):
        if whale_mask[i]:
            opt_round[i] = fade_pnl[i] if b_traded[i] and not np.isnan(fade_pnl[i]) else 0
        else:
            opt_round[i] = grid_pnl[i] + (mom_pnl[i] if b_traded[i] else 0)
    
    opt_cum = np.cumsum(opt_round)
    mdd = max_drawdown(opt_cum)
    sharpe = opt_round.mean() / (opt_round.std()+1e-8) * np.sqrt(252*24*12)
    
    return {
        'b1_pnl': b1_pnl, 'b1_n': b1_n, 'b1_wr': b1_wr,
        'b3_pnl': b3_pnl, 'b4_pnl': b4_pnl,
        'optimal': optimal, 'baseline': baseline,
        'improvement': optimal - baseline,
        'mdd': mdd, 'sharpe': sharpe,
        'opt_round': opt_round, 'opt_cum': opt_cum,
    }

print('\nEvaluating all signals...')
results = []
for cfg in all_configs:
    n_wh = cfg['n_whale']
    wh_pct = n_wh / N * 100
    
    # Skip configs with too few or too many whale rounds
    if n_wh < 20 or n_wh > N * 0.60:
        results.append({
            **{k:v for k,v in cfg.items() if k != 'whale'},
            'whale_pct': wh_pct, 'optimal': np.nan, 'improvement': np.nan,
            'mdd': np.nan, 'sharpe': np.nan,
            'b1_pnl': np.nan, 'b1_wr': np.nan, 'b3_pnl': np.nan, 'b4_pnl': np.nan,
        })
        continue
    
    ev = evaluate_signal(cfg['whale'])
    results.append({
        **{k:v for k,v in cfg.items() if k != 'whale'},
        'whale_pct': round(wh_pct, 1),
        'optimal': round(ev['optimal'], 1),
        'baseline': round(ev['baseline'], 1),
        'improvement': round(ev['improvement'], 1),
        'mdd': round(ev['mdd'], 1),
        'sharpe': round(ev['sharpe'], 3),
        'b1_pnl': round(ev['b1_pnl'], 1),
        'b1_wr': round(ev['b1_wr'], 1),
        'b3_pnl': round(ev['b3_pnl'], 1),
        'b4_pnl': round(ev['b4_pnl'], 1),
    })

rdf = pd.DataFrame(results)
rdf = rdf.dropna(subset=['optimal'])

# ═══════════════════════════════════════════════════════════
# 结果排序
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' TOP 20 信号 (按 Optimal PnL 排序)')
print('='*70)

top20 = rdf.nlargest(20, 'optimal')
print(top20[['signal','params','n_whale','whale_pct',
             'b1_pnl','b1_wr','b3_pnl','b4_pnl',
             'optimal','improvement','mdd','sharpe']].to_string(index=False))

print('\n' + '='*70)
print(' TOP 20 信号 (按 Sharpe 排序)')
print('='*70)

top20s = rdf.nlargest(20, 'sharpe')
print(top20s[['signal','params','n_whale','whale_pct',
              'b1_pnl','b1_wr','b3_pnl','b4_pnl',
              'optimal','improvement','mdd','sharpe']].to_string(index=False))

# ═══════════════════════════════════════════════════════════
# 各类信号最佳对比
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 每类信号最佳配置对比')
print('='*70)

signal_types = ['S1_momWR', 'S2_comboPnL', 'S3_consec', 'S4_gridWR', 'S5_combo', 'BASELINE_DipBuy']
best_per_type = []
best_whale_masks = {}

for st in signal_types:
    sub = rdf[rdf['signal'] == st]
    if len(sub) == 0: continue
    best = sub.loc[sub['optimal'].idxmax()]
    best_per_type.append(best)
    print(f'\n{st}: {best["params"]}')
    print(f'  Whale: {best["n_whale"]} ({best["whale_pct"]:.1f}%)')
    print(f'  B1(fade): PnL={best["b1_pnl"]:+.1f}, WR={best["b1_wr"]:.1f}%')
    print(f'  B3(normal): PnL={best["b3_pnl"]:+.1f}')
    print(f'  B4(whale+normal): PnL={best["b4_pnl"]:+.1f}')
    print(f'  ★ Optimal={best["optimal"]:+.1f}, Improve={best["improvement"]:+.1f}, '
          f'MDD={best["mdd"]:.1f}, Sharpe={best["sharpe"]:.3f}')
    
    # Save whale mask for chart
    for cfg in all_configs:
        if cfg['signal'] == st and cfg['params'] == best['params']:
            best_whale_masks[st] = cfg['whale']
            break

# ═══════════════════════════════════════════════════════════
# 最佳策略探针 vs DipBuy 详细对比
# ═══════════════════════════════════════════════════════════
# Find overall best strategy probe (excluding DipBuy)
strat_only = rdf[rdf['signal'] != 'BASELINE_DipBuy']
if len(strat_only) > 0:
    best_strat = strat_only.loc[strat_only['optimal'].idxmax()]
    
    print('\n' + '='*70)
    print(' 最佳策略探针 vs DipBuy探针')
    print('='*70)
    
    dip_row = rdf[rdf['signal'] == 'BASELINE_DipBuy'].iloc[0]
    
    print(f'\n  {"":>20} {"策略探针":>15} {"DipBuy探针":>15}')
    print(f'  {"信号":>20} {best_strat["signal"]+" "+best_strat["params"]:>15} {"W=7,T>=0.30":>15}')
    print(f'  {"Whale盘数":>20} {best_strat["n_whale"]:>15} {dip_row["n_whale"]:>15}')
    print(f'  {"Whale占比":>20} {best_strat["whale_pct"]:>14.1f}% {dip_row["whale_pct"]:>14.1f}%')
    print(f'  {"B1(fade) PnL":>20} {best_strat["b1_pnl"]:>+15.1f} {dip_row["b1_pnl"]:>+15.1f}')
    print(f'  {"B3(normal) PnL":>20} {best_strat["b3_pnl"]:>+15.1f} {dip_row["b3_pnl"]:>+15.1f}')
    print(f'  {"Optimal PnL":>20} {best_strat["optimal"]:>+15.1f} {dip_row["optimal"]:>+15.1f}')
    print(f'  {"Improvement":>20} {best_strat["improvement"]:>+15.1f} {dip_row["improvement"]:>+15.1f}')
    print(f'  {"MDD":>20} {best_strat["mdd"]:>15.1f} {dip_row["mdd"]:>15.1f}')
    print(f'  {"Sharpe":>20} {best_strat["sharpe"]:>15.3f} {dip_row["sharpe"]:>15.3f}')

# ═══════════════════════════════════════════════════════════
# 画图
# ═══════════════════════════════════════════════════════════
print('\n生成图表...')

# 找最佳策略探针和DipBuy的equity curves
best_strat_cfg = None
dip_cfg = None
for cfg in all_configs:
    if cfg['signal'] == best_strat['signal'] and cfg['params'] == best_strat['params']:
        best_strat_cfg = cfg
    if cfg['signal'] == 'BASELINE_DipBuy':
        dip_cfg = cfg

ev_strat = evaluate_signal(best_strat_cfg['whale'])
ev_dip = evaluate_signal(dip_cfg['whale'])

# 无信号baseline
base_round = np.zeros(N)
for i in range(N):
    base_round[i] = grid_pnl[i] + (mom_pnl[i] if b_traded[i] else 0)
base_cum = np.cumsum(base_round)

# ── 图1: Equity Curve 三线对比 ──
fig, axes = plt.subplots(2, 1, figsize=(16, 10), gridspec_kw={'height_ratios': [3, 1]})

ax = axes[0]
ax.plot(ev_strat['opt_cum'], linewidth=1.5, color='#2196F3',
        label=f'Strategy Probe ({best_strat["signal"]} {best_strat["params"]}) PnL={ev_strat["optimal"]:+.0f}')
ax.plot(ev_dip['opt_cum'], linewidth=1.2, color='#FF9800',
        label=f'DipBuy Probe (W=7,T≥0.30) PnL={ev_dip["optimal"]:+.0f}')
ax.plot(base_cum, linewidth=0.8, color='#999', alpha=0.6,
        label=f'No Signal (all normal) PnL={base_cum[-1]:+.0f}')
ax.axhline(0, color='black', linewidth=0.5)
ax.set_title('Strategy Probe vs DipBuy Probe vs No Signal | 50 shares', fontsize=13, fontweight='bold')
ax.set_ylabel('Cumulative PnL ($)')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)

# Drawdown
ax2 = axes[1]
for data, label, color in [
    (ev_strat['opt_cum'], 'Strategy Probe', '#2196F3'),
    (ev_dip['opt_cum'], 'DipBuy', '#FF9800'),
]:
    peak = np.maximum.accumulate(data)
    dd = peak - data
    ax2.plot(-dd, linewidth=1, color=color, alpha=0.7, label=label)
ax2.set_ylabel('Drawdown ($)')
ax2.set_xlabel('Round #')
ax2.legend(fontsize=9)
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'probe_comparison.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → probe_comparison.png')

# ── 图2: 24h 分时段对比 ──
fig, axes = plt.subplots(1, 3, figsize=(20, 6))

for idx, (ev, name, color) in enumerate([
    (ev_strat, f'Strategy Probe', '#2196F3'),
    (ev_dip, 'DipBuy Probe', '#FF9800'),
    (None, 'No Signal', '#999'),
]):
    ax = axes[idx]
    hourly = []
    for h in range(24):
        hm = hours == h
        if hm.sum() == 0: continue
        if ev is not None:
            pnl_h = ev['opt_round'][hm].sum()
        else:
            pnl_h = base_round[hm].sum()
        hourly.append((h, pnl_h))
    
    hs, ps = zip(*hourly)
    colors_bar = [('#4CAF50' if p >= 0 else '#F44336') for p in ps]
    ax.bar(hs, ps, color=colors_bar, alpha=0.8)
    ax.axhline(0, color='black', linewidth=0.5)
    ax.set_title(f'{name} - Hourly PnL', fontsize=11, fontweight='bold')
    ax.set_xlabel('Hour (UTC)')
    ax.set_ylabel('PnL ($)')
    ax.set_xticks(range(0, 24))
    ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'hourly_comparison.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → hourly_comparison.png')

# ── 图3: 各类信号最佳 Equity Curve 对比 ──
fig, ax = plt.subplots(figsize=(16, 8))
colors_map = {'S1_momWR': '#2196F3', 'S2_comboPnL': '#4CAF50', 'S3_consec': '#9C27B0',
              'S4_gridWR': '#FF5722', 'S5_combo': '#00BCD4', 'BASELINE_DipBuy': '#FF9800'}

for st in signal_types:
    if st not in best_whale_masks: continue
    ev_t = evaluate_signal(best_whale_masks[st])
    # Find params
    for r in results:
        if isinstance(r, dict) and r.get('signal') == st:
            params_str = r.get('params', '')
            break
    sub = rdf[rdf['signal'] == st]
    if len(sub) == 0: continue
    best_row = sub.loc[sub['optimal'].idxmax()]
    
    ax.plot(ev_t['opt_cum'], linewidth=1.5, color=colors_map.get(st, 'gray'),
            label=f'{st} ({best_row["params"]}) PnL={ev_t["optimal"]:+.0f}')

ax.plot(base_cum, linewidth=0.8, color='#999', alpha=0.5, label=f'No Signal PnL={base_cum[-1]:+.0f}')
ax.axhline(0, color='black', linewidth=0.5)
ax.set_title('Best Config per Signal Type - Equity Curves', fontsize=13, fontweight='bold')
ax.set_ylabel('Cumulative PnL ($)')
ax.set_xlabel('Round #')
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'all_signals_equity.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → all_signals_equity.png')

# ── 图4: 逐日PnL ──
fig, ax = plt.subplots(figsize=(16, 6))
unique_dates = sorted(set(dates))
daily_strat = [ev_strat['opt_round'][dates==d].sum() for d in unique_dates]
daily_dip = [ev_dip['opt_round'][dates==d].sum() for d in unique_dates]
daily_base = [base_round[dates==d].sum() for d in unique_dates]

x = np.arange(len(unique_dates))
w = 0.25
ax.bar(x - w, daily_strat, w, color='#2196F3', alpha=0.8, label='Strategy Probe')
ax.bar(x, daily_dip, w, color='#FF9800', alpha=0.8, label='DipBuy Probe')
ax.bar(x + w, daily_base, w, color='#999', alpha=0.6, label='No Signal')
ax.axhline(0, color='black', linewidth=0.5)
ax.set_xticks(x)
ax.set_xticklabels([d[5:] for d in unique_dates], rotation=45, fontsize=9)
ax.set_title('Daily PnL: Strategy Probe vs DipBuy vs No Signal', fontsize=13, fontweight='bold')
ax.set_ylabel('PnL ($)')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'daily_comparison.png'), dpi=150, bbox_inches='tight')
plt.close()
print('  → daily_comparison.png')

# ═══════════════════════════════════════════════════════════
# 三阶段稳健性 (最佳策略探针)
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 三阶段稳健性')
print('='*70)

n3 = N // 3
for label, data, name in [
    ('Strategy Probe', ev_strat, best_strat['signal']+' '+best_strat['params']),
    ('DipBuy Probe', ev_dip, 'DipBuy W=7,T>=0.30'),
]:
    print(f'\n  {label} ({name}):')
    for pn, s, e in [('P1', 0, n3), ('P2', n3, 2*n3), ('P3', 2*n3, N)]:
        p = data['opt_round'][s:e].sum()
        b = base_round[s:e].sum()
        flag = '✅' if p > b else '⚠️'
        print(f'    {pn}: Optimal={p:+.1f}, Baseline={b:+.1f}, Δ={p-b:+.1f} {flag}')

# Save
rdf.to_csv(os.path.join(OUT_DIR, 'all_signals.csv'), index=False)
print(f'\nAll results saved to {OUT_DIR}/')
print('Done!')
