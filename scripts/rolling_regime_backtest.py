"""
═══════════════════════════════════════════════════════════════
 滚动Regime检测：用前N盘胜负预测本盘
═══════════════════════════════════════════════════════════════

核心思路（探针系统）:
  - 跑动量策略，记录每盘输赢
  - 用最近N盘的亏损率判断regime
  - 亏损率 > 阈值 → WHALE_ACTIVE → 本盘反打（fade）
  - 亏损率 <= 阈值 → NORMAL → 本盘正常跟（momentum）

这是真正可以实盘使用的信号：只依赖历史结果，不需要未来信息。

测试变量:
  - 滚动窗口 N: 5, 10, 15, 20, 30, 50
  - 亏损率阈值: 30%, 40%, 50%, 60%, 70%, 80%
  - 连续亏损触发: 连亏3, 4, 5, 6, 7, 8次
"""

import pandas as pd, numpy as np, glob, os, time, warnings
warnings.filterwarnings('ignore')

DATA_DIR = r'C:\Users\ZHAOKAI\data'
OUT_DIR  = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\rolling_regime'
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
# STEP 1: Load all rounds in time order, get momentum results
# ─────────────────────────────────────────────────────
print("Loading all rounds in chronological order...")
t0 = time.time()
rounds = []

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

        # Momentum at t=250
        at250 = df[(df['elapsed'] >= 248) & (df['elapsed'] <= 252)]
        if len(at250) == 0:
            continue
        row250 = at250.iloc[0]
        up_mid_250 = row250.get('up_midpoint', np.nan)
        if pd.isna(up_mid_250):
            continue

        if up_mid_250 > 0.55:
            mom_side = 'up'
        elif up_mid_250 < 0.45:
            mom_side = 'down'
        else:
            continue

        # Entry prices for both momentum and fade
        mom_ask = f'{mom_side}_best_ask'
        mom_entry = row250.get(mom_ask, np.nan)
        if pd.isna(mom_entry) or mom_entry <= 0 or mom_entry >= 0.95:
            continue
        mom_entry = float(mom_entry)

        fade_side = 'down' if mom_side == 'up' else 'up'
        fade_ask = f'{fade_side}_best_ask'
        fade_entry = row250.get(fade_ask, np.nan)
        if pd.isna(fade_entry) or fade_entry <= 0:
            fade_entry = 1.0 - mom_entry  # approximate
        else:
            fade_entry = float(fade_entry)

        mom_won = settlement == mom_side
        pnl_mom = (1.0 - mom_entry) * 10 if mom_won else -mom_entry * 10
        pnl_fade = (1.0 - fade_entry) * 10 if not mom_won else -fade_entry * 10

        rounds.append({
            'file': os.path.basename(fpath),
            'settlement': settlement,
            'mom_side': mom_side,
            'mom_entry': mom_entry,
            'fade_entry': fade_entry,
            'mom_won': int(mom_won),
            'pnl_mom': round(pnl_mom, 2),
            'pnl_fade': round(pnl_fade, 2),
        })

    except:
        continue

    if (i+1) % 1000 == 0:
        print(f'  {i+1}/{len(files)} ({time.time()-t0:.0f}s)')

print(f'Done: {len(rounds)} tradeable rounds in {time.time()-t0:.0f}s')

df = pd.DataFrame(rounds)
print(f'Baseline momentum: WR={df["mom_won"].mean()*100:.1f}%, PnL={df["pnl_mom"].sum():+.1f}')

# ─────────────────────────────────────────────────────
# STEP 2: Rolling Window Regime Detection
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print(f'METHOD 1: Rolling Loss Rate')
print(f'  If loss_rate(last N) >= threshold → WHALE → fade this round')
print(f'{"="*70}')

windows = [5, 8, 10, 15, 20, 30]
thresholds = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]

results_lossrate = []

print(f'\n{"Window":>7} {"Thresh":>7} {"N_whale":>8} {"N_normal":>8} '
      f'{"Whale%":>7} {"FadeWR":>7} {"MomWR":>6} '
      f'{"PnL_sys":>9} {"vs_base":>9}')
print('-' * 85)

baseline_pnl = df['pnl_mom'].sum()

