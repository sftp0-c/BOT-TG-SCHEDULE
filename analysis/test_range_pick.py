# -*- coding: utf-8 -*-
"""Проверка выбора файла-диапазона на синтетических списках ссылок."""
from schedule_source import pick_schedule_url_from_html

PAGE = "https://collegelan.ru/studentam/"
LISTING = (
    '<a href="Расписание/23-27-23-29.pdf">1</a> '
    '<a href="Расписание/26-20-26-27.pdf">2</a> '
    '<a href="Расписание/25-27-25-31.pdf">3</a> '
    '<a href="Расписание/25-27-25-31 (изм).pdf">4</a> '
    '<a href="Расписание/25-31-25-35.pdf">5</a>'
)
SPACE = '<a href="Расписание/25-27 - 25-31.pdf">x</a>'
REVERSED = '<a href="Расписание/25-31-25-27 (изм).pdf">x</a>'


def pick(html: str, group: str = "25-28", pattern: str = "25-27-25-31") -> str | None:
    found = pick_schedule_url_from_html(html, PAGE, (pattern,), group)
    return found.name if found else None


CASES = [
    ("обычный список", pick(LISTING), "25-27-25-31 (изм).pdf"),
    ("пробелы вокруг дефиса", pick(SPACE), "25-27 - 25-31.pdf"),
    ("перевёрнутый диапазон", pick(REVERSED), "25-31-25-27 (изм).pdf"),
    ("только свой диапазон", pick('<a href="Расписание/25-27-25-31.pdf">x</a>'), "25-27-25-31.pdf"),
    ("ничего подходящего", pick('<a href="Расписание/26-20-26-27.pdf">x</a>'), None),
]
for name, got, want in CASES:
    status = "OK" if got == want else "FAIL"
    print(status, name, "->", repr(got), "ожидалось", repr(want))
