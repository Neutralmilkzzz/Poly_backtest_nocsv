"""
═══════════════════════════════════════════════════════════════
 动量策略B + 实时庄控信号 → 跟庄反打回测
═══════════════════════════════════════════════════════════════

核心思路:
  - 基础策略: t=250s 看方向，跟着买（动量）
  - 庄控检测: 用 t=250s 之前就能观测到的实时信号
  - 当检测到庄控 → 反向操作（fade）
  - 当正常市场 → 正常跟（momentum）

测试的实时信号:
  1. price_swing_240_250: 240-250s 价格波动幅度
  2. spread_250: t=250s 时的 bid-ask spread
  3. extreme_level: t=250s 时价格离0.5的距离（越远越极端）
  4. early_volatility: 0-240s 的价格波动幅度
  5. swing_150_250: 150-250s 的中期波动
  6. move_speed: 230-250s 价格变化速度（20秒内移动多少）
  7. bid_ask_imbalance: 250s时买卖压力不对称
"""

import pandas as pd, numpy as np, glob, os, time, warnings
warnings.filterwarnings('ignore')

DATA_DIR = r'C:\Users\ZHAOKAI\data'
OUT_DIR  = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\realtime_signals'
os.makedirs(OUT_DIR, exist_ok=True)

files = sorted(glob.glob(os.path.join(DATA_DIR, '*.csv')))
print(f'Found {len(files)} CSV files')

def determine_settlement(df):
    late = df[(df['elapsed'] >= 285) & (df['elapsed'] <= 298)]
    if len(late) > 0:
        up_vals = late['up_midpoint'].dropna()
        if len(up_vals) > 0:
            v = float(up_vals.iloc[-1])
            if v > 0.5: return 'up'
            elif v < 0.5: return 'down'
    mid = df[(df['elapsed'] >= 240) & (df['elapsed'] < 285)]
    if len(mid) > 0:
        up_vals = mid['up_midpoint'].dropna()
        if len(up_vals) > 0:
            v = float(up_vals.iloc[-1])
            if v > 0.5: return 'up'
            elif v < 0.5: return 'down'
    return None

# ─────────────────────────────────────────────────────
# LOAD ALL ROUNDS + COMPUTE REALTIME SIGNALS AT t=250
# ─────────────────────────────────────────────────────
print("Loading rounds and computing realtime signals...")
t0 = time.time()
rows = []

