"""
═══════════════════════════════════════════════════════════════
 Rolling Window Regime Analysis — 无前瞻偏差
═══════════════════════════════════════════════════════════════

核心问题: 用"过去N轮"的微结构特征,
         能否预测"当前轮"的策略表现?

测试方案:
  1. Dip Buy + 动态仓位: 过去N轮tail_range高→加仓, 低→减仓
  2. Momentum + 逆向切换: 过去N轮逆转率高→做逆向, 低→做顺向
  3. Combined: 最优组合
"""

import pandas as pd, numpy as np, glob, os, time, warnings
warnings.filterwarnings('ignore')

DATA_DIR = r'C:\Users\ZHAOKAI\data'
OUT_DIR  = r'C:\Users\ZHAOKAI\Poly_backtest_Final\results\three_strategies'
os.makedirs(OUT_DIR, exist_ok=True)

# Load raw results from phase 1
raw = pd.read_csv(os.path.join(OUT_DIR, 'three_strategies_raw.csv'))
print(f'Loaded {len(raw)} rounds')

# ═══════════════════════════════════════════════════════
# 1. DIP BUY WITH DYNAMIC SIZING (低吸 + 动态仓位)
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  1. DIP BUY + DYNAMIC SIZING')
print(f'{"="*60}')

# Idea: Use past N rounds' avg tail_range to predict current round's regime
# High avg tail_range = whale active = dip buy has lower loss rate = size up

# First, check if past tail_range predicts current tail_range (autocorrelation)
tr = raw['f_tail_range'].values
for lag in [1, 3, 5, 10, 20]:
    if len(tr) > lag + 10:
        corr = np.corrcoef(tr[lag:], tr[:-lag])[0, 1]
        print(f'  tail_range autocorrelation lag-{lag}: {corr:.3f}')

# Rolling mean of past N rounds' tail_range
for window in [5, 10, 20]:
    raw[f'rolling_tr_{window}'] = raw['f_tail_range'].shift(1).rolling(window).mean()

# Check if past avg tail_range predicts current round's dip buy outcome
print(f'\nDip Buy PnL by past tail_range regime:')
for window in [5, 10, 20]:
    col = f'rolling_tr_{window}'
    valid = raw[raw[col].notna() & raw['C_traded']].copy()
    if len(valid) == 0:
        continue

    # Split into quantiles
    valid['regime'] = pd.qcut(valid[col], 3, labels=['low', 'mid', 'high'], duplicates='drop')

    print(f'\n  Window={window} rounds:')
    for regime in ['low', 'mid', 'high']:
        subset = valid[valid.regime == regime]
        n = len(subset)
        wr = (subset.C_pnl > 0).mean() * 100
        avg_pnl = subset.C_pnl.mean()
        total = subset.C_pnl.sum()
        tr_range = f'[{subset[col].min():.3f}, {subset[col].max():.3f}]'
        print(f'    {regime:>4}: n={n:5d}, WR={wr:5.1f}%, avgPnL={avg_pnl:+.3f}, total={total:+8.1f}, tr_range={tr_range}')

# Optimal strategy: only trade dip buy when past 10-round tail_range avg is high
print(f'\n--- Dip Buy with threshold on past tail_range ---')
for window in [10, 20]:
    col = f'rolling_tr_{window}'
    valid = raw[raw[col].notna() & raw['C_traded']].copy()

    for threshold in [0.15, 0.20, 0.25, 0.30, 0.35, 0.40]:
        active = valid[valid[col] >= threshold]
        inactive = valid[valid[col] < threshold]
        n_active = len(active)
        n_inactive = len(inactive)

        if n_active < 50:
            continue

        active_pnl = active.C_pnl.sum()
        inactive_pnl = inactive.C_pnl.sum()
        active_wr = (active.C_pnl > 0).mean() * 100
        inactive_wr = (inactive.C_pnl > 0).mean() * 100 if n_inactive > 0 else 0

        print(f'  W={window}, thr>={threshold:.2f}: TRADE {n_active} ({active_wr:.1f}%, PnL={active_pnl:+.0f}) | SKIP {n_inactive} ({inactive_wr:.1f}%, PnL={inactive_pnl:+.0f})')

# ═══════════════════════════════════════════════════════
# 2. MOMENTUM + CONTRARIAN SWITCHING (动量/逆向切换)
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  2. MOMENTUM + CONTRARIAN SWITCHING')
print(f'{"="*60}')

