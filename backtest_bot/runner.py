from __future__ import annotations

import asyncio
import csv
import logging
import os
import sys
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config import BotSettings


logger = logging.getLogger(__name__)

UpdateCallback = Callable[[str], Awaitable[None]]


def _to_wsl_path(p: str | Path) -> str:
    """Convert Windows path to WSL /mnt/ path if running under WSL."""
    s = str(p)
    is_wsl = (
        sys.platform.startswith("linux") and
        (
            "microsoft" in os.uname().release.lower() or
            bool(os.environ.get("WSL_DISTRO_NAME"))
        )
    )
    if not is_wsl:
        return s
    # Only convert if it looks like a Windows absolute path (e.g. C:\... or C:/...)
    if len(s) >= 3 and s[1] == ':' and s[2] in ('\\', '/'):
        drive = s[0].lower()
        rest = s[3:].replace('\\', '/')
        return f"/mnt/{drive}/{rest}"
    return s.replace('\\', '/')


@dataclass
class BacktestJob:
    job_id: str
    command: list[str]
    config_path: Path
    results_path: Path
    reports_dir: Path
    overrides: dict[str, Any]
    max_rounds: int | None
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: datetime | None = None
    status: str = "running"
    return_code: int | None = None
    summary: dict[str, Any] | None = None
    output_lines: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    process: asyncio.subprocess.Process | None = None
    summary_kind: str = "backtest_results"
    notify_on_completion: bool = True

    def format_status(self) -> str:
        lines = [
            f"任务: {self.job_id}",
            f"状态: {self.status}",
            f"开始: {self.started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"样本数: {'全部' if self.max_rounds is None else self.max_rounds}",
        ]
        if self.finished_at is not None:
            lines.append(f"结束: {self.finished_at.strftime('%Y-%m-%d %H:%M:%S')}")
        if self.summary:
            lines.append(f"总PnL: {self.summary.get('total_pnl', 0):.4f}")
            lines.append(f"交易数: {self.summary.get('n_trades', 0)}")
            lines.append(f"胜率: {self.summary.get('win_rate_pct', 0):.1f}%")
        if self.output_lines:
            lines.append("最近输出:")
            lines.extend(list(self.output_lines)[-5:])
        return "\n".join(lines)