for i, fpath in enumerate(files):
    try:
        df = pd.read_csv(fpath)
        if len(df) < 20:
            continue
        ts = pd.to_datetime(df['timestamp'], format='ISO8601')
        df['elapsed'] = (ts - ts.iloc[0]).dt.total_seconds()
        if df['elapsed'].max() < 200:
            continue
        for c in ['up_best_bid','up_best_ask','up_midpoint',
                   'down_best_bid','down_best_ask','down_midpoint']:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors='coerce')
                df[c] = df[c].ffill()

        settlement = determine_settlement(df)
        if settlement is None:
            continue

        # ── Snapshot at t=250 ──
        at250 = df[(df['elapsed'] >= 248) & (df['elapsed'] <= 252)]
        if len(at250) == 0:
            continue
        row250 = at250.iloc[0]
        up_mid_250 = row250.get('up_midpoint', np.nan)
        if pd.isna(up_mid_250):
            continue

        # Momentum direction
        if up_mid_250 > 0.55:
            momentum_side = 'up'
        elif up_mid_250 < 0.45:
            momentum_side = 'down'
        else:
            continue  # no clear signal

        # Entry price
        ask_col = f'{momentum_side}_best_ask'
        entry_price = row250.get(ask_col, np.nan)
        if pd.isna(entry_price) or entry_price <= 0 or entry_price >= 0.95:
            continue
        entry_price = float(entry_price)

        # ── REALTIME SIGNALS (all observable at t=250) ──

        # Signal 1: price swing 240-250s
        window_240_250 = df[(df['elapsed'] >= 240) & (df['elapsed'] <= 252)]
        up_vals = window_240_250['up_midpoint'].dropna()
        price_swing_10s = float(up_vals.max() - up_vals.min()) if len(up_vals) > 1 else 0

        # Signal 2: spread at 250
        up_ask = row250.get('up_best_ask', np.nan)
        up_bid = row250.get('up_best_bid', np.nan)
        spread_250 = float(up_ask - up_bid) if (pd.notna(up_ask) and pd.notna(up_bid)) else np.nan

        # Signal 3: extreme level (distance from 0.5)
        extreme_level = abs(up_mid_250 - 0.5)

        # Signal 4: early volatility (0-240s range)
        early = df[df['elapsed'] <= 240]
        early_up = early['up_midpoint'].dropna()
        early_vol = float(early_up.max() - early_up.min()) if len(early_up) > 1 else 0

        # Signal 5: swing 150-250s
        mid_phase = df[(df['elapsed'] >= 150) & (df['elapsed'] <= 252)]
        mid_up = mid_phase['up_midpoint'].dropna()
        swing_150_250 = float(mid_up.max() - mid_up.min()) if len(mid_up) > 1 else 0

        # Signal 6: move speed 230-250s (directional, not absolute)
        at230 = df[(df['elapsed'] >= 228) & (df['elapsed'] <= 232)]
        if len(at230) > 0:
            up_mid_230 = float(at230.iloc[0]['up_midpoint'])
            move_speed = abs(up_mid_250 - up_mid_230)
        else:
            move_speed = 0

        # Signal 7: direction consistency (did price flip between 200-250?)
        phase_200_250 = df[(df['elapsed'] >= 200) & (df['elapsed'] <= 252)]
        up_200_250 = phase_200_250['up_midpoint'].dropna()
        if len(up_200_250) > 5:
            above_50 = (up_200_250 > 0.5).mean()
            direction_consistency = max(above_50, 1 - above_50)  # 1.0 = perfectly consistent
        else:
            direction_consistency = 0.5

        # Signal 8: volume of big moves in 200-250s
        if len(up_200_250) > 2:
            changes = up_200_250.diff().abs()
            n_big = int((changes > 0.03).sum())
        else:
            n_big = 0

        # ── Settlement outcome ──
        won = settlement == momentum_side
        fade_won = settlement != momentum_side

        pnl_momentum = (1.0 - entry_price) * 10 if won else (0.0 - entry_price) * 10
        fade_side = 'down' if momentum_side == 'up' else 'up'
        fade_ask_col = f'{fade_side}_best_ask'
        fade_entry = row250.get(fade_ask_col, np.nan)
        if pd.notna(fade_entry) and fade_entry > 0:
            fade_entry = float(fade_entry)
            pnl_fade = (1.0 - fade_entry) * 10 if fade_won else (0.0 - fade_entry) * 10
        else:
            pnl_fade = 0

        rows.append({
            'file': os.path.basename(fpath),
            'settlement': settlement,
            'momentum_side': momentum_side,
            'entry_price': entry_price,
            'up_mid_250': round(up_mid_250, 3),
            'won': int(won),
            'pnl_momentum': round(pnl_momentum, 2),
            'pnl_fade': round(pnl_fade, 2),
            # Signals
            'price_swing_10s': round(price_swing_10s, 4),
            'spread_250': round(spread_250, 4) if pd.notna(spread_250) else np.nan,
            'extreme_level': round(extreme_level, 3),
            'early_vol': round(early_vol, 4),
            'swing_150_250': round(swing_150_250, 4),
            'move_speed': round(move_speed, 4),
            'direction_consistency': round(direction_consistency, 3),
            'n_big_moves_200_250': n_big,
        })

    except Exception as e:
        continue

    if (i+1) % 1000 == 0:
        print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')

print(f'Done in {time.time()-t0:.0f}s, {len(rows)} tradeable rounds')

df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT_DIR, 'momentum_signals.csv'), index=False)

# ═══════════════════════════════════════════════════════
# BASELINE: Pure Momentum
# ═══════════════════════════════════════════════════════
print(f'\n{"="*70}')
print(f'BASELINE: Pure Momentum (follow direction at t=250)')
print(f'{"="*70}')
wr = df['won'].mean() * 100
print(f'Trades: {len(df)}, WR: {wr:.1f}%, PnL: {df["pnl_momentum"].sum():+.1f}')
print(f'Buy UP: {(df["momentum_side"]=="up").sum()}, Buy DOWN: {(df["momentum_side"]=="down").sum()}')

