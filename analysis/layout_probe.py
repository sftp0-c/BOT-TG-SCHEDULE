# -*- coding: utf-8 -*-
"""Верх и левый край страницы 1: понять структуру колонок и границы."""
import fitz
from pathlib import Path

PDF = Path(r"C:\BOT-TG-SCHEDULE\samples\schedules\26-31_adc562d352096b24.pdf")
doc = fitz.open(PDF)
page = doc[0]
words = [(w[4], fitz.Rect(w[:4])) for w in page.get_text("words")]
print("== СЛОВА ВВЕРХУ (y0 < 85) по (y, x):")
for text, rect in sorted(words, key=lambda wr: (wr[1].y0, wr[1].x0))[:80]:
    if rect.y0 < 85:
        print(f"   y{rect.y0:7.1f} x{rect.x0:7.1f}…{rect.x1:7.1f}  {text!r}")

print("\n== ЛЕВЫЙ КРАЙ (x1 < 160), весь лист, по y:")
for text, rect in sorted([wr for wr in words if wr[1].x1 < 160], key=lambda wr: wr[1].y0)[:60]:
    print(f"   y{rect.y0:7.1f} x{rect.x0:7.1f}…{rect.x1:7.1f}  {text!r}")

print("\n== DRAWINGS (линии/рамки), первые 30:")
drawings = page.get_drawings()
print("   всего примитивов:", len(drawings))
count = 0
for d in drawings:
    rl = d.get("rect")
    if rl and rl.width > 100 and rl.height > 3:
        print(f"   {(d.get('type'))} rect=({rl.x0:7.1f},{rl.y0:7.1f},{rl.x1:7.1f},{rl.y1:7.1f})")
        count += 1
        if count >= 30:
            break
doc.close()
