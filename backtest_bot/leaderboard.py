"""Leaderboard: 持久化回测参数表现排行榜。

存储格式: data/leaderboard.jsonl  (每行一条 JSON 记录)
容量上限: MAX_ENTRIES = 2000 条，超出后淘汰 PnL 最差的记录。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MAX_ENTRIES = 2000
_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "leaderboard.jsonl"


def _get_path() -> Path:
    return _DEFAULT_PATH


def record_result(
    overrides: dict[str, Any],
    strategy: str,
    max_rounds: int | None,
    summary: dict[str, Any],
    source: str = "run",
) -> None:
    """追加一条结果到排行榜。

    Parameters
    ----------
    overrides : dict  当次使用的参数覆盖
    strategy  : str   策略底座名称
    max_rounds: int|None 数据范围
    summary   : dict  回测 summary (n_trades, total_pnl, win_rate_pct, ...)
    source    : str   来源标记，"run" 或 "sweep"
    """
    entry = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy,
        "max_rounds": max_rounds,
        "source": source,
        "overrides": overrides,
        "n_trades": summary.get("n_trades", 0),
        "total_pnl": summary.get("total_pnl", 0.0),
        "win_rate_pct": summary.get("win_rate_pct", 0.0),
        "exit_breakdown": summary.get("exit_breakdown", {}),
    }

    path = _get_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # 读取已有记录
    entries: list[dict] = []
    if path.exists():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    entries.append(entry)

    # 按 PnL 降序排列，保留前 MAX_ENTRIES
    entries.sort(key=lambda e: e.get("total_pnl", 0.0), reverse=True)
    entries = entries[:MAX_ENTRIES]

    # 重写文件
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    logger.info("Leaderboard: 记录已写入 (PnL=%.4f, source=%s), 总计 %d 条",
                entry["total_pnl"], source, len(entries))


def get_top(n: int = 10) -> list[dict]:
    """返回排行榜 Top N 记录 (已按 PnL 降序)。"""
    path = _get_path()
    if not path.exists():
        return []

    entries: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    # 文件本身已排序，但以防万一再排一次
    entries.sort(key=lambda e: e.get("total_pnl", 0.0), reverse=True)
    return entries[:n]


def total_count() -> int:
    """返回榜单总条目数。"""
    path = _get_path()
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def format_leaderboard(n: int = 10) -> str:
    """格式化排行榜文本，用于 Telegram 发送。"""
    top = get_top(n)
    count = total_count()

    if not top:
        return "🏆 排行榜为空\n还没有回测记录，跑几轮试试！"

    lines = [
        f"🏆 参数排行榜 (Top {len(top)} / 共 {count} 条)",
        "─" * 34,
    ]

    for i, entry in enumerate(top, 1):
        pnl = entry.get("total_pnl", 0.0)
        wr = entry.get("win_rate_pct", 0.0)
        trd = entry.get("n_trades", 0)
        src = entry.get("source", "?")
        ts = entry.get("ts", "")[:10]  # 只取日期

        # 格式化关键覆盖参数（只显示非空 overrides）
        ov = entry.get("overrides", {})
        if ov:
            ov_parts = [f"{k}={v}" for k, v in ov.items()]
            ov_text = ", ".join(ov_parts[:4])  # 最多显示 4 个
            if len(ov_parts) > 4:
                ov_text += f" +{len(ov_parts)-4}"
        else:
            ov_text = "默认参数"

        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(i, f"{i}.")
        lines.append(
            f"{medal} PnL:{pnl:+.4f} | 胜率:{wr:.0f}% | 交易:{trd}"
        )
        lines.append(f"   {ov_text}")
        lines.append(f"   [{src}] {ts}")

    return "\n".join(lines)
