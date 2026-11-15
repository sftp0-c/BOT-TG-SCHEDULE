# -*- coding: utf-8 -*-
"""Проверка рендера на образцах: день, выходной, неделя, группы с (П)."""
import shutil
from datetime import date
from pathlib import Path

from schedule_render import build_images

SRC = Path(r"C:\BOT-TG-SCHEDULE\samples\schedules")
OUT = Path(r"C:\BOT-TG-SCHEDULE\analysis\out")
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

CASES = [
    ("26-31_adc562d352096b24.pdf", "26-28", date(2026, 10, 21), False, "среда обычной группы"),
    ("26-31_adc562d352096b24.pdf", "26-29(П)", date(2026, 10, 19), False, "понедельник группы с (П)"),
    ("26-31_adc562d352096b24.pdf", "26-31", date(2026, 10, 24), False, "суббота — страницы нет"),
    ("26-27_b2db108310b4fe32.pdf", "26-27", date(2026, 10, 23), False, "пятница, крайняя правая"),
    ("24-25_bd23aa0aff67f963.pdf", "24-25", date(2026, 10, 20), False, "широкий лист 1681 pt"),
    ("26-22_251bf070347c9439.pdf", "25-32", date(2026, 10, 20), True, "неделя целиком"),
]

for pdf_name, group, target, week, note in CASES:
    data = (SRC / pdf_name).read_bytes()
    result = build_images(data, target, OUT, group=group, week=week)
    images = ", ".join(f"{p.path.name}" for p in result.images) or "-"
    print(f"[{note}] {group} {pdf_name}")
    print(f"   ok={result.ok} date_ok={result.date_ok} not_cut_off={result.not_cut_off} "
          f"картинок={len(result.images)}: {images}")
    for n in result.notes:
        print("   note:", n)
