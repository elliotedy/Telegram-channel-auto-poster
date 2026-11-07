"""Environment configuration."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


def _parse_admin_ids(raw: str) -> frozenset[int]:
    admin_ids: set[int] = set()
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            admin_ids.add(int(chunk))
        except ValueError as exc:
            raise RuntimeError(
                f"ADMIN_IDS contains an invalid Telegram ID: {chunk!r}"
            ) from exc
    if not admin_ids:
        raise RuntimeError(
            "ADMIN_IDS is empty. Set it to your numeric Telegram user ID."
        )
    return frozenset(admin_ids)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(value, maximum))


def _parse_media_dirs(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    parts = [part.strip().strip('"').strip("'") for part in re.split(r"[;\n,]+", raw)]
    return tuple(part for part in parts if part)


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: frozenset[int]
    default_timezone: str = "UTC"
    database_path: Path = Path("data/autoposter.db")
    log_level: str = "INFO"
    enable_self_update: bool = True
    notify_startup: bool = True
    media_dirs: tuple[str, ...] = ()
    import_interval: int = 60
    import_max_per_scan: int = 25
    import_recursive: bool = True
    import_filename_caption: bool = False
    max_upload_mb: float = 49.0

    @classmethod
    def from_env(cls) -> Config:
        token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set. Copy .env.example to .env and add the token from @BotFather."
            )

        admin_ids = _parse_admin_ids(os.getenv("ADMIN_IDS", ""))

        timezone_name = (os.getenv("TIMEZONE") or "UTC").strip()
        try:
            ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise RuntimeError(
                f"TIMEZONE={timezone_name!r} is not a valid IANA timezone (for example: Asia/Kolkata)."
            ) from exc

        raw_database_path = (os.getenv("DATABASE_PATH") or "data/autoposter.db").strip()
        database_path = Path(raw_database_path)
        if not database_path.is_absolute():
            database_path = ROOT_DIR / database_path

        log_level = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            log_level = "INFO"

        return cls(
            bot_token=token,
            admin_ids=admin_ids,
            default_timezone=timezone_name,
            database_path=database_path,
            log_level=log_level,
            enable_self_update=_env_bool("ENABLE_SELF_UPDATE", True),
            notify_startup=_env_bool("NOTIFY_STARTUP", True),
            media_dirs=_parse_media_dirs(os.getenv("MEDIA_DIRS")),
            import_interval=_env_int("IMPORT_INTERVAL", 60, 15, 86_400),
            import_max_per_scan=_env_int("IMPORT_MAX_PER_SCAN", 25, 1, 500),
            import_recursive=_env_bool("IMPORT_RECURSIVE", True),
            import_filename_caption=_env_bool("IMPORT_FILENAME_CAPTION", False),
            max_upload_mb=_env_float("MAX_UPLOAD_MB", 49.0, 1.0, 2000.0),
        )
