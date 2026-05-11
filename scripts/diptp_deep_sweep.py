"""
DipTP (接飞刀止盈) 深度参数扫描
================================
核心优势：只用两个限价单(maker)，零滑点，零taker费
  1. 限价买单：挂在 cheap 价位，ask 跌到就成交
  2. 限价卖单：挂在 buy_price + tp，bid 涨到就止盈

参数空间：
  - cheap:  买入阈值 (0.05 ~ 0.35)
  - tp:     止盈幅度 (0.05 ~ 0.70)
  - window: 下单窗口 (全程 vs 中盘 vs 后半)
  - deadline: 未止盈时强平时间

同时测试：
  A) 仅鲸鱼期 (T=3/7)
  B) 全市场（验证是否限价单策略不需要regime）
"""

import pandas as pd
import numpy as np
import pickle
import os
import warnings
warnings.filterwarnings('ignore')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from itertools import product

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'results', 'diptp_sweep')
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50

# ── Load data ──
print("Loading data...")
existing = pd.read_csv(os.path.join(BASE, 'results', 'three_strategies_fixed',
                                     'three_strategies_fixed.csv'))
N = len(existing)
rids = existing['round_id'].values
settlement = existing['f_settlement'].values

with open(os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_probe_v2.pkl'), 'rb') as f:
    probe = pickle.load(f)
with open(os.path.join(BASE, 'results', 'fake_breakout', '_cache_all_ticks.pkl'), 'rb') as f:
    all_ticks = pickle.load(f)

# ── Build whale mask (T=3/7) ──
mid_events = np.full(N, np.nan)
for i, rid in enumerate(rids):
    if rid not in probe:
        continue
    w = probe[rid].get('mid')
    if w is None:
        continue
    for e in w:
        if e['min_ask'] <= 0.12:
            mid_events[i] = 1.0 if e['max_bid_after'] >= 0.50 else 0.0
            break

rwr = np.full(N, np.nan)
for i in range(N):
    cnt = 0; wins = 0
    for j in range(i - 1, -1, -1):
        if np.isnan(mid_events[j]):
            continue
        cnt += 1
        wins += mid_events[j]
        if cnt >= 7:
            break
    if cnt >= 7:
        rwr[i] = wins / cnt

whale_mask = (rwr >= 3/7 - 0.01)
whale_mask[np.isnan(rwr)] = False
normal_mask = ~whale_mask & ~np.isnan(rwr)

whale_idx = np.where(whale_mask)[0]
normal_idx = np.where(normal_mask)[0]
all_idx = np.arange(N)
print(f"Total: {N}, Whale: {len(whale_idx)}, Normal: {len(normal_idx)}")


def ffill(a):
    out = a.astype(np.float64).copy()
    mask = np.isnan(out)
    if not mask.any():
        return out
    idx = np.where(~mask, np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    return out[idx]


def run_diptp(round_idx, ticks_data, settlements, cheap, tp, entry_start, entry_end, deadline):
    """
    DipTP 策略：限价买 + 限价卖
    买入条件：在 [entry_start, entry_end] 窗口内，某一方 ask ≤ cheap
    卖出条件：bid ≥ entry_price + tp，或 deadline 时按 bid 强平
    """
    results = []
    for i in round_idx:
        rid = rids[i]
        if rid not in ticks_data:
            continue
        ticks = ticks_data[rid]
        settle = settlements[i]
        e = ticks['elapsed'].astype(np.float64)

        s_idx = int(np.searchsorted(e, entry_start))
        e_idx = int(np.searchsorted(e, entry_end))
        if s_idx >= len(e) or e_idx <= s_idx:
            continue

        traded = False
        for side in ['up', 'down']:
            ask = ffill(ticks[f'{side}_ask'])
            bid = ffill(ticks[f'{side}_bid'])

            seg_ask = ask[s_idx:e_idx]
            valid = (~np.isnan(seg_ask)) & (seg_ask > 0)
            cheap_mask = (seg_ask <= cheap) & valid
            if not cheap_mask.any():
                continue

            ci = int(np.argmax(cheap_mask)) + s_idx
            ep = float(ask[ci])
            if np.isnan(ep) or ep <= 0:
                continue

            # TP exit: limit sell at ep + tp
            dl_idx = int(np.searchsorted(e, deadline))
            if dl_idx >= len(e):
                dl_idx = len(e) - 1

            after_bid = bid[ci:dl_idx + 1]
            tp_price = ep + tp
            tp_mask = after_bid >= tp_price
            if tp_mask.any():
                pnl = tp * SHARES
                exit_type = 'tp'
            else:
                # Force exit at deadline bid, or hold to settlement
                exit_bid = float(bid[dl_idx])
                if np.isnan(exit_bid) or exit_bid <= 0:
                    # Fall back to settlement
                    if settle == side:
                        pnl = (1.0 - ep) * SHARES
                    else:
                        pnl = -ep * SHARES
                    exit_type = 'settle'
                else:
                    pnl = (exit_bid - ep) * SHARES
                    exit_type = 'deadline'

            results.append({
                'round_idx': i,
                'side': side,
                'entry_price': ep,
                'tp_price': tp_price,
                'pnl': pnl,
                'exit_type': exit_type,
                'entry_time': float(e[ci])
            })
            traded = True
            break  # one trade per round

    return results


# ═══════════════════════════════════════════════════════════════
# Parameter sweep
# ═══════════════════════════════════════════════════════════════

cheaps = [0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30]
tps = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60]
windows = [
    ('full', 120, 280),     # 全程（tick数据从120s开始）
    ('mid', 150, 240),      # 中盘
    ('late_mid', 150, 270), # 中盘延伸
    ('early', 120, 200),    # 前半
]
deadline = 295

print(f"\nSweeping {len(cheaps)}×{len(tps)}×{len(windows)} = "
      f"{len(cheaps)*len(tps)*len(windows)} configs...")

rows = []
total = len(cheaps) * len(tps) * len(windows)
done = 0

for wname, ws, we in windows:
    for c in cheaps:
        for t in tps:
            # Skip impossible configs (tp + cheap > 1.0)
            if c + t > 0.95:
                done += 1
                continue

            # Run on whale rounds
            w_res = run_diptp(whale_idx, all_ticks, settlement, c, t, ws, we, deadline)
            w_trades = len(w_res)
            w_pnl = sum(r['pnl'] for r in w_res) if w_res else 0
            w_tp_rate = (sum(1 for r in w_res if r['exit_type'] == 'tp') / w_trades * 100) if w_trades > 0 else 0

            # Run on normal rounds
            n_res = run_diptp(normal_idx, all_ticks, settlement, c, t, ws, we, deadline)
            n_trades = len(n_res)
            n_pnl = sum(r['pnl'] for r in n_res) if n_res else 0
            n_tp_rate = (sum(1 for r in n_res if r['exit_type'] == 'tp') / n_trades * 100) if n_trades > 0 else 0

            # Run on ALL rounds
            a_res = run_diptp(all_idx, all_ticks, settlement, c, t, ws, we, deadline)
            a_trades = len(a_res)
            a_pnl = sum(r['pnl'] for r in a_res) if a_res else 0
            a_tp_rate = (sum(1 for r in a_res if r['exit_type'] == 'tp') / a_trades * 100) if a_trades > 0 else 0

            rows.append({
                'window': wname,
                'cheap': c,
                'tp': t,
                'w_trades': w_trades,
                'w_pnl': round(w_pnl, 1),
                'w_avg': round(w_pnl / w_trades, 2) if w_trades > 0 else 0,
                'w_tp_rate': round(w_tp_rate, 1),
                'n_trades': n_trades,
                'n_pnl': round(n_pnl, 1),
                'n_avg': round(n_pnl / n_trades, 2) if n_trades > 0 else 0,
                'n_tp_rate': round(n_tp_rate, 1),
                'a_trades': a_trades,
                'a_pnl': round(a_pnl, 1),
                'a_avg': round(a_pnl / a_trades, 2) if a_trades > 0 else 0,
                'a_tp_rate': round(a_tp_rate, 1),
            })
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{total} configs done...")

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT_DIR, 'diptp_sweep_results.csv'), index=False)
print(f"\nTotal configs: {len(df)}")

