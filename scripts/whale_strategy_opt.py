"""
Whale-Period Strategy Optimization
===================================
Given whale rounds identified by mid-bounce probe (c<=0.12, b>=0.50, W=7, T>=0.25),
test many different trading strategies for those rounds:

Category A: Momentum fade (current) - reverse momentum at t=250, hold vs TP
Category B: DipBuy variants - buy when ask<=X during mid-window, sell at various targets
Category C: Hybrid - different timing, different exits

Uses tick-by-tick CSV data for accurate simulation.
"""

import pandas as pd
import numpy as np
import pickle
import os
import glob as glob_mod
import warnings
warnings.filterwarnings('ignore')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXIST_CSV = os.path.join(BASE, 'results', 'three_strategies_fixed', 'three_strategies_fixed.csv')
CACHE_PROBE = os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_probe_v2.pkl')
CACHE_GRID  = os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_grid.pkl')
DATA_DIR = r'C:\Users\ZHAOKAI\data'
OUT_DIR = os.path.join(BASE, 'results', 'whale_strategy_opt')
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50

# ── Load data ──
print("Loading base data...")
existing = pd.read_csv(EXIST_CSV)
N = len(existing)
settlement = existing['f_settlement'].values
up_mid_250 = existing['f_up_mid_250'].values.astype(float)
round_ids  = existing['round_id'].values

with open(CACHE_PROBE, 'rb') as f:
    probe_data = pickle.load(f)
with open(CACHE_GRID, 'rb') as f:
    grid_map = pickle.load(f)

# ── Identify whale rounds (c<=0.12, b>=0.50, W=7, T>=0.25) ──
print("Identifying whale rounds...")
mid_events = np.full(N, np.nan)
for i, rid in enumerate(round_ids):
    if rid not in probe_data:
        continue
    wdata = probe_data[rid].get('mid', None)
    if wdata is None:
        continue
    for evt in wdata:
        if evt['min_ask'] <= 0.12:
            mid_events[i] = 1.0 if evt['max_bid_after'] >= 0.50 else 0.0
            break

# Rolling WR
rwr = np.full(N, np.nan)
for i in range(N):
    cnt = 0; wins = 0
    for j in range(i-1, -1, -1):
        if np.isnan(mid_events[j]):
            continue
        cnt += 1
        wins += mid_events[j]
        if cnt >= 7:
            break
    if cnt >= 7:
        rwr[i] = wins / cnt

whale_mask = (rwr >= 0.25)
whale_mask[np.isnan(rwr)] = False
normal_mask = ~whale_mask

whale_indices = np.where(whale_mask)[0]
whale_rids = set(round_ids[whale_indices])
print(f"  Whale rounds: {len(whale_indices)} ({len(whale_indices)/N*100:.1f}%)")

# ── Normal-period PnL (fixed: grid + momentum) ──
b_traded = existing['B_traded'].values.astype(bool)
b_side   = existing['B_side'].values
b_entry  = existing['B_entry'].values.astype(float)

normal_pnl_per_round = np.zeros(N)
for i in range(N):
    if not normal_mask[i]:
        continue
    pnl = 0
    # Grid
    rid = round_ids[i]
    if rid in grid_map and grid_map[rid][0]:
        pnl += grid_map[rid][1]
    # Momentum
    if b_traded[i]:
        m = up_mid_250[i]
        if not np.isnan(m):
            s = 'up' if m > 0.55 else ('down' if m < 0.45 else None)
            if s is not None and b_side[i] == s:
                ep = b_entry[i]
                if not np.isnan(ep) and 0 < ep < 0.95:
                    if settlement[i] == s:
                        pnl += (1.0 - ep) * SHARES
                    else:
                        pnl += -ep * SHARES
    normal_pnl_per_round[i] = pnl

normal_total = normal_pnl_per_round.sum()
print(f"  Normal-period PnL (grid+momentum): +${normal_total:.0f}")

