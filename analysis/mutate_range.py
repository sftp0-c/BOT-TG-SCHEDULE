# -*- coding: utf-8 -*-
"""Мутационная проверка: ломаем разбор диапазона — выбор файла должен стать иным."""
import schedule_source as ss

real = ss._range_covers
ss._range_covers = lambda url, group: False  # нарочно ломаем правило

PAGE = "https://collegelan.ru/studentam/"
REVERSED = '<a href="Расписание/25-31-25-27 (изм).pdf">x</a>'
out = ss.pick_schedule_url_from_html(REVERSED, PAGE, ("25-27-25-31",), "25-28")
print("перевёрнутый диапазон при сломанной _range_covers ->", out)
