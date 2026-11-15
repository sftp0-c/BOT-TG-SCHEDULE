# -*- coding: utf-8 -*-
"""Быстрый анализ образцов расписаний: страницы, группы, якоря (дни/даты).

Ничего не меняет; только печатает, что видно в PDF.
"""
import re
import fitz
from pathlib import Path

SRC = Path(r"C:\BOT-TG-SCHEDULE\samples\schedules")
GROUP_RE = re.compile(r"^\d{2}-\d{2}[А-Яа-яЁёП]?$", re.I)
DAY_RE = re.compile(r"^(?:понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|пн|вт|ср|чт|пт|сб|вс)\.?$',-.|", re.I)


def scan():
    files = {p.stat().st_size: p for p in SRC.glob("*.pdf")}
    print("Файлов на диске:", len(list(SRC.glob('*.pdf'))), "уникальных по размеру:", len(files))
    for size in sorted(files):
        doc = fitz.open(files[size])
        groups = {}
        for page in doc:
            for word in page.get_text("words"):
                text = word[4]
                clean = text.strip("().,:;«» '\"")
                if GROUP_RE.match(clean):
                    groups.setdefault(clean, (page.number + 1, fitz.Rect(word[:4])))
        print(f"\n== {files[size].name}")
        print("   страниц:", len(doc), " размер стр.:", doc[0].rect)
        text_first = doc[0].get_text()[:200].replace("\n", " | ")
        print("   начало текста:", text_first[:180])
        print("   колонок-групп:", len(groups), sorted(groups))
        # Программное усложнение запрещено; вывод сырой.
        doc.close()


if __name__ == "__main__":
    scan()
