"""
═══════════════════════════════════════════════════════════════
 DipBuy 放松探针 V2 — 尾盘窗口 + 放松反弹定义
═══════════════════════════════════════════════════════════════

修正V1的bug: V1在整盘找便宜ask(99.9%触发), 正确做法是只看尾盘(t=240-285s)

核心改进:
  原始: 尾盘ask≤$0.20 → 该方赢了结算($1.00) → 记WIN
  放松: 尾盘ask≤$0.20 → 该方bid反弹到≥X → 记WIN
  
  X = 0.30, 0.35, 0.40, 0.45, 0.50, 1.00(结算赢=对照)

测试矩阵:
  - 便宜阈值: 0.15, 0.20, 0.25
  - 反弹阈值: 0.30, 0.35, 0.40, 0.45, 0.50, 1.00
  - 观测窗口: tail(240-285s), mid(150-240s), early(0-94s), full(0-285s)
  - 滚动窗口: W=5, 7, 10
  - Whale阈值: T=0.20, 0.25, 0.30, 0.35, 0.40
"""

import pandas as pd, numpy as np, glob, os, time, pickle, warnings
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
matplotlib.rcParams['font.family'] = ['SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False
warnings.filterwarnings('ignore')

DATA_DIR  = r'C:\Users\ZHAOKAI\data'
EXIST_CSV = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies_fixed\three_strategies_fixed.csv'
OUT_DIR   = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\dipbuy_relaxed'
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50
CACHE_PROBE2 = os.path.join(OUT_DIR, '_cache_probe_v2.pkl')
CACHE_GRID   = os.path.join(OUT_DIR, '_cache_grid.pkl')

# ═══════════════════════════════════════════════════════════
# PART 1: 加载已有数据
# ═══════════════════════════════════════════════════════════
existing = pd.read_csv(EXIST_CSV)
N = len(existing)
print(f'Loaded {N} rounds')

settlement = existing['f_settlement'].values
up_mid_250 = existing['f_up_mid_250'].values.astype(float)
round_ids  = existing['round_id'].values
hours      = existing['round_id'].str.extract(r'_(\d{2})-\d{2}-\d{2}')[0].astype(int).values
dates      = existing['round_id'].str.extract(r'^(\d{4}-\d{2}-\d{2})')[0].values
settlement_map = dict(zip(round_ids, settlement))

# 动量数据
b_traded = existing['B_traded'].values.astype(bool)
b_side   = existing['B_side'].values
b_entry  = existing['B_entry'].values.astype(float)

# 原始DipBuy (C策略) 作为对照
c_traded = existing['C_traded'].values.astype(bool)
c_pnl    = existing['C_pnl'].values.astype(float)
c_side   = existing['C_side'].values

print(f'Original DipBuy C: {c_traded.sum()} traded ({c_traded.mean()*100:.1f}%), '
      f'WR={(c_pnl[c_traded]>0).mean()*100:.1f}%')

# ═══════════════════════════════════════════════════════════
# PART 2: 扫描原始CSV — 提取多时间窗口的便宜端反弹数据
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 扫描原始CSV: 按时间窗口提取便宜端反弹')
print('='*70)

files = sorted(glob.glob(os.path.join(DATA_DIR, '*.csv')))
print(f'Found {len(files)} CSV files')

# 时间窗口定义
WINDOWS = {
    'tail':  (240, 285),   # 原始DipBuy窗口
    'mid':   (150, 240),   # 中盘
    'early': (0, 94),      # 早盘(=网格窗口)
    'full':  (0, 285),     # 全盘
}

def extract_probe_v2(fpath):
    """提取多时间窗口的便宜端数据"""
    try:
        df = pd.read_csv(fpath)
        if len(df) < 20: return None
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        df['elapsed'] = (ts - ts.iloc[0]).dt.total_seconds()
        if df['elapsed'].max() < 200: return None
        
        rid = os.path.basename(fpath).replace('.csv', '')
        
        for c in ['up_best_bid','up_best_ask','down_best_bid','down_best_ask']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce').ffill()
        
        settle_side = settlement_map.get(rid, None)
        result = {'round_id': rid}
        
        for wname, (t_start, t_end) in WINDOWS.items():
            window_df = df[(df['elapsed'] >= t_start) & (df['elapsed'] <= t_end)]
            if len(window_df) == 0:
                result[wname] = None
                continue
            
            wdata = []
            for side in ['up', 'down']:
                ask_c = f'{side}_best_ask'
                bid_c = f'{side}_best_bid'
                
                if ask_c not in window_df.columns:
                    continue
                
                # 窗口内的最便宜ask
                asks = window_df[ask_c].dropna()
                valid = asks[(asks > 0) & (asks < 1.0)]
                if len(valid) == 0:
                    continue
                
                min_ask = valid.min()
                min_ask_idx = valid.idxmin()
                min_ask_time = df.loc[min_ask_idx, 'elapsed']
                
                # 便宜ask出现后，该side的最高bid（在整个剩余时间内）
                after_df = df[df.index >= min_ask_idx]
                bids_after = after_df[bid_c].dropna()
                bids_after = bids_after[(bids_after > 0) & (bids_after <= 1.0)]
                max_bid_after = float(bids_after.max()) if len(bids_after) > 0 else 0.0
                
                won_settle = (settle_side == side) if settle_side else False
                
                wdata.append({
                    'side': side,
                    'min_ask': float(min_ask),
                    'min_ask_time': float(min_ask_time),
                    'max_bid_after': max_bid_after,
                    'won_settle': bool(won_settle),
                })
            
            result[wname] = wdata
        
        return result
    except:
        return None

t0 = time.time()
probe_data = {}

if os.path.exists(CACHE_PROBE2):
    print('  Loading probe v2 cache...')
    with open(CACHE_PROBE2, 'rb') as f:
        probe_data = pickle.load(f)
    print(f'  Loaded {len(probe_data)} rounds from cache')
else:
    for i, fpath in enumerate(files):
        res = extract_probe_v2(fpath)
        if res is not None:
            probe_data[res['round_id']] = res
        if (i+1) % 1000 == 0:
            print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')
    print(f'\nExtracted: {len(probe_data)} rounds in {time.time()-t0:.0f}s')
    with open(CACHE_PROBE2, 'wb') as f:
        pickle.dump(probe_data, f)
    print('  Saved probe v2 cache')

# ═══════════════════════════════════════════════════════════
# PART 3: 构建事件矩阵 (窗口 × 便宜阈值 × 反弹阈值)
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 构建事件矩阵')
print('='*70)

cheap_thresholds  = [0.15, 0.20, 0.25]
bounce_thresholds = [0.30, 0.35, 0.40, 0.45, 0.50, 1.00]

event_matrix = {}

for wname in WINDOWS:
    for cheap_thr in cheap_thresholds:
        for bounce_thr in bounce_thresholds:
            key = f'{wname}_c{cheap_thr:.2f}_b{bounce_thr:.2f}'
            events = np.full(N, np.nan)
            
            for i, rid in enumerate(round_ids):
                if rid not in probe_data:
                    continue
                wdata = probe_data[rid].get(wname, None)
                if wdata is None:
                    continue
                
                for evt in wdata:
                    if evt['min_ask'] > cheap_thr:
                        continue
                    
                    if bounce_thr >= 1.0:
                        events[i] = 1.0 if evt['won_settle'] else 0.0
                    else:
                        events[i] = 1.0 if evt['max_bid_after'] >= bounce_thr else 0.0
                    break
            
            n_events = np.sum(~np.isnan(events))
            n_wins = np.nansum(events)
            wr = n_wins / n_events * 100 if n_events > 0 else 0
            event_matrix[key] = events
            
            if wname == 'tail':
                print(f'  {key}: {int(n_events)} events, {int(n_wins)} wins ({wr:.1f}%)')

# 也打印其他窗口的概要
for wname in ['mid', 'early', 'full']:
    key_sample = f'{wname}_c0.20_b0.40'
    ev = event_matrix[key_sample]
    n = np.sum(~np.isnan(ev))
    w = np.nansum(ev)
    wr = w / n * 100 if n > 0 else 0
    print(f'  {wname} (c≤0.20, b≥0.40): {int(n)} events, {int(w)} wins ({wr:.1f}%)')

# ═══════════════════════════════════════════════════════════
# PART 4: 网格 (从缓存加载)
# ═══════════════════════════════════════════════════════════
print('\n网格加载...')

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
            return True, (0.26 - ep) * SHARES
        if len(sell_df) > 0:
            lb = sell_df[bc].dropna()
            if len(lb) > 0:
                return True, (float(lb.iloc[-1]) - ep) * SHARES
        xp = 1.0 if settle_side == side else 0.0
        return True, (xp - ep) * SHARES
    return False, 0.0

grid_map = {}
if os.path.exists(CACHE_GRID):
    with open(CACHE_GRID, 'rb') as f:
        grid_map = pickle.load(f)
    print(f'  Loaded {len(grid_map)} rounds from grid cache')
else:
    t0 = time.time()
    for i, fpath in enumerate(files):
        res = load_raw(fpath)
        if res is None: continue
        rid, df_raw = res
        if rid not in settlement_map: continue
        traded, pnl = grid_v2(df_raw, settlement_map[rid])
        grid_map[rid] = (traded, pnl, None)
        if (i+1) % 1000 == 0:
            print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')
    with open(CACHE_GRID, 'wb') as f:
        pickle.dump(grid_map, f)

grid_traded = np.zeros(N, dtype=bool)
grid_pnl = np.zeros(N)
for i, rid in enumerate(round_ids):
    if rid in grid_map:
        t, p = grid_map[rid][0], grid_map[rid][1]
        grid_traded[i] = t
        grid_pnl[i] = p

print(f'Grid: {grid_traded.sum()} trades, PnL={grid_pnl.sum():.1f}')

# 动量PnL
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
print(f'Momentum: {mom_traded.sum()} trades, PnL={mom_pnl.sum():.1f}')

# ═══════════════════════════════════════════════════════════
# PART 5: 全配置四桶测试
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 全配置四桶测试')
print('='*70)

def rolling_wr(events, window):
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

def four_bucket(is_whale):
    wh = is_whale; nw = ~is_whale
    b1_mask = wh & mom_traded
    b1_pnl = np.nansum(fade_pnl[b1_mask])
    b1_n = b1_mask.sum()
    
    b3_pnl = grid_pnl[nw & grid_traded].sum() + np.nansum(mom_pnl[nw & mom_traded])
    b4_pnl = grid_pnl[wh & grid_traded].sum() + np.nansum(mom_pnl[wh & mom_traded])
    
    optimal = b1_pnl + b3_pnl
    baseline = b3_pnl + b4_pnl
    
    opt_round = np.zeros(N)
    for i in range(N):
        if is_whale[i]:
            opt_round[i] = fade_pnl[i] if mom_traded[i] else 0
        else:
            opt_round[i] = grid_pnl[i] + (mom_pnl[i] if mom_traded[i] else 0)
    
    opt_cum = np.cumsum(opt_round)
    mdd = max_drawdown(opt_cum)
    sharpe = opt_round.mean() / (opt_round.std()+1e-8) * np.sqrt(252*24*12)
    
    return {
        'whale_n': int(wh.sum()), 'whale_pct': wh.mean()*100,
        'b1_pnl': b1_pnl, 'b1_n': b1_n,
        'b3_pnl': b3_pnl, 'b4_pnl': b4_pnl,
        'optimal': optimal, 'baseline': baseline,
        'improvement': optimal - baseline,
        'mdd': mdd, 'sharpe': sharpe,
        'equity': opt_cum,
    }

windows_W = [5, 7, 10]
whale_T   = [0.20, 0.25, 0.30, 0.35, 0.40]

all_results = []

for wname in WINDOWS:
    for cheap_thr in cheap_thresholds:
        for bounce_thr in bounce_thresholds:
            key = f'{wname}_c{cheap_thr:.2f}_b{bounce_thr:.2f}'
            events = event_matrix[key]
            n_events = int(np.sum(~np.isnan(events)))
            if n_events < 50:
                continue
            base_wr = np.nanmean(events) * 100
            
            for W in windows_W:
                rwr = rolling_wr(events, W)
                
                for T in whale_T:
                    is_whale = (rwr >= T)
                    is_whale[np.isnan(rwr)] = False
                    n_whale = is_whale.sum()
                    
                    if n_whale < 10 or n_whale > N * 0.60:
                        continue
                    
                    res = four_bucket(is_whale)
                    
                    all_results.append({
                        'window': wname,
                        'cheap_thr': cheap_thr,
                        'bounce_thr': bounce_thr,
                        'bounce_label': 'settle' if bounce_thr >= 1.0 else f'≥{bounce_thr:.2f}',
                        'W': W, 'T': T,
                        'n_events': n_events,
                        'base_wr': base_wr,
                        **res,
                    })

print(f'Tested {len(all_results)} configurations')

df_res = pd.DataFrame(all_results)
df_res.to_csv(os.path.join(OUT_DIR, 'all_configs_v2.csv'), index=False)

# ═══════════════════════════════════════════════════════════
# PART 6: 结果分析
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 结果排名')
print('='*70)

# === TOP 20 overall ===
top20 = df_res.nlargest(20, 'optimal')
print(f'\n{"win":>4} {"cheap":>5} {"bounce":>7} {"W":>2} {"T":>4} {"#evt":>5} {"bWR":>5} {"#wh":>5} {"wh%":>5} {"B1":>7} {"Opt":>7} {"Impr":>7} {"MDD":>5} {"SR":>5}')
for r in top20.to_dict('records'):
    print(f'{r["window"]:>4} {r["cheap_thr"]:>5.2f} {r["bounce_label"]:>7} {r["W"]:>2} {r["T"]:>4.2f} '
          f'{r["n_events"]:>5} {r["base_wr"]:>4.1f}% {r["whale_n"]:>5} {r["whale_pct"]:>4.1f}% '
          f'{r["b1_pnl"]:>+7.0f} {r["optimal"]:>+7.0f} {r["improvement"]:>+7.0f} {r["mdd"]:>5.0f} {r["sharpe"]:>5.2f}')

# === 按窗口分组最佳 ===
print('\n─── 每个时间窗口的最佳配置 ───')
for wname in WINDOWS:
    subset = df_res[df_res.window == wname]
    if len(subset) == 0:
        print(f'  {wname}: 无有效配置')
        continue
    best = subset.loc[subset.optimal.idxmax()].to_dict()
    print(f'  {wname:>5} ({WINDOWS[wname][0]}-{WINDOWS[wname][1]}s): '
          f'c≤{best["cheap_thr"]:.2f} b{best["bounce_label"]} W={best["W"]} T≥{best["T"]:.2f} '
          f'| #evt={best["n_events"]} bWR={best["base_wr"]:.1f}% '
          f'| whale={best["whale_n"]}({best["whale_pct"]:.1f}%) '
          f'| Opt={best["optimal"]:+.0f} Impr={best["improvement"]:+.0f} MDD={best["mdd"]:.0f} SR={best["sharpe"]:.2f}')

# === 仅tail窗口: 按bounce_thr分组 ===
print('\n─── TAIL窗口(240-285s): 各反弹阈值最佳 ───')
tail_res = df_res[df_res.window == 'tail']
for bounce_thr in bounce_thresholds:
    label = 'settle' if bounce_thr >= 1.0 else f'≥{bounce_thr:.2f}'
    subset = tail_res[tail_res.bounce_thr == bounce_thr]
    if len(subset) == 0:
        print(f'  bounce={label}: 无有效配置')
        continue
    best = subset.loc[subset.optimal.idxmax()].to_dict()
    print(f'  bounce={label}: c≤{best["cheap_thr"]:.2f} W={best["W"]} T≥{best["T"]:.2f} '
          f'| #evt={best["n_events"]} bWR={best["base_wr"]:.1f}% '
          f'| whale={best["whale_n"]}({best["whale_pct"]:.1f}%) '
          f'| Opt={best["optimal"]:+.0f} Impr={best["improvement"]:+.0f} MDD={best["mdd"]:.0f} SR={best["sharpe"]:.2f}')

# ═══════════════════════════════════════════════════════════
# PART 7: 图表
# ═══════════════════════════════════════════════════════════
print('\n' + '='*70)
print(' 生成图表')
print('='*70)

# --- Chart 1: 4窗口最佳equity curve对比 ---
fig, ax = plt.subplots(figsize=(14, 7))
wcolors = {'tail': '#e74c3c', 'mid': '#2ecc71', 'early': '#3498db', 'full': '#f39c12'}

for wname in WINDOWS:
    subset = df_res[df_res.window == wname]
    if len(subset) == 0: continue
    best = subset.loc[subset.optimal.idxmax()].to_dict()
    
    key = f'{wname}_c{best["cheap_thr"]:.2f}_b{best["bounce_thr"]:.2f}'
    events = event_matrix[key]
    rwr = rolling_wr(events, int(best["W"]))
    is_whale = (rwr >= best["T"])
    is_whale[np.isnan(rwr)] = False
    
    opt_round = np.zeros(N)
    for i in range(N):
        if is_whale[i]:
            opt_round[i] = fade_pnl[i] if mom_traded[i] else 0
        else:
            opt_round[i] = grid_pnl[i] + (mom_pnl[i] if mom_traded[i] else 0)
    
    cum = np.cumsum(opt_round)
    label = (f'{wname}({WINDOWS[wname][0]}-{WINDOWS[wname][1]}s) '
             f'c≤{best["cheap_thr"]:.2f} b{best["bounce_label"]} '
             f'W={best["W"]} T≥{best["T"]:.2f} (${best["optimal"]:+.0f})')
    ax.plot(cum, label=label, color=wcolors[wname], linewidth=2)

# Baseline
base_round = np.zeros(N)
for i in range(N):
    base_round[i] = grid_pnl[i] + (mom_pnl[i] if mom_traded[i] else 0)
base_cum = np.cumsum(base_round)
ax.plot(base_cum, label=f'无信号基线 (${base_cum[-1]:+.0f})', color='gray', linewidth=1.5, linestyle='--')

ax.set_title('各时间窗口最佳配置 — Equity Curve', fontsize=14, fontweight='bold')
ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.legend(fontsize=9, loc='upper left')
ax.grid(True, alpha=0.3)
ax.axhline(0, color='black', linewidth=0.5)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'window_comparison.png'), dpi=150)
plt.close()
print('  Saved window_comparison.png')

