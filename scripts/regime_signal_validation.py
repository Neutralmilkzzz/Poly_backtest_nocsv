"""
Regime信号验证：同一策略在鲸鱼/正常期间的表现差异
================================================
目的：证明庄家信号足够强 —— 常规Bot策略在庄操盘时明显失灵。
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

print("Loading data...")
existing = pd.read_csv(os.path.join(BASE, 'results', 'three_strategies_fixed',
                                     'three_strategies_fixed.csv'))
N = len(existing)
rids = existing['round_id'].values
settlement = existing['f_settlement'].values

with open(os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_probe_v2.pkl'), 'rb') as f:
    probe = pickle.load(f)
with open(os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_grid.pkl'), 'rb') as f:
    grid_cache = pickle.load(f)
with open(os.path.join(BASE, 'results', 'fake_breakout', '_cache_all_ticks.pkl'), 'rb') as f:
    all_ticks = pickle.load(f)

# ── Whale mask ──
mid_events = np.full(N, np.nan)
for i, rid in enumerate(rids):
    if rid not in probe: continue
    w = probe[rid].get('mid')
    if w is None: continue
    for e in w:
        if e['min_ask'] <= 0.12:
            mid_events[i] = 1.0 if e['max_bid_after'] >= 0.50 else 0.0
            break

rwr = np.full(N, np.nan)
for i in range(N):
    cnt = 0; wins = 0
    for j in range(i - 1, -1, -1):
        if np.isnan(mid_events[j]): continue
        cnt += 1; wins += mid_events[j]
        if cnt >= 7: break
    if cnt >= 7: rwr[i] = wins / cnt

whale_mask = (rwr >= 0.25)
whale_mask[np.isnan(rwr)] = False
print(f"Whale: {whale_mask.sum()}, Normal: {(~whale_mask).sum()}")


def ffill(a):
    out = a.astype(np.float64).copy()
    mask = np.isnan(out)
    if not mask.any(): return out
    idx = np.where(~mask, np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    return out[idx]


# ═══════════════════════════════════════════════════════════════
# 常规Bot策略（散户用的）
# ═══════════════════════════════════════════════════════════════

def bot_grid(ticks, settle, params):
    """网格策略：buy_price买入，sell_price卖出"""
    e = ticks['elapsed'].astype(np.float64)
    buy_p = params['buy_price']
    sell_p = params['sell_price']
    buy_end = params.get('buy_end', 94)
    sell_end = params.get('sell_end', 190)

    for side in ['up', 'down']:
        ask = ffill(ticks[f'{side}_ask'])
        bid = ffill(ticks[f'{side}_bid'])

        # Buy window
        be_idx = int(np.searchsorted(e, buy_end))
        if be_idx == 0: continue
        buy_seg = ask[:be_idx]
        valid_buy = (~np.isnan(buy_seg)) & (buy_seg > 0) & (buy_seg <= buy_p)
        if not valid_buy.any(): continue
        bi = int(np.argmax(valid_buy))
        entry = float(ask[bi])

        # Sell window
        se_idx = int(np.searchsorted(e, sell_end))
        if se_idx <= bi: continue
        sell_seg = bid[bi:se_idx]
        valid_sell = (~np.isnan(sell_seg)) & (sell_seg >= sell_p)
        if valid_sell.any():
            return (sell_p - entry) * SHARES, entry, side, float(e[bi])
        # Not sold - hold to settlement
        if settle == side:
            return (1.0 - entry) * SHARES, entry, side, float(e[bi])
        else:
            return (-entry) * SHARES, entry, side, float(e[bi])
    return None


def bot_tail_momentum(ticks, settle, params):
    """尾盘动量：在t=250后买领先方持有到结算"""
    e = ticks['elapsed'].astype(np.float64)
    entry_t = params.get('entry_time', 250)
    buy_thr = params.get('buy_threshold', 0.80)

    t_idx = int(np.searchsorted(e, entry_t))
    if t_idx >= len(e): return None

    up_bid = ffill(ticks['up_bid'])
    dn_bid = ffill(ticks['down_bid'])
    up_ask = ffill(ticks['up_ask'])
    dn_ask = ffill(ticks['down_ask'])

    ub = float(up_bid[t_idx]); db = float(dn_bid[t_idx])
    if np.isnan(ub) or np.isnan(db): return None

    # Buy the side with bid >= threshold
    if ub >= buy_thr:
        ep = float(up_ask[t_idx])
        side = 'up'
    elif db >= buy_thr:
        ep = float(dn_ask[t_idx])
        side = 'down'
    else:
        return None

    if np.isnan(ep) or ep <= 0 or ep >= 0.98: return None

    if settle == side:
        return (1.0 - ep) * SHARES, ep, side, entry_t
    else:
        return (-ep) * SHARES, ep, side, entry_t


def bot_dipbuy(ticks, settle, params):
    """低吸策略：在早期买极低价，等反弹"""
    e = ticks['elapsed'].astype(np.float64)
    cheap = params.get('cheap_threshold', 0.10)
    buy_end = params.get('buy_end', 120)

    for side in ['up', 'down']:
        ask = ffill(ticks[f'{side}_ask'])
        be_idx = int(np.searchsorted(e, buy_end))
        if be_idx == 0: continue
        buy_seg = ask[:be_idx]
        valid = (~np.isnan(buy_seg)) & (buy_seg > 0) & (buy_seg <= cheap)
        if not valid.any(): continue
        bi = int(np.argmax(valid))
        ep = float(ask[bi])

        if settle == side:
            return (1.0 - ep) * SHARES, ep, side, float(e[bi])
        else:
            return (-ep) * SHARES, ep, side, float(e[bi])
    return None


# ═══════════════════════════════════════════════════════════════
# 跟庄策略
# ═══════════════════════════════════════════════════════════════

def whale_chase_breakout(ticks, settle, params):
    """追突破（原Fade-Scalp）：检测急跌，追涨对面方"""
    e = ticks['elapsed'].astype(np.float64)
    drop_thr = params.get('drop_threshold', 0.15)
    tp = params.get('take_profit', 0.15)

    for side in ['up', 'down']:
        opp = 'down' if side == 'up' else 'up'
        bid_side = ffill(ticks[f'{side}_bid'])
        ask_opp = ffill(ticks[f'{opp}_ask'])
        bid_opp = ffill(ticks[f'{opp}_bid'])

        s_idx = int(np.searchsorted(e, 150))
        e_idx = int(np.searchsorted(e, 240))
        if s_idx >= len(e) or e_idx <= s_idx: continue

        seg = bid_side[s_idx:e_idx]
        valid = (~np.isnan(seg)) & (seg > 0)
        if not valid.any(): continue

        peak = np.maximum.accumulate(np.where(valid, seg, -np.inf))
        drop = peak - seg
        needle_mask = (drop >= drop_thr) & valid
        if not needle_mask.any(): continue

        ni = int(np.argmax(needle_mask)) + s_idx
        ep = float(ask_opp[ni])
        if np.isnan(ep) or ep <= 0 or ep >= 0.95: continue

        dl_idx = int(np.searchsorted(e, 295))
        if dl_idx >= len(e): dl_idx = len(e) - 1
        after_bid = bid_opp[ni:dl_idx + 1]
        tp_mask = after_bid >= ep + tp
        if tp_mask.any():
            pnl = tp * SHARES
        else:
            exit_p = float(bid_opp[dl_idx])
            if np.isnan(exit_p) or exit_p <= 0: continue
            pnl = (exit_p - ep) * SHARES
        return pnl, ep, opp, float(e[ni])
    return None


def whale_fade_hold(ticks, settle, params):
    """反打持有：检测急跌方，买对面持有到结算"""
    e = ticks['elapsed'].astype(np.float64)
    drop_thr = params.get('drop_threshold', 0.20)

    for side in ['up', 'down']:
        opp = 'down' if side == 'up' else 'up'
        bid = ffill(ticks[f'{side}_bid'])
        ask_opp = ffill(ticks[f'{opp}_ask'])

        s_idx = int(np.searchsorted(e, 150))
        e_idx = int(np.searchsorted(e, 240))
        if s_idx >= len(e) or e_idx <= s_idx: continue

        seg = bid[s_idx:e_idx]
        valid = (~np.isnan(seg)) & (seg > 0)
        if not valid.any(): continue

        peak = np.maximum.accumulate(np.where(valid, seg, -np.inf))
        drop = peak - seg
        needle_mask = (drop >= drop_thr) & valid
        if not needle_mask.any(): continue

        ni = int(np.argmax(needle_mask)) + s_idx
        ep = float(ask_opp[ni])
        if np.isnan(ep) or ep <= 0 or ep >= 0.95: continue

        if settle == opp:
            return (1.0 - ep) * SHARES, ep, opp, float(e[ni])
        else:
            return (-ep) * SHARES, ep, opp, float(e[ni])
    return None


# ═══════════════════════════════════════════════════════════════
# Run all
# ═══════════════════════════════════════════════════════════════

ALL_STRATS = {
    # ── 常规Bot策略 ──
    'Grid(0.18/0.26)':        (bot_grid, {'buy_price': 0.18, 'sell_price': 0.26}),
    'Grid(0.25/0.26)':        (bot_grid, {'buy_price': 0.25, 'sell_price': 0.26}),
    'Grid(0.15/0.25)':        (bot_grid, {'buy_price': 0.15, 'sell_price': 0.25}),
    'TailMom(t250,thr0.80)':  (bot_tail_momentum, {'entry_time': 250, 'buy_threshold': 0.80}),
    'TailMom(t250,thr0.85)':  (bot_tail_momentum, {'entry_time': 250, 'buy_threshold': 0.85}),
    'TailMom(t260,thr0.85)':  (bot_tail_momentum, {'entry_time': 260, 'buy_threshold': 0.85}),
    'DipBuy(cheap0.10)':      (bot_dipbuy, {'cheap_threshold': 0.10}),
    'DipBuy(cheap0.15)':      (bot_dipbuy, {'cheap_threshold': 0.15}),

    # ── 跟庄策略 ──
    'Chase(d15,tp15)':  (whale_chase_breakout, {'drop_threshold': 0.15, 'take_profit': 0.15}),
    'Chase(d20,tp15)':  (whale_chase_breakout, {'drop_threshold': 0.20, 'take_profit': 0.15}),
    'Chase(d15,tp10)':  (whale_chase_breakout, {'drop_threshold': 0.15, 'take_profit': 0.10}),
    'FadeHold(d20)':    (whale_fade_hold, {'drop_threshold': 0.20}),
    'FadeHold(d15)':    (whale_fade_hold, {'drop_threshold': 0.15}),
}

print(f"\nRunning {len(ALL_STRATS)} strategies...")

results = {}
for name, (func, params) in ALL_STRATS.items():
    w_pnls = []; n_pnls = []
    for i, rid in enumerate(rids):
        if rid not in all_ticks: continue
        res = func(all_ticks[rid], settlement[i], params)
        if res is None: continue
        pnl = res[0]
        if whale_mask[i]:
            w_pnls.append(pnl)
        else:
            n_pnls.append(pnl)

    wn = len(w_pnls); ws = sum(w_pnls)
    wwr = sum(1 for p in w_pnls if p > 0) / wn if wn else 0
    wavg = ws / wn if wn else 0
    nn = len(n_pnls); ns = sum(n_pnls)
    nwr = sum(1 for p in n_pnls if p > 0) / nn if nn else 0
    navg = ns / nn if nn else 0
    results[name] = {
        'whale_n': wn, 'whale_pnl': ws, 'whale_wr': wwr, 'whale_avg': wavg,
        'normal_n': nn, 'normal_pnl': ns, 'normal_wr': nwr, 'normal_avg': navg,
    }

# ═══════════════════════════════════════════════════════════════
# Display
# ═══════════════════════════════════════════════════════════════

bot_names = [n for n in ALL_STRATS if not n.startswith('Chase') and not n.startswith('Fade')]
whale_names = [n for n in ALL_STRATS if n.startswith('Chase') or n.startswith('Fade')]

print("\n" + "=" * 100)
print("REGIME SIGNAL VALIDATION: Same strategy, Whale vs Normal")
print("=" * 100)

print("\n── 常规Bot策略（散户用的）──")
print(f"{'Strategy':<25} | {'NORMAL':^40} | {'WHALE':^40}")
print(f"{'':25} | {'trades':>6} {'PnL':>8} {'WR':>6} {'$/trade':>8} | {'trades':>6} {'PnL':>8} {'WR':>6} {'$/trade':>8}")
print("-" * 100)
for name in bot_names:
    r = results[name]
    print(f"{name:<25} | {r['normal_n']:>6} {r['normal_pnl']:>+8.0f} {r['normal_wr']:>5.1%} {r['normal_avg']:>+8.1f}"
          f" | {r['whale_n']:>6} {r['whale_pnl']:>+8.0f} {r['whale_wr']:>5.1%} {r['whale_avg']:>+8.1f}")

print("\n── 跟庄策略 ──")
print(f"{'Strategy':<25} | {'NORMAL':^40} | {'WHALE':^40}")
print(f"{'':25} | {'trades':>6} {'PnL':>8} {'WR':>6} {'$/trade':>8} | {'trades':>6} {'PnL':>8} {'WR':>6} {'$/trade':>8}")
print("-" * 100)
for name in whale_names:
    r = results[name]
    print(f"{name:<25} | {r['normal_n']:>6} {r['normal_pnl']:>+8.0f} {r['normal_wr']:>5.1%} {r['normal_avg']:>+8.1f}"
          f" | {r['whale_n']:>6} {r['whale_pnl']:>+8.0f} {r['whale_wr']:>5.1%} {r['whale_avg']:>+8.1f}")

# Key metric: per-trade performance divergence
print("\n" + "=" * 100)
print("DIVERGENCE ANALYSIS ($/trade: Normal vs Whale)")
print("=" * 100)
for name in ALL_STRATS:
    r = results[name]
    diff = r['whale_avg'] - r['normal_avg']
    tag = "常规" if name in bot_names else "跟庄"
    bar = "+" * int(abs(diff) * 5) if diff > 0 else "-" * int(abs(diff) * 5)
    print(f"  [{tag}] {name:<25} Normal={r['normal_avg']:>+5.1f}  Whale={r['whale_avg']:>+5.1f}"
          f"  Diff={diff:>+5.1f}  {bar}")


# ═══════════════════════════════════════════════════════════════
# Chart
# ═══════════════════════════════════════════════════════════════

fig, axes = plt.subplots(2, 2, figsize=(18, 14))
fig.suptitle('Regime Signal Validation: Bot vs Whale Strategy Performance', fontsize=14)

# 1. Normal vs Whale avg PnL per trade - BOT strategies
ax = axes[0, 0]
names_b = bot_names
x = np.arange(len(names_b))
w = 0.35
normal_avgs = [results[n]['normal_avg'] for n in names_b]
whale_avgs = [results[n]['whale_avg'] for n in names_b]
ax.bar(x - w/2, normal_avgs, w, label='Normal', color='steelblue', alpha=0.8)
ax.bar(x + w/2, whale_avgs, w, label='Whale', color='coral', alpha=0.8)
ax.set_xticks(x)
ax.set_xticklabels([n.replace('TailMom','TM').replace('DipBuy','DB') for n in names_b],
                    rotation=30, ha='right', fontsize=8)
ax.set_ylabel('Avg PnL per Trade ($)')
ax.set_title('Bot Strategies: Normal vs Whale (should fail in whale)')
ax.legend()
ax.axhline(0, color='black', lw=0.5)

# 2. Same for whale strategies
ax = axes[0, 1]
names_w = whale_names
x2 = np.arange(len(names_w))
normal_avgs2 = [results[n]['normal_avg'] for n in names_w]
whale_avgs2 = [results[n]['whale_avg'] for n in names_w]
ax.bar(x2 - w/2, normal_avgs2, w, label='Normal', color='steelblue', alpha=0.8)
ax.bar(x2 + w/2, whale_avgs2, w, label='Whale', color='coral', alpha=0.8)
ax.set_xticks(x2)
ax.set_xticklabels(names_w, rotation=30, ha='right', fontsize=8)
ax.set_ylabel('Avg PnL per Trade ($)')
ax.set_title('Whale Strategies: Normal vs Whale')
ax.legend()
ax.axhline(0, color='black', lw=0.5)

# 3. Win Rate comparison
ax = axes[1, 0]
all_names = bot_names + whale_names
x3 = np.arange(len(all_names))
nwr = [results[n]['normal_wr'] * 100 for n in all_names]
wwr = [results[n]['whale_wr'] * 100 for n in all_names]
ax.bar(x3 - w/2, nwr, w, label='Normal WR', color='steelblue', alpha=0.8)
ax.bar(x3 + w/2, wwr, w, label='Whale WR', color='coral', alpha=0.8)
ax.set_xticks(x3)
short_names = [n.split('(')[0] for n in all_names]
ax.set_xticklabels(short_names, rotation=45, ha='right', fontsize=7)
ax.set_ylabel('Win Rate (%)')
ax.set_title('Win Rate: Normal vs Whale Period')
ax.legend()
ax.axhline(50, color='gray', ls='--', lw=0.5)
# Add vertical separator
ax.axvline(len(bot_names) - 0.5, color='red', ls='--', lw=1)
ax.text(len(bot_names) / 2, ax.get_ylim()[1] * 0.95, 'Bot', ha='center',
        fontsize=10, color='gray')
ax.text(len(bot_names) + len(whale_names) / 2, ax.get_ylim()[1] * 0.95, 'Whale',
        ha='center', fontsize=10, color='gray')

# 4. Total PnL comparison
ax = axes[1, 1]
npnl = [results[n]['normal_pnl'] for n in all_names]
wpnl = [results[n]['whale_pnl'] for n in all_names]
ax.bar(x3 - w/2, npnl, w, label='Normal PnL', color='steelblue', alpha=0.8)
ax.bar(x3 + w/2, wpnl, w, label='Whale PnL', color='coral', alpha=0.8)
ax.set_xticks(x3)
ax.set_xticklabels(short_names, rotation=45, ha='right', fontsize=7)
ax.set_ylabel('Total PnL ($)')
ax.set_title('Total PnL: Normal vs Whale Period')
ax.legend()
ax.axhline(0, color='black', lw=0.5)
ax.axvline(len(bot_names) - 0.5, color='red', ls='--', lw=1)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'regime_signal_validation.png'), dpi=150)
plt.close()
print(f"\nChart: {OUT_DIR}\\regime_signal_validation.png")

# Save
df_out = pd.DataFrame([{'strategy': n, 'type': 'bot' if n in bot_names else 'whale',
                          **results[n]} for n in ALL_STRATS])
df_out.to_csv(os.path.join(OUT_DIR, 'regime_validation.csv'), index=False)
print("Done!")
