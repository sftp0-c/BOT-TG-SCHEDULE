"""Настройки из окружения. Ничего секретного не храним в коде.

Значения перечитываются по ``reload()``: правишь ``.env`` — бот подхватит их
со следующей командой, перезапуск не нужен (кроме TG_BOT_TOKEN: сессия
создаётся при старте).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

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


def _get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return default if value is None else value.strip()


def _load() -> None:
    load_dotenv(override=True)
    globals_ = globals()
    globals_["TG_BOT_TOKEN"] = _get("TG_BOT_TOKEN")
    globals_["TG_PROXY"] = _get("TG_PROXY")
    globals_["SCHEDULE_PAGE_URL"] = _get(
        "SCHEDULE_PAGE_URL", "https://collegelan.ru/studentam/raspisanie-zanyatiy.php")
    # Раздел, в меню которого есть ссылка «Расписание занятий»: по нему бот
    # находит страницу расписаний заново, если php переименовали.
    globals_["SCHEDULE_MENU_URL"] = _get("SCHEDULE_MENU_URL", "https://collegelan.ru/studentam/")
    globals_["SCHEDULE_DIRECT_URL"] = _get("SCHEDULE_DIRECT_URL")
    globals_["SCHEDULE_PDF_PATTERN"] = _get("SCHEDULE_PDF_PATTERN", "25-27-25-31")
    globals_["SCHEDULE_LOCAL_PDF"] = _get("SCHEDULE_LOCAL_PDF")
    globals_["TARGET_GROUP"] = _get("TARGET_GROUP", "25-28")
    try:
        globals_["CUTOFF_HOUR_EKB"] = int(_get("CUTOFF_HOUR_EKB", "13") or "13")
    except ValueError:
        globals_["CUTOFF_HOUR_EKB"] = 13


def reload() -> None:
    """Перечитать .env: вызывается перед обработкой каждой команды."""
    _load()


_load()


def validate() -> list[str]:
    errors = []
    if not TG_BOT_TOKEN:
        errors.append("TG_BOT_TOKEN не задан (см. README)")
    if TG_PROXY and not TG_PROXY.lower().startswith(
            ("http://", "https://", "socks4://", "socks5://", "socks5h://")):
        errors.append(
            "TG_PROXY должен начинаться с http://, https://, socks4://, "
            "socks5:// или socks5h:// (см. .env)")
    if not SCHEDULE_PDF_PATTERN:
        errors.append("SCHEDULE_PDF_PATTERN пустой")
    return errors