# ── Scan whale-round CSVs for tick-by-tick simulation ──
CACHE_WHALE_TICKS = os.path.join(OUT_DIR, '_cache_whale_ticks.pkl')
if os.path.exists(CACHE_WHALE_TICKS):
    with open(CACHE_WHALE_TICKS, 'rb') as f:
        whale_ticks = pickle.load(f)
    print(f"  Loaded whale tick cache: {len(whale_ticks)} rounds")
else:
    print("  Scanning whale-round CSVs for tick data...")
    whale_ticks = {}  # round_id -> dict with arrays
    csv_files = sorted(glob_mod.glob(os.path.join(DATA_DIR, '*.csv')))
    
    for fi, fp in enumerate(csv_files):
        rid = os.path.basename(fp).replace('.csv', '')
        if rid not in whale_rids:
            continue
        try:
            df = pd.read_csv(fp)
        except:
            continue
        
        if 'timestamp' not in df.columns:
            continue
        
        ts = pd.to_datetime(df['timestamp'], errors='coerce')
        if ts.isna().all():
            continue
        t0 = ts.min()
        elapsed = (ts - t0).dt.total_seconds().values
        
        up_ask = pd.to_numeric(df.get('up_best_ask', pd.Series(dtype=float)), errors='coerce').values
        up_bid = pd.to_numeric(df.get('up_best_bid', pd.Series(dtype=float)), errors='coerce').values
        down_ask = pd.to_numeric(df.get('down_best_ask', pd.Series(dtype=float)), errors='coerce').values
        down_bid = pd.to_numeric(df.get('down_best_bid', pd.Series(dtype=float)), errors='coerce').values
        up_mid = pd.to_numeric(df.get('up_midpoint', pd.Series(dtype=float)), errors='coerce').values
        
        whale_ticks[rid] = {
            'elapsed': elapsed,
            'up_ask': up_ask, 'up_bid': up_bid,
            'down_ask': down_ask, 'down_bid': down_bid,
            'up_mid': up_mid,
        }
    
    with open(CACHE_WHALE_TICKS, 'wb') as f:
        pickle.dump(whale_ticks, f)
    print(f"  Cached {len(whale_ticks)} whale-round tick data")

# ── Settlement for whale rounds ──
whale_settle = {}  # rid -> 'up' or 'down'
for i in whale_indices:
    whale_settle[round_ids[i]] = settlement[i]

# ═══════════════════════════════════════════════════════════════
# Strategy simulation functions
# ═══════════════════════════════════════════════════════════════

def sim_fade_hold(ticks, settle, shares=SHARES):
    """Current fade: reverse momentum at t=250, hold to settlement."""
    e = ticks['elapsed']
    mask250 = (e >= 248) & (e <= 255)
    if mask250.sum() == 0:
        return None
    
    mid_vals = ticks['up_mid'][mask250]
    valid = ~np.isnan(mid_vals)
    if valid.sum() == 0:
        return None
    mid = mid_vals[valid][-1]
    
    if mid > 0.55:
        # Normal momentum buys UP; fade buys DOWN
        side = 'down'
        ask_arr = ticks['down_ask'][mask250]
    elif mid < 0.45:
        side = 'up'
        ask_arr = ticks['up_ask'][mask250]
    else:
        return None
    
    valid_ask = ask_arr[~np.isnan(ask_arr)]
    if len(valid_ask) == 0:
        return None
    entry = valid_ask[-1]
    if entry <= 0 or entry >= 0.95:
        return None
    
    if settle == side:
        pnl = (1.0 - entry) * shares
    else:
        pnl = -entry * shares
    
    return {'pnl': pnl, 'entry': entry, 'side': side, 'exit': 1.0 if settle == side else 0.0}