# ═══════════════════════════════════════════════════════
# TEST EACH SIGNAL AS WHALE DETECTOR
# ═══════════════════════════════════════════════════════
print(f'\n{"="*70}')
print(f'SIGNAL TESTING: Which realtime signal best detects reversals?')
print(f'{"="*70}')

signal_configs = [
    # (name, column, thresholds, direction)
    # direction='above' means signal>=threshold → whale active
    # direction='below' means signal<=threshold → whale active
    ('price_swing_10s', 'price_swing_10s', [0.02, 0.05, 0.08, 0.10, 0.15, 0.20], 'above'),
    ('spread_250', 'spread_250', [0.01, 0.015, 0.02, 0.03, 0.04], 'above'),
    ('extreme_level', 'extreme_level', [0.10, 0.20, 0.30, 0.35, 0.40, 0.45], 'above'),
    ('early_vol', 'early_vol', [0.10, 0.20, 0.30, 0.40, 0.50], 'above'),
    ('swing_150_250', 'swing_150_250', [0.10, 0.20, 0.30, 0.40, 0.50], 'above'),
    ('move_speed', 'move_speed', [0.02, 0.05, 0.08, 0.10, 0.15], 'above'),
    ('direction_consistency', 'direction_consistency', [0.95, 0.90, 0.85, 0.80, 0.75], 'below'),
    ('n_big_moves', 'n_big_moves_200_250', [2, 5, 8, 10, 15], 'above'),
]

best_system_pnl = df['pnl_momentum'].sum()
best_signal_desc = "No signal (pure momentum)"
all_results = []

for sig_name, col, thresholds, direction in signal_configs:
    if col not in df.columns:
        continue

    print(f'\n--- {sig_name} ---')
    print(f'  {"Thresh":>8} {"N_whale":>8} {"N_normal":>8} '
          f'{"WR_mom":>7} {"WR_whale":>9} {"WR_norm":>8} '
          f'{"PnL_mom":>9} {"PnL_system":>11} {"Improve":>9}')

    for t in thresholds:
        vals = df[col].dropna()
        if direction == 'above':
            whale_mask = df[col].fillna(0) >= t
        else:
            whale_mask = df[col].fillna(1) <= t

        normal_mask = ~whale_mask
        n_whale = whale_mask.sum()
        n_normal = normal_mask.sum()

        if n_whale < 20 or n_normal < 20:
            continue

        # Normal rounds: follow momentum
        normal_pnl = df.loc[normal_mask, 'pnl_momentum'].sum()
        normal_wr = df.loc[normal_mask, 'won'].mean() * 100

        # Whale rounds: FADE
        whale_pnl_fade = df.loc[whale_mask, 'pnl_fade'].sum()
        whale_wr_fade = (1 - df.loc[whale_mask, 'won']).mean() * 100

        # System: normal=momentum, whale=fade
        system_pnl = normal_pnl + whale_pnl_fade

        # Original momentum PnL for these rounds
        whale_pnl_orig = df.loc[whale_mask, 'pnl_momentum'].sum()
        improvement = system_pnl - df['pnl_momentum'].sum()

        marker = ' ★' if system_pnl > best_system_pnl else ''
        print(f'  {t:>8.3f} {n_whale:>8} {n_normal:>8} '
              f'{wr:>6.1f}% {whale_wr_fade:>8.1f}% {normal_wr:>7.1f}% '
              f'{df["pnl_momentum"].sum():>+8.1f} {system_pnl:>+10.1f} {improvement:>+8.1f}{marker}')

        all_results.append({
            'signal': sig_name,
            'threshold': t,
            'direction': direction,
            'n_whale': n_whale,
            'n_normal': n_normal,
            'whale_wr_fade': round(whale_wr_fade, 1),
            'normal_wr': round(normal_wr, 1),
            'system_pnl': round(system_pnl, 1),
            'improvement': round(improvement, 1),
        })

        if system_pnl > best_system_pnl:
            best_system_pnl = system_pnl
            best_signal_desc = f'{sig_name} {">=" if direction=="above" else "<="} {t}'

# ═══════════════════════════════════════════════════════
# COMBO SIGNALS
# ═══════════════════════════════════════════════════════
print(f'\n{"="*70}')
print(f'COMBO SIGNALS: Testing multi-signal whale detection')
print(f'{"="*70}')

