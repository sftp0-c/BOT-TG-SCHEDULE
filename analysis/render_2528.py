# -*- coding: utf-8 -*-
"""Рендер недели из файла-диапазона для НАСТОЯЩЕЙ группы 25-28."""
import shutil
from datetime import date
from pathlib import Path

from schedule_render import build_images

SRC = Path(r"C:\BOT-TG-SCHEDULE\samples\schedules\25-27-25-31_00e8da94d0354de8.pdf")
OUT = Path(r"C:\BOT-TG-SCHEDULE\analysis\out2528")
if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

data = SRC.read_bytes()
week = build_images(data, date(2026, 9, 28), OUT, group="25-28", week=True)
print("ok =", week.ok, "картинок =", len(week.images))
for image in week.images:
    print("  ", image.path.name, "->", image.week_label)
for note in week.notes:
    print("  note:", note)

print()
for day in (date(2026, 9, 29), date(2026, 10, 2), date(2026, 10, 4)):
    one = build_images(data, day, OUT, group="25-28")
    images = ", ".join(i.path.name for i in one.images) or "-"
    print(f"{day} ok={one.ok} date_ok={one.date_ok} cut={one.not_cut_off}: {images}")
    for note in one.notes[:2]:
        print("  note:", note)