for W in windows:
    mom_results = df['mom_won'].values
    pnl_mom_arr = df['pnl_mom'].values
    pnl_fade_arr = df['pnl_fade'].values

    for T in thresholds:
        system_pnl = 0
        n_whale = 0
        n_normal = 0
        whale_fade_wins = 0
        normal_mom_wins = 0

        for j in range(len(df)):
            if j < W:
                # Not enough history, default to momentum
                system_pnl += pnl_mom_arr[j]
                n_normal += 1
                normal_mom_wins += mom_results[j]
                continue

            # Look at last W rounds
            recent_losses = sum(1 for k in range(j-W, j) if mom_results[k] == 0)
            loss_rate = recent_losses / W

            if loss_rate >= T:
                # WHALE detected → fade
                system_pnl += pnl_fade_arr[j]
                n_whale += 1
                if mom_results[j] == 0:  # fade wins when momentum loses
                    whale_fade_wins += 1
            else:
                # NORMAL → momentum
                system_pnl += pnl_mom_arr[j]
                n_normal += 1
                normal_mom_wins += mom_results[j]

        whale_pct = n_whale / len(df) * 100
        fade_wr = whale_fade_wins / max(n_whale, 1) * 100
        mom_wr = normal_mom_wins / max(n_normal, 1) * 100
        improve = system_pnl - baseline_pnl

        marker = ' ★' if improve > 50 else ''
        print(f'{W:>7} {T:>7.0%} {n_whale:>8} {n_normal:>8} '
              f'{whale_pct:>6.1f}% {fade_wr:>6.1f}% {mom_wr:>5.1f}% '
              f'{system_pnl:>+8.1f} {improve:>+8.1f}{marker}')

        results_lossrate.append({
            'method': 'loss_rate',
            'window': W,
            'threshold': T,
            'n_whale': n_whale,
            'n_normal': n_normal,
            'whale_pct': round(whale_pct, 1),
            'fade_wr': round(fade_wr, 1),
            'mom_wr': round(mom_wr, 1),
            'system_pnl': round(system_pnl, 1),
            'improvement': round(improve, 1),
        })

# ─────────────────────────────────────────────────────
# STEP 3: Consecutive Loss Streak Detection
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print(f'METHOD 2: Consecutive Loss Streak')
print(f'  If last K rounds ALL lost → WHALE → fade this round')
print(f'  Stay in WHALE mode until we get a momentum win')
print(f'{"="*70}')

streaks = [2, 3, 4, 5, 6, 7, 8]
results_streak = []

print(f'\n{"Streak":>7} {"N_whale":>8} {"N_normal":>8} '
      f'{"Whale%":>7} {"FadeWR":>7} {"MomWR":>6} '
      f'{"PnL_sys":>9} {"vs_base":>9}')
print('-' * 75)

for K in streaks:
    mom_results = df['mom_won'].values
    pnl_mom_arr = df['pnl_mom'].values
    pnl_fade_arr = df['pnl_fade'].values

    system_pnl = 0
    n_whale = 0
    n_normal = 0
    whale_fade_wins = 0
    normal_mom_wins = 0
    in_whale_mode = False

    for j in range(len(df)):
        if j < K:
            system_pnl += pnl_mom_arr[j]
            n_normal += 1
            normal_mom_wins += mom_results[j]
            continue

        # Check if last K were all losses
        if all(mom_results[j-K:j] == 0):
            in_whale_mode = True

        if in_whale_mode:
            system_pnl += pnl_fade_arr[j]
            n_whale += 1
            if mom_results[j] == 0:
                whale_fade_wins += 1
            # Exit whale mode if momentum would have won
            if mom_results[j] == 1:
                in_whale_mode = False
        else:
            system_pnl += pnl_mom_arr[j]
            n_normal += 1
            normal_mom_wins += mom_results[j]

    whale_pct = n_whale / len(df) * 100
    fade_wr = whale_fade_wins / max(n_whale, 1) * 100
    mom_wr = normal_mom_wins / max(n_normal, 1) * 100
    improve = system_pnl - baseline_pnl

    marker = ' ★' if improve > 50 else ''
    print(f'{K:>7} {n_whale:>8} {n_normal:>8} '
          f'{whale_pct:>6.1f}% {fade_wr:>6.1f}% {mom_wr:>5.1f}% '
          f'{system_pnl:>+8.1f} {improve:>+8.1f}{marker}')

    results_streak.append({
        'method': 'streak',
        'streak_K': K,
        'n_whale': n_whale,
        'n_normal': n_normal,
        'whale_pct': round(whale_pct, 1),
        'fade_wr': round(fade_wr, 1),
        'mom_wr': round(mom_wr, 1),
        'system_pnl': round(system_pnl, 1),
        'improvement': round(improve, 1),
    })