class BacktestRunner:
    def __init__(self, settings: BotSettings):
        self.settings = settings
        self.current_job: BacktestJob | None = None
        self.last_job: BacktestJob | None = None

    async def start_job(
        self,
        overrides: dict[str, Any],
        max_rounds: int | None,
        active_strategy: str = "config/strategy.yaml",
        on_update: UpdateCallback | None = None,
        on_photos: UpdateCallback | None = None,
        use_latest: bool = False,
    ) -> BacktestJob:
        if self.current_job is not None and self.current_job.status == "running":
            raise RuntimeError("已有回测任务在运行")

        job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_root = self.settings.output_root / job_id
        reports_dir = job_root / "reports"
        results_path = job_root / "backtest_results.csv"
        config_path = job_root / "strategy.yaml"
        job_root.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        merged = self._build_config(overrides, active_strategy)
        config_path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")

        command = [
            self.settings.rscript_command,
            _to_wsl_path(self.settings.root_dir / "scripts" / "run_backtest.R"),
            "--config",
            _to_wsl_path(config_path),
            "--data-dir",
            _to_wsl_path(self.settings.data_dir),
            "--results",
            _to_wsl_path(results_path),
            "--reports-dir",
            _to_wsl_path(reports_dir),
            "--cores",
            str(self.settings.n_cores),
        ]
        if max_rounds is not None:
            command.extend(["--max", str(max_rounds)])
        if use_latest:
            command.append("--latest")

        logger.info("启动 R 命令: %s", " ".join(command))

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=_to_wsl_path(self.settings.root_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        job = BacktestJob(
            job_id=job_id,
            command=command,
            config_path=config_path,
            results_path=results_path,
            reports_dir=reports_dir,
            overrides=dict(overrides),
            max_rounds=max_rounds,
            process=process,
        )
        self.current_job = job

        asyncio.create_task(self._watch_job(job, on_update, on_photos))
        return job

    async def start_sweep_job(
        self,
        overrides: dict[str, Any],
        max_rounds: int | None,
        param_key: str,
        values: list[Any],
        active_strategy: str = "config/strategy.yaml",
        on_update: UpdateCallback | None = None,
        use_latest: bool = False,
    ) -> BacktestJob:
        if self.current_job is not None and self.current_job.status == "running":
            raise RuntimeError("已有回测任务在运行")

        job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_root = self.settings.output_root / job_id
        reports_dir = job_root / "reports"
        results_path = job_root / "sweep_summary.csv"
        config_path = job_root / "strategy.yaml"
        job_root.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        merged = self._build_config(overrides, active_strategy)
        config_path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")

        command = [
            self.settings.rscript_command,
            _to_wsl_path(self.settings.root_dir / "scripts" / "run_param_sweep.R"),
            "--config",
            _to_wsl_path(config_path),
            "--data-dir",
            _to_wsl_path(self.settings.data_dir),
            "--param",
            param_key,
            "--values",
            ",".join(str(v) for v in values),
            "--summary-out",
            _to_wsl_path(results_path),
            "--cores",
            str(self.settings.n_cores),
        ]
        if max_rounds is not None:
            command.extend(["--max", str(max_rounds)])
        if use_latest:
            command.append("--latest")

        logger.info("启动扫参 R 命令: %s", " ".join(command))

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=_to_wsl_path(self.settings.root_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        job = BacktestJob(
            job_id=job_id,
            command=command,
            config_path=config_path,
            results_path=results_path,
            reports_dir=reports_dir,
            overrides=dict(overrides),
            max_rounds=max_rounds,
            process=process,
            summary_kind="sweep_summary",
            notify_on_completion=False,
        )
        self.current_job = job

        asyncio.create_task(self._watch_job(job, on_update, None))
        return job

    async def start_script_job(
        self,
        script_name: str,
        script_args: list[str],
        results_name: str,
        overrides: dict[str, Any],
        max_rounds: int | None,
        active_strategy: str = "config/strategy.yaml",
        on_update: UpdateCallback | None = None,
        summary_kind: str = "custom",
        notify_on_completion: bool = False,
    ) -> BacktestJob:
        if self.current_job is not None and self.current_job.status == "running":
            raise RuntimeError("已有回测任务在运行")

        job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_root = self.settings.output_root / job_id
        reports_dir = job_root / "reports"
        results_path = job_root / results_name
        config_path = job_root / "strategy.yaml"
        job_root.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)

        merged = self._build_config(overrides, active_strategy)
        config_path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False), encoding="utf-8")

        resolved_script_args = []
        for arg in script_args:
            if arg == "__RESULTS_PATH__":
                resolved_script_args.append(_to_wsl_path(results_path))
            elif arg == "__OUT_DIR__":
                resolved_script_args.append(_to_wsl_path(job_root))
            else:
                resolved_script_args.append(arg)

        command = [
            self.settings.rscript_command,
            _to_wsl_path(self.settings.root_dir / "scripts" / script_name),
            "--config",
            _to_wsl_path(config_path),
            "--data-dir",
            _to_wsl_path(self.settings.data_dir),
        ] + resolved_script_args

        if max_rounds is not None:
            command.extend(["--max", str(max_rounds)])

        logger.info("启动分析 R 命令: %s", " ".join(command))

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=_to_wsl_path(self.settings.root_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        job = BacktestJob(
            job_id=job_id,
            command=command,
            config_path=config_path,
            results_path=results_path,
            reports_dir=reports_dir,
            overrides=dict(overrides),
            max_rounds=max_rounds,
            process=process,
            summary_kind=summary_kind,
            notify_on_completion=notify_on_completion,
        )
        self.current_job = job

        asyncio.create_task(self._watch_job(job, on_update, None))
        return job

    async def start_sync_job(
        self,
        on_update: UpdateCallback | None = None,
    ) -> BacktestJob:
        if self.current_job is not None and self.current_job.status == "running":
            raise RuntimeError("已有回测任务在运行")

        job_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        job_root = self.settings.output_root / job_id
        reports_dir = job_root / "reports"
        results_path = job_root / "sync_manifest.csv"
        config_path = job_root / "strategy.yaml"
        cache_dir = self.settings.root_dir / "data" / "cache" / "fst"
        manifest_path = self.settings.root_dir / "data" / "manifest.csv"

        job_root.mkdir(parents=True, exist_ok=True)
        reports_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text("", encoding="utf-8")

        command = [
            self.settings.rscript_command,
            _to_wsl_path(self.settings.root_dir / "scripts" / "build_fst_cache.R"),
            "--raw-dir",
            _to_wsl_path(self.settings.data_dir),
            "--cache-dir",
            _to_wsl_path(cache_dir),
        ]

        logger.info("启动缓存同步命令: %s", " ".join(command))

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=_to_wsl_path(self.settings.root_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        job = BacktestJob(
            job_id=job_id,
            command=command,
            config_path=config_path,
            results_path=manifest_path,
            reports_dir=reports_dir,
            overrides={},
            max_rounds=None,
            process=process,
            summary_kind="custom",
            notify_on_completion=False,
        )
        self.current_job = job

        asyncio.create_task(self._watch_job(job, on_update, None))
        return job

    async def cancel_current_job(self) -> bool:
        if self.current_job is None or self.current_job.process is None:
            return False
        if self.current_job.status != "running":
            return False

        self.current_job.process.terminate()
        self.current_job.status = "cancelled"
        return True

    def get_status(self) -> str:
        if self.current_job is not None:
            return self.current_job.format_status()
        if self.last_job is not None:
            return self.last_job.format_status()
        return "当前还没有运行过回测任务。"

    def _build_config(self, overrides: dict[str, Any], active_strategy: str = "config/strategy.yaml") -> dict[str, Any]:
        target_path = self.settings.root_dir / active_strategy
        if not target_path.exists():
            target_path = self.settings.config_path
            
        base = yaml.safe_load(target_path.read_text(encoding="utf-8")) or {}
        base.update(overrides)
        return base

    async def _watch_job(self, job: BacktestJob, on_update: UpdateCallback | None, on_photos: UpdateCallback | None) -> None:
        try:
            await self._read_output(job, on_update)
            if job.process is not None:
                job.return_code = await job.process.wait()

            if job.status == "cancelled":
                job.finished_at = datetime.now()
                self.last_job = job
                self.current_job = None
                return

            job.finished_at = datetime.now()
            if job.return_code == 0:
                job.status = "completed"
                if job.summary_kind == "sweep_summary":
                    job.summary = self._summarize_sweep_results(job.results_path)
                elif job.summary_kind == "custom_table":
                    job.summary = self._summarize_generic_table(job.results_path)
                else:
                    job.summary = self._summarize_results(job.results_path)
                    try:
                        from backtest_bot.leaderboard import record_result
                        record_result(
                            overrides=job.overrides,
                            strategy=str(job.config_path.parent.name),
                            max_rounds=job.max_rounds,
                            summary=job.summary,
                            source="run",
                        )
                    except Exception as lb_exc:
                        logger.warning("Leaderboard write failed: %s", lb_exc)
            else:
                job.status = "failed"

            self.last_job = job
            self.current_job = None

            if on_update is not None:
                if job.status == "completed":
                    if job.notify_on_completion:
                        await on_update(self._format_completion_message(job))
                    if on_photos is not None:
                        photo_candidates = list(job.reports_dir.glob("*.png"))
                        if photo_candidates:
                            await on_photos([(path, path.stem) for path in photo_candidates[:5]])
                else:
                    tail = list(job.output_lines)[-8:]
                    tail_text = "\n".join(tail) if tail else "(无输出)"
                    logger.error("回测失败 (rc=%s), 最后输出:\n%s", job.return_code, tail_text)
                    await on_update(
                        f"❌ 回测任务失败，返回码 {job.return_code}。\n"
                        f"错误输出:\n{tail_text}"
                    )
        except Exception as exc:
            logger.exception("Watch job failed: %s", exc)
            job.status = "failed"
            job.finished_at = datetime.now()
            self.last_job = job
            self.current_job = None
            if on_update is not None:
                await on_update(f"回测任务异常终止: {exc}")

    async def _read_output(self, job: BacktestJob, on_update: UpdateCallback | None) -> None:
        if job.process is None or job.process.stdout is None:
            return

        import time
        last_push_ts = 0.0

        while True:
            raw_line = await job.process.stdout.readline()
            if not raw_line:
                break

            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            job.output_lines.append(line)

            if on_update is None:
                continue

            if line.startswith("SWEEP_RESULT|"):
                continue

            if any(keyword in line for keyword in ("回测开始", "回测完成", "结果已保存到", "预加载开始", "任务规模")):
                await on_update(line)
            elif "进度:" in line:
                now = time.monotonic()
                if now - last_push_ts > 45: # throttle every 45s
                    elapsed = (datetime.now() - job.started_at).total_seconds()
                    mins, secs = divmod(int(elapsed), 60)
                    await on_update(f"⏳ 组合回测中... 已运行 {mins}分{secs}秒 | {line}")
                    last_push_ts = now

    def _summarize_results(self, results_path: Path) -> dict[str, Any]:
        if not results_path.exists():
            return {
                "n_total": 0,
                "n_trades": 0,
                "total_pnl": 0.0,
                "win_rate_pct": 0.0,
                "exit_breakdown": {},
                "skip_breakdown": {},
            }

        rows = []
        with results_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(row)

        traded_rows = [row for row in rows if str(row.get("traded", "")).upper() == "TRUE"]
        total_pnl = sum(float(row.get("pnl", 0) or 0) for row in traded_rows)
        wins = sum(1 for row in traded_rows if float(row.get("pnl", 0) or 0) > 0)

        exit_breakdown = Counter(row.get("exit_type", "") or "unknown" for row in traded_rows)
        skipped_rows = [row for row in rows if str(row.get("traded", "")).upper() != "TRUE"]
        skip_breakdown = Counter(row.get("skip_reason", "") or "unknown" for row in skipped_rows)

        return {
            "n_total": len(rows),
            "n_trades": len(traded_rows),
            "total_pnl": total_pnl,
            "win_rate_pct": (wins / len(traded_rows) * 100) if traded_rows else 0.0,
            "exit_breakdown": dict(exit_breakdown),
            "skip_breakdown": dict(skip_breakdown),
        }

    def _format_completion_message(self, job: BacktestJob) -> str:
        summary = job.summary or {}
        exit_breakdown = self._format_breakdown(summary.get("exit_breakdown", {}))
        skip_breakdown = self._format_breakdown(summary.get("skip_breakdown", {}))
        er_summary = self._read_bucket_summary(job.reports_dir / "er_bucket_summary.csv", "er_bucket")
        hurst_summary = self._read_bucket_summary(job.reports_dir / "hurst_bucket_summary.csv", "hurst_bucket")

        lines = [
            f"回测完成: {job.job_id}",
            f"轮次总数: {summary.get('n_total', 0)}",
            f"交易笔数: {summary.get('n_trades', 0)}",
            f"总PnL: {summary.get('total_pnl', 0.0):.4f}",
            f"胜率: {summary.get('win_rate_pct', 0.0):.1f}%",
            f"退出分布: {exit_breakdown}",
            f"跳过分布: {skip_breakdown}",
            f"结果文件: {job.results_path}",
            f"图表目录: {job.reports_dir}",
        ]
        if er_summary:
            lines.append(f"ER分桶: {er_summary}")
        if hurst_summary:
            lines.append(f"Hurst分桶: {hurst_summary}")
        return "\n".join(lines)

    @staticmethod
    def _format_breakdown(values: dict[str, Any]) -> str:
        if not values:
            return "无"
        parts = [f"{key}:{value}" for key, value in values.items()]
        return ", ".join(parts)

    def _summarize_sweep_results(self, results_path: Path) -> dict[str, Any]:
        if not results_path.exists():
            return {"rows": []}

        rows: list[dict[str, Any]] = []
        with results_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append({
                    "value": row.get("value", ""),
                    "n_total": int(float(row.get("n_total", 0) or 0)),
                    "n_trades": int(float(row.get("n_trades", 0) or 0)),
                    "total_pnl": float(row.get("total_pnl", 0) or 0),
                    "win_rate_pct": float(row.get("win_rate_pct", 0) or 0),
                })

        return {"rows": rows}

    def _summarize_generic_table(self, results_path: Path) -> dict[str, Any]:
        if not results_path.exists():
            return {"rows": []}

        rows: list[dict[str, Any]] = []
        with results_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(dict(row))
        return {"rows": rows}

    def _read_bucket_summary(self, csv_path: Path, bucket_col: str) -> str:
        if not csv_path.exists():
            return ""

        rows: list[dict[str, Any]] = []
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    n_trades = int(float(row.get("n_trades", 0) or 0))
                except ValueError:
                    n_trades = 0
                if n_trades <= 0:
                    continue
                rows.append(row)

        if not rows:
            return ""

        rows.sort(key=lambda row: float(row.get("win_rate_pct", 0) or 0), reverse=True)
        top = rows[:3]
        parts = []
        for row in top:
            parts.append(
                f"{row.get(bucket_col)} n={row.get('n_trades')} "
                f"win={float(row.get('win_rate_pct', 0) or 0):.1f}% "
                f"pnl={float(row.get('total_pnl', 0) or 0):.2f}"
            )
        return " | ".join(parts)
