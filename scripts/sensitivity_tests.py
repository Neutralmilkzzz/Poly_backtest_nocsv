"""
Sensitivity Tests around best mid-bounce 4-bucket configs.
Varies: cheap_thr, bounce_thr, shares, observation window edges.
"""
import pandas as pd, numpy as np, pickle, os, warnings
warnings.filterwarnings('ignore')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXIST_CSV = os.path.join(BASE, 'results', 'three_strategies_fixed', 'three_strategies_fixed.csv')
CACHE_PROBE = os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_probe_v2.pkl')
CACHE_GRID  = os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_grid.pkl')
DATA_DIR = r'C:\Users\ZHAOKAI\data'
OUT_DIR = os.path.join(BASE, 'results', 'mid_bounce_4bucket')
os.makedirs(OUT_DIR, exist_ok=True)

existing = pd.read_csv(EXIST_CSV)
N = len(existing)
settlement = existing['f_settlement'].values
up_mid_250 = existing['f_up_mid_250'].values.astype(float)
round_ids  = existing['round_id'].values

with open(CACHE_PROBE, 'rb') as f:
    probe_data = pickle.load(f)
with open(CACHE_GRID, 'rb') as f:
    grid_map = pickle.load(f)

# --- Momentum & Grid arrays ---
b_traded = existing['B_traded'].values.astype(bool)
b_side   = existing['B_side'].values
b_entry  = existing['B_entry'].values.astype(float)

mom_pnl = np.zeros(N); fade_pnl = np.zeros(N); mom_won = np.full(N, np.nan); mom_active = np.zeros(N, dtype=bool)
for i in range(N):
    if not b_traded[i]: continue
    m = up_mid_250[i]
    if np.isnan(m): continue
    s = 'up' if m > 0.55 else ('down' if m < 0.45 else None)
    if s is None or b_side[i] != s: continue
    ep = b_entry[i]
    if np.isnan(ep) or ep <= 0 or ep >= 0.95: continue
    mom_active[i] = True
    if settlement[i] == s:
        mom_pnl[i] = (1.0-ep)*50; fade_pnl[i] = -(1.0-ep)*50; mom_won[i] = 1
    else:
        mom_pnl[i] = -ep*50; fade_pnl[i] = ep*50; mom_won[i] = 0

grid_pnl = np.zeros(N); grid_active = np.zeros(N, dtype=bool)
for i, rid in enumerate(round_ids):
    if rid in grid_map:
        grid_active[i] = grid_map[rid][0]; grid_pnl[i] = grid_map[rid][1]

# --- Re-scan probe data for custom cheap/bounce thresholds ---
# We need raw per-round min_ask and max_bid_after for mid window
# Already in probe_data cache

def rolling_wr(events, W, n):
    result = np.full(n, np.nan)
    for i in range(n):
        cnt=0; wins=0
        for j in range(i-1,-1,-1):
            if np.isnan(events[j]): continue
            cnt+=1; wins+=events[j]
            if cnt>=W: break
        if cnt>=W: result[i]=wins/cnt
    return result

def run_config(cheap_thr, bounce_thr, W, T, shares=50):
    """Run one config, return dict with metrics."""
    events = np.full(N, np.nan)
    for i, rid in enumerate(round_ids):
        if rid not in probe_data: continue
        wdata = probe_data[rid].get('mid', None)
        if wdata is None: continue
        for evt in wdata:
            if evt['min_ask'] <= cheap_thr:
                events[i] = 1.0 if evt['max_bid_after'] >= bounce_thr else 0.0
                break
    
    rwr = rolling_wr(events, W, N)
    whale = (rwr >= T); whale[np.isnan(rwr)] = False
    normal = ~whale
    whale_n = int(whale.sum())
    
    scale = shares / 50.0
    
    b1 = (fade_pnl[whale & mom_active] * scale).sum()
    b3 = (mom_pnl[normal & mom_active] * scale).sum() + (grid_pnl[normal & grid_active] * scale).sum()
    b2 = (mom_pnl[whale & mom_active] * scale).sum() + (grid_pnl[whale & grid_active] * scale).sum()
    optimal = b1 + b3
    baseline = b2 + b3
    
    opt_pr = np.zeros(N)
    for i in range(N):
        if whale[i]:
            opt_pr[i] = fade_pnl[i]*scale if mom_active[i] else 0
        else:
            opt_pr[i] = (grid_pnl[i]*scale if grid_active[i] else 0) + (mom_pnl[i]*scale if mom_active[i] else 0)
    
    cum = np.cumsum(opt_pr)
    mdd = np.max(np.maximum.accumulate(cum) - cum) if len(cum) > 0 else 0
    sr = opt_pr.mean() / opt_pr.std() * np.sqrt(252*24) if opt_pr.std() > 0 else 0
    
    third = N//3
    p1 = opt_pr[:third].sum(); p2 = opt_pr[third:2*third].sum(); p3 = opt_pr[2*third:].sum()
    robust = (p1>0) and (p2>0) and (p3>0)
    
    wh_mom_wr = np.nanmean(mom_won[whale & mom_active])*100 if (whale & mom_active).sum()>0 else 0
    nw_mom_wr = np.nanmean(mom_won[normal & mom_active])*100 if (normal & mom_active).sum()>0 else 0
    
    return {
        'cheap': cheap_thr, 'bounce': bounce_thr, 'W': W, 'T': T, 'shares': shares,
        'whale_n': whale_n, 'whale_pct': whale_n/N*100,
        'wh_mom_wr': wh_mom_wr, 'nw_mom_wr': nw_mom_wr,
        'B1_fade': b1, 'B3_norm': b3, 'Optimal': optimal, 'Baseline': baseline,
        'MDD': mdd, 'Sharpe': sr,
        'P1': p1, 'P2': p2, 'P3': p3, 'Robust': robust,
        'equity': cum
    }

