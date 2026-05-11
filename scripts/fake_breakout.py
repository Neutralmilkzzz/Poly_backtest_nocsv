"""
Fake Breakout Counter-Attack (假动作反击) Strategy Backtest
============================================================
Last 60s: detect sharp needle → buy recovery → scalp profit.
Pure tick-level scalping, does NOT bet on settlement.
Fully vectorized with numpy for speed.
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
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = r'C:\Users\ZHAOKAI\data'
EXIST_CSV = os.path.join(BASE, 'results', 'three_strategies_fixed', 'three_strategies_fixed.csv')
CACHE_PROBE = os.path.join(BASE, 'results', 'dipbuy_relaxed', '_cache_probe_v2.pkl')
OUT_DIR = os.path.join(BASE, 'results', 'fake_breakout')
os.makedirs(OUT_DIR, exist_ok=True)

SHARES = 50
CACHE_ALL_TICKS = os.path.join(OUT_DIR, '_cache_all_ticks.pkl')

# ── Load base data ──
print("Loading base data...")
existing = pd.read_csv(EXIST_CSV)
N = len(existing)
settlement = existing['f_settlement'].values
round_ids = existing['round_id'].values

with open(CACHE_PROBE, 'rb') as f:
    probe_data = pickle.load(f)

# ── Identify whale rounds ──
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

rid_info = {}
for i, rid in enumerate(round_ids):
    rid_info[rid] = {'idx': i, 'settle': settlement[i], 'whale': bool(whale_mask[i])}

n_whale = sum(1 for v in rid_info.values() if v['whale'])
print(f"  Whale rounds: {n_whale}, Normal: {len(rid_info)-n_whale}")

# ── Load or build tick cache ──
if os.path.exists(CACHE_ALL_TICKS):
    with open(CACHE_ALL_TICKS, 'rb') as f:
        all_ticks = pickle.load(f)
    print(f"Loaded tick cache: {len(all_ticks)} rounds")
else:
    print("Scanning ALL CSVs for tick data (t >= 120s)...")
    all_ticks = {}
    csv_files = sorted(glob_mod.glob(os.path.join(DATA_DIR, '*.csv')))
    for fi, fp in enumerate(csv_files):
        if fi % 500 == 0:
            print(f"  {fi}/{len(csv_files)}...")
        rid = os.path.basename(fp).replace('.csv', '')
        if rid not in rid_info:
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
        mask = elapsed >= 120
        if mask.sum() < 5:
            continue
        all_ticks[rid] = {
            'elapsed': elapsed[mask].astype(np.float32),
            'up_ask': pd.to_numeric(df.get('up_best_ask', pd.Series(dtype=float)),
                                    errors='coerce').values[mask].astype(np.float32),
            'up_bid': pd.to_numeric(df.get('up_best_bid', pd.Series(dtype=float)),
                                    errors='coerce').values[mask].astype(np.float32),
            'down_ask': pd.to_numeric(df.get('down_best_ask', pd.Series(dtype=float)),
                                      errors='coerce').values[mask].astype(np.float32),
            'down_bid': pd.to_numeric(df.get('down_best_bid', pd.Series(dtype=float)),
                                      errors='coerce').values[mask].astype(np.float32),
        }
    with open(CACHE_ALL_TICKS, 'wb') as f:
        pickle.dump(all_ticks, f)
    print(f"Cached {len(all_ticks)} rounds")


# ═══════════════════════════════════════════════════════════════
# Vectorized: compute all configs for one round at once
# ═══════════════════════════════════════════════════════════════

def ffill(a):
    """Forward-fill NaN (vectorized numpy)."""
    out = a.astype(np.float64).copy()
    mask = np.isnan(out)
    if not mask.any():
        return out
    idx = np.where(~mask, np.arange(len(out)), 0)
    np.maximum.accumulate(idx, out=idx)
    return out[idx]


def compute_round_trades(ticks, settle, needle_thrs, recovery_thrs, profit_targets,
                         peak_start=210, window_start=240, exit_deadline=295):
    """
    For one round, compute all (needle, recovery, tp, hold) -> pnl.
    Uses vectorized numpy: no per-tick Python loop in the inner config sweep.
    """
    e = ticks['elapsed'].astype(np.float64)
    results = {}

    for side in ['up', 'down']:
        bid = ffill(ticks[f'{side}_bid'])
        ask = ffill(ticks[f'{side}_ask'])

        # Replace NaN/<=0 with sentinel
        bid_v = np.where((~np.isnan(bid)) & (bid > 0), bid, -np.inf)

        # Indices
        ps_idx = int(np.searchsorted(e, peak_start))
        ws_idx = int(np.searchsorted(e, window_start))
        ed_idx = int(np.searchsorted(e, exit_deadline))
        if ws_idx >= len(e):
            continue

        # Running peak from peak_start
        peak = np.full(len(e), -np.inf)
        if ps_idx < len(e):
            peak[ps_idx:] = np.maximum.accumulate(bid_v[ps_idx:])

        # Window arrays
        w_bid = bid_v[ws_idx:]
        w_ask = ask[ws_idx:]
        w_peak = peak[ws_idx:]
        w_e = e[ws_idx:]
        nw = len(w_bid)
        if nw == 0:
            continue

        # Drop from peak
        w_drop = w_peak - w_bid
        valid_bid = w_bid > -np.inf

        for needle_thr in needle_thrs:
            # First needle index
            needle_mask = (w_drop >= needle_thr) & (w_peak >= 0.30) & valid_bid
            if not needle_mask.any():
                continue
            ni = int(np.argmax(needle_mask))

            # Running min from needle
            tail_bid = w_bid[ni:]
            tail_safe = np.where(tail_bid > -np.inf, tail_bid, np.inf)
            cummin = np.minimum.accumulate(tail_safe)
            recovery = tail_bid - cummin
            tail_valid = tail_bid > -np.inf

            for recovery_thr in recovery_thrs:
                rec_mask = (recovery >= recovery_thr) & tail_valid
                if not rec_mask.any():
                    continue
                ri = int(np.argmax(rec_mask))
                abs_ri = ni + ri

                ep = float(w_ask[abs_ri])
                if np.isnan(ep) or ep <= 0 or ep >= 0.95:
                    continue
                entry_t = float(w_e[abs_ri])

                # After entry
                after_bid = w_bid[abs_ri:]
                after_e = w_e[abs_ri:]
                na = len(after_bid)
                if na == 0:
                    continue

                # Exit deadline relative index
                rel_ed = ed_idx - ws_idx - abs_ri
                if rel_ed < 0:
                    rel_ed = 0
                if rel_ed >= na:
                    rel_ed = na - 1

                for tp in profit_targets:
                    # TP check
                    tp_mask = after_bid >= ep + tp
                    tp_found = tp_mask.any()
                    tp_idx = int(np.argmax(tp_mask)) if tp_found else na

                    # SCALP: TP or time exit
                    key_s = (needle_thr, recovery_thr, tp, False)
                    if key_s not in results:
                        if tp_found and tp_idx <= rel_ed:
                            results[key_s] = (tp * SHARES, entry_t, side)
                        else:
                            eb = float(after_bid[rel_ed])
                            if eb > -np.inf:
                                results[key_s] = ((eb - ep) * SHARES, entry_t, side)

                    # HOLD: TP or settlement
                    key_h = (needle_thr, recovery_thr, tp, True)
                    if key_h not in results:
                        if tp_found:
                            results[key_h] = (tp * SHARES, entry_t, side)
                        else:
                            if settle == side:
                                results[key_h] = ((1.0 - ep) * SHARES, entry_t, side)
                            else:
                                results[key_h] = ((-ep) * SHARES, entry_t, side)

    # Deduplicate across sides: keep earliest entry per config
    final = {}
    for key, (pnl, et, side) in results.items():
        if key not in final or et < final[key][1]:
            final[key] = (pnl, et, side)

    return {k: v[0] for k, v in final.items()}


# ═══════════════════════════════════════════════════════════════
# Run sweep
# ═══════════════════════════════════════════════════════════════

NEEDLE_THRS = [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]
RECOVERY_THRS = [0.02, 0.05, 0.10]
PROFIT_TARGETS = [0.05, 0.10, 0.15, 0.20]

n_configs = len(NEEDLE_THRS) * len(RECOVERY_THRS) * len(PROFIT_TARGETS) * 2
print(f"\nSweeping {n_configs} configs across {len(all_ticks)} rounds...")

# Accumulators
whale_acc = defaultdict(list)  # key -> [pnl, pnl, ...]
normal_acc = defaultdict(list)

for ri, (rid, info) in enumerate(rid_info.items()):
    if rid not in all_ticks:
        continue
    if ri % 500 == 0:
        print(f"  Round {ri}/{len(rid_info)}...")

    round_results = compute_round_trades(
        all_ticks[rid], info['settle'],
        NEEDLE_THRS, RECOVERY_THRS, PROFIT_TARGETS,
    )

    target = whale_acc if info['whale'] else normal_acc
    for key, pnl in round_results.items():
        target[key].append(pnl)

# Build results table
print("\nBuilding results table...")
rows = []
for needle in NEEDLE_THRS:
    for rec in RECOVERY_THRS:
        for tp in PROFIT_TARGETS:
            for hold in [False, True]:
                key = (needle, rec, tp, hold)
                wp = whale_acc.get(key, [])
                np_ = normal_acc.get(key, [])

                wn = len(wp); ws = sum(wp)
                wwr = sum(1 for p in wp if p > 0) / wn if wn else 0
                nn = len(np_); ns = sum(np_)
                nwr = sum(1 for p in np_ if p > 0) / nn if nn else 0

                lbl = f"N{needle:.2f}_R{rec:.2f}_TP{tp:.2f}_{'HOLD' if hold else 'SCALP'}"
                rows.append({
                    'label': lbl,
                    'needle': needle, 'recovery': rec,
                    'tp': tp, 'hold': hold,
                    'whale_n': wn, 'whale_pnl': ws, 'whale_wr': wwr,
                    'normal_n': nn, 'normal_pnl': ns, 'normal_wr': nwr,
                    'total_n': wn + nn, 'total_pnl': ws + ns,
                })

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT_DIR, 'fake_breakout_results.csv'), index=False)

# ═══════════════════════════════════════════════════════════════
# Display results
# ═══════════════════════════════════════════════════════════════

print("\n" + "=" * 80)
print("TOP 15 by WHALE-PERIOD PnL")
print("=" * 80)
for _, r in df.nlargest(15, 'whale_pnl').iterrows():
    mode = 'HOLD' if r['hold'] else 'SCALP'
    print(f"  N={r['needle']:.2f} R={r['recovery']:.2f} TP={r['tp']:.2f} {mode:5s}"
          f"  | W: {r['whale_n']:4.0f}t ${r['whale_pnl']:+7.0f} WR={r['whale_wr']:.1%}"
          f"  | N: {r['normal_n']:4.0f}t ${r['normal_pnl']:+7.0f}"
          f"  | TOT=${r['total_pnl']:+7.0f}")

print("\n" + "=" * 80)
print("TOP 15 by TOTAL PnL")
print("=" * 80)
for _, r in df.nlargest(15, 'total_pnl').iterrows():
    mode = 'HOLD' if r['hold'] else 'SCALP'
    print(f"  N={r['needle']:.2f} R={r['recovery']:.2f} TP={r['tp']:.2f} {mode:5s}"
          f"  | W: {r['whale_n']:4.0f}t ${r['whale_pnl']:+7.0f} WR={r['whale_wr']:.1%}"
          f"  | N: {r['normal_n']:4.0f}t ${r['normal_pnl']:+7.0f}"
          f"  | TOT=${r['total_pnl']:+7.0f}")

scalp_df = df[~df['hold']].copy()
print("\n" + "=" * 80)
print("TOP 10 SCALP-ONLY (no settlement bet)")
print("=" * 80)
for _, r in scalp_df.nlargest(10, 'whale_pnl').iterrows():
    print(f"  N={r['needle']:.2f} R={r['recovery']:.2f} TP={r['tp']:.2f} SCALP"
          f"  | W: {r['whale_n']:4.0f}t ${r['whale_pnl']:+7.0f} WR={r['whale_wr']:.1%}"
          f"  | N: {r['normal_n']:4.0f}t ${r['normal_pnl']:+7.0f}"
          f"  | TOT=${r['total_pnl']:+7.0f}")

# ═══════════════════════════════════════════════════════════════
# Charts
# ═══════════════════════════════════════════════════════════════

fig, axes = plt.subplots(2, 2, figsize=(16, 12))
fig.suptitle('Fake Breakout (假动作反击): Whale-Period Parameter Sweep', fontsize=14)

# 1. Heatmap: Whale PnL (SCALP, R=0.05)
ax = axes[0, 0]
sub = scalp_df[scalp_df['recovery'] == 0.05]
if len(sub) > 0:
    piv = sub.pivot_table(index='needle', columns='tp', values='whale_pnl')
    im = ax.imshow(piv.values, cmap='RdYlGn', aspect='auto')
    ax.set_xticks(range(len(piv.columns)))
    ax.set_xticklabels([f'{c:.2f}' for c in piv.columns])
    ax.set_yticks(range(len(piv.index)))
    ax.set_yticklabels([f'{r:.2f}' for r in piv.index])
    ax.set_xlabel('Profit Target')
    ax.set_ylabel('Needle Threshold')
    ax.set_title('Whale PnL (SCALP, R=0.05)')
    for i in range(len(piv.index)):
        for j in range(len(piv.columns)):
            ax.text(j, i, f'${piv.values[i, j]:.0f}', ha='center', va='center', fontsize=9)
    fig.colorbar(im, ax=ax)

# 2. Trade count
ax = axes[0, 1]
if len(sub) > 0:
    piv2 = sub.pivot_table(index='needle', columns='tp', values='whale_n')
    im2 = ax.imshow(piv2.values, cmap='Blues', aspect='auto')
    ax.set_xticks(range(len(piv2.columns)))
    ax.set_xticklabels([f'{c:.2f}' for c in piv2.columns])
    ax.set_yticks(range(len(piv2.index)))
    ax.set_yticklabels([f'{r:.2f}' for r in piv2.index])
    ax.set_xlabel('Profit Target')
    ax.set_ylabel('Needle Threshold')
    ax.set_title('Whale Trade Count (SCALP, R=0.05)')
    for i in range(len(piv2.index)):
        for j in range(len(piv2.columns)):
            ax.text(j, i, f'{piv2.values[i, j]:.0f}', ha='center', va='center', fontsize=9)
    fig.colorbar(im2, ax=ax)

# 3. SCALP vs HOLD
ax = axes[1, 0]
for hold_val, color, label in [(False, 'steelblue', 'SCALP'), (True, 'coral', 'HOLD')]:
    sub2 = df[(df['hold'] == hold_val) & (df['recovery'] == 0.05)]
    sub_g = sub2.groupby('needle')['whale_pnl'].max().reset_index()
    offset = 0.012 if hold_val else -0.012
    ax.bar(sub_g['needle'] + offset, sub_g['whale_pnl'],
           width=0.02, color=color, label=label, alpha=0.8)
ax.set_xlabel('Needle Threshold')
ax.set_ylabel('Best Whale PnL ($)')
ax.set_title('SCALP vs HOLD per Needle')
ax.legend()
ax.axhline(0, color='black', lw=0.5)

# 4. Win Rate
ax = axes[1, 1]
if len(sub) > 0:
    piv3 = sub.pivot_table(index='needle', columns='tp', values='whale_wr')
    for col in piv3.columns:
        ax.plot(piv3.index, piv3[col] * 100, 'o-', label=f'TP={col:.2f}')
    ax.set_xlabel('Needle Threshold')
    ax.set_ylabel('Win Rate (%)')
    ax.set_title('Whale WR (SCALP, R=0.05)')
    ax.legend()
    ax.axhline(50, color='gray', ls='--', lw=0.5)

plt.tight_layout()
plt.savefig(os.path.join(OUT_DIR, 'fake_breakout_analysis.png'), dpi=150)
plt.close()
print(f"\nChart: {OUT_DIR}\\fake_breakout_analysis.png")

# ── System integration ──
print("\n" + "=" * 80)
print("SYSTEM INTEGRATION")
print("=" * 80)

best_s = scalp_df.loc[scalp_df['whale_pnl'].idxmax()] if len(scalp_df) > 0 else None
hold_df = df[df['hold']].copy()
best_h = hold_df.loc[hold_df['whale_pnl'].idxmax()] if len(hold_df) > 0 else None

print(f"Normal-period (grid+momentum):         +$2,575")
print(f"Whale STOP (avoid losses):             +$742")
print(f"Whale Fade-Hold (current best):        +$361")
if best_s is not None:
    print(f"Whale FakeBreakout SCALP (best):       +${best_s['whale_pnl']:.0f}"
          f"  (N={best_s['needle']:.2f} R={best_s['recovery']:.2f} TP={best_s['tp']:.2f},"
          f" {best_s['whale_n']:.0f} trades WR={best_s['whale_wr']:.1%})")
if best_h is not None:
    print(f"Whale FakeBreakout HOLD (best):        +${best_h['whale_pnl']:.0f}"
          f"  (N={best_h['needle']:.2f} R={best_h['recovery']:.2f} TP={best_h['tp']:.2f},"
          f" {best_h['whale_n']:.0f} trades WR={best_h['whale_wr']:.1%})")

print(f"\nSystem combos:")
print(f"  A. Normal + STOP:                    +$2,575")
print(f"  B. Normal + STOP + Fade:             +$2,936")
if best_s is not None:
    print(f"  C. Normal + STOP + FB-SCALP:         +${2575+best_s['whale_pnl']:.0f}")
if best_h is not None:
    print(f"  D. Normal + STOP + FB-HOLD:          +${2575+best_h['whale_pnl']:.0f}")

print("\nDone!")
