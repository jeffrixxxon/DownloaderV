"""Конфигурация бота: читается из переменных окружения / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _ids(name: str) -> set[int]:
    raw = os.getenv(name, "") or ""
    out: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            try:
                out.add(int(chunk))
            except ValueError:
                pass
    return out


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    max_upload_mb: int = _int("MAX_UPLOAD_MB", 49)
    max_concurrent: int = _int("MAX_CONCURRENT_DOWNLOADS", 2)
    user_cooldown: int = _int("USER_COOLDOWN_SEC", 5)
    max_user_queue: int = _int("MAX_USER_QUEUE", 2)
    download_dir: Path = Path(os.getenv("DOWNLOAD_DIR", "/tmp/vdlbot"))
    cookies_file: str = os.getenv("COOKIES_FILE", "") or ""
    proxy: str = os.getenv("PROXY", "") or ""
    universal_mode: bool = (os.getenv("UNIVERSAL_MODE", "1") or "").strip().lower() in {"1", "true", "yes", "on"}
    donate_enabled: bool = (os.getenv("DONATE_ENABLED", "1") or "").strip().lower() in {"1", "true", "yes", "on"}
    donate_every: int = _int("DONATE_EVERY", 10)
    db_path: Path = Path(os.getenv("DB_PATH", "/data/vdlbot.sqlite3"))
    force_ipv4: bool = (os.getenv("FORCE_IPV4", "") or "").strip().lower() in {"1", "true", "yes", "on"}
    local_bot_api: str = os.getenv("LOCAL_BOT_API", "") or ""
    allowed_users: set[int] = field(default_factory=lambda: _ids("ALLOWED_USERS"))

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def validate(self) -> None:
        if not self.bot_token:
            raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и впишите токен.")
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.cookies_file and not Path(self.cookies_file).exists():
            raise RuntimeError(f"COOKIES_FILE={self.cookies_file} не найден")


config = Config()