# --- Chart 2: TAIL窗口 bounce阈值对比 ---
fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# 2a: Equity curves per bounce threshold (tail only)
ax = axes[0, 0]
bcolors = plt.cm.plasma(np.linspace(0.1, 0.9, len(bounce_thresholds)))
for idx, bounce_thr in enumerate(bounce_thresholds):
    label = 'settle' if bounce_thr >= 1.0 else f'≥{bounce_thr:.2f}'
    subset = tail_res[tail_res.bounce_thr == bounce_thr]
    if len(subset) == 0: continue
    best = subset.loc[subset.optimal.idxmax()].to_dict()
    
    key = f'tail_c{best["cheap_thr"]:.2f}_b{bounce_thr:.2f}'
    events = event_matrix[key]
    rwr = rolling_wr(events, int(best["W"]))
    is_whale = (rwr >= best["T"])
    is_whale[np.isnan(rwr)] = False
    
    opt_round = np.zeros(N)
    for i in range(N):
        if is_whale[i]:
            opt_round[i] = fade_pnl[i] if mom_traded[i] else 0
        else:
            opt_round[i] = grid_pnl[i] + (mom_pnl[i] if mom_traded[i] else 0)
    
    cum = np.cumsum(opt_round)
    ax.plot(cum, label=f'bounce{label} (${best["optimal"]:+.0f})', color=bcolors[idx], linewidth=1.5)