# ─────────────────────────────────────────────────────
# STEP 4: Hybrid — Loss Rate + Streak
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print(f'METHOD 3: Hybrid (loss_rate OR streak)')
print(f'  WHALE if loss_rate(W) >= T OR last K all lost')
print(f'  Stay in WHALE until momentum win')
print(f'{"="*70}')

hybrid_configs = [
    (10, 0.50, 3),
    (10, 0.60, 3),
    (10, 0.50, 4),
    (10, 0.60, 4),
    (15, 0.50, 3),
    (15, 0.50, 4),
    (15, 0.60, 3),
    (20, 0.40, 3),
    (20, 0.50, 3),
    (20, 0.50, 4),
    (20, 0.60, 3),
    (5, 0.60, 3),
    (5, 0.80, 2),
    (8, 0.50, 3),
    (8, 0.60, 3),
]

results_hybrid = []

print(f'\n{"W":>3} {"T":>5} {"K":>3} {"N_whale":>8} {"N_normal":>8} '
      f'{"Whale%":>7} {"FadeWR":>7} {"MomWR":>6} '
      f'{"PnL_sys":>9} {"vs_base":>9}')
print('-' * 80)

for W, T, K in hybrid_configs:
    mom_results = df['mom_won'].values
    pnl_mom_arr = df['pnl_mom'].values
    pnl_fade_arr = df['pnl_fade'].values

    system_pnl = 0
    n_whale = 0
    n_normal = 0
    whale_fade_wins = 0
    normal_mom_wins = 0
    in_whale_mode = False

    for j in range(len(df)):
        start = max(0, j - W)
        window = mom_results[start:j]

        # Check triggers
        trigger_lossrate = len(window) >= W and sum(1 for x in window if x == 0) / len(window) >= T
        trigger_streak = j >= K and all(mom_results[j-K:j] == 0)

        if trigger_lossrate or trigger_streak:
            in_whale_mode = True

        if in_whale_mode:
            system_pnl += pnl_fade_arr[j]
            n_whale += 1
            if mom_results[j] == 0:
                whale_fade_wins += 1
            if mom_results[j] == 1:
                in_whale_mode = False
        else:
            system_pnl += pnl_mom_arr[j]
            n_normal += 1
            normal_mom_wins += mom_results[j]

    whale_pct = n_whale / len(df) * 100
    fade_wr = whale_fade_wins / max(n_whale, 1) * 100
    mom_wr = normal_mom_wins / max(n_normal, 1) * 100
    improve = system_pnl - baseline_pnl

    marker = ' ★' if improve > 50 else ''
    print(f'{W:>3} {T:>5.0%} {K:>3} {n_whale:>8} {n_normal:>8} '
          f'{whale_pct:>6.1f}% {fade_wr:>6.1f}% {mom_wr:>5.1f}% '
          f'{system_pnl:>+8.1f} {improve:>+8.1f}{marker}')

    results_hybrid.append({
        'method': 'hybrid',
        'window': W,
        'threshold': T,
        'streak_K': K,
        'n_whale': n_whale,
        'n_normal': n_normal,
        'whale_pct': round(whale_pct, 1),
        'fade_wr': round(fade_wr, 1),
        'mom_wr': round(mom_wr, 1),
        'system_pnl': round(system_pnl, 1),
        'improvement': round(improve, 1),
    })

# ─────────────────────────────────────────────────────
# STEP 5: Time-based analysis (does whale come in shifts?)
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print(f'BONUS: Time-of-day momentum WR (does whale have a schedule?)')
print(f'{"="*70}')

# Extract hour from filename
df['hour'] = df['file'].str.extract(r'_(\d{2})-\d{2}-\d{2}').astype(int)
hourly = df.groupby('hour').agg(
    n=('mom_won', 'count'),
    wr=('mom_won', 'mean'),
    pnl=('pnl_mom', 'sum'),
).reset_index()
hourly['wr'] = (hourly['wr'] * 100).round(1)

print(f'\n{"Hour":>6} {"N":>6} {"WR":>7} {"PnL":>9}')
for _, r in hourly.iterrows():
    print(f'{int(r["hour"]):>6} {int(r["n"]):>6} {r["wr"]:>6.1f}% {r["pnl"]:>+8.1f}')