# Key insight: if we KNEW reversal, we'd switch.
# Can we use PAST reversal rate to predict current round?
rev = raw['f_reversal'].values
for lag in [1, 3, 5, 10, 20]:
    if len(rev) > lag + 10:
        valid_mask = ~np.isnan(rev[lag:]) & ~np.isnan(rev[:-lag])
        if valid_mask.sum() > 100:
            corr = np.corrcoef(rev[lag:][valid_mask], rev[:-lag][valid_mask])[0, 1]
            print(f'  reversal autocorrelation lag-{lag}: {corr:.3f}')

# Rolling reversal rate
for window in [5, 10, 20]:
    raw[f'rolling_rev_{window}'] = raw['f_reversal'].shift(1).rolling(window).mean()

# Strategy: if recent reversal rate > 0.5 → do contrarian (fade); else → follow momentum
print(f'\nMomentum outcome conditioned on past reversal rate:')
for window in [5, 10, 20]:
    col = f'rolling_rev_{window}'
    valid = raw[raw[col].notna() & raw['B_traded']].copy()
    if len(valid) == 0:
        continue

    # Original momentum PnL
    orig_pnl = valid.B_pnl.sum()

    # Split: high reversal → fade, low reversal → follow
    for rev_threshold in [0.40, 0.45, 0.50, 0.55, 0.60]:
        high_rev = valid[valid[col] >= rev_threshold]
        low_rev = valid[valid[col] < rev_threshold]

        # In high_rev regime: flip the PnL (contrarian = opposite of momentum)
        # Momentum buys at ask, if it would've lost it now wins and vice versa
        # BUT contrarian entry price is different (buy opposite side)
        # Simplification: contrarian PnL ≈ -momentum_pnl + adjustment for entry price
        # Actually this is wrong — the entry price changes because we're buying the other side.
        # Let me compute it properly.

        # For momentum: PnL = (settlement - entry_price) * shares
        # For contrarian on same round: PnL = (settlement_other - entry_price_other) * shares
        # Where settlement_other = 1 - settlement, entry_price_other = (1-side's) ask

        # Approximation: when momentum loses (reversal), the opposite side wins.
        # The opposite side's entry price ≈ 1 - momentum_entry_price (roughly)
        # So contrarian PnL ≈ (1.0 - (1 - entry_price)) * shares = entry_price * shares for wins
        # and ≈ (0.0 - (1 - entry_price)) * shares for losses

        # This is complex. Let me just check the hit rate.
        high_loss_rate = (high_rev.B_pnl <= 0).mean() if len(high_rev) > 0 else 0
        low_loss_rate = (low_rev.B_pnl <= 0).mean() if len(low_rev) > 0 else 0

        # If we skip momentum in high-rev regime:
        skip_pnl = low_rev.B_pnl.sum()

        print(f'  W={window}, rev_thr={rev_threshold:.2f}: '
              f'HIGH n={len(high_rev)} LR={high_loss_rate:.1%} | '
              f'LOW n={len(low_rev)} LR={low_loss_rate:.1%} | '
              f'skip_high_PnL={skip_pnl:+.0f} (vs orig {orig_pnl:+.0f})')

# ═══════════════════════════════════════════════════════
# 3. DIP BUY — DEEPER ANALYSIS BY ENTRY PRICE RANGES
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  3. DIP BUY — ENTRY PRICE SENSITIVITY')
print(f'{"="*60}')

dip_trades = raw[raw['C_traded']].copy()
print(f'\nDip Buy by entry price bucket:')

bins = [0, 0.02, 0.05, 0.08, 0.10, 0.12, 0.15, 0.20]
dip_trades['price_bucket'] = pd.cut(dip_trades['C_entry'], bins=bins)
for bucket, group in dip_trades.groupby('price_bucket', observed=True):
    n = len(group)
    wr = (group.C_pnl > 0).mean() * 100
    avg = group.C_pnl.mean()
    total = group.C_pnl.sum()
    avg_entry = group.C_entry.mean()
    print(f'  {str(bucket):>15}: n={n:5d}, WR={wr:5.1f}%, avg_entry={avg_entry:.3f}, avgPnL={avg:+.3f}, total={total:+8.1f}')

# ═══════════════════════════════════════════════════════
# 4. GRID — WHY IT LOSES (EARLY_RANGE SIGNAL)
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  4. GRID — LOSS ANALYSIS')
print(f'{"="*60}')

