# -*- coding: utf-8 -*-
"""Где спрятаны заголовки «День - …» в файле 25-28_00e8da94…?"""
import fitz
from pathlib import Path

PDF = Path(r"C:\BOT-LPC\data\schedules\25-28_00e8da94d0354de8.pdf")
doc = fitz.open(PDF)
for pno, page in enumerate(doc, 1):
    words = [tuple(w) for w in page.get_text("words")]
    hits = []
    for i, w in enumerate(words):
        if w[4] == "День":
            chunk = " ".join(x[4] for x in words[i:i + 6])
            rect = fitz.Rect(w[:4])
            hits.append(f"y={rect.y0:.0f} «{chunk}»")
    print(f"стр.{pno}: {len(hits)} заголовков; " + "; ".join(hits[:4]) if hits
          else f"стр.{pno}: заголовков нет")
doc.close()
