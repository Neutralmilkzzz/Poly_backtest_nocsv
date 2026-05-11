"""
Mid-Window Bounce Probe: 4-Bucket Analysis
===========================================
Signal: mid-window(150-240s), cheap_ask <= 0.15, bounce >= 0.50
Sweep: W in [5,7,8,9,10], T in [0.20..0.50]
4 Buckets:
  B1 = whale + fade (contrarian)
  B2 = whale + normal (momentum+grid)
  B3 = normal + normal
  B4 = normal + fade
Optimal = B1 + B3  (fade when whale, normal otherwise)
"""

import pandas as pd
import numpy as np
import pickle
import os
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Paths
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXIST_CSV = os.path.join(BASE, 'results', 'three_strategies_fixed', 'three_strategies_fixed.csv')
CACHE_PROBE = os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_probe_v2.pkl')
CACHE_GRID  = os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_grid.pkl')
OUT_DIR = os.path.join(BASE, 'results', 'mid_bounce_4bucket')
os.makedirs(OUT_DIR, exist_ok=True)

# ── Load data ──
print("Loading data...")
existing = pd.read_csv(EXIST_CSV)
N = len(existing)
settlement = existing['f_settlement'].values
up_mid_250 = existing['f_up_mid_250'].values.astype(float)
round_ids  = existing['round_id'].values

with open(CACHE_PROBE, 'rb') as f:
    probe_data = pickle.load(f)
with open(CACHE_GRID, 'rb') as f:
    grid_map = pickle.load(f)

print(f"  Loaded {N} rounds, {len(probe_data)} probe records, {len(grid_map)} grid records")

# ── Compute per-round PnL arrays ──
# Momentum (50 shares)
b_traded = existing['B_traded'].values.astype(bool)
b_side   = existing['B_side'].values
b_entry  = existing['B_entry'].values.astype(float)

mom_pnl  = np.zeros(N)
fade_pnl = np.zeros(N)
mom_won  = np.full(N, np.nan)
mom_active = np.zeros(N, dtype=bool)

for i in range(N):
    if not b_traded[i]:
        continue
    m = up_mid_250[i]
    if np.isnan(m):
        continue
    s = 'up' if m > 0.55 else ('down' if m < 0.45 else None)
    if s is None or b_side[i] != s:
        continue
    ep = b_entry[i]
    if np.isnan(ep) or ep <= 0 or ep >= 0.95:
        continue
    mom_active[i] = True
    if settlement[i] == s:
        mom_pnl[i]  = (1.0 - ep) * 50
        fade_pnl[i] = -(1.0 - ep) * 50
        mom_won[i]  = 1
    else:
        mom_pnl[i]  = -ep * 50
        fade_pnl[i] = ep * 50
        mom_won[i]  = 0

# Grid (50 shares, buy<=0.18, sell>=0.26)
grid_pnl = np.zeros(N)
grid_active = np.zeros(N, dtype=bool)
for i, rid in enumerate(round_ids):
    if rid in grid_map:
        grid_active[i] = grid_map[rid][0]
        grid_pnl[i]    = grid_map[rid][1]

print(f"  Momentum: {mom_active.sum()} active trades, WR={np.nanmean(mom_won)*100:.1f}%")
print(f"  Grid: {grid_active.sum()} active trades")

# ── Build mid-window bounce events ──
# For each round: did any side have ask<=0.15 in t=150-240, and then bounce to bid>=0.50?
mid_bounce_event = np.full(N, np.nan)  # 1=bounced, 0=no bounce, nan=no cheap ask

n_events = 0
n_bounce = 0
for i, rid in enumerate(round_ids):
    if rid not in probe_data:
        continue
    wdata = probe_data[rid].get('mid', None)
    if wdata is None:
        continue
    for evt in wdata:
        if evt['min_ask'] <= 0.15:
            n_events += 1
            if evt['max_bid_after'] >= 0.50:
                mid_bounce_event[i] = 1.0
                n_bounce += 1
            else:
                mid_bounce_event[i] = 0.0
            break  # one event per round