# ===== TEST 1: cheap threshold sensitivity (bounce=0.50 fixed) =====
print("=" * 100)
print("TEST 1: Cheap Threshold Sensitivity (bounce=0.50, W=7)")
print("=" * 100)
print(f"{'cheap':>6} {'T':>5} {'Wh%':>6} | {'B1':>8} {'B3':>8} {'Opt':>8} {'SR':>7} {'MDD':>6} | {'P1':>7} {'P2':>7} {'P3':>7} {'Rob':>4}")
print("-"*90)

for cheap in [0.10, 0.12, 0.15, 0.18, 0.20, 0.25]:
    for T in [0.20, 0.25, 0.30]:
        r = run_config(cheap, 0.50, 7, T)
        rob = "YES" if r['Robust'] else "no"
        print(f"{cheap:>6.2f} {T:>5.2f} {r['whale_pct']:>5.1f}% | {r['B1_fade']:>+8.0f} {r['B3_norm']:>+8.0f} {r['Optimal']:>+8.0f} "
              f"{r['Sharpe']:>7.2f} {r['MDD']:>6.0f} | {r['P1']:>+7.0f} {r['P2']:>+7.0f} {r['P3']:>+7.0f} {rob:>4}")

# ===== TEST 2: bounce threshold sensitivity (cheap=0.15 fixed) =====
print("\n" + "=" * 100)
print("TEST 2: Bounce Threshold Sensitivity (cheap=0.15, W=7)")
print("=" * 100)
print(f"{'bounce':>7} {'T':>5} {'Wh%':>6} | {'B1':>8} {'B3':>8} {'Opt':>8} {'SR':>7} {'MDD':>6} | {'P1':>7} {'P2':>7} {'P3':>7} {'Rob':>4}")
print("-"*90)

for bounce in [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]:
    for T in [0.20, 0.25, 0.30]:
        r = run_config(0.15, bounce, 7, T)
        rob = "YES" if r['Robust'] else "no"
        print(f"{bounce:>7.2f} {T:>5.2f} {r['whale_pct']:>5.1f}% | {r['B1_fade']:>+8.0f} {r['B3_norm']:>+8.0f} {r['Optimal']:>+8.0f} "
              f"{r['Sharpe']:>7.2f} {r['MDD']:>6.0f} | {r['P1']:>+7.0f} {r['P2']:>+7.0f} {r['P3']:>+7.0f} {rob:>4}")

# ===== TEST 3: Shares sensitivity =====
print("\n" + "=" * 100)
print("TEST 3: Position Size Sensitivity (cheap=0.15, bounce=0.50, W=7, T=0.25)")
print("=" * 100)
print(f"{'shares':>7} | {'Opt':>8} {'MDD':>8} {'SR':>7} | {'P1':>8} {'P2':>8} {'P3':>8} {'Cost/rd':>8}")
print("-"*80)

for shares in [10, 25, 50, 75, 100]:
    r = run_config(0.15, 0.50, 7, 0.25, shares)
    cost = shares * 0.05 * 2  # est avg entry*shares*2sides rough
    print(f"{shares:>7} | {r['Optimal']:>+8.0f} {r['MDD']:>8.0f} {r['Sharpe']:>7.2f} | "
          f"{r['P1']:>+8.0f} {r['P2']:>+8.0f} {r['P3']:>+8.0f} {cost:>7.1f}$")

# ===== TEST 4: Window edge sensitivity =====
print("\n" + "=" * 100)
print("TEST 4: Observation Window Edge Sensitivity")
print("=" * 100)

# Rescan CSVs for custom windows — use a faster subset approach
# We'll re-extract from raw CSVs for different mid-window definitions
import glob as glob_mod

csv_files = sorted(glob_mod.glob(os.path.join(DATA_DIR, '*.csv')))
print(f"  Scanning {len(csv_files)} CSVs for custom windows...")

