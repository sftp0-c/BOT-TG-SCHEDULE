"""Время Екатеринбурга и правило «после 13:00 — следующий день»."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config

TZ = ZoneInfo(config.TIMEZONE)
WEEKDAYS_RU = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


@dataclass(frozen=True)
class TargetDay:
    day: date
    label: str  # "сегодня" | "завтра" | конкретная дата


def resolve_day(kind: str = "auto") -> TargetDay:
    """kind: auto | today | tomorrow | week."""
    current = now()
    base = current.date()

    if kind == "today":
        return TargetDay(base, "сегодня")
    if kind == "tomorrow":
        return TargetDay(base + timedelta(days=1), "завтра")
    if kind == "week":
        # Неделя как отдельный режим; конкретный диапазон согласуем.
        return TargetDay(base, "неделя")

    # auto: после порога — следующий день
    if current.hour >= config.CUTOFF_HOUR_EKB:
        return TargetDay(base + timedelta(days=1), "завтра (после 13:00 по Екб)")
    return TargetDay(base, "сегодня")


def weekday_name(day: date) -> str:
    return WEEKDAYS_RU[day.weekday()]
