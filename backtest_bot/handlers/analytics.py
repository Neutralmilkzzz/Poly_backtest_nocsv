from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from backtest_bot.handlers.config import PARAM_MAP, MODULE_MAP

if TYPE_CHECKING:
    from backtest_bot.bot import BacktestTelegramBot

logger = logging.getLogger(__name__)


# ─── Helpers ────────────────────────────────────────────────

def _count_csv_files(data_dir) -> int:
    try:
        return len([f for f in os.listdir(data_dir) if f.endswith(".csv")])
    except OSError:
        return 0


def _parse_key_value_args(text: str) -> dict[str, str]:
    parts = text.strip().split()[1:]
    parsed: dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if key and value:
            parsed[key] = value
    return parsed


def _parse_csv_values(raw: str, caster) -> list[Any]:
    values = []
    for item in raw.replace("，", ",").split(","):
        item = item.strip()
        if not item:
            continue
        values.append(caster(item))
    if not values:
        raise ValueError("empty values")
    return values


def _param_menu_text(bot: BacktestTelegramBot) -> str:
    merged = bot.runner._build_config(bot.draft_overrides, bot.active_strategy_name)
    items = list(PARAM_MAP.items())
    lines = ["📊 单因子扫参测试", "", "请选择要扫描的参数:"]
    for i, (label, (key, _)) in enumerate(items, 1):
        current = merged.get(key)
        display = "未设置" if current is None else current
        lines.append(f"  {i}. {label} (当前: {display})")
    lines.append("")
    lines.append("回复编号选择参数。")
    return "\n".join(lines)


def _build_confirm_message(bot: BacktestTelegramBot) -> str:
    state = bot.analyze_state
    merged = bot.runner._build_config(bot.draft_overrides, bot.active_strategy_name)
    max_rounds = state.get("max_rounds")
    rounds_text = "全部" if max_rounds is None else f"最新 {max_rounds} 个"

    param_lines = []
    for label, (key, _) in PARAM_MAP.items():
        val = merged.get(key)
        display = "未设置" if val is None else val
        marker = "  👈 扫描项" if key == state["param_key"] else ""
        param_lines.append(f"  {label}: {display}{marker}")

    module_lines = []
    for label, key in MODULE_MAP.items():
        module_lines.append(f"  {label}: {'开' if bool(merged.get(key)) else '关'}")

    return "\n".join([
        "📋 扫参配置确认",
        "",
        f"🎯 参数: {state['param_label']}",
        f"📏 测试值: {state['values']}",
        f"📁 数据: {rounds_text}",
        f"🧱 基准: {bot.active_strategy_name}",
        "",
        "当前参数:",
        *param_lines,
        "",
        "模块开关:",
        *module_lines,
        "",
        "操作:\n"
        '  回复 "跑" → 开始执行\n'
        '  改 参数名 值 → 修改参数\n'
        '  开/关 模块名 → 切换开关\n'
        '  取消 → 放弃',
    ])


# ─── Entry Points ──────────────────────────────────────────

async def handle_analyze(bot: BacktestTelegramBot, text: str) -> str:
    """Entry: /analyze → 启动引导式向导 Step 1."""
    bot.analyze_state = {"step": "choose_param"}
    bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
    return _param_menu_text(bot)


