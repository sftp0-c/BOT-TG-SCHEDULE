"""Разбор текста команды."""
from __future__ import annotations

import re
from datetime import date

from clock import resolve_day, TargetDay, today

# Поддерживаем /расписание и !расписание, регистр не важен.
CMD_RE = re.compile(r"^[!/](?:расписание|raspisanie|schedule)\b\s*(.*)$", re.IGNORECASE)
# Конкретная дата: 21.10 или 21.10.2026.
DATE_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?$")


def parse_command(text: str) -> TargetDay | None:
    """Возвращает целевой день или None, если это не наша команда."""
    if not text:
        return None
    match = CMD_RE.match(text.strip())
    if not match:
        return None
    arg = (match.group(1) or "").strip().lower()

    if not arg:
        return resolve_day("auto")
    if arg in {"завтра", "tomorrow"}:
        return resolve_day("tomorrow")
    if arg in {"сегодня", "today"}:
        return resolve_day("today")
    if arg in {"неделя", "week", "все", "всё"}:
        return resolve_day("week")

    # Конкретная дата («!расписание 21.10») — нужный день из любого файла:
    # год по умолчанию текущий, две цифры разворачиваем в 2000+.
    date_match = DATE_RE.match(arg)
    if date_match:
        day_number, month_number, year_part = (
            int(date_match.group(1)), int(date_match.group(2)), date_match.group(3))
        year = today().year if year_part is None else int(year_part)
        if year < 100:
            year += 2000
        try:
            explicit_day = date(year, month_number, day_number)
        except ValueError:
            return resolve_day("auto")  # «32.13» — как раньше, ведём себя тихо
        return TargetDay(explicit_day, f"{explicit_day:%d.%m.%Y}")

    # Неизвестный аргумент — пока ведём как auto; можно вернуть подсказку.
    return resolve_day("auto")
