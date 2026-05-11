"""
═══════════════════════════════════════════════════════════════
 多探针联合Regime检测
═══════════════════════════════════════════════════════════════

核心思路:
  网格策略95%WR → 平时几乎不亏 → 亏损是极强的异常信号
  动量策略79%WR → 亏损常见 → 单独用太弱
  DipBuy 16%WR → 几乎总是亏 → 作为反向探针

联合探针信号:
  1. 网格+动量双亏 → 强异常
  2. 网格亏损率突变 → 异常
  3. 三策略加权健康分

当检测到异常(WHALE) → 两种应对:
  A. 停盘（PnL=0）→ 资金保护
  B. fade反打 → 赌庄家存在
"""

import pandas as pd, numpy as np, os, time, warnings
warnings.filterwarnings('ignore')

INPUT = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies_fixed\three_strategies_fixed.csv'
OUT_DIR = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\multi_probe_regime'
os.makedirs(OUT_DIR, exist_ok=True)

df = pd.read_csv(INPUT)
print(f'Loaded {len(df)} rounds')
print(f'  Grid (A): {df["A_traded"].sum()} trades, WR={df[df["A_traded"]==1]["A_pnl"].apply(lambda x: x>0).mean()*100:.1f}%')
print(f'  Momentum (B): {df["B_traded"].sum()} trades, WR={df[df["B_traded"]==1]["B_pnl"].apply(lambda x: x>0).mean()*100:.1f}%')
print(f'  DipBuy (C): {df["C_traded"].sum()} trades, WR={df[df["C_traded"]==1]["C_pnl"].apply(lambda x: x>0).mean()*100:.1f}%')

# Build per-round outcome vectors
N = len(df)
# For each strategy: 1=win, 0=loss, NaN=no trade
grid_won = np.where(df['A_traded']==1, (df['A_pnl']>0).astype(float), np.nan)
mom_won  = np.where(df['B_traded']==1, (df['B_pnl']>0).astype(float), np.nan)
dip_won  = np.where(df['C_traded']==1, (df['C_pnl']>0).astype(float), np.nan)

# Momentum PnL for baseline and fade
mom_pnl = df['B_pnl'].values
# For fade: when B trades and has side info, compute fade PnL
# Settlement from features
mom_side = df['B_side'].values
settlement = df['f_settlement'].values

# Compute fade PnL for B
# fade wins when momentum loses (i.e. settlement != mom_side)
# fade entry ≈ 1 - mom_entry (opposite side ask ≈ 1 - mom_side ask)
mom_entry = df['B_entry'].values
fade_pnl = np.zeros(N)
b_traded = df['B_traded'].values.astype(bool)
for i in range(N):
    if not b_traded[i]:
        fade_pnl[i] = 0
        continue
    fade_entry = 1.0 - mom_entry[i]
    if settlement[i] != mom_side[i]:  # fade wins
        fade_pnl[i] = (1.0 - fade_entry) * 10
    else:
        fade_pnl[i] = -fade_entry * 10

baseline_pnl = mom_pnl[b_traded].sum()
print(f'\nBaseline (pure momentum): {baseline_pnl:+.1f}')
print(f'Baseline rounds: {b_traded.sum()}')

# ─────────────────────────────────────────────────────
# Helper: compute rolling loss rate (ignoring NaN/no-trade)
# ─────────────────────────────────────────────────────
def rolling_loss_rate(won_arr, window):
    """Compute rolling loss rate over last `window` TRADED rounds (not calendar rounds)."""
    n = len(won_arr)
    result = np.full(n, np.nan)
    for i in range(n):
        # Look back at most `window` traded rounds before round i
        count = 0
        losses = 0
        for j in range(i-1, -1, -1):
            if np.isnan(won_arr[j]):
                continue
            count += 1
            if won_arr[j] == 0:
                losses += 1
            if count >= window:
                break
        if count >= window:
            result[i] = losses / count
    return result

# ─────────────────────────────────────────────────────
# METHOD 1: Grid loss rate as whale signal
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print('METHOD 1: Grid (95% WR) loss rate as whale signal')
print('  If grid_loss_rate(last N) > threshold → WHALE')
print('  Action: stop momentum trading OR fade')
print(f'{"="*70}')

windows_grid = [5, 8, 10, 15, 20]
thresholds_grid = [0.10, 0.15, 0.20, 0.30, 0.40]

results = []