def sim_dipbuy(ticks, settle, entry_thr, exit_thr, entry_window, exit_window, shares=SHARES):
    """
    Buy the cheap side when ask <= entry_thr during entry_window.
    Sell when bid >= exit_thr during exit_window, or hold to settlement.
    """
    e = ticks['elapsed']
    
    # Find which side gets cheap during entry_window
    entry_mask = (e >= entry_window[0]) & (e <= entry_window[1])
    if entry_mask.sum() == 0:
        return None
    
    best_entry = None
    best_side = None
    
    for side, ask_key, bid_key in [('up', 'up_ask', 'up_bid'), ('down', 'down_ask', 'down_bid')]:
        ask_vals = ticks[ask_key][entry_mask]
        valid = ~np.isnan(ask_vals) & (ask_vals <= entry_thr) & (ask_vals > 0)
        if valid.sum() > 0:
            # Take first entry opportunity
            entry_price = ask_vals[valid][0]
            if best_entry is None or entry_price < best_entry:
                best_entry = entry_price
                best_side = side
    
    if best_entry is None:
        return None
    
    # Try to exit at target during exit_window
    bid_key = f'{best_side}_bid'
    exit_mask = (e >= entry_window[0]) & (e <= exit_window[1])  # can exit anytime after entry
    bid_vals = ticks[bid_key][exit_mask]
    
    # Find first bid >= exit_thr after entry
    # We need to find the entry point in the array first
    entry_found = False
    exited = False
    exit_price = None
    
    for idx in range(len(ticks['elapsed'])):
        t = ticks['elapsed'][idx]
        if t < entry_window[0]:
            continue
        
        ask_val = ticks[f'{best_side}_ask'][idx]
        if not entry_found:
            if not np.isnan(ask_val) and ask_val <= entry_thr and ask_val > 0:
                entry_found = True
                best_entry = ask_val
            continue
        
        if t > exit_window[1]:
            break
        
        bid_val = ticks[f'{best_side}_bid'][idx]
        if not np.isnan(bid_val) and bid_val >= exit_thr:
            exit_price = exit_thr  # limit order at exit_thr
            exited = True
            break
    
    if not entry_found:
        return None
    
    if exited:
        pnl = (exit_price - best_entry) * shares
    else:
        # Hold to settlement
        if settle == best_side:
            pnl = (1.0 - best_entry) * shares
        else:
            pnl = -best_entry * shares
    
    return {
        'pnl': pnl, 'entry': best_entry, 'side': best_side,
        'exit': exit_price if exited else (1.0 if settle == best_side else 0.0),
        'exited_early': exited,
    }


def sim_fade_tp(ticks, settle, tp_price, shares=SHARES):
    """Fade momentum at t=250, but take profit if opposite side bid reaches tp_price."""
    e = ticks['elapsed']
    mask250 = (e >= 248) & (e <= 255)
    if mask250.sum() == 0:
        return None
    
    mid_vals = ticks['up_mid'][mask250]
    valid = ~np.isnan(mid_vals)
    if valid.sum() == 0:
        return None
    mid = mid_vals[valid][-1]
    
    if mid > 0.55:
        side = 'down'
    elif mid < 0.45:
        side = 'up'
    else:
        return None
    
    ask_key = f'{side}_ask'
    bid_key = f'{side}_bid'
    
    ask_vals = ticks[ask_key][mask250]
    valid_ask = ask_vals[~np.isnan(ask_vals)]
    if len(valid_ask) == 0:
        return None
    entry = valid_ask[-1]
    if entry <= 0 or entry >= 0.95:
        return None
    
    # Check if bid reaches tp_price before settlement
    exited = False
    for idx in range(len(e)):
        if e[idx] < 250:
            continue
        if e[idx] > 298:
            break
        bid_val = ticks[bid_key][idx]
        if not np.isnan(bid_val) and bid_val >= tp_price:
            exited = True
            break
    
    if exited:
        pnl = (tp_price - entry) * shares
    else:
        if settle == side:
            pnl = (1.0 - entry) * shares
        else:
            pnl = -entry * shares
    
    return {'pnl': pnl, 'entry': entry, 'side': side, 'exited_early': exited}


# ═══════════════════════════════════════════════════════════════
# Run all strategies on whale rounds
# ═══════════════════════════════════════════════════════════════