ax.plot(base_cum, label='基线', color='gray', linewidth=1, linestyle='--')
ax.set_title('TAIL窗口: 各反弹阈值 Equity Curve', fontsize=12, fontweight='bold')
ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3)

# 2b: Optimal PnL bar chart
ax = axes[0, 1]
bounce_labels = []
opt_vals = []
impr_vals = []
for bounce_thr in bounce_thresholds:
    label = 'settle' if bounce_thr >= 1.0 else f'≥{bounce_thr:.2f}'
    subset = tail_res[tail_res.bounce_thr == bounce_thr]
    if len(subset) == 0:
        bounce_labels.append(label)
        opt_vals.append(0)
        impr_vals.append(0)
        continue
    best = subset.loc[subset.optimal.idxmax()].to_dict()
    bounce_labels.append(label)
    opt_vals.append(best['optimal'])
    impr_vals.append(best['improvement'])

x = np.arange(len(bounce_labels))
ax.bar(x - 0.15, opt_vals, 0.3, label='Optimal PnL', color='steelblue', alpha=0.8)
ax.bar(x + 0.15, impr_vals, 0.3, label='Improvement', color='coral', alpha=0.8)
ax.set_xticks(x)
ax.set_xticklabels(bounce_labels, rotation=45)
ax.set_title('TAIL窗口: 反弹阈值 vs PnL', fontsize=12, fontweight='bold')
ax.set_ylabel('PnL ($)')
ax.legend()
ax.grid(True, alpha=0.3)