# Cache key for custom window data
CACHE_CUSTOM = os.path.join(OUT_DIR, '_cache_custom_windows.pkl')
if os.path.exists(CACHE_CUSTOM):
    with open(CACHE_CUSTOM, 'rb') as f:
        custom_probe = pickle.load(f)
    print(f"  Loaded custom window cache ({len(custom_probe)} rounds)")
else:
    custom_windows = {
        'mid_120_240': (120, 240),
        'mid_150_240': (150, 240),  # current best
        'mid_150_270': (150, 270),
        'mid_180_240': (180, 240),
        'mid_180_270': (180, 270),
    }
    
    custom_probe = {}  # round_id -> {window_name: [{min_ask, max_bid_after}]}
    
    for fi, fp in enumerate(csv_files):
        if fi % 500 == 0:
            print(f"    {fi}/{len(csv_files)}...")
        try:
            df = pd.read_csv(fp)
        except:
            continue
        
        rid = os.path.basename(fp).replace('.csv', '')
        if 'elapsed' not in df.columns:
            continue
        
        up_cols = [c for c in df.columns if 'up' in c.lower() and 'ask' in c.lower()]
        down_cols = [c for c in df.columns if 'down' in c.lower() and 'ask' in c.lower()]
        up_bid_cols = [c for c in df.columns if 'up' in c.lower() and 'bid' in c.lower()]
        down_bid_cols = [c for c in df.columns if 'down' in c.lower() and 'bid' in c.lower()]
        
        if not up_cols or not down_cols or not up_bid_cols or not down_bid_cols:
            continue
        
        elapsed = df['elapsed'].values.astype(float)
        up_ask = pd.to_numeric(df[up_cols[0]], errors='coerce').values
        down_ask = pd.to_numeric(df[down_cols[0]], errors='coerce').values
        up_bid = pd.to_numeric(df[up_bid_cols[0]], errors='coerce').values
        down_bid = pd.to_numeric(df[down_bid_cols[0]], errors='coerce').values
        
        round_data = {}
        for wname, (t_start, t_end) in custom_windows.items():
            events = []
            for side_ask, side_bid, side_name in [(up_ask, up_bid, 'up'), (down_ask, down_bid, 'down')]:
                mask_window = (elapsed >= t_start) & (elapsed <= t_end)
                valid = mask_window & ~np.isnan(side_ask)
                if valid.sum() == 0:
                    continue
                min_ask = np.nanmin(side_ask[valid])
                min_idx = np.where(valid & (side_ask == min_ask))[0]
                if len(min_idx) == 0:
                    continue
                min_pos = min_idx[0]
                after = np.arange(min_pos, len(side_bid))
                valid_after = ~np.isnan(side_bid[after])
                if valid_after.sum() == 0:
                    max_bid = 0
                else:
                    max_bid = np.nanmax(side_bid[after[valid_after]])
                events.append({'side': side_name, 'min_ask': float(min_ask), 'max_bid_after': float(max_bid)})
            round_data[wname] = events
        
        custom_probe[rid] = round_data
    
    with open(CACHE_CUSTOM, 'wb') as f:
        pickle.dump(custom_probe, f)
    print(f"  Cached {len(custom_probe)} rounds")

# Now test each custom window
print(f"\n{'Window':<18} {'T':>5} {'Wh%':>6} | {'B1':>8} {'B3':>8} {'Opt':>8} {'SR':>7} {'MDD':>6} | {'P1':>7} {'P2':>7} {'P3':>7} {'Rob':>4}")
print("-"*100)

for wname in ['mid_120_240', 'mid_150_240', 'mid_150_270', 'mid_180_240', 'mid_180_270']:
    events = np.full(N, np.nan)
    for i, rid in enumerate(round_ids):
        if rid not in custom_probe: continue
        wdata = custom_probe[rid].get(wname, None)
        if wdata is None: continue
        for evt in wdata:
            if evt['min_ask'] <= 0.15:
                events[i] = 1.0 if evt['max_bid_after'] >= 0.50 else 0.0
                break
    
    rwr = rolling_wr(events, 7, N)
    for T in [0.20, 0.25, 0.30]:
        whale = (rwr >= T); whale[np.isnan(rwr)] = False
        normal = ~whale
        whale_n = int(whale.sum())
        
        b1 = fade_pnl[whale & mom_active].sum()
        b3 = mom_pnl[normal & mom_active].sum() + grid_pnl[normal & grid_active].sum()
        optimal = b1 + b3
        
        opt_pr = np.zeros(N)
        for ii in range(N):
            if whale[ii]:
                opt_pr[ii] = fade_pnl[ii] if mom_active[ii] else 0
            else:
                opt_pr[ii] = (grid_pnl[ii] if grid_active[ii] else 0) + (mom_pnl[ii] if mom_active[ii] else 0)
        cum = np.cumsum(opt_pr)
        mdd = np.max(np.maximum.accumulate(cum) - cum) if len(cum)>0 else 0
        sr = opt_pr.mean()/opt_pr.std()*np.sqrt(252*24) if opt_pr.std()>0 else 0
        third = N//3
        p1=opt_pr[:third].sum(); p2=opt_pr[third:2*third].sum(); p3=opt_pr[2*third:].sum()
        robust = (p1>0)and(p2>0)and(p3>0)
        rob = "YES" if robust else "no"
        print(f"{wname:<18} {T:>5.2f} {whale_n/N*100:>5.1f}% | {b1:>+8.0f} {b3:>+8.0f} {optimal:>+8.0f} "
              f"{sr:>7.2f} {mdd:>6.0f} | {p1:>+7.0f} {p2:>+7.0f} {p3:>+7.0f} {rob:>4}")