print(f'\n{"W":>3} {"T":>5} {"N_det":>6} {"Stop_PnL":>10} {"Fade_PnL":>10} '
      f'{"Stop_vs":>9} {"Fade_vs":>9}')
print('-'*65)

for W in windows_grid:
    grid_lr = rolling_loss_rate(grid_won, W)
    for T in thresholds_grid:
        stop_pnl = 0
        fade_pnl_sys = 0
        n_detected = 0

        for i in range(N):
            if not b_traded[i]:
                continue
            if not np.isnan(grid_lr[i]) and grid_lr[i] >= T:
                # WHALE detected
                n_detected += 1
                stop_pnl += 0  # don't trade
                fade_pnl_sys += fade_pnl[i]
            else:
                stop_pnl += mom_pnl[i]
                fade_pnl_sys += mom_pnl[i]

        stop_vs = stop_pnl - baseline_pnl
        fade_vs = fade_pnl_sys - baseline_pnl

        marker = ''
        if stop_vs > 30: marker = ' ★s'
        if fade_vs > 30: marker += ' ★f'

        print(f'{W:>3} {T:>5.0%} {n_detected:>6} {stop_pnl:>+9.1f} {fade_pnl_sys:>+9.1f} '
              f'{stop_vs:>+8.1f} {fade_vs:>+8.1f}{marker}')

        results.append({
            'method': 'grid_only',
            'window': W, 'threshold': T,
            'n_detected': n_detected,
            'stop_pnl': round(stop_pnl, 1),
            'fade_pnl': round(fade_pnl_sys, 1),
            'stop_improve': round(stop_vs, 1),
            'fade_improve': round(fade_vs, 1),
        })

# ─────────────────────────────────────────────────────
# METHOD 2: Grid + Momentum combined signal
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print('METHOD 2: Grid AND Momentum both losing → WHALE')
print('  Both probes must have elevated loss rates')
print(f'{"="*70}')

windows_combo = [10, 15, 20]
grid_thresholds = [0.10, 0.15, 0.20]
mom_thresholds = [0.25, 0.30, 0.35, 0.40]

print(f'\n{"W":>3} {"GT":>5} {"MT":>5} {"N_det":>6} {"Stop_PnL":>10} {"Fade_PnL":>10} '
      f'{"Stop_vs":>9} {"Fade_vs":>9}')
print('-'*70)

for W in windows_combo:
    grid_lr = rolling_loss_rate(grid_won, W)
    mom_lr = rolling_loss_rate(mom_won, W)

    for GT in grid_thresholds:
        for MT in mom_thresholds:
            stop_pnl = 0
            fade_pnl_sys = 0
            n_detected = 0

            for i in range(N):
                if not b_traded[i]:
                    continue
                g = grid_lr[i] if not np.isnan(grid_lr[i]) else 0
                m = mom_lr[i] if not np.isnan(mom_lr[i]) else 0
                if g >= GT and m >= MT:
                    n_detected += 1
                    stop_pnl += 0
                    fade_pnl_sys += fade_pnl[i]
                else:
                    stop_pnl += mom_pnl[i]
                    fade_pnl_sys += mom_pnl[i]

            stop_vs = stop_pnl - baseline_pnl
            fade_vs = fade_pnl_sys - baseline_pnl

            marker = ''
            if stop_vs > 30: marker = ' ★s'
            if fade_vs > 30: marker += ' ★f'

            print(f'{W:>3} {GT:>5.0%} {MT:>5.0%} {n_detected:>6} {stop_pnl:>+9.1f} {fade_pnl_sys:>+9.1f} '
                  f'{stop_vs:>+8.1f} {fade_vs:>+8.1f}{marker}')

            results.append({
                'method': 'grid_and_mom',
                'window': W, 'grid_threshold': GT, 'mom_threshold': MT,
                'n_detected': n_detected,
                'stop_pnl': round(stop_pnl, 1),
                'fade_pnl': round(fade_pnl_sys, 1),
                'stop_improve': round(stop_vs, 1),
                'fade_improve': round(fade_vs, 1),
            })

# ─────────────────────────────────────────────────────
# METHOD 3: Weighted Health Score
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print('METHOD 3: Weighted Health Score')
print('  health = grid_wr * 2.0 + mom_wr * 1.0  (grid weighted 2x)')
print('  When health < threshold → WHALE')
print(f'{"="*70}')

windows_health = [10, 15, 20, 30]
health_thresholds = [1.5, 1.6, 1.7, 1.8, 1.9]