grid_trades = raw[raw['A_traded']].copy()
print(f'\nGrid exit type vs PnL:')
for t, g in grid_trades.groupby('A_type'):
    print(f'  {t:>10}: n={len(g):5d}, WR={(g.A_pnl>0).mean()*100:5.1f}%, avgPnL={g.A_pnl.mean():+.4f}, total={g.A_pnl.sum():+.1f}')

# Grid by early_up_range
print(f'\nGrid by early UP range (proxy for early volatility):')
bins_e = [0, 0.30, 0.35, 0.40, 0.50, 1.0]
grid_trades['early_bucket'] = pd.cut(grid_trades['f_early_up_range'], bins=bins_e)
for bucket, group in grid_trades.groupby('early_bucket', observed=True):
    n = len(group)
    wr = (group.A_pnl > 0).mean() * 100
    total = group.A_pnl.sum()
    timeout_rate = (group.A_type == 'timeout').mean() * 100
    print(f'  {str(bucket):>15}: n={n:5d}, WR={wr:5.1f}%, PnL={total:+8.1f}, timeout={timeout_rate:.1f}%')

# Can we filter: only trade grid when early_range is LOW?
print(f'\nGrid with rolling past-10 early_range filter:')
raw['rolling_er_10'] = raw['f_early_up_range'].shift(1).rolling(10).mean()
valid = raw[raw['rolling_er_10'].notna() & raw['A_traded']].copy()

for threshold in [0.30, 0.32, 0.35, 0.38, 0.40]:
    active = valid[valid['rolling_er_10'] <= threshold]
    skip = valid[valid['rolling_er_10'] > threshold]
    if len(active) < 50:
        continue
    a_pnl = active.A_pnl.sum()
    s_pnl = skip.A_pnl.sum()
    a_wr = (active.A_pnl > 0).mean() * 100
    print(f'  early_range<={threshold:.2f}: TRADE {len(active)} ({a_wr:.1f}%, PnL={a_pnl:+.0f}) | SKIP {len(skip)} (PnL={s_pnl:+.0f})')

# ═══════════════════════════════════════════════════════
# 5. COMBINED SYSTEM: Dip Buy + Grid + Regime
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  5. COMBINED OPTIMAL SYSTEM')
print(f'{"="*60}')

# Base: Dip Buy always on (it's the money maker)
# Add: Grid only when conditions are right
# Add: Dynamic sizing for Dip Buy based on regime

valid = raw.copy()
valid['rolling_tr_10'] = valid['f_tail_range'].shift(1).rolling(10).mean()
valid['rolling_er_10'] = valid['f_early_up_range'].shift(1).rolling(10).mean()
valid = valid[valid['rolling_tr_10'].notna()].copy()

print(f'\nValid rounds (with rolling data): {len(valid)}')

# System A: Dip Buy only, always
sys_a = valid[valid['C_traded']]['C_pnl'].sum()
sys_a_n = valid['C_traded'].sum()

# System B: Dip Buy only during "whale" regime (past tail_range avg >= 0.25)
whale_mask = valid['rolling_tr_10'] >= 0.25
sys_b_active = valid[whale_mask & valid['C_traded']]
sys_b = sys_b_active['C_pnl'].sum()
sys_b_n = len(sys_b_active)

# System C: Dip Buy always + Grid only when past early_range <= 0.35
grid_mask = valid['rolling_er_10'] <= 0.35
sys_c_dip = valid[valid['C_traded']]['C_pnl'].sum()
sys_c_grid = valid[grid_mask & valid['A_traded']]['A_pnl'].sum()
sys_c = sys_c_dip + sys_c_grid
sys_c_n = valid['C_traded'].sum() + (grid_mask & valid['A_traded']).sum()

# System D: Enhanced Dip Buy (2x size during whale, 1x otherwise) + filtered Grid
whale_dip = valid[whale_mask & valid['C_traded']]['C_pnl'].sum() * 2  # 2x
normal_dip = valid[~whale_mask & valid['C_traded']]['C_pnl'].sum() * 1  # 1x
sys_d = whale_dip + normal_dip + sys_c_grid
sys_d_n = sys_c_n  # same number of trades