# ===== TEST 5: Combined chart =====
print("\n\nGenerating sensitivity charts...")

fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# Chart 1: cheap threshold vs Optimal
ax = axes[0, 0]
for T_val, color, ls in [(0.20, 'red', '-'), (0.25, 'blue', '--'), (0.30, 'green', ':')]:
    cheaps = [0.10, 0.12, 0.15, 0.18, 0.20, 0.25]
    opts = []
    for c in cheaps:
        r = run_config(c, 0.50, 7, T_val)
        opts.append(r['Optimal'])
    ax.plot(cheaps, opts, color=color, linestyle=ls, marker='o', label=f'T>={T_val:.2f}')
ax.set_xlabel('Cheap Threshold')
ax.set_ylabel('Optimal PnL ($)')
ax.set_title('Cheap Threshold Sensitivity (bounce=0.50, W=7)')
ax.legend()
ax.grid(True, alpha=0.3)

# Chart 2: bounce threshold vs Optimal
ax = axes[0, 1]
for T_val, color, ls in [(0.20, 'red', '-'), (0.25, 'blue', '--'), (0.30, 'green', ':')]:
    bounces = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    opts = []
    for b in bounces:
        r = run_config(0.15, b, 7, T_val)
        opts.append(r['Optimal'])
    ax.plot(bounces, opts, color=color, linestyle=ls, marker='o', label=f'T>={T_val:.2f}')
ax.set_xlabel('Bounce Threshold')
ax.set_ylabel('Optimal PnL ($)')
ax.set_title('Bounce Threshold Sensitivity (cheap=0.15, W=7)')
ax.legend()
ax.grid(True, alpha=0.3)

# Chart 3: Equity curves for key configs
ax = axes[1, 0]
configs_to_plot = [
    (0.15, 0.50, 7, 0.25, 'Best: c=0.15,b=0.50,W7,T25', 'red'),
    (0.15, 0.50, 8, 0.30, 'c=0.15,b=0.50,W8,T30', 'blue'),
    (0.15, 0.45, 7, 0.25, 'c=0.15,b=0.45,W7,T25', 'green'),
    (0.12, 0.50, 7, 0.25, 'c=0.12,b=0.50,W7,T25', 'purple'),
    (0.20, 0.50, 7, 0.25, 'c=0.20,b=0.50,W7,T25', 'orange'),
]

base_pr = np.zeros(N)
for i in range(N):
    base_pr[i] = (grid_pnl[i] if grid_active[i] else 0) + (mom_pnl[i] if mom_active[i] else 0)
ax.plot(np.cumsum(base_pr), color='gray', alpha=0.4, linewidth=1, label='Baseline')

for cheap, bounce, W, T, label, color in configs_to_plot:
    r = run_config(cheap, bounce, W, T)
    ax.plot(r['equity'], color=color, linewidth=1.5, label=f'{label} (SR={r["Sharpe"]:.1f})')

ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.set_title('Equity Curves: Parameter Sensitivity')
ax.legend(fontsize=7, loc='upper left')
ax.grid(True, alpha=0.3)

# Chart 4: W sensitivity for best config
ax = axes[1, 1]
for T_val in [0.20, 0.25, 0.30, 0.35]:
    Ws = [5, 6, 7, 8, 9, 10]
    srs = []
    for W in Ws:
        r = run_config(0.15, 0.50, W, T_val)
        srs.append(r['Sharpe'] if r['Robust'] else 0)
    ax.plot(Ws, srs, marker='s', label=f'T>={T_val:.2f}')
ax.set_xlabel('Window W')
ax.set_ylabel('Sharpe (0 if not robust)')
ax.set_title('W Sensitivity (cheap=0.15, bounce=0.50)')
ax.legend()
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'sensitivity_tests.png'), dpi=150)
print(f"  Saved sensitivity_tests.png")

print("\nAll sensitivity tests complete!")