print(f'\n{"W":>3} {"HT":>5} {"N_det":>6} {"Stop_PnL":>10} {"Fade_PnL":>10} '
      f'{"Stop_vs":>9} {"Fade_vs":>9}')
print('-'*65)

for W in windows_health:
    grid_lr = rolling_loss_rate(grid_won, W)  # loss rate
    mom_lr = rolling_loss_rate(mom_won, W)

    for HT in health_thresholds:
        stop_pnl = 0
        fade_pnl_sys = 0
        n_detected = 0

        for i in range(N):
            if not b_traded[i]:
                continue
            g_wr = 1.0 - (grid_lr[i] if not np.isnan(grid_lr[i]) else 0.05)
            m_wr = 1.0 - (mom_lr[i] if not np.isnan(mom_lr[i]) else 0.21)
            health = g_wr * 2.0 + m_wr * 1.0  # max = 3.0

            if health < HT:
                n_detected += 1
                stop_pnl += 0
                fade_pnl_sys += fade_pnl[i]
            else:
                stop_pnl += mom_pnl[i]
                fade_pnl_sys += mom_pnl[i]

        stop_vs = stop_pnl - baseline_pnl
        fade_vs = fade_pnl_sys - baseline_pnl

        marker = ''
        if stop_vs > 30: marker = ' ★s'
        if fade_vs > 30: marker += ' ★f'

        print(f'{W:>3} {HT:>5.1f} {n_detected:>6} {stop_pnl:>+9.1f} {fade_pnl_sys:>+9.1f} '
              f'{stop_vs:>+8.1f} {fade_vs:>+8.1f}{marker}')

        results.append({
            'method': 'health_score',
            'window': W, 'health_threshold': HT,
            'n_detected': n_detected,
            'stop_pnl': round(stop_pnl, 1),
            'fade_pnl': round(fade_pnl_sys, 1),
            'stop_improve': round(stop_vs, 1),
            'fade_improve': round(fade_vs, 1),
        })

# ─────────────────────────────────────────────────────
# METHOD 4: Grid loss EVENT trigger (state machine)
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print('METHOD 4: Grid Loss Event Trigger (state machine)')
print('  网格平时95%赢 → 一旦连亏K次 → WHALE(停N轮)')
print(f'{"="*70}')

grid_K_values = [1, 2, 3]  # consecutive grid losses to trigger
cooldown_values = [3, 5, 8, 10, 15, 20]

print(f'\n{"K":>3} {"Cool":>5} {"N_det":>6} {"Stop_PnL":>10} {"Fade_PnL":>10} '
      f'{"Stop_vs":>9} {"Fade_vs":>9}')
print('-'*65)

for K in grid_K_values:
    for cooldown in cooldown_values:
        stop_pnl = 0
        fade_pnl_sys = 0
        n_detected = 0

        # Track consecutive grid losses
        consec_grid_loss = 0
        whale_cooldown = 0  # rounds remaining in WHALE mode

        for i in range(N):
            # Update grid loss tracking
            if df['A_traded'].iloc[i] == 1:
                if df['A_pnl'].iloc[i] <= 0:
                    consec_grid_loss += 1
                else:
                    consec_grid_loss = 0

            # Check trigger
            if consec_grid_loss >= K:
                whale_cooldown = cooldown
                consec_grid_loss = 0

            if not b_traded[i]:
                if whale_cooldown > 0:
                    whale_cooldown -= 1
                continue

            if whale_cooldown > 0:
                n_detected += 1
                stop_pnl += 0
                fade_pnl_sys += fade_pnl[i]
                whale_cooldown -= 1
            else:
                stop_pnl += mom_pnl[i]
                fade_pnl_sys += mom_pnl[i]

        stop_vs = stop_pnl - baseline_pnl
        fade_vs = fade_pnl_sys - baseline_pnl

        marker = ''
        if stop_vs > 30: marker = ' ★s'
        if fade_vs > 30: marker += ' ★f'

        print(f'{K:>3} {cooldown:>5} {n_detected:>6} {stop_pnl:>+9.1f} {fade_pnl_sys:>+9.1f} '
              f'{stop_vs:>+8.1f} {fade_vs:>+8.1f}{marker}')

        results.append({
            'method': 'grid_event',
            'grid_K': K, 'cooldown': cooldown,
            'n_detected': n_detected,
            'stop_pnl': round(stop_pnl, 1),
            'fade_pnl': round(fade_pnl_sys, 1),
            'stop_improve': round(stop_vs, 1),
            'fade_improve': round(fade_vs, 1),
        })