# 2c: Events count & base WR
ax = axes[1, 0]
for cheap_thr in cheap_thresholds:
    wr_list = []
    for bounce_thr in bounce_thresholds:
        key = f'tail_c{cheap_thr:.2f}_b{bounce_thr:.2f}'
        ev = event_matrix[key]
        n = np.sum(~np.isnan(ev))
        wr = np.nanmean(ev) * 100 if n > 0 else 0
        wr_list.append(wr)
    ax.plot(range(len(bounce_thresholds)), wr_list, 'o-', label=f'cheap≤{cheap_thr:.2f}', linewidth=2)

ax.set_xticks(range(len(bounce_thresholds)))
ax.set_xticklabels(bounce_labels, rotation=45)
ax.set_title('TAIL窗口: 反弹阈值 vs 基础WR', fontsize=12, fontweight='bold')
ax.set_xlabel('反弹阈值')
ax.set_ylabel('Base WR (%)')
ax.legend()
ax.grid(True, alpha=0.3)

# 2d: Heatmap for tail, cheap=0.20
ax = axes[1, 1]
heat_data = tail_res[tail_res.cheap_thr == 0.20]
if len(heat_data) > 0:
    pivot = heat_data.pivot_table(index='bounce_thr', columns='T', values='optimal', aggfunc='max')
    im = ax.imshow(pivot.values, aspect='auto', cmap='RdYlGn')
    ax.set_xticks(range(len(pivot.columns)))
    ax.set_xticklabels([f'{t:.2f}' for t in pivot.columns])
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels([f'≥{b:.2f}' if b < 1.0 else 'settle' for b in pivot.index])
    for r in range(len(pivot.index)):
        for c in range(len(pivot.columns)):
            val = pivot.values[r, c]
            if not np.isnan(val):
                ax.text(c, r, f'{val:.0f}', ha='center', va='center', fontsize=9,
                       color='white' if val < np.nanmean(pivot.values) else 'black')
    plt.colorbar(im, ax=ax, shrink=0.8)