# ═══════════════════════════════════════════════════════════════
# Analysis & Reporting
# ═══════════════════════════════════════════════════════════════

print("\n" + "="*70)
print("🏆 DipTP 深度扫描结果")
print("="*70)

# Filter configs with at least some trades
df_w = df[df['w_trades'] >= 10].copy()
df_a = df[df['a_trades'] >= 50].copy()

# ── Top by whale PnL ──
print("\n📊 鲸鱼期 Top 15 (按PnL):")
top_w = df_w.nlargest(15, 'w_pnl')
print(f"{'Window':<10} {'Cheap':>5} {'TP':>5} {'Trades':>6} {'PnL':>8} {'Avg':>7} {'TP%':>6}")
print("-" * 55)
for _, r in top_w.iterrows():
    print(f"{r['window']:<10} {r['cheap']:>5.2f} {r['tp']:>5.2f} "
          f"{r['w_trades']:>6} {r['w_pnl']:>8.1f} {r['w_avg']:>7.2f} {r['w_tp_rate']:>5.1f}%")

# ── Top by whale avg PnL per trade ──
df_w10 = df_w[df_w['w_trades'] >= 30]
if len(df_w10) > 0:
    print("\n📊 鲸鱼期 Top 15 (按每笔收益, ≥30笔):")
    top_wa = df_w10.nlargest(15, 'w_avg')
    print(f"{'Window':<10} {'Cheap':>5} {'TP':>5} {'Trades':>6} {'PnL':>8} {'Avg':>7} {'TP%':>6}")
    print("-" * 55)
    for _, r in top_wa.iterrows():
        print(f"{r['window']:<10} {r['cheap']:>5.2f} {r['tp']:>5.2f} "
              f"{r['w_trades']:>6} {r['w_pnl']:>8.1f} {r['w_avg']:>7.2f} {r['w_tp_rate']:>5.1f}%")