base_wr = n_bounce / n_events if n_events > 0 else 0
print(f"\n  Mid-window events: {n_events} (cheap<=0.15)")
print(f"  Bounced to >=0.50: {n_bounce} ({base_wr*100:.1f}%)")

# ── Rolling WR function ──
def rolling_wr(events, W):
    """Rolling win-rate over last W non-NaN events (lookback before current round)."""
    result = np.full(N, np.nan)
    for i in range(N):
        cnt = 0
        wins = 0
        for j in range(i - 1, -1, -1):
            if np.isnan(events[j]):
                continue
            cnt += 1
            wins += events[j]
            if cnt >= W:
                break
        if cnt >= W:
            result[i] = wins / cnt
    return result

# ── Sweep W and T ──
W_LIST = [5, 7, 8, 9, 10]
T_LIST = [0.20, 0.25, 0.30, 0.35, 0.40, 0.43, 0.50]

results = []

for W in W_LIST:
    rwr = rolling_wr(mid_bounce_event, W)
    
    for T in T_LIST:
        whale = (rwr >= T)
        whale[np.isnan(rwr)] = False
        normal = ~whale
        
        whale_n = int(whale.sum())
        whale_pct = whale_n / N * 100
        
        # 4 Buckets
        # B1: whale + fade
        mask_b1 = whale & mom_active
        b1_pnl = fade_pnl[mask_b1].sum()
        b1_n   = mask_b1.sum()
        b1_wr  = (fade_pnl[mask_b1] > 0).mean() * 100 if b1_n > 0 else 0
        
        # B2: whale + normal (momentum + grid)
        b2_pnl = mom_pnl[whale & mom_active].sum() + grid_pnl[whale & grid_active].sum()
        
        # B3: normal + normal
        b3_pnl = mom_pnl[normal & mom_active].sum() + grid_pnl[normal & grid_active].sum()
        
        # B4: normal + fade
        mask_b4 = normal & mom_active
        b4_pnl = fade_pnl[mask_b4].sum()
        
        optimal = b1_pnl + b3_pnl
        baseline = b2_pnl + b3_pnl  # = total normal PnL
        
        # Whale-period momentum WR
        wh_mom_wr = np.nanmean(mom_won[whale & mom_active]) * 100 if (whale & mom_active).sum() > 0 else 0
        nw_mom_wr = np.nanmean(mom_won[normal & mom_active]) * 100 if (normal & mom_active).sum() > 0 else 0
        
        # Per-round Sharpe (Optimal strategy)
        opt_per_round = np.zeros(N)
        for i in range(N):
            if whale[i]:
                opt_per_round[i] = fade_pnl[i] if mom_active[i] else 0
            else:
                opt_per_round[i] = (grid_pnl[i] if grid_active[i] else 0) + (mom_pnl[i] if mom_active[i] else 0)
        
        cum = np.cumsum(opt_per_round)
        mdd = np.max(np.maximum.accumulate(cum) - cum)
        sr = opt_per_round.mean() / opt_per_round.std() * np.sqrt(252 * 24) if opt_per_round.std() > 0 else 0
        
        # 3-period robustness
        third = N // 3
        p_pnls = []
        for s, e in [(0, third), (third, 2*third), (2*third, N)]:
            p_pnls.append(opt_per_round[s:e].sum())
        all_pos = all(p > 0 for p in p_pnls)
        
        results.append({
            'W': W, 'T': T,
            'whale_n': whale_n, 'whale_pct': whale_pct,
            'wh_mom_wr': wh_mom_wr, 'nw_mom_wr': nw_mom_wr,
            'B1_fade': b1_pnl, 'B1_n': b1_n, 'B1_wr': b1_wr,
            'B2_wh_norm': b2_pnl,
            'B3_nw_norm': b3_pnl,
            'B4_nw_fade': b4_pnl,
            'Optimal': optimal,
            'Baseline': b2_pnl + b3_pnl,
            'Delta': optimal - (b2_pnl + b3_pnl),
            'MDD': mdd,
            'Sharpe': sr,
            'P1': p_pnls[0], 'P2': p_pnls[1], 'P3': p_pnls[2],
            'Robust': all_pos,
        })