async def handle_trendscan(bot: BacktestTelegramBot, text: str) -> str:
    if bot.runner.current_job is not None:
        return "当前有任务运行中，请等待或 /cancel。"

    args = _parse_key_value_args(text)
    try:
        entry_values = _parse_csv_values(args.get("entry", "0.60,0.65,0.70"), float)
        profit_values = _parse_csv_values(args.get("profit", "0.80,0.85"), float)
        stop_values = _parse_csv_values(args.get("stop", "0.40,0.50"), float)
    except ValueError:
        return '参数格式错误。示例: /trendscan entry=0.60,0.65 profit=0.80,0.85 stop=0.40,0.50 side=both'

    trend_side = args.get("side", str(bot.draft_overrides.get("trend_side", "both"))).lower()
    if trend_side not in ("up", "down", "both"):
        return "side 只支持 up、down、both"

    async def _run() -> None:
        await bot.push_update(
            f"🔄 开始趋势策略参数扫描：entry={entry_values}, profit={profit_values}, stop={stop_values}, side={trend_side}"
        )
        try:
            job = await bot.runner.start_script_job(
                script_name="run_trend_grid_search.R",
                script_args=[
                    "--summary-out", "__RESULTS_PATH__",
                    "--out-dir", "__OUT_DIR__",
                    "--entry-values", ",".join(str(v) for v in entry_values),
                    "--profit-values", ",".join(str(v) for v in profit_values),
                    "--stop-values", ",".join(str(v) for v in stop_values),
                    "--trend-side", trend_side,
                ],
                results_name="trend_grid_search_summary.csv",
                overrides=bot.draft_overrides,
                max_rounds=bot.max_rounds,
                active_strategy=bot.active_strategy_name,
                on_update=bot.push_update,
                summary_kind="custom_table",
            )
        except Exception as exc:
            await bot.push_update(f"❌ 趋势扫描启动失败: {exc}")
            return

        while job.status == "running":
            await asyncio.sleep(2)

        if job.status != "completed":
            await bot.push_update("❌ 趋势扫描失败。")
            return

        rows = (job.summary or {}).get("rows", [])[:5]
        if not rows:
            await bot.push_update(f"✅ 趋势扫描完成，但未读到结果表。\n结果目录: {job.results_path}")
            return

        lines = ["✅ 趋势扫描完成", f"结果文件: {job.results_path}", "Top 结果:"]
        for row in rows:
            lines.append(
                f"entry={row.get('trend_entry_price')} profit={row.get('trend_profit_price')} "
                f"stop={row.get('trend_stop_price')} pnl={row.get('total_pnl')} win={row.get('win_rate_pct')}%"
            )
        await bot.push_update("\n".join(lines))

    asyncio.create_task(_run())
    return (
        "🚀 已启动趋势参数扫描，完成后会推送 Top 结果。\n"
        "用法示例: /trendscan entry=0.60,0.65,0.70 profit=0.80,0.85 stop=0.40,0.50 side=both"
    )


async def handle_er(bot: BacktestTelegramBot, text: str) -> str:
    return await _run_factor_analysis(
        bot,
        text=text,
        script_name="run_er_analysis.R",
        results_name="er_bucket_summary.csv",
        label="ER",
        window_key="er_window_seconds",
        value_key="er_bucket",
    )


async def handle_hurst(bot: BacktestTelegramBot, text: str) -> str:
    return await _run_factor_analysis(
        bot,
        text=text,
        script_name="run_hurst_analysis.R",
        results_name="hurst_bucket_summary.csv",
        label="Hurst",
        window_key="hurst_window_seconds",
        value_key="hurst_bucket",
    )


async def handle_analyze_reply(bot: BacktestTelegramBot, text: str) -> str:
    """Dispatch based on wizard step."""
    state = bot.analyze_state if bot.analyze_state else {}
    step = state.get("step", "choose_param")

    if step == "choose_param":
        return await _step_choose_param(bot, text)
    if step == "set_values":
        return await _step_set_values(bot, text)
    if step == "set_sample":
        return await _step_set_sample(bot, text)
    if step == "confirm":
        return await _step_confirm(bot, text)

    bot.analyze_state = {}
    bot.client.set_pending_reply(None)
    return "状态异常，请重新发送 /analyze"


# ─── Wizard Steps ──────────────────────────────────────────

async def _step_choose_param(bot: BacktestTelegramBot, text: str) -> str:
    """Step 1: 用户选择参数编号或名称。"""
    items = list(PARAM_MAP.items())
    label_str = text.strip()

    try:
        idx = int(label_str)
        if idx < 1 or idx > len(items):
            bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
            return f"请输入 1~{len(items)} 之间的编号。"
        label, (key, caster) = items[idx - 1]
    except ValueError:
        if label_str in PARAM_MAP:
            label = label_str
            key, caster = PARAM_MAP[label]
        else:
            bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
            return f"未识别输入，请回复编号 (1~{len(items)})。"

    merged = bot.runner._build_config(bot.draft_overrides, bot.active_strategy_name)
    current = merged.get(key)
    display = "未设置" if current is None else current

    bot.analyze_state = {
        "step": "set_values",
        "param_label": label,
        "param_key": key,
        "param_caster": caster,
    }
    bot.client.set_pending_reply(bot.handle_analyze_reply_bound)

    return (
        f"已选择: {label} (当前基准值: {display})\n\n"
        f"请输入要测试的值列表（逗号分隔）:\n"
        f"例如: 40,50,60,80,100"
    )


