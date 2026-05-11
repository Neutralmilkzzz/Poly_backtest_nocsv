"""Full strategy parameter and cost breakdown"""
import pandas as pd, numpy as np

df = pd.read_csv('results/three_strategies_fixed/three_strategies_fixed.csv')
b = df[df['B_traded']==1].copy()
b['won'] = (b['B_pnl'] > 0).astype(int)
wins = b[b['won']==1]
losses = b[b['won']==0]

print("="*60)
print("MOMENTUM STRATEGY - 完整参数")
print("="*60)
print("Entry time:   t=250s (248-252s)")
print("Entry signal: up_midpoint > 0.55 -> buy UP")
print("              up_midpoint < 0.45 -> buy DOWN")
print("              0.45-0.55 -> skip (不交易)")
print("Entry price:  market buy (吃ask)")
print("Shares:       10 shares")
print("Exit:         持有到结算(t=300s), 无TP/SL")
print("  Win:  收 $1.00/share")
print("  Lose: 收 $0.00/share")
print()
print("--- 数据统计 ---")
print(f"Trades: {len(b)}")
print(f"Entry price: mean={b['B_entry'].mean():.3f}, median={b['B_entry'].median():.3f}")
print(f"  min={b['B_entry'].min():.3f}, max={b['B_entry'].max():.3f}")
print(f"COST per trade: ${b['B_entry'].mean()*10:.2f} (10 shares x ${b['B_entry'].mean():.3f})")
print()
print(f"Win: {len(wins)} ({len(wins)/len(b)*100:.1f}%), Avg win PnL: +${wins['B_pnl'].mean():.2f}")
print(f"Loss: {len(losses)} ({len(losses)/len(b)*100:.1f}%), Avg loss PnL: -${abs(losses['B_pnl'].mean()):.2f}")
print(f"Total PnL: +${b['B_pnl'].sum():.1f} over {len(b)} trades")
print()

# DipBuy probe (virtual)
c = df[df['C_traded']==1]
cw = c[c['C_pnl']>0]
print("="*60)
print("DIPBUY PROBE - 虚拟探针 (不需要真买)")
print("="*60)
print("观察条件: 每盘看是否有某一边ask <= $0.20")
print(f"  触发频率: {len(c)}/{len(df)} rounds ({len(c)/len(df)*100:.0f}%)")
print(f"  正常胜率: {len(cw)/len(c)*100:.1f}% (便宜端很少翻盘)")
print()
print("信号计算:")
print("  滚动记录最近7盘中,便宜端赢了几盘")
print("  如果 >= 30% (7盘中>=3盘) -> WHALE模式")
print("  否则 -> NORMAL模式")
print("  ** 完全免费: 只需观察结算结果 **")
print()

# Fade trade details
fade_entries = 1.0 - b['B_entry'].values
print("="*60)
print("FADE TRADE - whale模式下的操作")
print("="*60)
print("Trigger: dip_wr(last 7) >= 0.30")
print("Action:  动量说买UP -> fade买DOWN (便宜端)")
print("         动量说买DOWN -> fade买UP (便宜端)")
print(f"Fade entry: mean=${np.nanmean(fade_entries):.3f}, median=${np.nanmedian(fade_entries):.3f}")
print(f"COST per FADE: ${np.nanmean(fade_entries)*10:.2f} (10 shares x ${np.nanmean(fade_entries):.3f})")
print()
print("Fade payoff:")
print(f"  Win:  (1.0 - 0.22)*10 = +$7.80  (huge payoff)")
print(f"  Lose: (0.0 - 0.22)*10 = -$2.20  (small loss)")
print(f"  盈亏比: 3.5:1")
print(f"  保本胜率: 22%")
print()

# === System (no time filter) ===
# From dip_probe_deep_analysis: W=7, T=0.30
# 310 whale rounds, 2337 normal rounds
print("="*60)
print("FINAL SYSTEM (无时间过滤) - W=7, T=0.30")
print("="*60)
print("Normal (2337 trades): 跟动量, cost ~$7.80/trade")
print("Whale  (310 trades):  fade反打, cost ~$2.20/trade")
print()

