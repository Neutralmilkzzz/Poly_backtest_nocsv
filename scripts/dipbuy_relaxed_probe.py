"""
═══════════════════════════════════════════════════════════════
 DipBuy 放松探针 — 不要求结算赢，只要求反弹到一定幅度
═══════════════════════════════════════════════════════════════

核心改进:
  原始: ask≤$0.20 → 该方赢了结算($1.00) → 记WIN
  放松: ask≤$0.20 → 该方bid反弹到≥X(0.30/0.35/0.40/0.45/0.50) → 记WIN

这样能捕获更多"庄家大逆转但最终没赢"的事件，信号更丰富。

测试矩阵:
  - 便宜阈值: 0.15, 0.20 (什么算"便宜")
  - 反弹阈值: 0.30, 0.35, 0.40, 0.45, 0.50, 1.00(=结算赢，对照)
  - 滚动窗口: W=5, 7, 10
  - Whale阈值: T=0.20, 0.25, 0.30, 0.35, 0.40
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
V2_DIR    = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\four_bucket_v2'
OUT_DIR   = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\dipbuy_relaxed'
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50

# ═══════════════════════════════════════════════════════════
# PART 1: 加载已有数据
# ═══════════════════════════════════════════════════════════
existing = pd.read_csv(EXIST_CSV)
N = len(existing)
print(f'Loaded {N} rounds from existing CSV')

settlement   = existing['f_settlement'].values
up_mid_250   = existing['f_up_mid_250'].values.astype(float)
round_ids    = existing['round_id'].values
hours        = existing['round_id'].str.extract(r'_(\d{2})-\d{2}-\d{2}')[0].astype(int).values
dates        = existing['round_id'].str.extract(r'^(\d{4}-\d{2}-\d{2})')[0].values
settlement_map = dict(zip(round_ids, settlement))

# 动量数据 (δ=0.05)
b_traded = existing['B_traded'].values.astype(bool)
b_side   = existing['B_side'].values
b_entry  = existing['B_entry'].values.astype(float)

# 原始DipBuy对照
c_traded = existing['C_traded'].values.astype(bool)
c_pnl    = existing['C_pnl'].values.astype(float)
dip_won_orig = np.where(c_traded, (c_pnl > 0).astype(float), np.nan)

# ═══════════════════════════════════════════════════════════
# PART 2: 扫描原始CSV，提取便宜端反弹数据
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 扫描原始CSV: 提取便宜端反弹数据')
print('='*70)

import pickle
CACHE_PROBE = os.path.join(OUT_DIR, '_cache_probe.pkl')
CACHE_GRID  = os.path.join(OUT_DIR, '_cache_grid.pkl')

files = sorted(glob.glob(os.path.join(DATA_DIR, '*.csv')))
print(f'Found {len(files)} CSV files')

# 对每盘记录:
# - 哪个side出现了便宜ask
# - 便宜ask的最低价
# - 便宜ask出现后该side的最高bid (反弹高度)
# - 是否赢了结算

probe_data = {}  # round_id -> dict

def extract_probe_info(fpath):
    """从原始CSV提取便宜端反弹信息"""
    try:
        df = pd.read_csv(fpath)
        if len(df) < 20:
            return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        df['elapsed'] = (ts - ts.iloc[0]).dt.total_seconds()
        if df['elapsed'].max() < 200:
            return None
        
        rid = os.path.basename(fpath).replace('.csv', '')
        
        for c in ['up_best_bid','up_best_ask','down_best_bid','down_best_ask']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce').ffill()
        
        result = {'round_id': rid, 'events': []}
        
        for side in ['up', 'down']:
            ask_c = f'{side}_best_ask'
            bid_c = f'{side}_best_bid'
            
            if ask_c not in df.columns or bid_c not in df.columns:
                continue
            
            asks = df[ask_c].dropna()
            if len(asks) == 0:
                continue
            
            # 找最便宜的ask
            valid_asks = asks[(asks > 0) & (asks < 1.0)]
            if len(valid_asks) == 0:
                continue
            
            min_ask = valid_asks.min()
            min_ask_idx = valid_asks.idxmin()
            min_ask_time = df.loc[min_ask_idx, 'elapsed']
            
            # 便宜ask之后的最高bid
            after_df = df[df.index >= min_ask_idx]
            bids_after = after_df[bid_c].dropna()
            bids_after = bids_after[(bids_after > 0) & (bids_after <= 1.0)]
            
            max_bid_after = bids_after.max() if len(bids_after) > 0 else 0.0
            
            # 整盘最高bid
            all_bids = df[bid_c].dropna()
            all_bids = all_bids[(all_bids > 0) & (all_bids <= 1.0)]
            max_bid_all = all_bids.max() if len(all_bids) > 0 else 0.0
            
            # 结算
            settle_side = settlement_map.get(rid, None)
            won_settle = (settle_side == side) if settle_side else False
            
            result['events'].append({
                'side': side,
                'min_ask': float(min_ask),
                'min_ask_time': float(min_ask_time),
                'max_bid_after': float(max_bid_after),
                'max_bid_all': float(max_bid_all),
                'won_settle': bool(won_settle),
            })
        
        return result
    except:
        return None

t0 = time.time()
if os.path.exists(CACHE_PROBE):
    print('  Loading probe cache...')
    with open(CACHE_PROBE, 'rb') as f:
        probe_data = pickle.load(f)
    print(f'  Loaded {len(probe_data)} rounds from cache')
else:
    for i, fpath in enumerate(files):
        res = extract_probe_info(fpath)
        if res is not None:
            probe_data[res['round_id']] = res
        if (i+1) % 1000 == 0:
            print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')
    print(f'\nExtracted probe data: {len(probe_data)} rounds in {time.time()-t0:.0f}s')
    with open(CACHE_PROBE, 'wb') as f:
        pickle.dump(probe_data, f)
    print('  Saved probe cache')

# ═══════════════════════════════════════════════════════════
# PART 3: 构建多种探针事件定义
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 构建探针事件矩阵')
print('='*70)

# 便宜阈值: 什么价格算"便宜"
cheap_thresholds = [0.15, 0.20, 0.25]

# 反弹阈值: 反弹到什么价格算"大逆转"
bounce_thresholds = [0.30, 0.35, 0.40, 0.45, 0.50, 1.00]
# 1.00 = 结算赢 (对照组)

# 对每种 (cheap_thr, bounce_thr) 组合，生成事件序列
# event[i] = NaN (没有便宜ask出现), 1.0 (出现了且反弹达标), 0.0 (出现了但没达标)

event_matrix = {}

for cheap_thr in cheap_thresholds:
    for bounce_thr in bounce_thresholds:
        key = f'cheap{cheap_thr:.2f}_bounce{bounce_thr:.2f}'
        events = np.full(N, np.nan)
        
        for i, rid in enumerate(round_ids):
            if rid not in probe_data:
                continue
            
            pd_info = probe_data[rid]
            
            # 找便宜端事件
            for evt in pd_info['events']:
                if evt['min_ask'] > cheap_thr:
                    continue
                
                # 有便宜ask出现
                if bounce_thr >= 1.0:
                    # 对照: 结算赢
                    events[i] = 1.0 if evt['won_settle'] else 0.0
                else:
                    # 放松: 反弹到bounce_thr
                    events[i] = 1.0 if evt['max_bid_after'] >= bounce_thr else 0.0
                break  # 只取第一个便宜端
        
        n_events = np.sum(~np.isnan(events))
        n_wins = np.nansum(events)
        wr = n_wins / n_events * 100 if n_events > 0 else 0
        event_matrix[key] = events
        print(f'  {key}: {int(n_events)} events, {int(n_wins)} wins ({wr:.1f}%)')

# ═══════════════════════════════════════════════════════════
# PART 4: 重新跑网格 (新配置)
# ═══════════════════════════════════════════════════════════
print('\n重新计算网格 (buy≤0.18, sell≥0.26, 0-94/0-190, 50shares)...')

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

def grid_v2(df_raw, settle_side):
    for side in ['up', 'down']:
        ac = f'{side}_best_ask'
        bc = f'{side}_best_bid'
        entry_df = df_raw[(df_raw['elapsed'] >= 0) & (df_raw['elapsed'] <= 94)]
        cheap = entry_df[(entry_df[ac].fillna(999) > 0) & (entry_df[ac].fillna(999) <= 0.18)]
        if len(cheap) == 0: continue
        ep = float(cheap.iloc[0][ac])
        et = cheap.iloc[0]['elapsed']
        sell_df = df_raw[(df_raw['elapsed'] > et) & (df_raw['elapsed'] <= 190)]
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

t0 = time.time()
grid_map = {}
if os.path.exists(CACHE_GRID):
    print('  Loading grid cache...')
    with open(CACHE_GRID, 'rb') as f:
        grid_map = pickle.load(f)
    print(f'  Loaded {len(grid_map)} rounds from cache')
else:
    for i, fpath in enumerate(files):
        res = load_raw(fpath)
        if res is None: continue
        rid, df_raw = res
        if rid not in settlement_map: continue
        traded, pnl, typ = grid_v2(df_raw, settlement_map[rid])
        grid_map[rid] = (traded, pnl, typ)
        if (i+1) % 1000 == 0:
            print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')
    print(f'Grid done: {len(grid_map)} rounds in {time.time()-t0:.0f}s')
    with open(CACHE_GRID, 'wb') as f:
        pickle.dump(grid_map, f)
    print('  Saved grid cache')

grid_traded = np.zeros(N, dtype=bool)
grid_pnl = np.zeros(N)
for i, rid in enumerate(round_ids):
    if rid in grid_map:
        t, p, typ = grid_map[rid]
        grid_traded[i] = t
        grid_pnl[i] = p

gt = grid_traded.sum()
print(f'Grid: {gt} trades ({gt/N*100:.1f}%), PnL={grid_pnl.sum():.1f}')

# 动量PnL (δ=0.05)
mom_pnl = np.zeros(N)
mom_won = np.full(N, np.nan)
fade_pnl = np.full(N, np.nan)

for i in range(N):
    if not b_traded[i]: continue
    m = up_mid_250[i]
    if np.isnan(m): continue
    if m > 0.55: s = 'up'
    elif m < 0.45: s = 'down'
    else: continue
    
    if b_side[i] != s: continue
    ep = b_entry[i]
    if np.isnan(ep) or ep <= 0 or ep >= 0.95: continue
    
    if settlement[i] == s:
        mom_pnl[i] = (1.0 - ep) * SHARES
        fade_pnl[i] = -(1.0 - ep) * SHARES
        mom_won[i] = 1
    else:
        mom_pnl[i] = -ep * SHARES
        fade_pnl[i] = ep * SHARES
        mom_won[i] = 0

mom_traded = b_traded & ~np.isnan(mom_won)

# ═══════════════════════════════════════════════════════════
# PART 5: 全配置4桶测试
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 全配置测试: cheap × bounce × W × T')
print('='*70)

def rolling_wr(events, window):
    """滚动胜率: 最近window个事件的WR"""
    result = np.full(N, np.nan)
    for i in range(N):
        count = 0; wins = 0
        for j in range(i-1, -1, -1):
            if np.isnan(events[j]): continue
            count += 1
            wins += events[j]
            if count >= window: break
        if count >= window:
            result[i] = wins / count
    return result

def max_drawdown(cum):
    peak = np.maximum.accumulate(cum)
    return (peak - cum).max()

def four_bucket_analysis(is_whale):
    """给定whale标记，计算4桶PnL和最优系统"""
    wh = is_whale
    nw = ~is_whale
    
    # B1: whale + fade
    b1_mask = wh & mom_traded
    b1_pnl = np.nansum(fade_pnl[b1_mask])
    b1_n = b1_mask.sum()
    
    # B3: normal + regular (grid + momentum)
    b3_grid = grid_pnl[nw & grid_traded].sum()
    b3_mom = np.nansum(mom_pnl[nw & mom_traded])
    b3_pnl = b3_grid + b3_mom
    
    # B4: whale + regular
    b4_grid = grid_pnl[wh & grid_traded].sum()
    b4_mom = np.nansum(mom_pnl[wh & mom_traded])
    b4_pnl = b4_grid + b4_mom
    
    optimal = b1_pnl + b3_pnl
    baseline = b3_pnl + b4_pnl
    
    # Equity curve
    opt_round = np.zeros(N)
    for i in range(N):
        g = grid_pnl[i]
        if is_whale[i]:
            opt_round[i] = (fade_pnl[i] if mom_traded[i] else 0)
        else:
            opt_round[i] = g + (mom_pnl[i] if mom_traded[i] else 0)
    
    opt_cum = np.cumsum(opt_round)
    opt_mdd = max_drawdown(opt_cum)
    opt_sharpe = opt_round.mean() / (opt_round.std()+1e-8) * np.sqrt(252*24*12)
    
    return {
        'whale_n': wh.sum(),
        'whale_pct': wh.mean()*100,
        'b1_pnl': b1_pnl, 'b1_n': b1_n,
        'b3_pnl': b3_pnl, 'b4_pnl': b4_pnl,
        'optimal': optimal, 'baseline': baseline,
        'improvement': optimal - baseline,
        'mdd': opt_mdd, 'sharpe': opt_sharpe,
        'equity': opt_cum,
    }

# 测试所有配置
windows = [5, 7, 10]
whale_thresholds = [0.20, 0.25, 0.30, 0.35, 0.40]

all_results = []

for cheap_thr in cheap_thresholds:
    for bounce_thr in bounce_thresholds:
        key = f'cheap{cheap_thr:.2f}_bounce{bounce_thr:.2f}'
        events = event_matrix[key]
        n_events = np.sum(~np.isnan(events))
        base_wr = np.nanmean(events) * 100 if n_events > 0 else 0
        
        for W in windows:
            rwr = rolling_wr(events, W)
            
            for T in whale_thresholds:
                is_whale = (rwr >= T)
                is_whale[np.isnan(rwr)] = False
                n_whale = is_whale.sum()
                
                if n_whale < 10 or n_whale > N * 0.60:
                    continue
                
                res = four_bucket_analysis(is_whale)
                
                all_results.append({
                    'cheap_thr': cheap_thr,
                    'bounce_thr': bounce_thr,
                    'bounce_label': 'settle_win' if bounce_thr >= 1.0 else f'≥{bounce_thr:.2f}',
                    'W': W, 'T': T,
                    'n_events': n_events,
                    'base_wr': base_wr,
                    'whale_n': res['whale_n'],
                    'whale_pct': res['whale_pct'],
                    'b1_pnl': res['b1_pnl'],
                    'b3_pnl': res['b3_pnl'],
                    'b4_pnl': res['b4_pnl'],
                    'optimal': res['optimal'],
                    'baseline': res['baseline'],
                    'improvement': res['improvement'],
                    'mdd': res['mdd'],
                    'sharpe': res['sharpe'],
                })

print(f'\nTested {len(all_results)} configurations')

# ═══════════════════════════════════════════════════════════
# PART 6: 结果分析
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 结果排名')
print('='*70)

df_res = pd.DataFrame(all_results)
df_res.to_csv(os.path.join(OUT_DIR, 'all_configs.csv'), index=False)

# Top 20 by optimal PnL
top20 = df_res.nlargest(20, 'optimal')
print('\n─── TOP 20 by Optimal PnL ───')
print(f'{"cheap":>5} {"bounce":>10} {"W":>3} {"T":>5} {"#evt":>5} {"baseWR":>6} {"#whale":>6} {"whale%":>6} {"B1":>8} {"Opt":>8} {"Impr":>8} {"MDD":>6} {"Sharpe":>7}')
for r in top20.to_dict('records'):
    print(f'{r["cheap_thr"]:>5.2f} {r["bounce_label"]:>10} {int(r["W"]):>3} {r["T"]:>5.2f} '
          f'{int(r["n_events"]):>5} {r["base_wr"]:>5.1f}% {int(r["whale_n"]):>6} {r["whale_pct"]:>5.1f}% '
          f'{r["b1_pnl"]:>+8.1f} {r["optimal"]:>+8.1f} {r["improvement"]:>+8.1f} {r["mdd"]:>6.0f} {r["sharpe"]:>7.2f}')

# 按bounce_thr分组的最佳配置
print('\n─── 每种反弹阈值的最佳配置 ───')
for bounce_thr in bounce_thresholds:
    label = 'settle_win' if bounce_thr >= 1.0 else f'≥{bounce_thr:.2f}'
    subset = df_res[df_res.bounce_thr == bounce_thr]
    if len(subset) == 0:
        print(f'  bounce={label}: 无有效配置')
        continue
    best = subset.loc[subset.optimal.idxmax()].to_dict()
    print(f'  bounce={label}: cheap≤{best["cheap_thr"]:.2f} W={int(best["W"])} T≥{best["T"]:.2f} '
          f'| events={int(best["n_events"])} baseWR={best["base_wr"]:.1f}% '
          f'| whale={int(best["whale_n"])}({best["whale_pct"]:.1f}%) '
          f'| Opt={best["optimal"]:+.0f} Impr={best["improvement"]:+.0f} MDD={best["mdd"]:.0f} Sharpe={best["sharpe"]:.2f}')

# 按cheap_thr分组
print('\n─── 每种便宜阈值的最佳配置 ───')
for cheap_thr in cheap_thresholds:
    subset = df_res[df_res.cheap_thr == cheap_thr]
    if len(subset) == 0: continue
    best = subset.loc[subset.optimal.idxmax()].to_dict()
    print(f'  cheap≤{cheap_thr:.2f}: bounce={best["bounce_label"]} W={int(best["W"])} T≥{best["T"]:.2f} '
          f'| Opt={best["optimal"]:+.0f} Impr={best["improvement"]:+.0f} Sharpe={best["sharpe"]:.2f}')

# 原始DipBuy对照 (cheap≤0.20, settle_win, W=7, T≥0.30)
orig_ref = df_res[(df_res.cheap_thr == 0.20) & (df_res.bounce_thr == 1.00) & 
                  (df_res.W == 7) & (df_res.T == 0.30)]
if len(orig_ref) > 0:
    orig = orig_ref.iloc[0].to_dict()
    print(f'\n★ 原始DipBuy对照 (cheap≤0.20, settle_win, W=7, T≥0.30):')
    print(f'  Opt={orig["optimal"]:+.0f} Impr={orig["improvement"]:+.0f} MDD={orig["mdd"]:.0f} Sharpe={orig["sharpe"]:.2f}')

# ═══════════════════════════════════════════════════════════
# PART 7: 图表
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 生成图表')
print('='*70)

# --- Chart 1: 各反弹阈值最佳配置的equity curve对比 ---
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# Chart 1a: Equity curves of best config per bounce threshold
ax = axes[0, 0]
colors = plt.cm.viridis(np.linspace(0, 0.9, len(bounce_thresholds)))

for idx, bounce_thr in enumerate(bounce_thresholds):
    label = 'settle_win' if bounce_thr >= 1.0 else f'≥{bounce_thr:.2f}'
    subset = df_res[df_res.bounce_thr == bounce_thr]
    if len(subset) == 0: continue
    best_row = subset.loc[subset.optimal.idxmax()].to_dict()
    
    # Reconstruct equity curve for this config
    key = f'cheap{best_row["cheap_thr"]:.2f}_bounce{bounce_thr:.2f}'
    events = event_matrix[key]
    rwr = rolling_wr(events, int(best_row["W"]))
    is_whale = (rwr >= best_row["T"])
    is_whale[np.isnan(rwr)] = False
    
    opt_round = np.zeros(N)
    for i in range(N):
        g = grid_pnl[i]
        if is_whale[i]:
            opt_round[i] = (fade_pnl[i] if mom_traded[i] else 0)
        else:
            opt_round[i] = g + (mom_pnl[i] if mom_traded[i] else 0)
    
    opt_cum = np.cumsum(opt_round)
    ax.plot(opt_cum, label=f'bounce{label} (c≤{best_row["cheap_thr"]:.2f},W={int(best_row["W"])},T≥{best_row["T"]:.2f})',
            color=colors[idx], alpha=0.8, linewidth=1.5)

ax.set_title('各反弹阈值最佳配置 - Equity Curve', fontsize=12, fontweight='bold')
ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3)
ax.axhline(0, color='black', linewidth=0.5)

# Chart 1b: Optimal PnL vs bounce threshold (grouped by cheap_thr)
ax = axes[0, 1]
for cheap_thr in cheap_thresholds:
    subset = df_res[df_res.cheap_thr == cheap_thr]
    if len(subset) == 0: continue
    # Best optimal per bounce_thr
    best_per_bounce = subset.groupby('bounce_thr')['optimal'].max()
    bounce_labels = [f'≥{b:.2f}' if b < 1.0 else 'settle' for b in best_per_bounce.index]
    ax.plot(range(len(best_per_bounce)), best_per_bounce.values, 
            'o-', label=f'cheap≤{cheap_thr:.2f}', markersize=8, linewidth=2)

ax.set_xticks(range(len(bounce_thresholds)))
ax.set_xticklabels([f'≥{b:.2f}' if b < 1.0 else 'settle' for b in bounce_thresholds], rotation=45)
ax.set_title('反弹阈值 vs 最优PnL', fontsize=12, fontweight='bold')
ax.set_xlabel('反弹阈值 (Bounce Threshold)')
ax.set_ylabel('Optimal PnL ($)')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)

# Chart 1c: Whale detection rate vs bounce threshold
ax = axes[1, 0]
for cheap_thr in cheap_thresholds:
    subset = df_res[df_res.cheap_thr == cheap_thr]
    if len(subset) == 0: continue
    best_per_bounce = subset.loc[subset.groupby('bounce_thr')['optimal'].idxmax()]
    ax.plot(range(len(best_per_bounce)), best_per_bounce['whale_pct'].values,
            's-', label=f'cheap≤{cheap_thr:.2f}', markersize=8, linewidth=2)

ax.set_xticks(range(len(bounce_thresholds)))
ax.set_xticklabels([f'≥{b:.2f}' if b < 1.0 else 'settle' for b in bounce_thresholds], rotation=45)
ax.set_title('反弹阈值 vs Whale检出率', fontsize=12, fontweight='bold')
ax.set_xlabel('反弹阈值 (Bounce Threshold)')
ax.set_ylabel('Whale Period %')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)

# Chart 1d: Sharpe ratio comparison
ax = axes[1, 1]
for cheap_thr in cheap_thresholds:
    subset = df_res[df_res.cheap_thr == cheap_thr]
    if len(subset) == 0: continue
    best_per_bounce = subset.loc[subset.groupby('bounce_thr')['optimal'].idxmax()]
    ax.bar(np.arange(len(best_per_bounce)) + cheap_thresholds.index(cheap_thr)*0.25,
           best_per_bounce['sharpe'].values, width=0.25,
           label=f'cheap≤{cheap_thr:.2f}', alpha=0.8)

ax.set_xticks(np.arange(len(bounce_thresholds)) + 0.25)
ax.set_xticklabels([f'≥{b:.2f}' if b < 1.0 else 'settle' for b in bounce_thresholds], rotation=45)
ax.set_title('反弹阈值 vs Sharpe Ratio', fontsize=12, fontweight='bold')
ax.set_xlabel('反弹阈值 (Bounce Threshold)')
ax.set_ylabel('Sharpe Ratio')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'bounce_threshold_comparison.png'), dpi=150)
plt.close()
print('  Saved bounce_threshold_comparison.png')

# --- Chart 2: 事件频率和基础WR ---
fig, axes = plt.subplots(1, 2, figsize=(14, 6))

# 事件数量
ax = axes[0]
for cheap_thr in cheap_thresholds:
    n_events_per_bounce = []
    for bounce_thr in bounce_thresholds:
        key = f'cheap{cheap_thr:.2f}_bounce{bounce_thr:.2f}'
        events = event_matrix[key]
        n_events_per_bounce.append(np.sum(~np.isnan(events)))
    ax.plot(range(len(bounce_thresholds)), n_events_per_bounce,
            'o-', label=f'cheap≤{cheap_thr:.2f}', markersize=8, linewidth=2)

ax.set_xticks(range(len(bounce_thresholds)))
ax.set_xticklabels([f'≥{b:.2f}' if b < 1.0 else 'settle' for b in bounce_thresholds], rotation=45)
ax.set_title('探针事件数量', fontsize=12, fontweight='bold')
ax.set_xlabel('反弹阈值')
ax.set_ylabel('事件数量')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)

# 基础WR
ax = axes[1]
for cheap_thr in cheap_thresholds:
    wr_per_bounce = []
    for bounce_thr in bounce_thresholds:
        key = f'cheap{cheap_thr:.2f}_bounce{bounce_thr:.2f}'
        events = event_matrix[key]
        wr = np.nanmean(events) * 100 if np.sum(~np.isnan(events)) > 0 else 0
        wr_per_bounce.append(wr)
    ax.plot(range(len(bounce_thresholds)), wr_per_bounce,
            's-', label=f'cheap≤{cheap_thr:.2f}', markersize=8, linewidth=2)

ax.set_xticks(range(len(bounce_thresholds)))
ax.set_xticklabels([f'≥{b:.2f}' if b < 1.0 else 'settle' for b in bounce_thresholds], rotation=45)
ax.set_title('探针事件基础胜率', fontsize=12, fontweight='bold')
ax.set_xlabel('反弹阈值')
ax.set_ylabel('基础WR (%)')
ax.legend(fontsize=10)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'event_stats.png'), dpi=150)
plt.close()
print('  Saved event_stats.png')

# --- Chart 3: Heatmap - Optimal PnL for cheap=0.20 across (bounce, W, T) ---
fig, axes = plt.subplots(1, 3, figsize=(18, 6))

for w_idx, W in enumerate(windows):
    ax = axes[w_idx]
    subset = df_res[(df_res.cheap_thr == 0.20) & (df_res.W == W)]
    
    if len(subset) == 0:
        ax.set_title(f'W={W}: 无数据')
        continue
    
    pivot = subset.pivot_table(index='bounce_thr', columns='T', values='optimal', aggfunc='max')
    
    im = ax.imshow(pivot.values, aspect='auto', cmap='RdYlGn',
                   vmin=pivot.values.min(), vmax=pivot.values.max())
    
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f'{t:.2f}' for t in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f'≥{b:.2f}' if b < 1.0 else 'settle' for b in pivot.index])
    
    for r in range(len(pivot.index)):
        for c in range(len(pivot.columns)):
            val = pivot.values[r, c]
            if not np.isnan(val):
                ax.text(c, r, f'{val:.0f}', ha='center', va='center', fontsize=8,
                       color='white' if val < pivot.values.mean() else 'black')
    
    ax.set_title(f'Optimal PnL (cheap≤0.20, W={W})', fontsize=11, fontweight='bold')
    ax.set_xlabel('Whale阈值 (T)')
    ax.set_ylabel('反弹阈值')
    plt.colorbar(im, ax=ax, shrink=0.8)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'heatmap_optimal.png'), dpi=150)
plt.close()
print('  Saved heatmap_optimal.png')

# --- Chart 4: Top5配置 equity curve ---
fig, ax = plt.subplots(figsize=(14, 7))
top5 = df_res.nlargest(5, 'optimal')
colors_top = ['#e74c3c', '#2ecc71', '#3498db', '#f39c12', '#9b59b6']

for idx, row in enumerate(top5.to_dict('records')):
    key = f'cheap{row["cheap_thr"]:.2f}_bounce{row["bounce_thr"]:.2f}'
    events = event_matrix[key]
    rwr = rolling_wr(events, int(row["W"]))
    is_whale = (rwr >= row["T"])
    is_whale[np.isnan(rwr)] = False
    
    opt_round = np.zeros(N)
    for i in range(N):
        g = grid_pnl[i]
        if is_whale[i]:
            opt_round[i] = (fade_pnl[i] if mom_traded[i] else 0)
        else:
            opt_round[i] = g + (mom_pnl[i] if mom_traded[i] else 0)
    
    opt_cum = np.cumsum(opt_round)
    label = f'#{idx+1}: c≤{row["cheap_thr"]:.2f} b{row["bounce_label"]} W={int(row["W"])} T≥{row["T"]:.2f} (${row["optimal"]:+.0f})'
    ax.plot(opt_cum, label=label, color=colors_top[idx], linewidth=2 if idx == 0 else 1.2, 
            alpha=1.0 if idx == 0 else 0.7)

# 也画baseline
base_round = np.zeros(N)
for i in range(N):
    base_round[i] = grid_pnl[i] + (mom_pnl[i] if mom_traded[i] else 0)
base_cum = np.cumsum(base_round)
ax.plot(base_cum, label=f'无信号基线 (${base_cum[-1]:+.0f})', color='gray', linewidth=1, linestyle='--', alpha=0.6)

ax.set_title('TOP 5 配置 Equity Curve 对比', fontsize=14, fontweight='bold')
ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.legend(fontsize=9, loc='upper left')
ax.grid(True, alpha=0.3)
ax.axhline(0, color='black', linewidth=0.5)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'top5_equity.png'), dpi=150)
plt.close()
print('  Saved top5_equity.png')

# --- Chart 5: 反弹分布直方图 ---
fig, axes = plt.subplots(1, len(cheap_thresholds), figsize=(6*len(cheap_thresholds), 5))
if len(cheap_thresholds) == 1:
    axes = [axes]

for idx, cheap_thr in enumerate(cheap_thresholds):
    ax = axes[idx]
    bounces = []
    
    for i, rid in enumerate(round_ids):
        if rid not in probe_data: continue
        for evt in probe_data[rid]['events']:
            if evt['min_ask'] <= cheap_thr:
                bounces.append(evt['max_bid_after'])
                break
    
    if len(bounces) > 0:
        ax.hist(bounces, bins=50, edgecolor='black', alpha=0.7, color='steelblue')
        ax.axvline(0.40, color='red', linestyle='--', linewidth=2, label='0.40阈值')
        ax.axvline(0.50, color='orange', linestyle='--', linewidth=2, label='0.50阈值')
        
        pct_above_40 = sum(1 for b in bounces if b >= 0.40) / len(bounces) * 100
        pct_above_50 = sum(1 for b in bounces if b >= 0.50) / len(bounces) * 100
        ax.set_title(f'cheap≤{cheap_thr:.2f}: 反弹分布 (n={len(bounces)})\n'
                     f'≥0.40: {pct_above_40:.1f}% | ≥0.50: {pct_above_50:.1f}%',
                     fontsize=11, fontweight='bold')
    else:
        ax.set_title(f'cheap≤{cheap_thr:.2f}: 无数据')
    
    ax.set_xlabel('最大反弹价格 (Max Bid After)')
    ax.set_ylabel('频次')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'bounce_distribution.png'), dpi=150)
plt.close()
print('  Saved bounce_distribution.png')

print('\n' + '='*70)
print(' 完成！所有结果保存到: ' + OUT_DIR)
print('='*70)
