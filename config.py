"""Настройки из окружения (.env). Ничего секретного не храним в коде.

Значения перечитываются по ``reload()``: правишь ``.env`` — бот подхватит их
со следующей командой, перезапуск не нужен (кроме TG_BOT_TOKEN и TG_PROXY:
сессия Telegram создаётся при старте).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
STATE_PATH = BASE_DIR / "data" / "state.json"

TG_BOT_TOKEN = ""
TG_PROXY = ""
SCHEDULE_PAGE_URL = ""
SCHEDULE_MENU_URL = ""
SCHEDULE_DIRECT_URL = ""
SCHEDULE_PDF_PATTERN = ""
SCHEDULE_LOCAL_PDF = ""
TARGET_GROUP = ""
CUTOFF_HOUR_EKB = 13
TIMEZONE = "Asia/Yekaterinburg"

ADMIN_IDS: tuple[int, ...] = ()
WEEKLY_CHAT_IDS: tuple[int, ...] = ()
WEEKLY_SEND_WEEKDAY = 6
WEEKLY_SEND_HOUR = 18
WEEKLY_SEND_MINUTE = 0

USER_COOLDOWN_SEC = 20.0
CHAT_COOLDOWN_SEC = 8.0
SAME_TARGET_COOLDOWN_SEC = 30.0


def _get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _parse_int_list(raw: str) -> tuple[int, ...]:
    items: list[int] = []
    for token in re.split(r"[,;\s]+", raw.strip()):
        if not token:
            continue
        try:
            val = int(token)
        except ValueError:
            continue
        if val not in items:
            items.append(val)
    return tuple(items)


def _parse_int(raw: str, default: int, *, min_val: int | None = None, max_val: int | None = None) -> int:
    try:
        val = int(raw)
    except ValueError:
        return default
    if min_val is not None and val < min_val:
        return default
    if max_val is not None and val > max_val:
        return default
    return val


def _parse_float(raw: str, default: float, *, min_val: float = 0.0) -> float:
    try:
        val = float(raw)
    except ValueError:
        return default
    return max(min_val, val)


def _load() -> None:
    if ENV_PATH.is_file():
        load_dotenv(ENV_PATH, override=True)
    else:
        load_dotenv(override=True)
    globals_ = globals()
    globals_["TG_BOT_TOKEN"] = _get("TG_BOT_TOKEN")
    globals_["TG_PROXY"] = _get("TG_PROXY")
    globals_["SCHEDULE_PAGE_URL"] = _get(
        "SCHEDULE_PAGE_URL", "https://collegelan.ru/studentam/raspisanie-zanyatiy.php"
    )
    globals_["SCHEDULE_MENU_URL"] = _get("SCHEDULE_MENU_URL", "https://collegelan.ru/studentam/")
    globals_["SCHEDULE_DIRECT_URL"] = _get("SCHEDULE_DIRECT_URL")
    globals_["SCHEDULE_PDF_PATTERN"] = _get("SCHEDULE_PDF_PATTERN", "25-27-25-31")
    globals_["SCHEDULE_LOCAL_PDF"] = _get("SCHEDULE_LOCAL_PDF")
    globals_["TARGET_GROUP"] = _get("TARGET_GROUP", "25-28")
    globals_["TIMEZONE"] = _get("TIMEZONE", "Asia/Yekaterinburg") or "Asia/Yekaterinburg"
    globals_["CUTOFF_HOUR_EKB"] = _parse_int(_get("CUTOFF_HOUR_EKB", "13"), 13, min_val=0, max_val=23)

    globals_["ADMIN_IDS"] = _parse_int_list(_get("ADMIN_IDS", "1075591078"))
    globals_["WEEKLY_CHAT_IDS"] = _parse_int_list(_get("WEEKLY_CHAT_IDS", ""))
    globals_["WEEKLY_SEND_WEEKDAY"] = _parse_int(
        _get("WEEKLY_SEND_WEEKDAY", "6"), 6, min_val=0, max_val=6
    )
    globals_["WEEKLY_SEND_HOUR"] = _parse_int(
        _get("WEEKLY_SEND_HOUR", "18"), 18, min_val=0, max_val=23
    )
    globals_["WEEKLY_SEND_MINUTE"] = _parse_int(
        _get("WEEKLY_SEND_MINUTE", "0"), 0, min_val=0, max_val=59
    )

    globals_["USER_COOLDOWN_SEC"] = _parse_float(_get("USER_COOLDOWN_SEC", "20"), 20.0)
    globals_["CHAT_COOLDOWN_SEC"] = _parse_float(_get("CHAT_COOLDOWN_SEC", "8"), 8.0)
    globals_["SAME_TARGET_COOLDOWN_SEC"] = _parse_float(
        _get("SAME_TARGET_COOLDOWN_SEC", "30"), 30.0
    )


def reload() -> None:
    """Перечитать .env: вызывается перед обработкой каждой команды."""
    _load()


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


_load()


def validate() -> list[str]:
    errors = []
    if not TG_BOT_TOKEN:
        errors.append("TG_BOT_TOKEN не задан в .env (получите токен у @BotFather)")
    if TG_PROXY and not TG_PROXY.lower().startswith(
        ("http://", "https://", "socks4://", "socks5://", "socks5h://")
    ):
        errors.append(
            "TG_PROXY должен начинаться с http://, https://, socks4://, "
            "socks5:// или socks5h:// (см. .env)"
        )
    if not SCHEDULE_PDF_PATTERN and not TARGET_GROUP:
        errors.append("Задайте TARGET_GROUP или SCHEDULE_PDF_PATTERN в .env")
    return errors