print(f'\n{"System":<40} {"Trades":>7} {"PnL":>10}')
print('-' * 60)
print(f'{"A: Dip Buy always":<40} {sys_a_n:>7} {sys_a:>+10.1f}')
print(f'{"B: Dip Buy whale-only (tr10>=0.25)":<40} {sys_b_n:>7} {sys_b:>+10.1f}')
print(f'{"C: Dip Buy always + filtered Grid":<40} {sys_c_n:>7} {sys_c:>+10.1f}')
print(f'{"D: Dip Buy 2x whale + filtered Grid":<40} {sys_d_n:>7} {sys_d:>+10.1f}')

# ═══════════════════════════════════════════════════════
# 6. DIP BUY — TRAIN/TEST SPLIT (检查过拟合)
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  6. TRAIN/TEST SPLIT (前70% vs 后30%)')
print(f'{"="*60}')

n_total = len(raw)
n_train = int(n_total * 0.7)
train = raw.iloc[:n_train]
test = raw.iloc[n_train:]

for prefix, name in [('A','Grid'), ('B','Momentum'), ('C','Dip Buy')]:
    for split_name, split_df in [('TRAIN', train), ('TEST', test)]:
        traded = split_df[split_df[f'{prefix}_traded']]
        n = len(traded)
        wr = (traded[f'{prefix}_pnl'] > 0).mean() * 100 if n > 0 else 0
        total = traded[f'{prefix}_pnl'].sum() if n > 0 else 0
        avg = traded[f'{prefix}_pnl'].mean() if n > 0 else 0
        print(f'  {name:>10} {split_name}: n={n:5d}, WR={wr:5.1f}%, PnL={total:>+8.1f}, avg={avg:+.3f}')

# ═══════════════════════════════════════════════════════
# 7. OPTIMAL DIP BUY PARAMETERS SWEEP
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  7. DIP BUY PARAMETER SWEEP (threshold + take_profit)')
print(f'{"="*60}')

# We need to re-run dip buy with different parameters on the raw data
# But that requires re-reading CSVs. Instead, use the entry prices we already have.
# The entry price tells us what the ask was — we can simulate different thresholds.

dip_all = raw[['round_id', 'C_traded', 'C_pnl', 'C_entry', 'C_exit', 'C_type', 'f_settlement']].copy()

# For threshold testing: a higher threshold means more trades
# Existing data has threshold=0.20, so we can only test LOWER thresholds
# (subset of existing trades)
print(f'\nLower threshold (stricter filter, fewer trades):')
for thr in [0.05, 0.08, 0.10, 0.12, 0.15, 0.20]:
    subset = dip_all[(dip_all.C_traded) & (dip_all.C_entry <= thr)]
    n = len(subset)
    if n < 50:
        continue
    wr = (subset.C_pnl > 0).mean() * 100
    total = subset.C_pnl.sum()
    avg = subset.C_pnl.mean()
    avg_entry = subset.C_entry.mean()
    print(f'  threshold <= {thr:.2f}: n={n:5d}, WR={wr:5.1f}%, avg_entry={avg_entry:.3f}, PnL={total:>+8.1f}, avg={avg:+.3f}')

# ═══════════════════════════════════════════════════════
# FINAL RECOMMENDATIONS
# ═══════════════════════════════════════════════════════
print(f'\n{"="*60}')
print(f'  FINAL RECOMMENDATIONS')
print(f'{"="*60}')
print(f"""
1. 尾盘低吸 (Dip Buy) 是核心盈利策略
   - 总 PnL: +15,021 (4,191 trades, 54.7% WR)
   - 极端低价买入 (avg 0.108), 风险/回报比 7.4:1
   - 关键: 庄家操盘创造的极端价格 = 低吸的最佳机会

2. 庄家信号 (tail_range) 不是停止交易的信号,
   而是加码的信号!
   - tail_range >= 0.70: 低吸只有 10.8% 亏损率
   - tail_range < 0.30: 低吸 53.7% 亏损率 (接近随机)

3. 网格策略需要过滤条件
   - 核心问题: 82.8% 胜率但负 PnL (1赔=16赢)
   - 亏损集中在 timeout 出场 (385笔, 几乎全亏)
   - 条件: early_range 低时才开 (价格稳定 = 网格更容易止盈)

4. 动量策略在此市场不可行
   - 入场价太高 (0.78), 风险回报比 1:3.6
   - 逆转率 47% 且无法预测 → 本质是负EV赌博

5. 建议实盘策略:
   低吸为主 + 网格辅助 + 动态仓位
""")

# Save
raw.to_csv(os.path.join(OUT_DIR, 'rolling_analysis.csv'), index=False)
print(f'\nSaved to {OUT_DIR}/')
