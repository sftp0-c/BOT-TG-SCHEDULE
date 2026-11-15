"""PDF расписания колледжа → картинки для Telegram.

Реальная структура файлов колледжа (проверено на образцах от 19–23.10.2026):
  * одна страница = один день; в шапке строка «День - Понедельник, 19.10.2026»;
  * под шапкой строка с кодами групп: №, 26-28, 26-29, 26-29(П), 26-30, 26-31;
  * таблица ограничена большим заполненным прямоугольником (fill), её нижняя
    граница — конец таблицы, а не «на глаз»;
  * у каждого блока группы три подколонки: «Предмет, вид занятия,»,
    «преподаватель», «Ауд.»; шаг блоков постоянный внутри файла.

Из-за этого вырезка делается так:
  1. находим страницу с датой нужного дня в заголовке (date_ok из шапки);
  2. границы блока группы берём по шапке таблицы (№ | Предмет | Ауд.), а не
     фиксированной высотой — снизу границей служит сама таблица => «не обрезан»;
  3. картинка собирается из двух частей: шапка дня («День - …») сверху и
     № пары + колонка группы снизу, склейка через Pillow.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import fitz  # PyMuPDF

import config
from clock import WEEKDAYS_RU

log = logging.getLogger("tg-schedule")

DPI = 300
MAX_IMAGES = 6  # за один день страниц нет; защита на «неделю» и fallback
MIN_WIDTH_RATIO = 0.6  # ширина картинки ≥ 0.6 высоты: белые поля для Telegram
DATE_TITLE_RE = re.compile(r"День\s*-\s*([^,\n]+),\s*([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{2,4})")
GROUP_TOKEN_RE = re.compile(r"^\d{2}-\d{2}[А-Яа-яЁёA-Za-z(\s()]*$", re.I)

WEEKDAY_IN_TITLE = {
    "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
    "пятница": 4, "суббота": 5, "воскресенье": 6,
}


@dataclass
class ImageSlice:
    page_no: int
    path: Path
    day: date
    weekday: str
    week_label: str  # «понедельник 19.10.2026»


@dataclass
class RenderOut:
    ok: bool
    images: list[ImageSlice] = field(default_factory=list)
    date_ok: bool | None = None        # установлен только при совпадении даты шапки
    not_cut_off: bool | None = None
    notes: list[str] = field(default_factory=list)
    requested_group: str = ""
    found_in_file: bool = False


def _mid(a: float, b: float) -> float:
    return (a + b) / 2.0


def _band_words(page, y0: float, y1: float) -> list[tuple[str, fitz.Rect]]:
    out = []
    for word in page.get_text("words"):
        rect = fitz.Rect(word[:4])
        if y0 <= rect.y0 <= y1:
            out.append((word[4], rect))
    return out


def _table_rect(page) -> fitz.Rect:
    """Большая заполненная таблица (не фон страницы)."""
    best = fitz.Rect()
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        if rect.width * rect.height <= best.width * best.height:
            continue
        if rect.contains(page.rect) or rect.width > 0.95 * page.rect.width:
            continue  # это фон/поле листа, не таблица
        best = rect
    return best if best.width > 0 else page.rect


def _title_of(page) -> tuple[date | None, str]:
    """Дата и название дня из шапки: «День - Понедельник, 19.10.2026»."""
    top = page.get_text("text", clip=fitz.Rect(0, 0, page.rect.x1, 70))
    match = DATE_TITLE_RE.search(top)
    if not match:
        return None, ""
    day_name = match.group(1).strip().lower().rstrip(" ,")
    day, month, year = match.group(2).split(".")
    year = int(year)
    if year < 100:
        year += 2000
    try:
        parsed = date(year, int(month), int(day))
    except ValueError:
        return None, day_name
    return parsed, day_name


def _columns(page) -> tuple[list[str], list[float], list[float], float | None, fitz.Rect]:
    """(коды групп, левые границы блоков, правые границы блоков, x1 шапки №, таблица)."""
    table = _table_rect(page)
    headers = [(text, rect) for text, rect in sorted(_band_words(page, table.y0 - 1, table.y0 + 28),
                                                     key=lambda wr: wr[1].x0)]
    groups = [(text, rect) for text, rect in headers
              if 1 <= text.count("-") <= 2 and GROUP_TOKEN_RE.match(text.strip())]
    # На шапке может лежать заголовок «Предмет, вид занятия,…» следующей строчки?
    # Нет: шапка групп — y от table.y0 до +20. Отбрасываем слова-не-коды уже сделано.
    if not groups:
        return [], [], [], None, table

    number_cell = next((rect for text, rect in headers if text == "№"), None)
    band_low, band_high = table.y0 + 18, table.y0 + 48
    subjects = sorted((rect for text, rect in _band_words(page, band_low, band_high)
                       if text.startswith("Предмет")), key=lambda r: r.x0)
    rooms = sorted((rect for text, rect in _band_words(page, band_low, band_high)
                    if text == "Ауд."), key=lambda r: r.x0)

    lefts: list[float] = []
    rights: list[float] = []
    count = len(groups)
    has_sub = len(subjects) >= count and len(rooms) >= count
    for index in range(count):
        if has_sub:
            subject0 = subjects[index].x0
            room_prev_x1 = rooms[index - 1].x1 if index else (
                number_cell.x1 if number_cell else table.x0)
            lefts.append(_mid(room_prev_x1, subject0))
            room_this_x1 = rooms[index].x1
            if index + 1 < count:
                rights.append(_mid(room_this_x1, subjects[index + 1].x0))
            else:
                rights.append(table.x1)
        else:
            prev = groups[index - 1][1].x1 if index else (number_cell.x1 if number_cell else table.x0)
            lefts.append(_mid(prev, groups[index][1].x0))
            rights.append(_mid(groups[index][1].x1, groups[index + 1][1].x0)
                          if index + 1 < count else table.x1)
    codes = [text.strip() for text, _rect in groups]
    return codes, lefts, rights, (number_cell.x1 if number_cell else None), table


def _match_group(codes: list[str], group: str) -> int | None:
    needle = group.strip().lower()
    for index, code in enumerate(codes):
        if code.lower().replace(" ", "") == needle:
            return index
    return None


def _compose(page, title_clip: fitz.Rect, number_clip: fitz.Rect, block_clip: fitz.Rect,
             out: Path) -> None:
    """Шапка дня сверху, ниже — № пары и колонка группы. Проводится без сдвига y."""
    from PIL import Image
    title_path = out.with_suffix(".title.png")
    number_path = out.with_suffix(".nos.png")
    block_path = out.with_suffix(".col.png")
    page.get_pixmap(dpi=DPI, clip=fitz.Rect(title_clip) & page.rect).save(title_path)
    page.get_pixmap(dpi=DPI, clip=fitz.Rect(number_clip) & page.rect).save(number_path)
    page.get_pixmap(dpi=DPI, clip=fitz.Rect(block_clip) & page.rect).save(block_path)

    title = Image.open(title_path)
    number = Image.open(number_path)
    block = Image.open(block_path)
    width = max(title.width, number.width + block.width)
    result = Image.new("RGB", (width, title.height + number.height), "white")
    result.paste(title, (0, 0))
    result.paste(number, (0, title.height))
    result.paste(block, (number.width, title.height))

    # Telegram в превью чата сильно ужимает вытянутые фото: колонка 1:3.6
    # становится мелкой и «обрезанной». Добираем белые поля по бокам до
    # «страничной» пропорции (ширина 0.6 высоты) и центрируем содержимое.
    target_width = max(width, int(result.height * MIN_WIDTH_RATIO))
    if target_width > width:
        canvas = Image.new("RGB", (target_width, result.height), "white")
        canvas.paste(result, ((target_width - width) // 2, 0))
        result = canvas

    result.save(out)
    for piece in (title_path, number_path, block_path):
        piece.unlink(missing_ok=True)


def _crop_day(page, group: str, out: Path) -> bool:
    codes, lefts, rights, number_x1, table = _columns(page)
    index = _match_group(codes, group)
    if index is None:
        return False
    # Блок начинается от строки заголовков (там виден код группы) и до низа
    # нарисованной таблицы. Полоса «№» берётся строго от левого столбца
    # таблицы до границы первого блока, чтобы не захватить чужие группы.
    block_clip = fitz.Rect(lefts[index], table.y0 - 0.5, rights[index], table.y1)
    number_clip = fitz.Rect(table.x0, table.y0 - 0.5, lefts[0], table.y1)
    if block_clip.width < 60:
        return False
    parsed, _day_name = _title_of(page)
    # Шапка «День - …» — берём по центру таблицы шириной чуть больше колонки,
    # иначе справа остаётся гигантская белая пустота.
    content_width = number_clip.width + block_clip.width
    center = (table.x0 + table.x1) / 2.0
    half = min(content_width / 2.0, (table.x1 - table.x0) / 2.0)
    title_clip = fitz.Rect(center - half, 14.0, center + half, table.y0)
    _compose(page, title_clip, number_clip, block_clip, out)
    return parsed is not None


def build_images(data: bytes, target: date, out_dir: Path, *,
                 group: str | None = None, week: bool = False) -> RenderOut:
    """Собирает изображения для дня target (или всю неделю) по группе."""
    group = group or config.TARGET_GROUP
    out_dir.mkdir(parents=True, exist_ok=True)
    result = RenderOut(ok=False, requested_group=group)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        titled: list[tuple[date, str, int, fitz.Page]] = []
        for index, page in enumerate(doc):
            parsed, day_name = _title_of(page)
            if parsed is not None:
                titled.append((parsed, day_name, index, page))
        if not titled:
            result.notes.append("В PDF не нашлись страницы в формате «День - …, дата».")
            return result

        if week:
            chosen = sorted(titled, key=lambda item: item[0])
        else:
            chosen = [item for item in sorted(titled, key=lambda item: item[0])
                      if item[0] == target]
            if not chosen:
                first, last = titled[0][0], titled[-1][0]
                result.notes.append(
                    f"В файле неделя {first:%d.%m.%Y}–{last:%d.%m.%Y}; страницы «{WEEKDAYS_RU[target.weekday()]} "
                    f"{target:%d.%m.%Y}» нет (выходной или праздники).")
                return result

        for parsed, day_name, index, page in chosen[:MAX_IMAGES]:
            out = out_dir / f"day_{parsed:%d%m}_p{index + 1}.png"
            ok_crop = _crop_day(page, group, out)
            if not ok_crop and not out.exists():
                continue  # группы нет на этой странице — раньше сообщим ниже
            result.images.append(ImageSlice(
                page_no=index + 1, path=out, day=parsed, weekday=day_name,
                week_label=f"{day_name or WEEKDAYS_RU[parsed.weekday()]} {parsed:%d.%m.%Y}"))

        if not result.images:
            result.notes.append(
                f"Группу {group} в файле не нашли: этот PDF, похоже, не для неё "
                "(проверьте ссылку/шаблон).")
            return result

        result.found_in_file = True
        if not week:
            page_day = result.images[0].day
            result.date_ok = page_day == target
            if not result.date_ok:
                result.notes.append(
                    f"Дата шапки {page_day:%d.%m.%Y} не совпала с запрошенной "
                    f"{target:%d.%m.%Y} — файл, вероятно, за прошлую неделю.")
        result.not_cut_off = True  # снизу граница — нарисованная линия таблицы
        result.ok = True
    finally:
        doc.close()
    return result


def caption(out: RenderOut, target: date, label: str, file_name: str) -> str:
    """Подпись к альбому/картинке."""
    group = out.requested_group
    head = (f"📅 Расписание {group} — {label}" if label == "неделя"
            else f"📅 Расписание {group} — {target:%d.%m.%Y} ({WEEKDAYS_RU[target.weekday()]})")
    lines = [head, f"Источник: {file_name}"]
    if out.ok and out.date_ok is False:
        lines.append("⚠️ Дата в файле не совпала с запрошенным днём — сверьте глазами: "
                     "файл может быть за прошлую неделю.")
    if not out.found_in_file:
        pass
    for note in out.notes[:2]:
        lines.append(f"ℹ️ {note}")
    return "\n".join(lines)
