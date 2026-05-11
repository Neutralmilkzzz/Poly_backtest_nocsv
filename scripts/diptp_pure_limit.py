"""
DipTP 纯限价单版本 (settle fallback)
====================================
真正零市价单策略：
  1. 限价买单：挂在 cheap 价位
  2. 限价卖单：挂在 cheap + tp 价位
  3. TP命中 → 赚 tp × shares
  4. TP没命中 → 自动结算($1 或 $0)，零额外操作

对比三种退出模式：
  A) TP-only:  只统计TP命中的交易
  B) TP+Settle: TP没中就持有到结算（纯限价单）
  C) TP+Deadline: TP没中在295s市价卖（原版，需taker单）
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

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BASE, 'results', 'diptp_sweep')
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50

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

# ── Whale mask T=3/7 ──
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
print(f"Whale: {len(whale_idx)}, Normal: {len(normal_idx)}")


def ffill(a):
    out = a.astype(np.float64).copy()
    mask = np.isnan(out)
    if not mask.any():
        return out
    idx = np.where(~mask, np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    return out[idx]


def run_diptp_3modes(round_indices, cheap, tp, entry_start, entry_end):
    """Run DipTP and return per-trade results with all 3 exit modes"""
    results = []
    for i in round_indices:
        rid = rids[i]
        if rid not in all_ticks:
            continue
        ticks = all_ticks[rid]
        settle = settlement[i]
        e = ticks['elapsed'].astype(np.float64)

        s_idx = int(np.searchsorted(e, entry_start))
        e_idx = int(np.searchsorted(e, entry_end))
        if s_idx >= len(e) or e_idx <= s_idx:
            continue

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

            tp_price = ep + tp

            # Check if TP hits before settlement
            after_bid = bid[ci:]
            tp_hit = np.any(after_bid >= tp_price)

            # Settlement PnL
            if settle == side:
                settle_pnl = (1.0 - ep) * SHARES
                settle_win = True
            else:
                settle_pnl = -ep * SHARES
                settle_win = False

            # Deadline exit PnL (t=295)
            dl_idx = int(np.searchsorted(e, 295))
            if dl_idx >= len(e):
                dl_idx = len(e) - 1
            dl_bid = float(bid[dl_idx])
            if np.isnan(dl_bid) or dl_bid <= 0:
                dl_pnl = settle_pnl
            else:
                dl_pnl = (dl_bid - ep) * SHARES

            # 3 exit modes
            if tp_hit:
                pnl_tp_only = tp * SHARES
                pnl_tp_settle = tp * SHARES
                pnl_tp_deadline = tp * SHARES
                exit_mode = 'tp'
            else:
                pnl_tp_only = None  # skip this trade
                pnl_tp_settle = settle_pnl
                pnl_tp_deadline = dl_pnl
                exit_mode = 'settle' if settle_win else 'settle_loss'

            results.append({
                'round_idx': i,
                'side': side,
                'entry_price': ep,
                'tp_hit': tp_hit,
                'settle_win': settle_win,
                'pnl_tp_only': pnl_tp_only,
                'pnl_tp_settle': pnl_tp_settle,
                'pnl_tp_deadline': pnl_tp_deadline,
                'exit_mode': exit_mode,
            })
            break

    return results


# ═══════════════════════════════════════════════════════════════
# Test key configs across all 3 exit modes
# ═══════════════════════════════════════════════════════════════

configs = [
    # (cheap, tp, window_start, window_end, label)
    (0.25, 0.60, 120, 200, 'c25_tp60_early'),
    (0.25, 0.50, 120, 200, 'c25_tp50_early'),
    (0.25, 0.40, 120, 200, 'c25_tp40_early'),
    (0.20, 0.60, 150, 240, 'c20_tp60_mid'),
    (0.20, 0.50, 150, 240, 'c20_tp50_mid'),
    (0.20, 0.40, 150, 240, 'c20_tp40_mid'),
    (0.15, 0.60, 150, 240, 'c15_tp60_mid'),
    (0.15, 0.50, 150, 240, 'c15_tp50_mid'),
    (0.15, 0.40, 150, 240, 'c15_tp40_mid'),
    (0.10, 0.60, 150, 270, 'c10_tp60_latemid'),
    (0.10, 0.50, 150, 270, 'c10_tp50_latemid'),
    (0.08, 0.60, 120, 280, 'c08_tp60_full'),
    (0.08, 0.50, 120, 280, 'c08_tp50_full'),
    (0.12, 0.50, 150, 240, 'c12_tp50_mid'),
    (0.12, 0.40, 150, 240, 'c12_tp40_mid'),
    (0.20, 0.30, 150, 240, 'c20_tp30_mid'),
    (0.25, 0.30, 120, 200, 'c25_tp30_early'),
    (0.30, 0.50, 120, 200, 'c30_tp50_early'),
    (0.30, 0.40, 120, 200, 'c30_tp40_early'),
    (0.30, 0.30, 120, 200, 'c30_tp30_early'),
]

print(f"\nTesting {len(configs)} configs × 3 exit modes...")
print("="*90)

rows = []
for cheap, tp, ws, we, label in configs:
    w_res = run_diptp_3modes(whale_idx, cheap, tp, ws, we)
    n_res = run_diptp_3modes(normal_idx, cheap, tp, ws, we)

    for period, res, n_total in [('whale', w_res, len(whale_idx)),
                                  ('normal', n_res, len(normal_idx))]:
        n_trades = len(res)
        n_tp = sum(1 for r in res if r['tp_hit'])
        n_settle_win = sum(1 for r in res if not r['tp_hit'] and r['settle_win'])
        n_settle_loss = sum(1 for r in res if not r['tp_hit'] and not r['settle_win'])

        # Mode A: TP-only (pure profit, skip non-TP trades)
        tp_only_pnl = sum(r['pnl_tp_only'] for r in res if r['pnl_tp_only'] is not None)

        # Mode B: TP + Settle (pure limit order, zero taker)
        tp_settle_pnl = sum(r['pnl_tp_settle'] for r in res)

        # Mode C: TP + Deadline exit (needs taker at 295s)
        tp_deadline_pnl = sum(r['pnl_tp_deadline'] for r in res)

        rows.append({
            'config': label,
            'cheap': cheap, 'tp': tp,
            'period': period,
            'trades': n_trades,
            'tp_hits': n_tp,
            'settle_wins': n_settle_win,
            'settle_losses': n_settle_loss,
            'tp_rate': round(n_tp/n_trades*100, 1) if n_trades > 0 else 0,
            'settle_wr': round(n_settle_win/(n_settle_win+n_settle_loss)*100, 1) if (n_settle_win+n_settle_loss) > 0 else 0,
            'pnl_A_tp_only': round(tp_only_pnl, 1),
            'pnl_B_tp_settle': round(tp_settle_pnl, 1),
            'pnl_C_tp_deadline': round(tp_deadline_pnl, 1),
            'avg_B': round(tp_settle_pnl/n_trades, 2) if n_trades > 0 else 0,
        })

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT_DIR, 'diptp_3modes_results.csv'), index=False)

# ── Pretty print comparison ──
print("\n" + "="*95)
print("🏆 三种退出模式对比 — 鲸鱼期 (T=3/7, 492轮)")
print("="*95)
print(f"{'Config':<20} {'笔数':>4} {'TP命中':>6} {'TP率':>5} {'结算WR':>6} │ "
      f"{'A:仅TP':>8} {'B:TP+结算':>9} {'C:TP+强平':>9} │ {'B每笔':>6}")
print("-"*95)

whale_df = df[df['period'] == 'whale'].sort_values('pnl_B_tp_settle', ascending=False)
for _, r in whale_df.iterrows():
    marker = '★' if r['pnl_B_tp_settle'] > 200 else ' '
    print(f"{marker}{r['config']:<19} {r['trades']:>4} {r['tp_hits']:>6} "
          f"{r['tp_rate']:>4.0f}% {r['settle_wr']:>5.0f}% │ "
          f"{r['pnl_A_tp_only']:>+8.0f} {r['pnl_B_tp_settle']:>+9.0f} {r['pnl_C_tp_deadline']:>+9.0f} │ "
          f"{r['avg_B']:>+6.2f}")

print("\n" + "="*95)
print("📊 同配置 Whale vs Normal 对比 (Mode B: 纯限价单)")
print("="*95)
print(f"{'Config':<20} │ {'鲸鱼笔':>5} {'鲸鱼PnL':>8} {'鲸鱼avg':>7} │ "
      f"{'正常笔':>5} {'正常PnL':>8} {'正常avg':>7} │ {'差异':>7}")
print("-"*95)

for label in whale_df['config'].values:
    w = df[(df['config'] == label) & (df['period'] == 'whale')].iloc[0]
    n = df[(df['config'] == label) & (df['period'] == 'normal')].iloc[0]
    diff = w['avg_B'] - n['avg_B']
    marker = '🔥' if diff > 1.0 else '  '
    print(f"{marker}{label:<18} │ {w['trades']:>5} {w['pnl_B_tp_settle']:>+8.0f} {w['avg_B']:>+7.2f} │ "
          f"{n['trades']:>5} {n['pnl_B_tp_settle']:>+8.0f} {n['avg_B']:>+7.2f} │ {diff:>+7.2f}")


# ═══════════════════════════════════════════════════════════════
# Visualization: 3 modes comparison
# ═══════════════════════════════════════════════════════════════

fig, axes = plt.subplots(1, 3, figsize=(20, 7))
fig.suptitle('DipTP: Three Exit Modes Compared (Whale Period)\n'
             'A=TP-Only | B=TP+Settlement (Pure Limit) | C=TP+Deadline (Needs Taker)',
             fontsize=13, fontweight='bold')

w_df = df[df['period'] == 'whale'].copy()
w_df = w_df.sort_values('pnl_B_tp_settle', ascending=True)
labels = w_df['config'].values
y = np.arange(len(labels))

# Panel 1: PnL comparison
ax = axes[0]
width = 0.25
ax.barh(y - width, w_df['pnl_A_tp_only'].values, width, label='A: 仅TP命中', color='#2196F3', alpha=0.8)
ax.barh(y, w_df['pnl_B_tp_settle'].values, width, label='B: TP+结算 (纯限价)', color='#4CAF50', alpha=0.8)
ax.barh(y + width, w_df['pnl_C_tp_deadline'].values, width, label='C: TP+强平 (需taker)', color='#FF9800', alpha=0.8)
ax.set_yticks(y)
ax.set_yticklabels(labels, fontsize=7)
ax.set_xlabel('PnL ($)')
ax.set_title('Total PnL by Exit Mode')
ax.axvline(0, color='black', linewidth=0.5)
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3, axis='x')

# Panel 2: TP rate vs Settle WR
ax = axes[1]
ax.barh(y - 0.15, w_df['tp_rate'].values, 0.3, label='TP命中率', color='#2196F3', alpha=0.8)
ax.barh(y + 0.15, w_df['settle_wr'].values, 0.3, label='未TP时结算胜率', color='#E91E63', alpha=0.8)
ax.set_yticks(y)
ax.set_yticklabels(labels, fontsize=7)
ax.set_xlabel('%')
ax.set_title('TP Hit Rate vs Settlement Win Rate')
ax.legend(fontsize=8)
ax.grid(True, alpha=0.3, axis='x')

# Panel 3: Equity curves for top B configs
ax = axes[2]
top_b = w_df.nlargest(5, 'pnl_B_tp_settle')
for _, r in top_b.iterrows():
    cfg = r['config']
    c, t = r['cheap'], r['tp']
    # Find matching window
    for cc, tt, ws, we, ll in configs:
        if ll == cfg:
            break
    res = run_diptp_3modes(whale_idx, c, t, ws, we)
    pnls = [x['pnl_tp_settle'] for x in res]
    cum = np.cumsum(pnls)
    ax.plot(cum, label=f'{cfg} ({r["pnl_B_tp_settle"]:+.0f})', linewidth=1.2)

ax.set_xlabel('Trade #')
ax.set_ylabel('Cumulative PnL ($)')
ax.set_title('Top 5 Equity Curves (Mode B: Pure Limit)')
ax.axhline(0, color='gray', linewidth=0.5, linestyle='--')
ax.legend(fontsize=7)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'diptp_3modes_comparison.png'), dpi=150, bbox_inches='tight')
print(f"\n✅ Chart saved to {os.path.join(OUT_DIR, 'diptp_3modes_comparison.png')}")

# ── Final summary ──
print("\n" + "="*70)
best_b = w_df.nlargest(1, 'pnl_B_tp_settle').iloc[0]
best_c = w_df.nlargest(1, 'pnl_C_tp_deadline').iloc[0]
print(f"🏆 纯限价单最优 (Mode B): {best_b['config']}")
print(f"   PnL={best_b['pnl_B_tp_settle']:+.0f}  笔数={best_b['trades']}  "
      f"TP率={best_b['tp_rate']:.0f}%  结算WR={best_b['settle_wr']:.0f}%  "
      f"每笔={best_b['avg_B']:+.2f}")
print(f"\n💡 对比需taker单的Mode C最优: {best_c['config']}")
print(f"   PnL={best_c['pnl_C_tp_deadline']:+.0f}")
print(f"\n⚡ 纯限价单的执行优势:")
print(f"   - 零滑点, 零taker费")
print(f"   - 买单: 限价 @ {best_b['cheap']:.2f}")
print(f"   - 卖单: 限价 @ {best_b['cheap']+best_b['tp']:.2f}")
print(f"   - 未TP → 自动结算 (胜率{best_b['settle_wr']:.0f}%)")