df = pd.DataFrame(results)
df.to_csv(os.path.join(OUT_DIR, 'sweep_results.csv'), index=False)

# ── Print summary ──
print("\n" + "=" * 130)
print("Mid-Window Bounce Probe 4-Bucket Sweep (cheap<=0.15, bounce>=0.50)")
print("=" * 130)
print(f"{'W':>3} {'T':>5} {'Wh%':>6} {'Wh_N':>6} | {'MomWR_wh':>9} {'MomWR_nw':>9} | "
      f"{'B1(fade)':>9} {'B2(wh+n)':>9} {'B3(nw+n)':>9} {'B4(nw+f)':>9} | "
      f"{'Optimal':>8} {'Delta':>7} {'MDD':>6} {'SR':>7} | {'P1':>7} {'P2':>7} {'P3':>7} {'Rob':>4}")
print("-" * 130)

for _, r in df.iterrows():
    rob = "YES" if r['Robust'] else "no"
    print(f"{int(r['W']):>3} {r['T']:>5.2f} {r['whale_pct']:>5.1f}% {int(r['whale_n']):>6} | "
          f"{r['wh_mom_wr']:>8.1f}% {r['nw_mom_wr']:>8.1f}% | "
          f"{r['B1_fade']:>+9.0f} {r['B2_wh_norm']:>+9.0f} {r['B3_nw_norm']:>+9.0f} {r['B4_nw_fade']:>+9.0f} | "
          f"{r['Optimal']:>+8.0f} {r['Delta']:>+7.0f} {r['MDD']:>6.0f} {r['Sharpe']:>7.2f} | "
          f"{r['P1']:>+7.0f} {r['P2']:>+7.0f} {r['P3']:>+7.0f} {rob:>4}")

# ── Highlight best configs ──
print("\n" + "=" * 80)
print("TOP 10 by Optimal PnL (robust only)")
print("=" * 80)
robust = df[df['Robust']].nlargest(10, 'Optimal')
if len(robust) == 0:
    print("No robust configs found! Showing top 10 overall:")
    robust = df.nlargest(10, 'Optimal')

for _, r in robust.iterrows():
    print(f"  W={int(r['W'])}, T>={r['T']:.2f}  |  Whale={r['whale_pct']:.1f}%({int(r['whale_n'])}r)  "
          f"MomWR(wh/nw)={r['wh_mom_wr']:.0f}/{r['nw_mom_wr']:.0f}%  |  "
          f"Opt={r['Optimal']:+.0f}  MDD={r['MDD']:.0f}  SR={r['Sharpe']:.2f}  |  "
          f"P1={r['P1']:+.0f} P2={r['P2']:+.0f} P3={r['P3']:+.0f}")

print("\nTOP 5 by Sharpe (robust only)")
robust_sr = df[df['Robust']].nlargest(5, 'Sharpe')
if len(robust_sr) == 0:
    robust_sr = df.nlargest(5, 'Sharpe')
for _, r in robust_sr.iterrows():
    print(f"  W={int(r['W'])}, T>={r['T']:.2f}  |  Whale={r['whale_pct']:.1f}%  "
          f"SR={r['Sharpe']:.2f}  Opt={r['Optimal']:+.0f}  MDD={r['MDD']:.0f}")

# ── Compare with original DipBuy probe ──
print("\n" + "=" * 80)
print("Comparison with Original DipBuy Probe (settle-based, W=7, T>=0.30)")
print("=" * 80)

# Rebuild original signal
c_traded = existing['C_traded'].values.astype(bool)
c_pnl_arr = existing['C_pnl'].values.astype(float)
dip_won = np.where(c_traded, (c_pnl_arr > 0).astype(float), np.nan)
orig_rwr = rolling_wr(dip_won, 7)
whale_orig = (orig_rwr >= 0.30)
whale_orig[np.isnan(orig_rwr)] = False
normal_orig = ~whale_orig

og_whale_n = whale_orig.sum()
og_b1 = fade_pnl[whale_orig & mom_active].sum()
og_b3 = mom_pnl[normal_orig & mom_active].sum() + grid_pnl[normal_orig & grid_active].sum()
og_opt = og_b1 + og_b3