async def _step_set_values(bot: BacktestTelegramBot, text: str) -> str:
    """Step 2: 用户输入测试值列表。"""
    state = bot.analyze_state
    caster = state["param_caster"]
    values_str = text.strip().replace("\uff0c", ",")  # 中文逗号兼容
    try:
        values = [caster(v.strip()) for v in values_str.split(",") if v.strip()]
    except ValueError:
        bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
        return f"值解析失败，请确保能转为 {caster.__name__}。重新输入:"

    if not values:
        bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
        return "未提取到有效值，请重新输入。"

    csv_count = _count_csv_files(bot.settings.data_dir)

    state["values"] = values
    state["step"] = "set_sample"
    bot.client.set_pending_reply(bot.handle_analyze_reply_bound)

    return (
        f"测试值: {values}\n\n"
        f"请设置数据范围（使用最新的 N 个 CSV）:\n"
        f"当前可用文件: {csv_count} 个\n"
        f"默认 300，回复数字或 all 跑全量。"
    )


async def _step_set_sample(bot: BacktestTelegramBot, text: str) -> str:
    """Step 3: 用户设置数据范围。"""
    state = bot.analyze_state
    raw = text.strip().lower()

    if raw == "all":
        max_rounds = None
    else:
        try:
            max_rounds = int(raw)
            if max_rounds <= 0:
                bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
                return "数量必须大于 0。"
        except ValueError:
            bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
            return "请输入数字或 all。"

    state["max_rounds"] = max_rounds
    state["step"] = "confirm"
    bot.client.set_pending_reply(bot.handle_analyze_reply_bound)

    return _build_confirm_message(bot)


async def _step_confirm(bot: BacktestTelegramBot, text: str) -> str:
    """Step 4: 确认并执行 / 修改参数 / 取消。"""
    raw = text.strip()

    if raw == "取消":
        bot.analyze_state = {}
        bot.client.set_pending_reply(None)
        return "已取消扫参任务。"

    if raw.startswith("改"):
        parts = raw.split(maxsplit=2)
        if len(parts) < 3:
            bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
            return '格式: 改 参数名 值\n例如: 改 买入价 0.24'
        from backtest_bot.handlers.config import _set_param
        result = _set_param(bot, parts[1], parts[2])
        bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
        return f"{result}\n\n{_build_confirm_message(bot)}"

    if raw.startswith("开 ") or raw.startswith("关 "):
        action = raw[0]
        module_label = raw[2:].strip()
        from backtest_bot.handlers.config import MODULE_MAP as _MM
        if module_label not in _MM:
            bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
            return "模块名不存在。可选:\n" + "\n".join(_MM.keys())
        mkey = _MM[module_label]
        target_val = (action == "开")
        bot.draft_overrides[mkey] = target_val
        bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
        return f"{module_label} 已设为{'开' if target_val else '关'}\n\n{_build_confirm_message(bot)}"

    if raw in ("跑", "确认", "开始", "run", "go"):
        state = bot.analyze_state
        if bot.runner.current_job is not None:
            bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
            return "当前有任务运行中，请等待或 /cancel。"

        bot.client.set_pending_reply(None)
        max_rounds = state["max_rounds"]
        rounds_text = "全部" if max_rounds is None else f"最新 {max_rounds} 个"

        asyncio.create_task(_run_sweep(
            bot,
            key=state["param_key"],
            label=state["param_label"],
            values=state["values"],
            max_rounds=max_rounds,
        ))
        bot.analyze_state = {}

        return (
            f"🚀 扫参已启动\n"
            f"🎯 参数: {state['param_label']}\n"
            f"📏 测试值: {state['values']}\n"
            f"📁 数据: {rounds_text}\n"
            f"⏳ 执行中，完成后推送结果..."
        )

    bot.client.set_pending_reply(bot.handle_analyze_reply_bound)
    return '未识别。可用: "跑" | "改 参数名 值" | "开/关 模块名" | "取消"'


# ─── Sweep Execution ───────────────────────────────────────

