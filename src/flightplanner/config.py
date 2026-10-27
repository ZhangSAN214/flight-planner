"""读取 .env 和环境变量。环境变量优先于 .env 文件。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Settings:
    serpapi_key: str = ""
    duffel_token: str = ""
    travelpayouts_token: str = ""
    currency: str = "GBP"
    home_airport: str = "MAN"
    serpapi_monthly_limit: int = 250
    cache_minutes: int = 30
    watch_interval_hours: float = 12
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_email_to: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    deal_feeds: list[str] = field(default_factory=list)
    data_dir: Path = PROJECT_ROOT / "data"
    force_demo: bool = False

    @property
    def demo_mode(self) -> bool:
        """没有填任何航班数据 key 时，用演示数据。"""
        return self.force_demo or not (self.serpapi_key or self.duffel_token or self.travelpayouts_token)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "flightplanner.db"


def load_settings(env_file: Path | None = None) -> Settings:
    file_values = _load_dotenv(env_file or PROJECT_ROOT / ".env")

    def get(name: str, default: str = "") -> str:
        return os.environ.get(name, file_values.get(name, default))

    feeds = [f.strip() for f in get("DEAL_FEEDS", "https://www.secretflying.com/feed/").split(",") if f.strip()]
    data_dir = get("DATA_DIR")
    return Settings(
        serpapi_key=get("SERPAPI_KEY"),
        duffel_token=get("DUFFEL_TOKEN"),
        travelpayouts_token=get("TRAVELPAYOUTS_TOKEN"),
        currency=get("CURRENCY", "GBP").upper(),
        home_airport=get("HOME_AIRPORT", "MAN").upper(),
        serpapi_monthly_limit=int(get("SERPAPI_MONTHLY_LIMIT", "250") or 250),
        cache_minutes=int(get("CACHE_MINUTES", "30") or 30),
        watch_interval_hours=float(get("WATCH_INTERVAL_HOURS", "12") or 12),
        smtp_host=get("SMTP_HOST"),
        smtp_port=int(get("SMTP_PORT", "587") or 587),
        smtp_user=get("SMTP_USER"),
        smtp_password=get("SMTP_PASSWORD"),
        alert_email_to=get("ALERT_EMAIL_TO"),
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=get("TELEGRAM_CHAT_ID"),
        deal_feeds=feeds,
        data_dir=Path(data_dir) if data_dir else PROJECT_ROOT / "data",
        force_demo=get("DEMO_MODE", "").lower() in {"1", "true", "yes"},
    )
