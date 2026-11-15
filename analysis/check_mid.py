# -*- coding: utf-8 -*-
"""Один кейс: средний блок 26-29(П) с новой полосой «№», чтобы имя файла не пересекалось."""
from datetime import date
from pathlib import Path

from schedule_render import build_images

SRC = Path(r"C:\BOT-TG-SCHEDULE\samples\schedules\26-31_adc562d352096b24.pdf")
OUT = Path(r"C:\BOT-TG-SCHEDULE\analysis\out")
result = build_images(SRC.read_bytes(), date(2026, 10, 19), OUT, group="26-29(П)")
for image in result.images:
    renamed = image.path.with_name("mid_2629p_mon.png")
    image.path.rename(renamed)
    print("картинка:", renamed)
print("ok =", result.ok, "date_ok =", result.date_ok, "not_cut_off =", result.not_cut_off)
