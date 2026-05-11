"""
鲸鱼期间跟庄策略对比回测
========================
已识别 whale 轮次后，测试多种跟庄/反杀策略：

策略1: Fade-Hold（反打持有）
  - 庄家砸哪边，买另一边，持有到结算
  - 逻辑：庄家砸盘制造恐慌，最终会拉回

策略2: Momentum-Follow（跟庄同向）
  - 庄家拉哪边，跟着买同一边，持有到结算
  - 逻辑：庄家方向就是最终结算方向

策略3: Fade-Scalp（反打止盈）
  - 买被砸的一边，但不等结算，设止盈就跑
  - 逻辑：吃砸盘后的反弹差价

策略4: Mid-Dip-Buy（中盘抄底）
  - 在中盘(150-240s)检测到暴跌时直接买入最便宜方
  - 逻辑：在庄家砸盘的最低点抄底

策略5: Late-Momentum（尾盘跟强）
  - 在t=250s后，买当前领先方（bid最高的一边）
  - 逻辑：尾盘格局已定，跟强势方

所有策略用 tick 级数据模拟，考虑 ask 价买入。
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
OUT_DIR = os.path.join(BASE, 'results', 'whale_counter')
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

# ── Build whale mask ──
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

whale_mask = (rwr >= 0.25)
whale_mask[np.isnan(rwr)] = False
whale_indices = np.where(whale_mask)[0]
print(f"Whale rounds: {len(whale_indices)}")


def ffill(a):
    out = a.astype(np.float64).copy()
    mask = np.isnan(out)
    if not mask.any():
        return out
    idx = np.where(~mask, np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    return out[idx]


# ═══════════════════════════════════════════════════════════════
# Strategy definitions
# ═══════════════════════════════════════════════════════════════

def strategy_fade_hold(ticks, settle, params):
    """反打持有：在mid-window检测砸盘方，买另一边持有到结算"""
    e = ticks['elapsed'].astype(np.float64)
    entry_start = params.get('entry_start', 150)
    entry_end = params.get('entry_end', 240)
    drop_thr = params.get('drop_threshold', 0.15)

    for side in ['up', 'down']:
        opp = 'down' if side == 'up' else 'up'
        bid = ffill(ticks[f'{side}_bid'])
        ask = ffill(ticks[f'{opp}_ask'])

        s_idx = int(np.searchsorted(e, entry_start))
        e_idx = int(np.searchsorted(e, entry_end))
        if s_idx >= len(e) or e_idx <= s_idx:
            continue

        seg = bid[s_idx:e_idx]
        valid = (~np.isnan(seg)) & (seg > 0)
        if not valid.any():
            continue

        peak = np.maximum.accumulate(np.where(valid, seg, -np.inf))
        drop = peak - seg
        needle_mask = (drop >= drop_thr) & valid
        if not needle_mask.any():
            continue

        ni = int(np.argmax(needle_mask)) + s_idx
        entry_price = float(ask[ni])
        if np.isnan(entry_price) or entry_price <= 0 or entry_price >= 0.95:
            continue

        if settle == opp:
            pnl = (1.0 - entry_price) * SHARES
        else:
            pnl = (-entry_price) * SHARES
        return pnl, entry_price, opp, float(e[ni])

    return None


def strategy_momentum_follow(ticks, settle, params):
    """跟庄同向：在mid-window检测拉升方，买同一方持有到结算"""
    e = ticks['elapsed'].astype(np.float64)
    entry_start = params.get('entry_start', 150)
    entry_end = params.get('entry_end', 240)
    rise_thr = params.get('rise_threshold', 0.15)

    for side in ['up', 'down']:
        bid = ffill(ticks[f'{side}_bid'])
        ask = ffill(ticks[f'{side}_ask'])

        s_idx = int(np.searchsorted(e, entry_start))
        e_idx = int(np.searchsorted(e, entry_end))
        if s_idx >= len(e) or e_idx <= s_idx:
            continue

        seg = bid[s_idx:e_idx]
        valid = (~np.isnan(seg)) & (seg > 0)
        if not valid.any():
            continue

        # Detect rise: current - running min
        run_min = np.minimum.accumulate(np.where(valid, seg, np.inf))
        rise = seg - run_min
        rise_mask = (rise >= rise_thr) & valid
        if not rise_mask.any():
            continue

        ri = int(np.argmax(rise_mask)) + s_idx
        entry_price = float(ask[ri])
        if np.isnan(entry_price) or entry_price <= 0 or entry_price >= 0.95:
            continue

        if settle == side:
            pnl = (1.0 - entry_price) * SHARES
        else:
            pnl = (-entry_price) * SHARES
        return pnl, entry_price, side, float(e[ri])

    return None


def strategy_fade_scalp(ticks, settle, params):
    """反打止盈：买被砸方，设止盈就跑（不赌结算）"""
    e = ticks['elapsed'].astype(np.float64)
    entry_start = params.get('entry_start', 150)
    entry_end = params.get('entry_end', 240)
    drop_thr = params.get('drop_threshold', 0.15)
    tp = params.get('take_profit', 0.10)
    deadline = params.get('deadline', 295)

    for side in ['up', 'down']:
        opp = 'down' if side == 'up' else 'up'
        bid_side = ffill(ticks[f'{side}_bid'])
        ask_opp = ffill(ticks[f'{opp}_ask'])
        bid_opp = ffill(ticks[f'{opp}_bid'])

        s_idx = int(np.searchsorted(e, entry_start))
        e_idx = int(np.searchsorted(e, entry_end))
        if s_idx >= len(e) or e_idx <= s_idx:
            continue

        seg = bid_side[s_idx:e_idx]
        valid = (~np.isnan(seg)) & (seg > 0)
        if not valid.any():
            continue

        peak = np.maximum.accumulate(np.where(valid, seg, -np.inf))
        drop = peak - seg
        needle_mask = (drop >= drop_thr) & valid
        if not needle_mask.any():
            continue

        ni = int(np.argmax(needle_mask)) + s_idx
        ep = float(ask_opp[ni])
        if np.isnan(ep) or ep <= 0 or ep >= 0.95:
            continue

        # Look for TP or deadline
        dl_idx = int(np.searchsorted(e, deadline))
        if dl_idx >= len(e):
            dl_idx = len(e) - 1

        after_bid = bid_opp[ni:dl_idx + 1]
        tp_mask = after_bid >= ep + tp
        if tp_mask.any():
            pnl = tp * SHARES
        else:
            exit_p = float(bid_opp[dl_idx])
            if np.isnan(exit_p) or exit_p <= 0:
                continue
            pnl = (exit_p - ep) * SHARES

        return pnl, ep, opp, float(e[ni])

    return None


def strategy_mid_dip_buy(ticks, settle, params):
    """中盘抄底：在中盘检测ask跌破cheap_thr时买入"""
    e = ticks['elapsed'].astype(np.float64)
    entry_start = params.get('entry_start', 150)
    entry_end = params.get('entry_end', 240)
    cheap_thr = params.get('cheap_threshold', 0.12)
    hold_to_settle = params.get('hold_to_settle', True)
    tp = params.get('take_profit', 0.10)
    deadline = params.get('deadline', 295)

    for side in ['up', 'down']:
        ask = ffill(ticks[f'{side}_ask'])
        bid = ffill(ticks[f'{side}_bid'])

        s_idx = int(np.searchsorted(e, entry_start))
        e_idx = int(np.searchsorted(e, entry_end))
        if s_idx >= len(e) or e_idx <= s_idx:
            continue

        seg = ask[s_idx:e_idx]
        valid = (~np.isnan(seg)) & (seg > 0)
        cheap_mask = (seg <= cheap_thr) & valid
        if not cheap_mask.any():
            continue

        ci = int(np.argmax(cheap_mask)) + s_idx
        ep = float(ask[ci])
        if np.isnan(ep) or ep <= 0:
            continue

        if hold_to_settle:
            if settle == side:
                pnl = (1.0 - ep) * SHARES
            else:
                pnl = (-ep) * SHARES
        else:
            dl_idx = int(np.searchsorted(e, deadline))
            if dl_idx >= len(e):
                dl_idx = len(e) - 1
            after_bid = bid[ci:dl_idx + 1]
            tp_mask = after_bid >= ep + tp
            if tp_mask.any():
                pnl = tp * SHARES
            else:
                exit_p = float(bid[dl_idx])
                if np.isnan(exit_p) or exit_p <= 0:
                    continue
                pnl = (exit_p - ep) * SHARES

        return pnl, ep, side, float(e[ci])

    return None


def strategy_late_momentum(ticks, settle, params):
    """尾盘跟强：在t=250后买当前领先方"""
    e = ticks['elapsed'].astype(np.float64)
    entry_t = params.get('entry_time', 250)
    hold_to_settle = params.get('hold_to_settle', True)
    tp = params.get('take_profit', 0.10)
    deadline = params.get('deadline', 295)

    t_idx = int(np.searchsorted(e, entry_t))
    if t_idx >= len(e):
        return None

    up_bid = ffill(ticks['up_bid'])
    dn_bid = ffill(ticks['down_bid'])
    up_ask = ffill(ticks['up_ask'])
    dn_ask = ffill(ticks['down_ask'])

    ub = float(up_bid[t_idx])
    db = float(dn_bid[t_idx])
    if np.isnan(ub) or np.isnan(db):
        return None

    if ub >= db:
        buy_side = 'up'
        ep = float(up_ask[t_idx])
    else:
        buy_side = 'down'
        ep = float(dn_ask[t_idx])

    if np.isnan(ep) or ep <= 0 or ep >= 0.95:
        return None

    bid = up_bid if buy_side == 'up' else dn_bid

    if hold_to_settle:
        if settle == buy_side:
            pnl = (1.0 - ep) * SHARES
        else:
            pnl = (-ep) * SHARES
    else:
        dl_idx = int(np.searchsorted(e, deadline))
        if dl_idx >= len(e):
            dl_idx = len(e) - 1
        after_bid = bid[t_idx:dl_idx + 1]
        tp_mask = after_bid >= ep + tp
        if tp_mask.any():
            pnl = tp * SHARES
        else:
            exit_p = float(bid[dl_idx])
            if np.isnan(exit_p) or exit_p <= 0:
                return None
            pnl = (exit_p - ep) * SHARES

    return pnl, ep, buy_side, entry_t


# ═══════════════════════════════════════════════════════════════
# Strategy configs to test
# ═══════════════════════════════════════════════════════════════

CONFIGS = {
    # ── 策略1: 反打持有 ──
    'Fade-Hold_drop15': (strategy_fade_hold,
        {'drop_threshold': 0.15}),
    'Fade-Hold_drop20': (strategy_fade_hold,
        {'drop_threshold': 0.20}),
    'Fade-Hold_drop25': (strategy_fade_hold,
        {'drop_threshold': 0.25}),
    'Fade-Hold_drop30': (strategy_fade_hold,
        {'drop_threshold': 0.30}),
    'Fade-Hold_drop10': (strategy_fade_hold,
        {'drop_threshold': 0.10}),

    # ── 策略2: 跟庄同向 ──
    'MomFollow_rise15': (strategy_momentum_follow,
        {'rise_threshold': 0.15}),
    'MomFollow_rise20': (strategy_momentum_follow,
        {'rise_threshold': 0.20}),
    'MomFollow_rise25': (strategy_momentum_follow,
        {'rise_threshold': 0.25}),
    'MomFollow_rise10': (strategy_momentum_follow,
        {'rise_threshold': 0.10}),
    'MomFollow_rise30': (strategy_momentum_follow,
        {'rise_threshold': 0.30}),

    # ── 策略3: 反打止盈 ──
    'Fade-Scalp_d15_tp05': (strategy_fade_scalp,
        {'drop_threshold': 0.15, 'take_profit': 0.05}),
    'Fade-Scalp_d15_tp10': (strategy_fade_scalp,
        {'drop_threshold': 0.15, 'take_profit': 0.10}),
    'Fade-Scalp_d15_tp15': (strategy_fade_scalp,
        {'drop_threshold': 0.15, 'take_profit': 0.15}),
    'Fade-Scalp_d20_tp05': (strategy_fade_scalp,
        {'drop_threshold': 0.20, 'take_profit': 0.05}),
    'Fade-Scalp_d20_tp10': (strategy_fade_scalp,
        {'drop_threshold': 0.20, 'take_profit': 0.10}),
    'Fade-Scalp_d20_tp15': (strategy_fade_scalp,
        {'drop_threshold': 0.20, 'take_profit': 0.15}),
    'Fade-Scalp_d25_tp10': (strategy_fade_scalp,
        {'drop_threshold': 0.25, 'take_profit': 0.10}),
    'Fade-Scalp_d25_tp15': (strategy_fade_scalp,
        {'drop_threshold': 0.25, 'take_profit': 0.15}),
    'Fade-Scalp_d25_tp20': (strategy_fade_scalp,
        {'drop_threshold': 0.25, 'take_profit': 0.20}),

    # ── 策略4: 中盘抄底（持有到结算） ──
    'MidDip_c12_hold': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.12, 'hold_to_settle': True}),
    'MidDip_c10_hold': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.10, 'hold_to_settle': True}),
    'MidDip_c08_hold': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.08, 'hold_to_settle': True}),
    'MidDip_c15_hold': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.15, 'hold_to_settle': True}),
    # 中盘抄底（止盈跑路）
    'MidDip_c12_tp10': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.12, 'hold_to_settle': False, 'take_profit': 0.10}),
    'MidDip_c12_tp20': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.12, 'hold_to_settle': False, 'take_profit': 0.20}),
    'MidDip_c15_tp10': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.15, 'hold_to_settle': False, 'take_profit': 0.10}),
    'MidDip_c15_tp20': (strategy_mid_dip_buy,
        {'cheap_threshold': 0.15, 'hold_to_settle': False, 'take_profit': 0.20}),

    # ── 策略5: 尾盘跟强 ──
    'LateMom_t250_hold': (strategy_late_momentum,
        {'entry_time': 250, 'hold_to_settle': True}),
    'LateMom_t260_hold': (strategy_late_momentum,
        {'entry_time': 260, 'hold_to_settle': True}),
    'LateMom_t270_hold': (strategy_late_momentum,
        {'entry_time': 270, 'hold_to_settle': True}),
    'LateMom_t250_tp10': (strategy_late_momentum,
        {'entry_time': 250, 'hold_to_settle': False, 'take_profit': 0.10}),
    'LateMom_t260_tp10': (strategy_late_momentum,
        {'entry_time': 260, 'hold_to_settle': False, 'take_profit': 0.10}),
    'LateMom_t270_tp10': (strategy_late_momentum,
        {'entry_time': 270, 'hold_to_settle': False, 'take_profit': 0.10}),
}

# ═══════════════════════════════════════════════════════════════
# Run all strategies
# ═══════════════════════════════════════════════════════════════

print(f"\nRunning {len(CONFIGS)} strategy configs on {len(whale_indices)} whale rounds...")

results = {}
for name, (func, params) in CONFIGS.items():
    whale_pnls = []
    normal_pnls = []

    for i, rid in enumerate(rids):
        if rid not in all_ticks:
            continue
        res = func(all_ticks[rid], settlement[i], params)
        if res is None:
            continue
        pnl = res[0]
        if whale_mask[i]:
            whale_pnls.append(pnl)
        else:
            normal_pnls.append(pnl)

    wn = len(whale_pnls)
    ws = sum(whale_pnls)
    wwr = sum(1 for p in whale_pnls if p > 0) / wn if wn else 0
    wavg = ws / wn if wn else 0
    nn = len(normal_pnls)
    ns = sum(normal_pnls)
    nwr = sum(1 for p in normal_pnls if p > 0) / nn if nn else 0

    results[name] = {
        'whale_n': wn, 'whale_pnl': ws, 'whale_wr': wwr, 'whale_avg': wavg,
        'normal_n': nn, 'normal_pnl': ns, 'normal_wr': nwr,
        'total_pnl': ws + ns,
    }

# ═══════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 95)
print("ALL STRATEGIES — WHALE PERIOD PERFORMANCE (sorted by whale PnL)")
print("=" * 95)
print(f"{'Strategy':<28} {'W_trades':>7} {'W_PnL':>8} {'W_WR':>6} {'W_avg':>7}"
      f" {'N_trades':>7} {'N_PnL':>8} {'Total':>8}")
print("-" * 95)

sorted_names = sorted(results.keys(), key=lambda n: results[n]['whale_pnl'], reverse=True)
for name in sorted_names:
    r = results[name]
    print(f"{name:<28} {r['whale_n']:>7} {r['whale_pnl']:>+8.0f} {r['whale_wr']:>5.1%}"
          f" {r['whale_avg']:>+7.1f} {r['normal_n']:>7} {r['normal_pnl']:>+8.0f}"
          f" {r['total_pnl']:>+8.0f}")

# ── By strategy category ──
categories = {
    'Fade-Hold (反打持有)': [n for n in sorted_names if n.startswith('Fade-Hold')],
    'MomFollow (跟庄同向)': [n for n in sorted_names if n.startswith('MomFollow')],
    'Fade-Scalp (反打止盈)': [n for n in sorted_names if n.startswith('Fade-Scalp')],
    'MidDip (中盘抄底)': [n for n in sorted_names if n.startswith('MidDip')],
    'LateMom (尾盘跟强)': [n for n in sorted_names if n.startswith('LateMom')],
}

print("\n" + "=" * 95)
print("BEST PER CATEGORY (whale PnL)")
print("=" * 95)
cat_best = {}
for cat, names in categories.items():
    if not names:
        continue
    best = max(names, key=lambda n: results[n]['whale_pnl'])
    r = results[best]
    cat_best[cat] = (best, r)
    print(f"\n  {cat}:")
    print(f"    Best: {best}")
    print(f"    Whale: {r['whale_n']} trades, PnL=${r['whale_pnl']:+.0f},"
          f" WR={r['whale_wr']:.1%}, avg=${r['whale_avg']:+.1f}/trade")
    print(f"    Normal: {r['normal_n']} trades, PnL=${r['normal_pnl']:+.0f}")

# ── System integration ──
print("\n" + "=" * 95)
print("SYSTEM INTEGRATION (Normal=$2,575 + whale strategy)")
print("=" * 95)
print(f"  Baseline (no whale strategy):        $2,575")
print(f"  + STOP only (avoid whale):           $2,575 + $742 = $3,317")
for cat, (best, r) in cat_best.items():
    total = 2575 + r['whale_pnl']
    delta = r['whale_pnl'] - 742
    print(f"  + {cat:<30} $2,575 + ${r['whale_pnl']:+.0f} = ${total:,.0f}"
          f"  (vs STOP: {delta:+.0f})")

# ═══════════════════════════════════════════════════════════════
# Charts
# ═══════════════════════════════════════════════════════════════

fig, axes = plt.subplots(2, 2, figsize=(18, 14))
fig.suptitle('Whale Counter-Strategies Comparison (50 shares)', fontsize=14)

# 1. Bar chart: whale PnL by category
ax = axes[0, 0]
cat_names = []
cat_pnls = []
cat_colors = ['#e74c3c', '#3498db', '#f39c12', '#2ecc71', '#9b59b6']
for ci, (cat, names) in enumerate(categories.items()):
    for n in sorted(names, key=lambda x: results[x]['whale_pnl'], reverse=True):
        cat_names.append(n.split('_', 1)[1] if '_' in n else n)
        cat_pnls.append(results[n]['whale_pnl'])
colors = []
ci = 0
for cat, names in categories.items():
    colors.extend([cat_colors[ci]] * len(names))
    ci += 1
ax.barh(range(len(cat_names)), cat_pnls, color=colors, alpha=0.8)
ax.set_yticks(range(len(cat_names)))
ax.set_yticklabels(cat_names, fontsize=7)
ax.set_xlabel('Whale Period PnL ($)')
ax.set_title('All Configs: Whale PnL')
ax.axvline(0, color='black', lw=0.5)
ax.axvline(742, color='red', ls='--', lw=1, label='STOP baseline ($742)')
ax.legend(fontsize=8)
ax.invert_yaxis()

# 2. Win rate vs PnL scatter
ax = axes[0, 1]
for ci, (cat, names) in enumerate(categories.items()):
    wrs = [results[n]['whale_wr'] * 100 for n in names]
    pnls = [results[n]['whale_pnl'] for n in names]
    ax.scatter(wrs, pnls, c=cat_colors[ci], label=cat.split('(')[0].strip(),
               s=80, alpha=0.7, edgecolors='black', lw=0.5)
ax.set_xlabel('Win Rate (%)')
ax.set_ylabel('Whale PnL ($)')
ax.set_title('Win Rate vs PnL (whale period)')
ax.axhline(0, color='gray', ls='--', lw=0.5)
ax.axhline(742, color='red', ls='--', lw=1, label='STOP=$742')
ax.legend(fontsize=7, loc='lower right')

# 3. Top 10 strategies
ax = axes[1, 0]
top10 = sorted_names[:10]
y_pos = range(len(top10))
whale_vals = [results[n]['whale_pnl'] for n in top10]
normal_vals = [results[n]['normal_pnl'] for n in top10]
ax.barh(y_pos, whale_vals, height=0.4, label='Whale PnL', color='coral', alpha=0.8)
ax.barh([y + 0.4 for y in y_pos], normal_vals, height=0.4, label='Normal PnL',
        color='steelblue', alpha=0.8)
ax.set_yticks([y + 0.2 for y in y_pos])
ax.set_yticklabels(top10, fontsize=7)
ax.set_xlabel('PnL ($)')
ax.set_title('Top 10 by Whale PnL: Whale vs Normal')
ax.legend(fontsize=8)
ax.axvline(0, color='black', lw=0.5)
ax.invert_yaxis()

# 4. Trade count & avg PnL
ax = axes[1, 1]
for ci, (cat, names) in enumerate(categories.items()):
    ns = [results[n]['whale_n'] for n in names]
    avgs = [results[n]['whale_avg'] for n in names]
    ax.scatter(ns, avgs, c=cat_colors[ci], label=cat.split('(')[0].strip(),
               s=80, alpha=0.7, edgecolors='black', lw=0.5)
ax.set_xlabel('Trade Count (whale)')
ax.set_ylabel('Avg PnL per Trade ($)')
ax.set_title('Trade Frequency vs Avg PnL')
ax.axhline(0, color='gray', ls='--', lw=0.5)
ax.legend(fontsize=7)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'whale_counter_strategies.png'), dpi=150)
plt.close()
print(f"\nChart saved: {OUT_DIR}\\whale_counter_strategies.png")

# Save CSV
df_out = pd.DataFrame([
    {'strategy': name, **results[name]} for name in sorted_names
])
df_out.to_csv(os.path.join(OUT_DIR, 'whale_counter_results.csv'), index=False)
print(f"CSV saved: {OUT_DIR}\\whale_counter_results.csv")
print("\nDone!")
