# -*- coding: utf-8 -*-
"""Ключевой анализ: одна страница = один день.

Гипотеза по распечатке деталей:
  1. Верхняя строка страницы: «День - Понедельник, 19.10.2026» (полная дата!).
  2. Под шапкой строка с кодами групп (№, 26-28, 26-29, 26-29(П), …).
  3. Под каждой группой — своя таблица: «Предмет, вид занятия, | преподаватель | Ауд.».
  4. Шаг блоков группы постоянный (255 pt на узком листе), таблица огранинена
     большим заполненным прямоугольником (fill), а не «на глаз».

Вывод: БОТ должен выбирать страницу по ДАТЕ из заголовка, а не искать строку
дня по тексту. Обрезать надо: x = блок группы, y = от низа строки заголовка
группы до нижней границы большой таблицы (fill-прямоугольника).
"""
import re
import fitz
from pathlib import Path

PDF = Path(r"C:\BOT-TG-SCHEDULE\samples\schedules\26-31_adc562d352096b24.pdf")
doc = fitz.open(PDF)
page = doc[0]

top = page.get_text("text", clip=fitz.Rect(0, 0, page.rect.x1, 70))
print("ЗАГОЛОВОК:", " ".join(top.split()))

# Группа: подчеркнуть границы блока
words = [(w[4], fitz.Rect(w[:4])) for w in page.get_text("words")]


def in_band(y0, y1):
    return [(t, r) for t, r in words if y0 <= r.y0 <= y1]


predmety = sorted([r for t, r in in_band(60, 74) if t.startswith("Предмет")], key=lambda r: r.x0)
auds = sorted([r for t, r in in_band(60, 74) if t == "Ауд."], key=lambda r: r.x0)
rooms = sorted([r for t, r in in_band(60, 74) if t == "преподаватель"], key=lambda r: r.x0)
numbercell = [r for t, r in in_band(38, 62) if t == "№"]
print("№ колонка:", numbercell[:1])
print("Предмет x0:", [round(r.x0, 1) for r in predmety])
print("Ауд.   x1:", [round(r.x1, 1) for r in auds])

fills = sorted((fitz.Rect(d["rect"]) for d in page.get_drawings() if d.get("rect")),
               key=lambda r: -r.width * r.height)
print("Самый большой fill-rect:", fills[0], " площадь:", fills[0].width * fills[0].height)

L = (auds[1].x1 + predmety[2].x0) / 2   # начало блока «26-29(П)»: между «Ауд.» блока 2 и «Предмет.» блока 3
right = (auds[2].x1 + predmety[3].x0) / 2
top_y = [r for t, r in words if t == "26-29(П)"][0].y1
print("Пример блок 26-29(П): x", round(L, 1), "→", round(right, 1), " y от", round(top_y, 1), "до", round(fills[0].y1, 1))

clip = fitz.Rect(L, top_y, right, fills[0].y1)
out = Path(r"C:\BOT-TG-SCHEDULE\analysis\out\probe_col.png")
out.parent.mkdir(parents=True, exist_ok=True)
page.get_pixmap(dpi=200, clip=clip).save(out)
print("Проверочная вырезка сохранена:", out)
doc.close()