# Recompute from scratch
dip_won = np.where(df['C_traded']==1, (df['C_pnl']>0).astype(float), np.nan)
N = len(df)

# Rolling DipBuy WR (window=7)
dip_wr = np.full(N, np.nan)
for i in range(N):
    count = 0; wins_count = 0
    for j in range(i-1, -1, -1):
        if np.isnan(dip_won[j]): continue
        count += 1; wins_count += dip_won[j]
        if count >= 7: break
    if count >= 7:
        dip_wr[i] = wins_count / count

settlement = df['f_settlement'].values
mom_side_arr = df['B_side'].values
mom_entry_arr = df['B_entry'].values
mom_pnl_arr = df['B_pnl'].values
b_traded = df['B_traded'].values.astype(bool)

total_mom_pnl = 0
total_fade_pnl = 0
n_mom = 0
n_fade = 0
total_mom_cost = 0
total_fade_cost = 0
fade_wins = 0
mom_wins = 0

for i in range(N):
    if not b_traded[i]: continue
    is_whale = not np.isnan(dip_wr[i]) and dip_wr[i] >= 0.30

    if is_whale:
        # Fade
        fade_entry = 1.0 - mom_entry_arr[i]
        if settlement[i] != mom_side_arr[i]:
            pnl = (1.0 - fade_entry) * 10
            fade_wins += 1
        else:
            pnl = -fade_entry * 10
        total_fade_pnl += pnl
        total_fade_cost += fade_entry * 10
        n_fade += 1
    else:
        # Momentum
        total_mom_pnl += mom_pnl_arr[i]
        total_mom_cost += mom_entry_arr[i] * 10
        n_mom += 1
        if mom_pnl_arr[i] > 0:
            mom_wins += 1

sys_pnl = total_mom_pnl + total_fade_pnl
print(f"--- Momentum trades ---")
print(f"  N: {n_mom}")
print(f"  WR: {mom_wins/n_mom*100:.1f}%")
print(f"  PnL: +${total_mom_pnl:.1f}")
print(f"  Total cost (sum of all entries): ${total_mom_cost:.0f}")
print(f"  Avg cost per trade: ${total_mom_cost/n_mom:.2f}")
print()
print(f"--- Fade trades ---")
print(f"  N: {n_fade}")
print(f"  WR: {fade_wins/n_fade*100:.1f}%")
print(f"  PnL: +${total_fade_pnl:.1f}")
print(f"  Total cost (sum of all entries): ${total_fade_cost:.0f}")
print(f"  Avg cost per trade: ${total_fade_cost/n_fade:.2f}")
print()
print(f"--- SYSTEM TOTAL ---")
print(f"  Trades: {n_mom + n_fade}")
print(f"  PnL: +${sys_pnl:.1f}")
print(f"  Avg PnL per trade: +${sys_pnl/(n_mom+n_fade):.3f}")
print()

# Capital requirement
print("="*60)
print("CAPITAL REQUIREMENT")
print("="*60)
print(f"每次只持仓一笔(sequential rounds)")
print(f"Momentum: 最多 $9.45 (max entry $0.945 * 10)")
print(f"Fade:     最多 $4.50 (max fade entry ~$0.45 * 10)")
print(f"=> 准备 $10 够了")
print()

# ROI calculation
print("="*60)
print("ROI (Return on Investment)")
print("="*60)
print(f"19天, {n_mom+n_fade}笔交易")
print(f"初始资金 $10 (max single position)")
print(f"总利润: +${sys_pnl:.1f}")
print(f"ROI: {sys_pnl/10*100:.0f}% over 19 days")
print(f"Daily ROI: {sys_pnl/10/19*100:.1f}%")
print()
print(f"如果每次投入 $2:")
print(f"  Shares: 2/0.78 = ~2.6 shares per momentum trade")
print(f"  Total PnL: +${sys_pnl * 2 / 10:.1f} ($2 is 1/5 of $10 position)")
print()
print(f"如果每次投入 $20:")
print(f"  Shares: 20/0.78 = ~25.6 shares per momentum trade")
print(f"  Total PnL: +${sys_pnl * 20 / 10:.1f}")