async def _run_sweep(bot: BacktestTelegramBot, key: str, label: str,
                     values: list, max_rounds: int | None) -> None:
    results_summary: list[tuple] = []

    while bot.runner.current_job is not None:
        await asyncio.sleep(2)

    await bot.push_update(
        f"🔄 开始单进程扫参: {label} = {values}\n"
        f"   会先预加载一次数据，再复用同一批数据完成全部测试。"
    )

    try:
        job = await bot.runner.start_sweep_job(
            overrides=bot.draft_overrides,
            max_rounds=max_rounds,
            param_key=key,
            values=values,
            active_strategy=bot.active_strategy_name,
            on_update=bot.push_update,
            use_latest=True,
        )
    except Exception as exc:
        await bot.push_update(f"❌ 启动失败: {exc}")
        return

    while job.status == "running":
        await asyncio.sleep(2)

    if job.status == "cancelled":
        await bot.push_update("⚠️ 扫参被取消。")
        return

    if job.status != "completed":
        await bot.push_update("❌ 扫参任务失败，已终止。")
        return

    rows = (job.summary or {}).get("rows", [])
    for idx, row in enumerate(rows, 1):
        value = row.get("value")
        trd = row.get("n_trades", 0)
        pnl = row.get("total_pnl", 0.0)
        wr = row.get("win_rate_pct", 0.0)
        results_summary.append((value, trd, pnl, wr))

        try:
            from backtest_bot.leaderboard import record_result
            temp_overrides = dict(bot.draft_overrides)
            temp_overrides[key] = value
            record_result(
                overrides=temp_overrides,
                strategy=bot.active_strategy_name,
                max_rounds=max_rounds,
                summary=row,
                source="sweep",
            )
        except Exception:
            pass

        await bot.push_update(
            f"✅ [{idx}/{len(rows)}] {label}={value}\n"
            f"   交易:{trd} | 胜率:{wr:.1f}% | PnL:{pnl:.4f}"
        )

    lines = [
        "📊 单因子扫参报告",
        f"基准: {bot.active_strategy_name}",
        f"参数: {label}",
        f"数据: {'全部' if max_rounds is None else f'最新 {max_rounds} 个'}",
        "─" * 32,
    ]
    for val, trd, pnl, wr in results_summary:
        lines.append(f"  {label}={val}  交易:{trd}  胜率:{wr:.1f}%  PnL:{pnl:.4f}")

    if results_summary:
        best = max(results_summary, key=lambda x: x[2])
        lines.append("─" * 32)
        lines.append(f"最优: {label}={best[0]} (PnL={best[2]:.4f})")

    await bot.push_update("\n".join(lines))


async def _run_factor_analysis(
    bot: BacktestTelegramBot,
    text: str,
    script_name: str,
    results_name: str,
    label: str,
    window_key: str,
    value_key: str,
) -> str:
    if bot.runner.current_job is not None:
        return "当前有任务运行中，请等待或 /cancel。"

    args = _parse_key_value_args(text)
    merged = bot.runner._build_config(bot.draft_overrides, bot.active_strategy_name)
    window_value = args.get("window", str(merged.get(window_key)))
    breaks_value = args.get("breaks")

    async def _run() -> None:
        try:
            script_args = [
                "--summary-out",
                "__RESULTS_PATH__",
                "--out-dir",
                "__OUT_DIR__",
                "--window",
                str(window_value),
            ]
            if breaks_value:
                script_args.extend(["--breaks", breaks_value])

            job = await bot.runner.start_script_job(
                script_name=script_name,
                script_args=script_args,
                results_name=results_name,
                overrides=bot.draft_overrides,
                max_rounds=bot.max_rounds,
                active_strategy=bot.active_strategy_name,
                on_update=bot.push_update,
                summary_kind="custom_table",
            )
        except Exception as exc:
            await bot.push_update(f"❌ {label} 分析启动失败: {exc}")
            return

        while job.status == "running":
            await asyncio.sleep(2)

        if job.status != "completed":
            await bot.push_update(f"❌ {label} 分析失败。")
            return

        rows = (job.summary or {}).get("rows", [])
        non_empty = [row for row in rows if row.get("n_trades") not in ("", "0", 0, None)]
        top_rows = non_empty[:5]
        if not top_rows:
            await bot.push_update(f"✅ {label} 分析完成，但没有形成有效分桶。\n结果文件: {job.results_path}")
            return

        lines = [f"✅ {label} 分析完成", f"结果文件: {job.results_path}", "分桶摘要:"]
        for row in top_rows:
            lines.append(
                f"{row.get(value_key)} n={row.get('n_trades')} "
                f"win={row.get('win_rate_pct')}% pnl={row.get('total_pnl')} p={row.get('p_value_win_rate')}"
            )
        await bot.push_update("\n".join(lines))

    asyncio.create_task(_run())
    sample = (
        f"/{label.lower()} window={window_value} breaks=0,0.1,0.2,0.3,0.4,0.5,1.01"
        if label == "ER"
        else f"/{label.lower()} window={window_value} breaks=0,0.3,0.4,0.5,0.6,0.7,1.01"
    )
    return f"🚀 已启动 {label} 分析，完成后会推送分桶结果。\n用法示例: {sample}"
