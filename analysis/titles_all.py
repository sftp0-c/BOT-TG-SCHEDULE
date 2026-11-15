"""Заголовок страницы = один день. Проверяем на всех четырёх файлах."""
import re
import fitz
from pathlib import Path

for pdf in sorted(Path(r"C:\BOT-TG-SCHEDULE\samples\schedules").glob("*.pdf")):
    doc = fitz.open(pdf)
    titles = []
    for pno, page in enumerate(doc, 1):
        top = " ".join(page.get_text("text", clip=fitz.Rect(0, 0, page.rect.x1, 70)).split())
        m = re.search(r"День\s*-?\s*([^,\n]+),\s*(\d{1,2}\.\d{1,2}\.\d{2,4})", top)
        titles.append(m.groups() if m else None)
    print(pdf.name, "→", titles[:3], "…", titles[-1:])
    doc.close()