strategies = {}

# --- Category A: Fade variants ---
strategies['A1: Fade-Hold (current)'] = lambda t, s: sim_fade_hold(t, s)
for tp in [0.30, 0.40, 0.50, 0.60, 0.70]:
    key = f'A2: Fade-TP@{tp:.2f}'
    strategies[key] = lambda t, s, _tp=tp: sim_fade_tp(t, s, _tp)

# --- Category B: DipBuy in mid-window with exit targets ---
for entry_thr in [0.05, 0.08, 0.10, 0.12, 0.15]:
    for exit_thr in [0.20, 0.30, 0.40, 0.50]:
        key = f'B: Buy<={entry_thr:.2f},Sell>={exit_thr:.2f}(mid)'
        strategies[key] = lambda t, s, _e=entry_thr, _x=exit_thr: sim_dipbuy(
            t, s, _e, _x, (120, 260), (120, 295))

# --- Category C: DipBuy hold to settlement ---
for entry_thr in [0.05, 0.08, 0.10, 0.12, 0.15]:
    key = f'C: Buy<={entry_thr:.2f},Hold-Settle'
    strategies[key] = lambda t, s, _e=entry_thr: sim_dipbuy(
        t, s, _e, 999.0, (120, 260), (120, 295))  # exit_thr=999 = never TP

# --- Category D: DipBuy in wider window ---
for entry_thr in [0.10, 0.15]:
    for exit_thr in [0.25, 0.35, 0.50]:
        key = f'D: Buy<={entry_thr:.2f},Sell>={exit_thr:.2f}(wide)'
        strategies[key] = lambda t, s, _e=entry_thr, _x=exit_thr: sim_dipbuy(
            t, s, _e, _x, (60, 280), (60, 295))

print(f"\nTesting {len(strategies)} strategies on {len(whale_indices)} whale rounds...")

# Run all strategies
results = {}  # strategy_name -> list of per-whale-round PnL

for sname in strategies:
    results[sname] = []

for idx_count, wi in enumerate(whale_indices):
    rid = round_ids[wi]
    if rid not in whale_ticks:
        for sname in strategies:
            results[sname].append(0)
        continue
    
    ticks = whale_ticks[rid]
    settle = whale_settle.get(rid, 'up')
    
    for sname, sfunc in strategies.items():
        res = sfunc(ticks, settle)
        if res is not None:
            results[sname].append(res['pnl'])
        else:
            results[sname].append(0)

# ═══════════════════════════════════════════════════════════════
# Analyze results
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 120)
print("WHALE-PERIOD STRATEGY COMPARISON")
print(f"(Signal: mid c<=0.12, b>=0.50, W=7, T>=0.25 | {len(whale_indices)} whale rounds | Normal PnL: +${normal_total:.0f})")
print("=" * 120)

summary = []
for sname in strategies:
    pnls = np.array(results[sname])
    traded = pnls != 0
    n_traded = traded.sum()
    total_pnl = pnls.sum()
    
    if n_traded > 0:
        wr = (pnls[traded] > 0).mean() * 100
        avg_win = pnls[traded][pnls[traded] > 0].mean() if (pnls[traded] > 0).sum() > 0 else 0
        avg_loss = pnls[traded][pnls[traded] < 0].mean() if (pnls[traded] < 0).sum() > 0 else 0
    else:
        wr = 0; avg_win = 0; avg_loss = 0
    
    # Optimal = this strategy in whale + normal strategy outside
    optimal = total_pnl + normal_total
    
    # Per-round Sharpe of combined system
    combined = np.copy(normal_pnl_per_round)
    for j, wi in enumerate(whale_indices):
        combined[wi] = pnls[j]
    
    cum = np.cumsum(combined)
    mdd = np.max(np.maximum.accumulate(cum) - cum) if len(cum) > 0 else 0
    sr = combined.mean() / combined.std() * np.sqrt(252*24) if combined.std() > 0 else 0
    
    summary.append({
        'strategy': sname,
        'n_traded': n_traded,
        'total_pnl': total_pnl,
        'wr': wr,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'optimal': optimal,
        'mdd': mdd,
        'sharpe': sr,
        'equity': cum,
    })

