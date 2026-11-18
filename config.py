"""Настройки из окружения и проектного ``.env``.

Файл перечитывается по ``reload()`` перед обработкой событий. Переменные процесса
имеют приоритет над ``.env``; сам файл читается напрямую и не изменяет
``os.environ``, поэтому удалённая строка действительно сбрасывает настройку.
"""
from __future__ import annotations

import math
import os
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

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

_MAX_COOLDOWN_SEC = 3600.0
_CONFIG_KEYS = {
    "TG_BOT_TOKEN",
    "TG_PROXY",
    "SCHEDULE_PAGE_URL",
    "SCHEDULE_MENU_URL",
    "SCHEDULE_DIRECT_URL",
    "SCHEDULE_PDF_PATTERN",
    "SCHEDULE_LOCAL_PDF",
    "TARGET_GROUP",
    "CUTOFF_HOUR_EKB",
    "TIMEZONE",
    "ADMIN_IDS",
    "WEEKLY_CHAT_IDS",
    "WEEKLY_SEND_WEEKDAY",
    "WEEKLY_SEND_HOUR",
    "WEEKLY_SEND_MINUTE",
    "USER_COOLDOWN_SEC",
    "CHAT_COOLDOWN_SEC",
    "SAME_TARGET_COOLDOWN_SEC",
}


def _values() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.is_file():
        for key, value in dotenv_values(ENV_PATH).items():
            if key in _CONFIG_KEYS and value is not None:
                values[key] = value
    for key in _CONFIG_KEYS:
        if key in os.environ:
            values[key] = os.environ[key]
    return values


def _get(values: Mapping[str, str], name: str, default: str = "") -> str:
    value = values.get(name)
    return default if value is None else value.strip()


def _parse_int_list(raw: str, *, allow_negative: bool = False) -> tuple[int, ...]:
    items: list[int] = []
    for token in re.split(r"[,;\s]+", raw.strip()):
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        valid = value != 0 if allow_negative else value > 0
        if valid and value not in items:
            items.append(value)
    return tuple(items)


def _parse_int(
    raw: str,
    default: int,
    *,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    try:
        value = int(raw)
    except ValueError:
        return default
    if min_val is not None and value < min_val:
        return default
    if max_val is not None and value > max_val:
        return default
    return value


def _parse_float(
    raw: str,
    default: float,
    *,
    min_val: float = 0.0,
    max_val: float = _MAX_COOLDOWN_SEC,
) -> float:
    try:
        value = float(raw)
    except ValueError:
        return default
    if not math.isfinite(value) or not min_val <= value <= max_val:
        return default
    return value


def _load() -> None:
    values = _values()
    loaded = {
        "TG_BOT_TOKEN": _get(values, "TG_BOT_TOKEN"),
        "TG_PROXY": _get(values, "TG_PROXY"),
        "SCHEDULE_PAGE_URL": _get(
            values,
            "SCHEDULE_PAGE_URL",
            "https://collegelan.ru/studentam/raspisanie-zanyatiy.php",
        ),
        "SCHEDULE_MENU_URL": _get(
            values, "SCHEDULE_MENU_URL", "https://collegelan.ru/studentam/"
        ),
        "SCHEDULE_DIRECT_URL": _get(values, "SCHEDULE_DIRECT_URL"),
        "SCHEDULE_PDF_PATTERN": _get(
            values, "SCHEDULE_PDF_PATTERN", "25-27-25-31"
        ),
        "SCHEDULE_LOCAL_PDF": _get(values, "SCHEDULE_LOCAL_PDF"),
        "TARGET_GROUP": _get(values, "TARGET_GROUP", "25-28"),
        "TIMEZONE": _get(values, "TIMEZONE", "Asia/Yekaterinburg")
        or "Asia/Yekaterinburg",
        "CUTOFF_HOUR_EKB": _parse_int(
            _get(values, "CUTOFF_HOUR_EKB", "13"), 13, min_val=0, max_val=23
        ),
        "ADMIN_IDS": _parse_int_list(_get(values, "ADMIN_IDS")),
        "WEEKLY_CHAT_IDS": _parse_int_list(
            _get(values, "WEEKLY_CHAT_IDS"),
            allow_negative=True,
        ),
        "WEEKLY_SEND_WEEKDAY": _parse_int(
            _get(values, "WEEKLY_SEND_WEEKDAY", "6"),
            6,
            min_val=0,
            max_val=6,
        ),
        "WEEKLY_SEND_HOUR": _parse_int(
            _get(values, "WEEKLY_SEND_HOUR", "18"),
            18,
            min_val=0,
            max_val=23,
        ),
        "WEEKLY_SEND_MINUTE": _parse_int(
            _get(values, "WEEKLY_SEND_MINUTE", "0"),
            0,
            min_val=0,
            max_val=59,
        ),
        "USER_COOLDOWN_SEC": _parse_float(
            _get(values, "USER_COOLDOWN_SEC", "20"), 20.0
        ),
        "CHAT_COOLDOWN_SEC": _parse_float(
            _get(values, "CHAT_COOLDOWN_SEC", "8"), 8.0
        ),
        "SAME_TARGET_COOLDOWN_SEC": _parse_float(
            _get(values, "SAME_TARGET_COOLDOWN_SEC", "30"), 30.0
        ),
    }
    globals().update(loaded)


def reload() -> None:
    """Перечитать только проектный ``.env`` и явные переменные процесса."""
    _load()


def is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def _is_http_url(raw: str) -> bool:
    if not raw:
        return True
    parsed = urlsplit(raw)
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def validate() -> list[str]:
    errors: list[str] = []
    if not TG_BOT_TOKEN:
        errors.append("TG_BOT_TOKEN не задан в .env (получите токен у @BotFather)")
    if TG_PROXY and not TG_PROXY.lower().startswith(
        ("http://", "https://", "socks4://", "socks5://", "socks5h://")
    ):
        errors.append(
            "TG_PROXY должен начинаться с http://, https://, socks4://, "
            "socks5:// или socks5h:// (см. .env)"
        )
    for page_url in (part.strip() for part in SCHEDULE_PAGE_URL.split(",")):
        if page_url and not _is_http_url(page_url):
            errors.append(f"Некорректный URL страницы расписаний: {page_url}")
    for name, url in (
        ("SCHEDULE_MENU_URL", SCHEDULE_MENU_URL),
        ("SCHEDULE_DIRECT_URL", SCHEDULE_DIRECT_URL),
    ):
        if not _is_http_url(url):
            errors.append(f"{name} должен быть абсолютным http(s)-URL")
    try:
        ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        errors.append(f"Неизвестный часовой пояс TIMEZONE={TIMEZONE!r}")
    if not SCHEDULE_PDF_PATTERN and not TARGET_GROUP:
        errors.append("Задайте TARGET_GROUP или SCHEDULE_PDF_PATTERN в .env")
    return errors


_load()