# ─────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print(f'FINAL SUMMARY')
print(f'{"="*70}')
print(f'Baseline (pure momentum): {baseline_pnl:+.1f}')
print()

# Best from each method
lr_df = pd.DataFrame(results_lossrate).sort_values('system_pnl', ascending=False)
st_df = pd.DataFrame(results_streak).sort_values('system_pnl', ascending=False)
hy_df = pd.DataFrame(results_hybrid).sort_values('system_pnl', ascending=False)

print('Top 3 loss-rate configs:')
for _, r in lr_df.head(3).iterrows():
    print(f'  W={int(r["window"]):>2}, T={r["threshold"]:.0%}: '
          f'whale={r["n_whale"]}, fade_WR={r["fade_wr"]:.1f}%, '
          f'sys_PnL={r["system_pnl"]:+.1f} ({r["improvement"]:+.1f})')

print('\nTop 3 streak configs:')
for _, r in st_df.head(3).iterrows():
    print(f'  K={int(r["streak_K"])}: '
          f'whale={r["n_whale"]}, fade_WR={r["fade_wr"]:.1f}%, '
          f'sys_PnL={r["system_pnl"]:+.1f} ({r["improvement"]:+.1f})')

print('\nTop 3 hybrid configs:')
for _, r in hy_df.head(3).iterrows():
    print(f'  W={int(r["window"])}, T={r["threshold"]:.0%}, K={int(r["streak_K"])}: '
          f'whale={r["n_whale"]}, fade_WR={r["fade_wr"]:.1f}%, '
          f'sys_PnL={r["system_pnl"]:+.1f} ({r["improvement"]:+.1f})')

# Cumulative PnL for best config
best = hy_df.iloc[0]
W_b, T_b, K_b = int(best['window']), best['threshold'], int(best['streak_K'])
print(f'\nBest overall: W={W_b}, T={T_b:.0%}, K={K_b}')
print(f'  System PnL: {best["system_pnl"]:+.1f} (baseline: {baseline_pnl:+.1f}, improvement: {best["improvement"]:+.1f})')

# Re-run best config to get cumulative PnL curve
mom_results = df['mom_won'].values
pnl_mom_arr = df['pnl_mom'].values
pnl_fade_arr = df['pnl_fade'].values

cum_sys = []
cum_base = []
regime_log = []
in_whale = False
running_sys = 0
running_base = 0

for j in range(len(df)):
    running_base += pnl_mom_arr[j]
    start = max(0, j - W_b)
    window = mom_results[start:j]
    trigger_lr = len(window) >= W_b and sum(1 for x in window if x == 0) / len(window) >= T_b
    trigger_sk = j >= K_b and all(mom_results[j-K_b:j] == 0)
    if trigger_lr or trigger_sk:
        in_whale = True
    if in_whale:
        running_sys += pnl_fade_arr[j]
        regime_log.append('whale')
        if mom_results[j] == 1:
            in_whale = False
    else:
        running_sys += pnl_mom_arr[j]
        regime_log.append('normal')
    cum_sys.append(running_sys)
    cum_base.append(running_base)

print(f'\nCumulative PnL (every 200 rounds):')
print(f'{"Round":>7} {"Baseline":>10} {"System":>10} {"Diff":>9} {"Regime":>8}')
for j in range(0, len(df), 200):
    rg = regime_log[j]
    print(f'{j:>7} {cum_base[j]:>+9.1f} {cum_sys[j]:>+9.1f} '
          f'{cum_sys[j]-cum_base[j]:>+8.1f} {rg:>8}')
j = len(df) - 1
print(f'{j:>7} {cum_base[j]:>+9.1f} {cum_sys[j]:>+9.1f} '
      f'{cum_sys[j]-cum_base[j]:>+8.1f} {regime_log[j]:>8}')

# Save detailed results
df['regime'] = regime_log
df['pnl_system'] = [cum_sys[j] - (cum_sys[j-1] if j > 0 else 0) for j in range(len(df))]
df.to_csv(os.path.join(OUT_DIR, 'rolling_regime_results.csv'), index=False)

all_res = pd.concat([lr_df, st_df, hy_df], ignore_index=True)
all_res.to_csv(os.path.join(OUT_DIR, 'all_configs.csv'), index=False)
print(f'\nResults saved to: {OUT_DIR}/')