ax.set_title('TAIL c≤0.20: Optimal PnL 热力图', fontsize=12, fontweight='bold')
ax.set_xlabel('Whale阈值 (T)')
ax.set_ylabel('反弹阈值')

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'tail_bounce_analysis.png'), dpi=150)
plt.close()
print('  Saved tail_bounce_analysis.png')

# --- Chart 3: 反弹分布直方图 (TAIL窗口) ---
fig, axes = plt.subplots(1, 3, figsize=(18, 5))
for idx, cheap_thr in enumerate(cheap_thresholds):
    ax = axes[idx]
    bounces = []
    
    for rid in round_ids:
        if rid not in probe_data: continue
        wdata = probe_data[rid].get('tail', None)
        if wdata is None: continue
        for evt in wdata:
            if evt['min_ask'] <= cheap_thr:
                bounces.append(evt['max_bid_after'])
                break
    
    if len(bounces) > 0:
        ax.hist(bounces, bins=40, edgecolor='black', alpha=0.7, color='steelblue')
        for thr, clr in [(0.30, 'green'), (0.40, 'red'), (0.50, 'orange')]:
            pct = sum(1 for b in bounces if b >= thr) / len(bounces) * 100
            ax.axvline(thr, color=clr, linestyle='--', linewidth=2, label=f'≥{thr}: {pct:.1f}%')
        ax.set_title(f'TAIL cheap≤{cheap_thr:.2f}: 反弹分布 (n={len(bounces)})', fontsize=11, fontweight='bold')
    
    ax.set_xlabel('最大反弹价格')
    ax.set_ylabel('频次')
    ax.legend(fontsize=9)
    ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'tail_bounce_distribution.png'), dpi=150)