summary_df = pd.DataFrame(summary)
summary_df = summary_df.sort_values('optimal', ascending=False)
summary_df.to_csv(os.path.join(OUT_DIR, 'whale_strategy_comparison.csv'), index=False)

# Print top results
print(f"\n{'Strategy':<40} {'Trades':>7} {'WhalePnL':>9} {'WR':>6} {'AvgW':>7} {'AvgL':>7} | {'Optimal':>8} {'MDD':>6} {'SR':>7}")
print("-" * 120)

for _, r in summary_df.iterrows():
    print(f"{r['strategy']:<40} {int(r['n_traded']):>7} {r['total_pnl']:>+9.0f} {r['wr']:>5.1f}% "
          f"{r['avg_win']:>+7.1f} {r['avg_loss']:>+7.1f} | {r['optimal']:>+8.0f} {r['mdd']:>6.0f} {r['sharpe']:>7.2f}")

# ═══════════════════════════════════════════════════════════════
# Charts
# ═══════════════════════════════════════════════════════════════

print("\nGenerating charts...")

fig, axes = plt.subplots(2, 2, figsize=(18, 14))

# 1. Bar chart: Top 15 strategies by Optimal PnL
ax = axes[0, 0]
top15 = summary_df.nlargest(15, 'optimal')
colors = ['#2ecc71' if v > 0 else '#e74c3c' for v in top15['total_pnl']]
y_pos = range(len(top15))
bars = ax.barh(y_pos, top15['optimal'].values, color=colors, alpha=0.7, edgecolor='black')
ax.set_yticks(y_pos)
ax.set_yticklabels(top15['strategy'].values, fontsize=7)
for i, (opt, wpnl) in enumerate(zip(top15['optimal'].values, top15['total_pnl'].values)):
    ax.text(opt + 20, i, f'+${opt:.0f} (whale:{wpnl:+.0f})', va='center', fontsize=7)
ax.set_xlabel('Optimal PnL ($)')
ax.set_title('Top 15 Whale Strategies (Normal PnL fixed)')
ax.axvline(x=normal_total, color='gray', linestyle='--', alpha=0.5, label=f'Normal only: +${normal_total:.0f}')
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3, axis='x')
ax.invert_yaxis()

# 2. Equity curves: top 5 + baseline
ax = axes[0, 1]
base_pr = np.copy(normal_pnl_per_round)
# Add whale period momentum (current system without fade)
for i in whale_indices:
    rid = round_ids[i]
    if b_traded[i]:
        m = up_mid_250[i]
        if not np.isnan(m):
            s = 'up' if m > 0.55 else ('down' if m < 0.45 else None)
            if s is not None and b_side[i] == s:
                ep = b_entry[i]
                if not np.isnan(ep) and 0 < ep < 0.95:
                    if settlement[i] == s:
                        base_pr[i] = (1.0 - ep) * SHARES
                    else:
                        base_pr[i] = -ep * SHARES

ax.plot(np.cumsum(base_pr), color='gray', alpha=0.5, linewidth=1, label='No switching (always momentum)')

top5 = summary_df.nlargest(5, 'sharpe')
colors_eq = ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00']
for idx, (_, r) in enumerate(top5.iterrows()):
    ax.plot(r['equity'], color=colors_eq[idx], linewidth=1.5,
            label=f"{r['strategy'][:30]} (SR={r['sharpe']:.1f})")

ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.set_title('Equity Curves: Top 5 by Sharpe')
ax.legend(fontsize=6, loc='upper left')
ax.grid(True, alpha=0.3)

