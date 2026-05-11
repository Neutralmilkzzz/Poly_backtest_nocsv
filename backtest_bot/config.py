from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / "backtest_bot" / ".env"
DEFAULT_USER_DATA_DIR = Path(r"C:\Users\ZHAOKAI\data")


def load_env() -> None:
    """Load environment variables from backtest_bot/.env if present."""
    try:
        from dotenv import load_dotenv

        if ENV_PATH.exists():
            load_dotenv(ENV_PATH)
    except ImportError:
        if not ENV_PATH.exists():
            return
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if "#" in value:
                value = value[:value.index("#")].strip()
            os.environ.setdefault(key, value)


@dataclass
class BotSettings:
    telegram_bot_token: str
    telegram_chat_id: str
    proxy: str | None
    rscript_command: str
    root_dir: Path
    config_path: Path
    data_dir: Path
    output_root: Path
    n_cores: int = 1


def load_settings() -> BotSettings:
    load_env()

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    proxy = (os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or "").strip() or None
    rscript_command = os.getenv("BACKTEST_RSCRIPT", "Rscript").strip() or "Rscript"

    default_config = ROOT_DIR / "strategies" / "grid_er_hurst_1200.yaml"
    if not default_config.exists():
        default_config = ROOT_DIR / "config" / "strategy.yaml"

    if DEFAULT_USER_DATA_DIR.exists():
        default_data_dir = DEFAULT_USER_DATA_DIR
    else:
        default_data_dir = ROOT_DIR / "data" / "raw"

    config_path = Path(os.getenv("BACKTEST_CONFIG", str(default_config)))
    data_dir = Path(os.getenv("BACKTEST_DATA_DIR", str(default_data_dir)))
    output_root = Path(os.getenv("BACKTEST_OUTPUT_ROOT", str(ROOT_DIR / "results" / "telegram_jobs")))
    n_cores = int(os.getenv("BACKTEST_CORES", "1"))

    return BotSettings(
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        proxy=proxy,
        rscript_command=rscript_command,
        root_dir=ROOT_DIR,
        config_path=config_path,
        data_dir=data_dir,
        output_root=output_root,
        n_cores=n_cores,
    )
