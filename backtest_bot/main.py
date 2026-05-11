from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from .bot import BacktestTelegramBot
from .config import load_settings

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "telegram_bot.log"

_LOG_DIR.mkdir(exist_ok=True)

_fmt = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(_fmt)

logging.basicConfig(
    level=logging.DEBUG,
    handlers=[_console_handler, _file_handler],
)


_PID_FILE = _LOG_DIR / "bot.pid"


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False

    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle == 0:
        return False
    kernel32.CloseHandle(handle)
    return True


def _terminate_pid(pid: int) -> None:
    if pid <= 0:
        return

    if sys.platform != "win32":
        import signal
        os.kill(pid, signal.SIGTERM)
        return

    import ctypes

    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if handle == 0:
        return
    try:
        kernel32.TerminateProcess(handle, 0)
    finally:
        kernel32.CloseHandle(handle)


def _check_and_write_pid() -> None:
    """Ensure only one bot instance is running. Kill stale instances."""
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            # Check if old process is still alive
            if not _pid_exists(old_pid):
                raise ProcessLookupError
            # Still alive — kill it
            logging.getLogger(__name__).warning(
                "检测到旧 bot 进程 (PID %d)，正在终止...", old_pid
            )
            _terminate_pid(old_pid)
            import time
            time.sleep(1)
        except (ValueError, ProcessLookupError, PermissionError):
            pass  # old pid invalid or process already dead

    _PID_FILE.write_text(str(os.getpid()))


def _remove_pid() -> None:
    try:
        if _PID_FILE.exists() and _PID_FILE.read_text().strip() == str(os.getpid()):
            _PID_FILE.unlink()
    except OSError:
        pass


async def async_main() -> None:
    _check_and_write_pid()

    settings = load_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，请先配置 backtest_bot/.env")

    bot = BacktestTelegramBot(settings)
    await bot.start()

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await bot.stop()
        _remove_pid()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