# 3. Scatter: WR vs Avg PnL per trade (for DipBuy strategies only)
ax = axes[1, 0]
dipbuy_strats = summary_df[summary_df['strategy'].str.startswith('B:') | summary_df['strategy'].str.startswith('C:')]
dipbuy_strats = dipbuy_strats[dipbuy_strats['n_traded'] > 20]

if len(dipbuy_strats) > 0:
    sc = ax.scatter(dipbuy_strats['wr'], dipbuy_strats['total_pnl'] / dipbuy_strats['n_traded'],
                    c=dipbuy_strats['optimal'], cmap='RdYlGn', s=80, edgecolor='black', alpha=0.7)
    plt.colorbar(sc, ax=ax, label='Optimal PnL')
    for _, r in dipbuy_strats.iterrows():
        ax.annotate(r['strategy'][3:20], (r['wr'], r['total_pnl']/r['n_traded']),
                   fontsize=5, alpha=0.7)
ax.set_xlabel('Win Rate (%)')
ax.set_ylabel('Avg PnL per Trade ($)')
ax.set_title('DipBuy Strategies: WR vs Avg PnL')
ax.grid(True, alpha=0.3)

# 4. Category comparison
ax = axes[1, 1]
categories = {
    'A: Fade(current)': summary_df[summary_df['strategy'].str.startswith('A1')]['optimal'].values,
    'A: Fade+TP': summary_df[summary_df['strategy'].str.startswith('A2')]['optimal'].max(),
    'B: DipBuy+TP(best)': summary_df[summary_df['strategy'].str.startswith('B:')]['optimal'].max(),
    'C: DipBuy+Hold(best)': summary_df[summary_df['strategy'].str.startswith('C:')]['optimal'].max(),
    'D: DipBuy Wide(best)': summary_df[summary_df['strategy'].str.startswith('D:')]['optimal'].max(),
}

cat_names = list(categories.keys())
cat_vals = []
for k, v in categories.items():
    if isinstance(v, np.ndarray):
        cat_vals.append(v[0] if len(v) > 0 else 0)
    else:
        cat_vals.append(v if not np.isnan(v) else 0)

colors_cat = ['#2ecc71' if v > normal_total else '#e74c3c' for v in cat_vals]
bars = ax.bar(range(len(cat_names)), cat_vals, color=colors_cat, alpha=0.7, edgecolor='black')
ax.set_xticks(range(len(cat_names)))
ax.set_xticklabels(cat_names, rotation=20, ha='right', fontsize=8)
for bar, v in zip(bars, cat_vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 20, f'+${v:.0f}',
            ha='center', va='bottom', fontsize=9, fontweight='bold')
ax.axhline(y=normal_total, color='gray', linestyle='--', alpha=0.5, label=f'Normal only: +${normal_total:.0f}')
ax.set_ylabel('Optimal PnL ($)')
ax.set_title('Best Strategy per Category')
ax.legend()
ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'whale_strategy_optimization.png'), dpi=150)
print(f"  Saved whale_strategy_optimization.png")

# ── Print final recommendation ──
print("\n" + "=" * 80)
print("FINAL RECOMMENDATION")
print("=" * 80)
best = summary_df.iloc[0]
print(f"  Best strategy: {best['strategy']}")
print(f"  Whale PnL:     {best['total_pnl']:+.0f} ({int(best['n_traded'])} trades, WR={best['wr']:.1f}%)")
print(f"  Optimal:       {best['optimal']:+.0f}")
print(f"  Sharpe:        {best['sharpe']:.2f}")
print(f"  MDD:           {best['mdd']:.0f}")

best_sr = summary_df.loc[summary_df['sharpe'].idxmax()]
if best_sr['strategy'] != best['strategy']:
    print(f"\n  Best by Sharpe: {best_sr['strategy']}")
    print(f"  Whale PnL:     {best_sr['total_pnl']:+.0f}")
    print(f"  Optimal:       {best_sr['optimal']:+.0f}")
    print(f"  Sharpe:        {best_sr['sharpe']:.2f}")
    print(f"  MDD:           {best_sr['mdd']:.0f}")

print("\nDone!")
