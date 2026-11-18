"""Разбор текста команды и аргументов."""
from __future__ import annotations

import re
from datetime import date

from clock import TargetDay, resolve_day, today

# Поддерживаем /расписание, !расписание (включая /расписание@bot_name в группах), регистр не важен.
CMD_RE = re.compile(
    r"^[!/](?:расписание|raspisanie|schedule)(?:@[a-zA-Z0-9_]+)?\b\s*(.*)$",
    re.IGNORECASE,
)
# Конкретная дата: 21.10 или 21.10.2026.
DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?$")


class ScheduleArgumentError(ValueError):
    """Команда расписания распознана, но её аргумент некорректен."""


def parse_argument(arg_raw: str) -> TargetDay:
    """Разбирает аргумент команды (пусто, сегодня, завтра, неделя, DD.MM)."""
    arg = (arg_raw or "").strip().lower()

    if not arg:
        return resolve_day("auto")
    if arg in {"завтра", "tomorrow", "зв"}:
        return resolve_day("tomorrow")
    if arg in {"послезавтра", "пз"}:
        return resolve_day("day_after")
    if arg in {"сегодня", "today", "сг"}:
        return resolve_day("today")
    if arg in {"неделя", "на неделю", "week", "все", "всё"}:
        return resolve_day("week")

    date_match = DATE_RE.match(arg)
    if date_match:
        day_number, month_number, year_part = (
            int(date_match.group(1)),
            int(date_match.group(2)),
            date_match.group(3),
        )
        year = today().year if year_part is None else int(year_part)
        if year < 100:
            year += 2000
        try:
            explicit_day = date(year, month_number, day_number)
        except ValueError as exc:
            raise ScheduleArgumentError("Такой даты не существует.") from exc
        return TargetDay(explicit_day, f"{explicit_day:%d.%m.%Y}")

    raise ScheduleArgumentError(
        "Не понял аргумент. Используйте: сегодня, завтра, послезавтра, неделя или дату DD.MM."
    )


def parse_command(text: str, *, allow_bare: bool = False) -> TargetDay | None:
    """Возвращает целевой день или None, если это не наша команда.

    При ``allow_bare=True`` (личные сообщения) понимает также короткие фразы
    без префикса: ``сегодня``, ``завтра``, ``неделя``, ``21.10``.
    """
    if not text:
        return None
    stripped = text.strip()
    match = CMD_RE.match(stripped)
    if match:
        return parse_argument(match.group(1) or "")

    if allow_bare:
        lower = stripped.lower()
        if lower in {
            "расписание",
            "сегодня",
            "завтра",
            "послезавтра",
            "неделя",
            "на неделю",
            "все",
            "всё",
            "today",
            "tomorrow",
            "week",
        } or DATE_RE.match(lower):
            arg = "" if lower == "расписание" else lower
            return parse_argument(arg)

    return None