# ── Top by ALL-market PnL ──
print("\n📊 全市场 Top 15 (按PnL):")
top_a = df_a.nlargest(15, 'a_pnl')
print(f"{'Window':<10} {'Cheap':>5} {'TP':>5} {'Trades':>6} {'PnL':>8} {'Avg':>7} {'TP%':>6}")
print("-" * 55)
for _, r in top_a.iterrows():
    print(f"{r['window']:<10} {r['cheap']:>5.2f} {r['tp']:>5.2f} "
          f"{r['a_trades']:>6} {r['a_pnl']:>8.1f} {r['a_avg']:>7.2f} {r['a_tp_rate']:>5.1f}%")

# ── Compare whale vs normal for best configs ──
print("\n📊 最佳配置的 Whale vs Normal 对比:")
best_ids = top_w.head(5).index
for idx in best_ids:
    r = df.loc[idx]
    print(f"\n  {r['window']} c={r['cheap']:.2f} tp={r['tp']:.2f}:")
    print(f"    鲸鱼: {r['w_trades']}笔  PnL={r['w_pnl']:+.1f}  avg={r['w_avg']:+.2f}  TP率={r['w_tp_rate']:.1f}%")
    print(f"    正常: {r['n_trades']}笔  PnL={r['n_pnl']:+.1f}  avg={r['n_avg']:+.2f}  TP率={r['n_tp_rate']:.1f}%")
    print(f"    全部: {r['a_trades']}笔  PnL={r['a_pnl']:+.1f}  avg={r['a_avg']:+.2f}  TP率={r['a_tp_rate']:.1f}%")

# ═══════════════════════════════════════════════════════════════
# Heatmap visualization: cheap × tp for each window
# ═══════════════════════════════════════════════════════════════

fig, axes = plt.subplots(2, 4, figsize=(22, 10))
fig.suptitle('DipTP Strategy: PnL Heatmaps (cheap × tp)\nTop: Whale Period | Bottom: All Market',
             fontsize=14, fontweight='bold')

