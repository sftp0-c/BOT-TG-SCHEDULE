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

_DURATION_PART_RE = re.compile(
    r"(\d+)\s*(w|н|нед|недел[яьи]?|d|д|дн|дней|дня|день|h|ч|час(?:а|ов)?|m|м|мин(?:ут[аы]?)?|s|с|сек(?:унд[аы]?)?)",
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
    """Разбирает строку длительности вроде '30m', '2h', '7d', '1w', '1d12h'.

    Если строка не является длительностью (например причина бана), возвращает None.
    """
    token = (raw or "").strip()
    if not token:
        return None
    matches = list(_DURATION_PART_RE.finditer(token))
    if not matches:
        return None
    # Вся строка должна состоять только из валидных частей длительности (без лишнего текста)
    joined = "".join(m.group(0) for m in matches)
    if re.sub(r"\s+", "", joined) != re.sub(r"\s+", "", token):
        return None

    total = timedelta()
    for m in matches:
        amount = int(m.group(1))
        if amount <= 0:
            return None
        unit = m.group(2).lower()
        if unit.startswith(("w", "н")):
            total += timedelta(weeks=amount)
        elif unit.startswith(("d", "д")):
            total += timedelta(days=amount)
        elif unit.startswith(("h", "ч")):
            total += timedelta(hours=amount)
        elif unit.startswith(("m", "м")):
            total += timedelta(minutes=amount)
        elif unit.startswith(("s", "с")):
            total += timedelta(seconds=amount)
    return total if total.total_seconds() > 0 else None


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
