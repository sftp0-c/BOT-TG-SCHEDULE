"""Время Екатеринбурга и правило «после порога (13:00) — следующий день»."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config

WEEKDAYS_RU = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)

MAX_BAN_DURATION = timedelta(days=365 * 5)
_DURATION_PART_RE = re.compile(
    r"(?P<amount>\d+)\s*(?P<unit>"
    r"недел(?:я|и|ь)|нед|weeks?|w|н|"
    r"дней|дня|день|дн|days?|d|д|"
    r"часов|часа|час|hours?|h|ч|"
    r"минут(?:а|ы)?|мин|minutes?|m|м|"
    r"секунд(?:а|ы)?|сек|seconds?|s|с)\s*",
    re.IGNORECASE,
)


def tz() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def now() -> datetime:
    return datetime.now(tz())


def today() -> date:
    return now().date()


@dataclass(frozen=True)
class TargetDay:
    day: date
    label: str  # "сегодня" | "завтра" | "неделя" | конкретная дата


def resolve_day(kind: str = "auto", *, current_dt: datetime | None = None) -> TargetDay:
    """kind: auto | today | tomorrow | day_after | week."""
    current = current_dt if current_dt is not None else now()
    base = current.date()

    if kind == "today":
        return TargetDay(base, "сегодня")
    if kind == "tomorrow":
        return TargetDay(base + timedelta(days=1), "завтра")
    if kind == "day_after":
        return TargetDay(base + timedelta(days=2), "послезавтра")
    if kind == "week":
        return TargetDay(base, "неделя")

    # auto: после порога (по умолчанию 13:00) — следующий день
    cutoff = config.CUTOFF_HOUR_EKB
    if current.hour >= cutoff:
        return TargetDay(base + timedelta(days=1), f"завтра (после {cutoff:02d}:00)")
    return TargetDay(base, "сегодня")


def weekday_name(day: date) -> str:
    return WEEKDAYS_RU[day.weekday()]


def next_monday(base: date | None = None) -> date:
    """Ближайший будущий понедельник (если сегодня Вс — завтрашний Пн)."""
    ref = base if base is not None else today()
    days_ahead = 7 - ref.weekday()
    return ref + timedelta(days=days_ahead)


def week_range(monday: date) -> tuple[date, date]:
    """Границы недели [понедельник, воскресенье]."""
    return monday, monday + timedelta(days=6)


def parse_duration(raw: str) -> timedelta | None:
    """Разбирает срок бана и безопасно отклоняет неверные/чрезмерные значения."""
    token = re.sub(r"\s+", "", (raw or "").strip().lower())
    if not token:
        return None

    position = 0
    total_seconds = 0
    while position < len(token):
        match = _DURATION_PART_RE.match(token, position)
        if match is None:
            return None
        amount_text = match.group("amount")
        if len(amount_text) > 9:
            return None
        amount = int(amount_text)
        if amount <= 0:
            return None

        unit = match.group("unit").lower()
        if unit.startswith(("w", "week", "н")):
            multiplier = 7 * 24 * 60 * 60
        elif unit.startswith(("d", "day", "д")):
            multiplier = 24 * 60 * 60
        elif unit.startswith(("h", "hour", "ч")):
            multiplier = 60 * 60
        elif unit.startswith(("m", "minute", "м")):
            multiplier = 60
        else:
            multiplier = 1

        total_seconds += amount * multiplier
        if total_seconds > int(MAX_BAN_DURATION.total_seconds()):
            return None
        position = match.end()

    return timedelta(seconds=total_seconds) if total_seconds else None


def format_ban_until(until_ts: float | None, *, now_ts: float | None = None) -> str:
    """Человекочитаемый срок ограничения доступа."""
    if until_ts is None:
        return "навсегда"
    current_ts = now_ts if now_ts is not None else now().timestamp()
    rem = max(0, int(until_ts - current_ts))
    until_dt = datetime.fromtimestamp(until_ts, tz=tz())
    days, rem_s = divmod(rem, 86400)
    hours, rem_s = divmod(rem_s, 3600)
    minutes, seconds = divmod(rem_s, 60)

    parts: list[str] = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if minutes:
        parts.append(f"{minutes} мин")
    if not parts:
        parts.append(f"{seconds} сек")

    return f"до {until_dt:%d.%m.%Y %H:%M} (осталось {' '.join(parts)})"