combos = [
    ("swing10s>=0.05 AND spread>=0.015",
     (df['price_swing_10s'] >= 0.05) & (df['spread_250'].fillna(0) >= 0.015)),
    ("swing10s>=0.05 AND extreme>=0.30",
     (df['price_swing_10s'] >= 0.05) & (df['extreme_level'] >= 0.30)),
    ("swing10s>=0.08 AND spread>=0.015",
     (df['price_swing_10s'] >= 0.08) & (df['spread_250'].fillna(0) >= 0.015)),
    ("swing150>=0.30 AND spread>=0.015",
     (df['swing_150_250'] >= 0.30) & (df['spread_250'].fillna(0) >= 0.015)),
    ("extreme>=0.35 AND spread>=0.015",
     (df['extreme_level'] >= 0.35) & (df['spread_250'].fillna(0) >= 0.015)),
    ("swing10s>=0.05 AND n_big>=5",
     (df['price_swing_10s'] >= 0.05) & (df['n_big_moves_200_250'] >= 5)),
    ("swing10s>=0.05 AND consistency<=0.85",
     (df['price_swing_10s'] >= 0.05) & (df['direction_consistency'] <= 0.85)),
    ("extreme>=0.30 AND consistency<=0.85",
     (df['extreme_level'] >= 0.30) & (df['direction_consistency'] <= 0.85)),
    ("swing150>=0.20 AND extreme>=0.30 AND spread>=0.01",
     (df['swing_150_250'] >= 0.20) & (df['extreme_level'] >= 0.30) & (df['spread_250'].fillna(0) >= 0.01)),
    ("move_speed>=0.05 AND spread>=0.015",
     (df['move_speed'] >= 0.05) & (df['spread_250'].fillna(0) >= 0.015)),
]

print(f'\n  {"Combo":<55} {"N_wh":>5} {"N_nm":>5} '
      f'{"WR_fade":>8} {"WR_mom":>7} {"Sys_PnL":>9} {"Improve":>9}')
print('-' * 110)

for desc, whale_mask in combos:
    normal_mask = ~whale_mask
    n_w = whale_mask.sum()
    n_n = normal_mask.sum()
    if n_w < 20 or n_n < 20:
        continue

    normal_pnl = df.loc[normal_mask, 'pnl_momentum'].sum()
    normal_wr = df.loc[normal_mask, 'won'].mean() * 100
    whale_fade_pnl = df.loc[whale_mask, 'pnl_fade'].sum()
    whale_fade_wr = (1 - df.loc[whale_mask, 'won']).mean() * 100
    sys_pnl = normal_pnl + whale_fade_pnl
    imp = sys_pnl - df['pnl_momentum'].sum()

    marker = ' ★' if sys_pnl > best_system_pnl else ''
    print(f'  {desc:<55} {n_w:>5} {n_n:>5} '
          f'{whale_fade_wr:>7.1f}% {normal_wr:>6.1f}% {sys_pnl:>+8.1f} {imp:>+8.1f}{marker}')

    if sys_pnl > best_system_pnl:
        best_system_pnl = sys_pnl
        best_signal_desc = desc

# ═══════════════════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════════════════
print(f'\n{"="*70}')
print(f'FINAL SUMMARY')
print(f'{"="*70}')
print(f'Baseline (pure momentum): PnL = {df["pnl_momentum"].sum():+.1f}')
print(f'Best system:              PnL = {best_system_pnl:+.1f}')
print(f'Best signal:              {best_signal_desc}')
print(f'Improvement:              {best_system_pnl - df["pnl_momentum"].sum():+.1f}')

# Top 10 single signals
res_df = pd.DataFrame(all_results).sort_values('system_pnl', ascending=False)
print(f'\nTop 10 single signals:')
for _, r in res_df.head(10).iterrows():
    print(f'  {r["signal"]:<25} {r["direction"]:>5} {r["threshold"]:>6.3f}  '
          f'n_whale={r["n_whale"]:>5}  fade_WR={r["whale_wr_fade"]:>5.1f}%  '
          f'sys_PnL={r["system_pnl"]:>+8.1f}  imp={r["improvement"]:>+8.1f}')

res_df.to_csv(os.path.join(OUT_DIR, 'signal_results.csv'), index=False)
print(f'\nResults saved to: {OUT_DIR}/')
