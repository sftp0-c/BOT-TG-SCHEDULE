# -*- coding: utf-8 -*-
"""Поиск колонки «25-28» на конкретных страницах всех доступных PDF."""
import re
import sys
import fitz
from pathlib import Path

GROUP_RE = re.compile(r"^\d{2}-\d{2}[А-Яа-яЁёП(]?[П)']*$")
TARGET = "25-28"

paths = list(Path(r"C:\BOT-TG-SCHEDULE\samples\schedules").glob("*.pdf"))
extra = list(Path(r"C:\BOT-LPC").glob("**/*.pdf")) if (sys.argv[1:] == ["all"]) else []
for path in paths + extra:
    title_hit = ""
    try:
        doc = fitz.open(path)
    except Exception as exc:  # noqa: BLE001
        print(f"{path.name}: не открыть ({type(exc).__name__})")
        continue
    pages_with_group = []
    for pno, page in enumerate(doc, 1):
        words = [w[4] for w in page.get_text("words")]
        cleaned = {w.strip("(),.:;«» \"'") for w in words}
        if TARGET in cleaned:
            pages_with_group.append(pno)
        if pno == 1:
            top = " ".join(sorted(set(w for w in words if GROUP_RE.match(w.strip("(),.:;")))))
            title_hit = top
    if pages_with_group:
        print(f"{path.name}: группа {TARGET} на страницах {pages_with_group}")
    else:
        first = doc[0].get_text("text", clip=fitz.Rect(0, 0, doc[0].rect.x1, 70))
        m = re.search(r"День[^0-9]*([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{2,4})", first)
        when = m.group(1) if m else "??"
        print(f"{path.name}: нет {TARGET}; группы стр.1: {title_hit[:90]}; неделя от {when}")
    doc.close()