og_per_round = np.zeros(N)
for i in range(N):
    if whale_orig[i]:
        og_per_round[i] = fade_pnl[i] if mom_active[i] else 0
    else:
        og_per_round[i] = (grid_pnl[i] if grid_active[i] else 0) + (mom_pnl[i] if mom_active[i] else 0)
og_cum = np.cumsum(og_per_round)
og_mdd = np.max(np.maximum.accumulate(og_cum) - og_cum)
og_sr = og_per_round.mean() / og_per_round.std() * np.sqrt(252*24) if og_per_round.std() > 0 else 0

# Best robust mid config
if len(df[df['Robust']]) > 0:
    best = df[df['Robust']].loc[df[df['Robust']]['Optimal'].idxmax()]
else:
    best = df.loc[df['Optimal'].idxmax()]

print(f"  Original DipBuy: Whale={og_whale_n}({og_whale_n/N*100:.1f}%)  Opt={og_opt:+.0f}  MDD={og_mdd:.0f}  SR={og_sr:.2f}")
print(f"  Best Mid-Bounce: W={int(best['W'])},T>={best['T']:.2f}  Whale={best['whale_pct']:.1f}%  "
      f"Opt={best['Optimal']:+.0f}  MDD={best['MDD']:.0f}  SR={best['Sharpe']:.2f}")

# ── Charts ──
print("\nGenerating charts...")

fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# 1. Heatmap: Optimal PnL by W x T
ax = axes[0, 0]
pivot = df.pivot_table(index='W', columns='T', values='Optimal')
im = ax.imshow(pivot.values, cmap='RdYlGn', aspect='auto')
ax.set_xticks(range(len(pivot.columns)))
ax.set_xticklabels([f'{t:.2f}' for t in pivot.columns])
ax.set_yticks(range(len(pivot.index)))
ax.set_yticklabels([f'W={w}' for w in pivot.index])
for i in range(len(pivot.index)):
    for j in range(len(pivot.columns)):
        v = pivot.values[i, j]
        rob = df[(df['W'] == pivot.index[i]) & (df['T'] == pivot.columns[j])]['Robust'].values[0]
        marker = '*' if rob else ''
        ax.text(j, i, f'{v:+.0f}{marker}', ha='center', va='center', fontsize=8,
                fontweight='bold' if rob else 'normal')
plt.colorbar(im, ax=ax)
ax.set_title('Optimal PnL by W x T (* = robust)')
ax.set_xlabel('Threshold T')

# 2. Heatmap: Whale% by W x T
ax = axes[0, 1]
pivot2 = df.pivot_table(index='W', columns='T', values='whale_pct')
im2 = ax.imshow(pivot2.values, cmap='YlOrRd', aspect='auto')
ax.set_xticks(range(len(pivot2.columns)))
ax.set_xticklabels([f'{t:.2f}' for t in pivot2.columns])
ax.set_yticks(range(len(pivot2.index)))
ax.set_yticklabels([f'W={w}' for w in pivot2.index])
for i in range(len(pivot2.index)):
    for j in range(len(pivot2.columns)):
        ax.text(j, i, f'{pivot2.values[i, j]:.0f}%', ha='center', va='center', fontsize=9)
plt.colorbar(im2, ax=ax)
ax.set_title('Whale % by W x T')
ax.set_xlabel('Threshold T')

# 3. Equity curves: top 5 robust + original + baseline
ax = axes[1, 0]

# Baseline (pure momentum + grid)
base_per_round = np.zeros(N)
for i in range(N):
    base_per_round[i] = (grid_pnl[i] if grid_active[i] else 0) + (mom_pnl[i] if mom_active[i] else 0)
ax.plot(np.cumsum(base_per_round), color='gray', alpha=0.5, linewidth=1, label='Baseline(mom+grid)')

# Original DipBuy
ax.plot(og_cum, color='black', linewidth=1.5, linestyle='--', label=f'OG-DipBuy(SR={og_sr:.1f})')

# Top 5 robust by Sharpe
colors = ['#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00']
top5 = df[df['Robust']].nlargest(5, 'Sharpe') if len(df[df['Robust']]) >= 5 else df.nlargest(5, 'Sharpe')