# ─────────────────────────────────────────────────────
# METHOD 5: DipBuy as REVERSE probe (dip_buy winning = unusual)
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print('METHOD 5: DipBuy Reverse Probe')
print('  DipBuy normally 16% WR. When it starts winning → 波动大 → 特殊市场')
print('  Track: when dip_buy_wr(last N) > threshold → possible whale volatility')
print(f'{"="*70}')

windows_dip = [10, 15, 20]
dip_win_thresholds = [0.25, 0.30, 0.40, 0.50]

print(f'\n{"W":>3} {"DT":>5} {"N_det":>6} {"Stop_PnL":>10} {"Fade_PnL":>10} '
      f'{"Stop_vs":>9} {"Fade_vs":>9}')
print('-'*65)

for W in windows_dip:
    dip_wr_rolling = np.full(N, np.nan)
    for i in range(N):
        count = 0
        wins = 0
        for j in range(i-1, -1, -1):
            if np.isnan(dip_won[j]):
                continue
            count += 1
            wins += dip_won[j]
            if count >= W:
                break
        if count >= W:
            dip_wr_rolling[i] = wins / count

    for DT in dip_win_thresholds:
        stop_pnl = 0
        fade_pnl_sys = 0
        n_detected = 0

        for i in range(N):
            if not b_traded[i]:
                continue
            d_wr = dip_wr_rolling[i] if not np.isnan(dip_wr_rolling[i]) else 0.16
            if d_wr >= DT:
                n_detected += 1
                stop_pnl += 0
                fade_pnl_sys += fade_pnl[i]
            else:
                stop_pnl += mom_pnl[i]
                fade_pnl_sys += mom_pnl[i]

        stop_vs = stop_pnl - baseline_pnl
        fade_vs = fade_pnl_sys - baseline_pnl

        marker = ''
        if stop_vs > 30: marker = ' ★s'
        if fade_vs > 30: marker += ' ★f'

        print(f'{W:>3} {DT:>5.0%} {n_detected:>6} {stop_pnl:>+9.1f} {fade_pnl_sys:>+9.1f} '
              f'{stop_vs:>+8.1f} {fade_vs:>+8.1f}{marker}')

        results.append({
            'method': 'dip_reverse',
            'window': W, 'dip_win_threshold': DT,
            'n_detected': n_detected,
            'stop_pnl': round(stop_pnl, 1),
            'fade_pnl': round(fade_pnl_sys, 1),
            'stop_improve': round(stop_vs, 1),
            'fade_improve': round(fade_vs, 1),
        })

# ─────────────────────────────────────────────────────
# METHOD 6: Multi-probe consensus (any 2 of 3 abnormal)
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print('METHOD 6: Multi-probe consensus')
print('  Abnormal = grid_lr >= 0.15 OR mom_lr >= 0.35 OR dip_wr >= 0.30')
print('  WHALE when >= 2 of 3 probes abnormal')
print(f'{"="*70}')

W = 15
grid_lr_15 = rolling_loss_rate(grid_won, W)
mom_lr_15 = rolling_loss_rate(mom_won, W)
dip_wr_15 = np.full(N, np.nan)
for i in range(N):
    count = 0; wins = 0
    for j in range(i-1, -1, -1):
        if np.isnan(dip_won[j]): continue
        count += 1; wins += dip_won[j]
        if count >= W: break
    if count >= W:
        dip_wr_15[i] = wins / count

consensus_configs = [
    (0.10, 0.30, 0.25, 2),
    (0.10, 0.30, 0.30, 2),
    (0.15, 0.30, 0.25, 2),
    (0.15, 0.35, 0.30, 2),
    (0.10, 0.25, 0.25, 2),
    (0.10, 0.30, 0.25, 1),  # any 1 abnormal
    (0.15, 0.35, 0.30, 1),
]

print(f'\n{"GT":>5} {"MT":>5} {"DT":>5} {"Min":>4} {"N_det":>6} {"Stop_PnL":>10} {"Fade_PnL":>10} '
      f'{"Stop_vs":>9} {"Fade_vs":>9}')
print('-'*75)

