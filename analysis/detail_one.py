# -*- coding: utf-8 -*-
"""Детальный разбор одного файла: где группы, где дни, где даты, есть ли «Неделя»."""
import re
import fitz
from pathlib import Path

PDF = Path(r"C:\BOT-TG-SCHEDULE\samples\schedules\26-31_adc562d352096b24.pdf")
DAY_RE = re.compile(r"^(?:понедельник|вторник|среда|четверг|пятница|суббота|воскресенье|пн|вт|ср|чт|пт|сб|вс)[,.]?$", re.I)
DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{2,4})?$")
GROUP_RE = re.compile(r"^\d{2}-\d{2}[А-Яа-яЁёП]?$", re.I)

doc = fitz.open(PDF)
for page_no, page in enumerate(doc, 1):
    words = [(w[4], fitz.Rect(w[:4])) for w in page.get_text("words")]
    print(f"\n######## Страница {page_no} (rect={page.rect})")
    title_hits = [t for t, _ in words if "неделя" in t.lower() or "с" == t.lower()]
    # Строка с неделей: соберём текст первой строки страницы
    line1 = " ".join(t for t, r in words if r.y0 < 40)[:150]
    print("  верхняя строка:", line1)

    for label, pattern in (("ДНИ", DAY_RE), ("ДАТЫ", DATE_RE), ("ГРУППЫ", GROUP_RE)):
        hits = [(t, tuple(round(v, 1) for v in r)) for t, r in words if pattern.match(t)]
        uniq = {}
        for t, r in hits:
            uniq.setdefault(t, []).append(r)
        shown = []
        for t, rects in uniq.items():
            shown.append(f"{t}×{len(rects)} @ {rects[0]}")
        print(f"  {label}: {', '.join(shown[:12])}{' …' if len(shown) > 12 else ''} (всего {len(shown)})")
doc.close()