for col, (wname, ws, we) in enumerate(windows):
    sub = df[df['window'] == wname]
    if sub.empty:
        continue

    for row, (label, col_name) in enumerate([('Whale', 'w_pnl'), ('All', 'a_pnl')]):
        ax = axes[row, col]
        pivot = sub.pivot_table(index='cheap', columns='tp', values=col_name, aggfunc='first')
        pivot = pivot.sort_index(ascending=False)

        im = ax.imshow(pivot.values, aspect='auto', cmap='RdYlGn',
                       vmin=-500, vmax=max(1500, pivot.values[~np.isnan(pivot.values)].max() if not np.all(np.isnan(pivot.values)) else 1500))
        ax.set_xticks(range(len(pivot.columns)))
        ax.set_xticklabels([f'{v:.2f}' for v in pivot.columns], fontsize=7, rotation=45)
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels([f'{v:.2f}' for v in pivot.index], fontsize=7)
        ax.set_xlabel('Take Profit')
        ax.set_ylabel('Cheap Threshold')
        ax.set_title(f'{label} | {wname} ({ws}-{we}s)')

        # Annotate cells
        for yi in range(pivot.shape[0]):
            for xi in range(pivot.shape[1]):
                v = pivot.values[yi, xi]
                if not np.isnan(v):
                    color = 'white' if abs(v) > 800 else 'black'
                    ax.text(xi, yi, f'{v:.0f}', ha='center', va='center',
                            fontsize=5, color=color)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'diptp_heatmaps.png'), dpi=150, bbox_inches='tight')
print(f"\n✅ Heatmap saved to {os.path.join(OUT_DIR, 'diptp_heatmaps.png')}")

# ═══════════════════════════════════════════════════════════════
# Equity curve for top 3 configs
# ═══════════════════════════════════════════════════════════════

print("\n📈 Drawing equity curves for top 3 configs...")
top3 = top_w.head(3)
fig2, axes2 = plt.subplots(1, 3, figsize=(18, 5))
fig2.suptitle('DipTP Top 3 Configs — Equity Curves (Whale Period)', fontsize=13, fontweight='bold')

for ax_i, (idx, r) in enumerate(top3.iterrows()):
    c, t, wname = r['cheap'], r['tp'], r['window']
    ws, we = [(s, e) for n, s, e in windows if n == wname][0]

    res = run_diptp(whale_idx, all_ticks, settlement, c, t, ws, we, deadline)
    if not res:
        continue

    pnls = [x['pnl'] for x in res]
    cum = np.cumsum(pnls)

    ax = axes2[ax_i]
    ax.plot(cum, linewidth=1.2)
    ax.axhline(0, color='gray', linewidth=0.5, linestyle='--')
    ax.set_title(f'{wname} c={c:.2f} tp={t:.2f}\nPnL={sum(pnls):.0f} | {len(pnls)}trades | '
                 f'TP率={r["w_tp_rate"]:.0f}%', fontsize=10)
    ax.set_xlabel('Trade #')
    ax.set_ylabel('Cumulative PnL ($)')
    ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'diptp_equity_curves.png'), dpi=150, bbox_inches='tight')
print(f"✅ Equity curves saved")

# ═══════════════════════════════════════════════════════════════
# TP Rate vs Avg PnL scatter
# ═══════════════════════════════════════════════════════════════

fig3, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
fig3.suptitle('DipTP: TP Rate vs Avg PnL per Trade', fontsize=13, fontweight='bold')

# Whale
sub_w = df_w[df_w['w_trades'] >= 20]
sc1 = ax1.scatter(sub_w['w_tp_rate'], sub_w['w_avg'], c=sub_w['w_pnl'],
                  cmap='RdYlGn', s=40, alpha=0.7, edgecolors='gray', linewidth=0.5)
ax1.set_xlabel('TP Hit Rate (%)')
ax1.set_ylabel('Avg PnL per Trade ($)')
ax1.set_title('Whale Period')
ax1.axhline(0, color='gray', linewidth=0.5, linestyle='--')
ax1.grid(True, alpha=0.3)
plt.colorbar(sc1, ax=ax1, label='Total PnL')

# All
sub_a = df_a[df_a['a_trades'] >= 100]
sc2 = ax2.scatter(sub_a['a_tp_rate'], sub_a['a_avg'], c=sub_a['a_pnl'],
                  cmap='RdYlGn', s=40, alpha=0.7, edgecolors='gray', linewidth=0.5)
ax2.set_xlabel('TP Hit Rate (%)')
ax2.set_ylabel('Avg PnL per Trade ($)')
ax2.set_title('All Market')
ax2.axhline(0, color='gray', linewidth=0.5, linestyle='--')
ax2.grid(True, alpha=0.3)
plt.colorbar(sc2, ax=ax2, label='Total PnL')

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'diptp_scatter.png'), dpi=150, bbox_inches='tight')
print(f"✅ Scatter plot saved")

print("\n🏁 DipTP sweep complete!")