for idx, (_, r) in enumerate(top5.iterrows()):
    W_val = int(r['W'])
    T_val = r['T']
    
    rwr_temp = rolling_wr(mid_bounce_event, W_val)
    wh_temp = (rwr_temp >= T_val)
    wh_temp[np.isnan(rwr_temp)] = False
    
    opt_temp = np.zeros(N)
    for i in range(N):
        if wh_temp[i]:
            opt_temp[i] = fade_pnl[i] if mom_active[i] else 0
        else:
            opt_temp[i] = (grid_pnl[i] if grid_active[i] else 0) + (mom_pnl[i] if mom_active[i] else 0)
    
    ax.plot(np.cumsum(opt_temp), color=colors[idx], linewidth=1.5,
            label=f'W={W_val},T={T_val:.2f}(SR={r["Sharpe"]:.1f})')

ax.set_title('Equity Curves: Top 5 Robust Configs')
ax.set_xlabel('Round #')
ax.set_ylabel('Cumulative PnL ($)')
ax.legend(fontsize=7, loc='upper left')
ax.grid(True, alpha=0.3)

# 4. Bar chart: 4-bucket breakdown for best config
ax = axes[1, 1]
if len(df[df['Robust']]) > 0:
    best_row = df[df['Robust']].loc[df[df['Robust']]['Sharpe'].idxmax()]
else:
    best_row = df.loc[df['Sharpe'].idxmax()]

buckets = ['B1\nWhale+Fade', 'B2\nWhale+Normal', 'B3\nNormal+Normal', 'B4\nNormal+Fade']
vals = [best_row['B1_fade'], best_row['B2_wh_norm'], best_row['B3_nw_norm'], best_row['B4_nw_fade']]
colors_bar = ['green' if v > 0 else 'red' for v in vals]
bars = ax.bar(buckets, vals, color=colors_bar, alpha=0.7, edgecolor='black')
for bar, v in zip(bars, vals):
    ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 5, f'${v:+.0f}',
            ha='center', va='bottom', fontsize=10, fontweight='bold')
ax.axhline(y=0, color='black', linewidth=0.5)
ax.set_title(f'4-Bucket: W={int(best_row["W"])}, T>={best_row["T"]:.2f} '
             f'(Whale={best_row["whale_pct"]:.0f}%, SR={best_row["Sharpe"]:.1f})')
ax.set_ylabel('PnL ($)')
ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'mid_bounce_4bucket.png'), dpi=150)
print(f"  Saved {os.path.join(OUT_DIR, 'mid_bounce_4bucket.png')}")

# ── Extra chart: Sharpe heatmap ──
fig2, ax2 = plt.subplots(figsize=(10, 6))
pivot_sr = df.pivot_table(index='W', columns='T', values='Sharpe')
im3 = ax2.imshow(pivot_sr.values, cmap='RdYlGn', aspect='auto')
ax2.set_xticks(range(len(pivot_sr.columns)))
ax2.set_xticklabels([f'{t:.2f}' for t in pivot_sr.columns])
ax2.set_yticks(range(len(pivot_sr.index)))
ax2.set_yticklabels([f'W={w}' for w in pivot_sr.index])
for i in range(len(pivot_sr.index)):
    for j in range(len(pivot_sr.columns)):
        v = pivot_sr.values[i, j]
        rob = df[(df['W'] == pivot_sr.index[i]) & (df['T'] == pivot_sr.columns[j])]['Robust'].values[0]
        marker = '*' if rob else ''
        ax2.text(j, i, f'{v:.1f}{marker}', ha='center', va='center', fontsize=9,
                fontweight='bold' if rob else 'normal')
plt.colorbar(im3, ax=ax2)
ax2.set_title('Sharpe Ratio by W x T (* = 3-period robust)')
ax2.set_xlabel('Threshold T (min rolling WR to trigger whale)')
ax2.set_ylabel('Window W (lookback events)')
plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'sharpe_heatmap.png'), dpi=150)
print(f"  Saved {os.path.join(OUT_DIR, 'sharpe_heatmap.png')}")

print("\nDone!")