for GT, MT, DT, min_abnormal in consensus_configs:
    stop_pnl = 0
    fade_pnl_sys = 0
    n_detected = 0

    for i in range(N):
        if not b_traded[i]:
            continue
        g = grid_lr_15[i] if not np.isnan(grid_lr_15[i]) else 0
        m = mom_lr_15[i] if not np.isnan(mom_lr_15[i]) else 0
        d = dip_wr_15[i] if not np.isnan(dip_wr_15[i]) else 0.16

        n_abnormal = (g >= GT) + (m >= MT) + (d >= DT)

        if n_abnormal >= min_abnormal:
            n_detected += 1
            stop_pnl += 0
            fade_pnl_sys += fade_pnl[i]
        else:
            stop_pnl += mom_pnl[i]
            fade_pnl_sys += mom_pnl[i]

    stop_vs = stop_pnl - baseline_pnl
    fade_vs = fade_pnl_sys - baseline_pnl

    marker = ''
    if stop_vs > 30: marker = ' ★s'
    if fade_vs > 30: marker += ' ★f'

    print(f'{GT:>5.0%} {MT:>5.0%} {DT:>5.0%} {min_abnormal:>4} {n_detected:>6} {stop_pnl:>+9.1f} {fade_pnl_sys:>+9.1f} '
          f'{stop_vs:>+8.1f} {fade_vs:>+8.1f}{marker}')

    results.append({
        'method': 'consensus',
        'grid_threshold': GT, 'mom_threshold': MT, 'dip_threshold': DT,
        'min_abnormal': min_abnormal,
        'n_detected': n_detected,
        'stop_pnl': round(stop_pnl, 1),
        'fade_pnl': round(fade_pnl_sys, 1),
        'stop_improve': round(stop_vs, 1),
        'fade_improve': round(fade_vs, 1),
    })

# ─────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────
print(f'\n{"="*70}')
print('FINAL SUMMARY')
print(f'{"="*70}')
print(f'Baseline (pure momentum): {baseline_pnl:+.1f}')

res_df = pd.DataFrame(results)
print(f'\nTotal configs tested: {len(res_df)}')

# Top by stop
top_stop = res_df.nlargest(5, 'stop_improve')
print(f'\nTop 5 by STOP (don\'t trade in whale mode):')
for _, r in top_stop.iterrows():
    det = f"n_det={r['n_detected']}"
    print(f'  {r["method"]:15} {det:>10}: stop_PnL={r["stop_pnl"]:+.1f} ({r["stop_improve"]:+.1f})')

# Top by fade
top_fade = res_df.nlargest(5, 'fade_improve')
print(f'\nTop 5 by FADE (reverse trade in whale mode):')
for _, r in top_fade.iterrows():
    det = f"n_det={r['n_detected']}"
    print(f'  {r["method"]:15} {det:>10}: fade_PnL={r["fade_pnl"]:+.1f} ({r["fade_improve"]:+.1f})')

# Also check: what if we just skip the worst hours?
print(f'\n{"="*70}')
print('COMPARISON: Time-of-day filter (for reference)')
print(f'{"="*70}')

df['hour'] = df['round_id'].str.extract(r'_(\d{2})-\d{2}-\d{2}').astype(int)
hourly_mom = df[b_traded].groupby('hour').agg(
    n=('B_pnl', 'count'),
    wr=('B_pnl', lambda x: (x>0).mean()),
    pnl=('B_pnl', 'sum')
).reset_index()

# Skip hours where WR < 76%
bad_hours = hourly_mom[hourly_mom['wr'] < 0.76]['hour'].tolist()
good_pnl = df[b_traded & ~df['hour'].isin(bad_hours)]['B_pnl'].sum()
skip_n = df[b_traded & df['hour'].isin(bad_hours)].shape[0]
print(f'Bad hours (WR<76%): {bad_hours}')
print(f'Skip {skip_n} rounds, keep {b_traded.sum()-skip_n} rounds')
print(f'PnL with time filter: {good_pnl:+.1f} (improvement: {good_pnl-baseline_pnl:+.1f})')

# Skip hours where WR < 78%
bad_hours2 = hourly_mom[hourly_mom['wr'] < 0.78]['hour'].tolist()
good_pnl2 = df[b_traded & ~df['hour'].isin(bad_hours2)]['B_pnl'].sum()
skip_n2 = df[b_traded & df['hour'].isin(bad_hours2)].shape[0]
print(f'\nBad hours (WR<78%): {bad_hours2}')
print(f'Skip {skip_n2} rounds, keep {b_traded.sum()-skip_n2} rounds')
print(f'PnL with time filter: {good_pnl2:+.1f} (improvement: {good_pnl2-baseline_pnl:+.1f})')

# Save
res_df.to_csv(os.path.join(OUT_DIR, 'all_configs.csv'), index=False)
print(f'\nResults saved to {OUT_DIR}/')