plt.close()
print('  Saved tail_bounce_distribution.png')

# --- Chart 4: TOP 5 overall equity curve ---
fig, ax = plt.subplots(figsize=(14, 7))
top5 = df_res.nlargest(5, 'optimal')
colors5 = ['#e74c3c', '#2ecc71', '#3498db', '#f39c12', '#9b59b6']

for idx, r in enumerate(top5.to_dict('records')):
    key = f'{r["window"]}_c{r["cheap_thr"]:.2f}_b{r["bounce_thr"]:.2f}'
    events = event_matrix[key]
    rwr = rolling_wr(events, int(r["W"]))
    is_whale = (rwr >= r["T"])
    is_whale[np.isnan(rwr)] = False
    
    opt_round = np.zeros(N)
    for i in range(N):
        if is_whale[i]:
            opt_round[i] = fade_pnl[i] if mom_traded[i] else 0
        else:
            opt_round[i] = grid_pnl[i] + (mom_pnl[i] if mom_traded[i] else 0)
    
    cum = np.cumsum(opt_round)
    label = f'#{idx+1}: {r["window"]} c≤{r["cheap_thr"]:.2f} b{r["bounce_label"]} W={r["W"]} T≥{r["T"]:.2f} (${r["optimal"]:+.0f})'
    ax.plot(cum, label=label, color=colors5[idx], linewidth=2 if idx == 0 else 1.2)

ax.plot(base_cum, label=f'基线 (${base_cum[-1]:+.0f})', color='gray', linewidth=1.5, linestyle='--')
ax.set_title('TOP 5 配置 Equity Curve', fontsize=14, fontweight='bold')
ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.legend(fontsize=9, loc='upper left')
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'top5_equity_v2.png'), dpi=150)
plt.close()
print('  Saved top5_equity_v2.png')

print('\n' + '='*70)
print(f' 完成！结果保存到: {OUT_DIR}')
print('='*70)
