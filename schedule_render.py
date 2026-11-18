"""PDF расписания колледжа → картинки для Telegram.

Структура файлов колледжа:
  * одна страница = один день; в шапке строка «День - Понедельник, 19.10.2026»;
  * под шапкой строка с кодами групп: №, 26-28, 26-29, 26-29(П), 26-30, 26-31;
  * таблица ограничена большим прямоугольником (fill), её нижняя граница —
    конец таблицы;
  * у каждого блока группы три подколонки: «Предмет, вид занятия,»,
    «преподаватель», «Ауд.».
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pymupdf as fitz

import config
from clock import WEEKDAYS_RU

log = logging.getLogger("tg-schedule")

DPI = 300
MAX_IMAGES = 7  # до 7 дней (Пн–Вс) на «неделю»
MAX_PDF_PAGES = 32
MAX_PAGE_POINTS = 5000
MAX_PAGE_PIXELS = 60_000_000
MIN_WIDTH_RATIO = 0.6  # ширина картинки >= 0.6 высоты: белые поля для Telegram
DATE_TITLE_RE = re.compile(
    r"День\s*[-–—]\s*([^,\n]+),\s*([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{2,4})"
)
GROUP_TOKEN_RE = re.compile(r"^\d{2}-\d{2}[А-Яа-яЁёA-Za-z(\s()]*$", re.I)

WEEKDAY_IN_TITLE = {
    "понедельник": 0,
    "вторник": 1,
    "среда": 2,
    "четверг": 3,
    "пятница": 4,
    "суббота": 5,
    "воскресенье": 6,
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
    date_ok: bool | None = None
    not_cut_off: bool | None = None
    notes: list[str] = field(default_factory=list)
    requested_group: str = ""
    found_in_file: bool = False


def _mid(a: float, b: float) -> float:
    return (a + b) / 2.0


def _band_words(page: fitz.Page, y0: float, y1: float) -> list[tuple[str, fitz.Rect]]:
    out = []
    for word in page.get_text("words"):
        rect = fitz.Rect(word[:4])
        if y0 <= rect.y0 <= y1:
            out.append((word[4], rect))
    return out


def _table_rect(page: fitz.Page) -> fitz.Rect:
    """Большая заполненная таблица (не фон страницы)."""
    best = fitz.Rect()
    for drawing in page.get_drawings():
        rect = drawing.get("rect")
        if not rect:
            continue
        if rect.width * rect.height <= best.width * best.height:
            continue
        if rect.contains(page.rect) or rect.width > 0.95 * page.rect.width:
            continue  # фон/поле листа, не таблица
        best = rect
    return best if best.width > 0 else page.rect


def _title_of(page: fitz.Page) -> tuple[date | None, str]:
    """Дата и название дня из шапки: «День - Понедельник, 19.10.2026»."""
    top = page.get_text("text", clip=fitz.Rect(0, 0, page.rect.x1, 70))
    match = DATE_TITLE_RE.search(top)
    if not match:
        return None, ""
    day_name = match.group(1).strip().lower().rstrip(" ,")
    day, month, year = match.group(2).split(".")
    year_int = int(year)
    if year_int < 100:
        year_int += 2000
    try:
        parsed = date(year_int, int(month), int(day))
    except ValueError:
        return None, day_name
    return parsed, day_name


def _merge_adjacent_group_tokens(
    headers: list[tuple[str, fitz.Rect]],
) -> list[tuple[str, fitz.Rect]]:
    """Склеивает код группы с соседним суффиксом (например «26-29» + «(П)»), если PyMuPDF разбил их."""
    merged: list[tuple[str, fitz.Rect]] = []
    idx = 0
    while idx < len(headers):
        text, rect = headers[idx]
        if (
            1 <= text.count("-") <= 2
            and GROUP_TOKEN_RE.match(text.strip())
            and idx + 1 < len(headers)
        ):
            next_text, next_rect = headers[idx + 1]
            same_row = abs(next_rect.y0 - rect.y0) <= 5.0
            close_x = 0 <= (next_rect.x0 - rect.x1) <= 18.0
            is_suffix = bool(re.match(r"^[(\[]?[А-Яа-яЁёA-Za-z0-9]+[)\]]?$", next_text.strip()))
            if same_row and close_x and is_suffix and not GROUP_TOKEN_RE.match(next_text.strip()):
                text = f"{text.strip()}{next_text.strip()}"
                rect = rect | next_rect
                idx += 2
                merged.append((text, rect))
                continue
        merged.append((text, rect))
        idx += 1
    return merged


def _columns(
    page: fitz.Page,
) -> tuple[list[str], list[float], list[float], float | None, fitz.Rect]:
    """(коды групп, левые границы блоков, правые границы блоков, x1 шапки №, таблица)."""
    table = _table_rect(page)
    raw_headers = sorted(
        _band_words(page, table.y0 - 1, table.y0 + 28),
        key=lambda wr: wr[1].x0,
    )
    headers = _merge_adjacent_group_tokens(raw_headers)
    groups = [
        (text, rect)
        for text, rect in headers
        if 1 <= text.count("-") <= 2 and GROUP_TOKEN_RE.match(text.strip())
    ]
    if not groups:
        return [], [], [], None, table

    number_cell = next((rect for text, rect in headers if text == "№"), None)
    band_low, band_high = table.y0 + 18, table.y0 + 48
    subjects = sorted(
        (
            rect
            for text, rect in _band_words(page, band_low, band_high)
            if text.startswith("Предмет")
        ),
        key=lambda r: r.x0,
    )
    rooms = sorted(
        (rect for text, rect in _band_words(page, band_low, band_high) if text == "Ауд."),
        key=lambda r: r.x0,
    )

    lefts: list[float] = []
    rights: list[float] = []
    count = len(groups)
    has_sub = len(subjects) >= count and len(rooms) >= count
    for index in range(count):
        if has_sub:
            subject0 = subjects[index].x0
            room_prev_x1 = (
                rooms[index - 1].x1
                if index
                else (number_cell.x1 if number_cell else table.x0)
            )
            lefts.append(_mid(room_prev_x1, subject0))
            room_this_x1 = rooms[index].x1
            if index + 1 < count:
                rights.append(_mid(room_this_x1, subjects[index + 1].x0))
            else:
                rights.append(table.x1)
        else:
            prev = (
                groups[index - 1][1].x1
                if index
                else (number_cell.x1 if number_cell else table.x0)
            )
            lefts.append(_mid(prev, groups[index][1].x0))
            rights.append(
                _mid(groups[index][1].x1, groups[index + 1][1].x0)
                if index + 1 < count
                else table.x1
            )
    codes = [text.strip() for text, _rect in groups]
    return codes, lefts, rights, (number_cell.x1 if number_cell else None), table


def _normalize_group_code(code: str) -> str:
    return re.sub(r"\s+", "", code.strip().lower())


def _match_group(codes: list[str], group: str) -> int | None:
    needle = _normalize_group_code(group)
    for index, code in enumerate(codes):
        if _normalize_group_code(code) == needle:
            return index
    return None


def _compose(
    page: fitz.Page,
    title_clip: fitz.Rect,
    number_clip: fitz.Rect,
    block_clip: fitz.Rect,
    out: Path,
) -> None:
    """Шапка дня сверху, ниже — № пары и колонка группы."""
    from PIL import Image

    title_path = out.with_suffix(".title.png")
    number_path = out.with_suffix(".nos.png")
    block_path = out.with_suffix(".col.png")
    page.get_pixmap(dpi=DPI, clip=fitz.Rect(title_clip) & page.rect).save(title_path)
    page.get_pixmap(dpi=DPI, clip=fitz.Rect(number_clip) & page.rect).save(number_path)
    page.get_pixmap(dpi=DPI, clip=fitz.Rect(block_clip) & page.rect).save(block_path)

    with Image.open(title_path) as title_im, Image.open(number_path) as num_im, Image.open(
        block_path
    ) as blk_im:
        title = title_im.convert("RGB")
        number = num_im.convert("RGB")
        block = blk_im.convert("RGB")

    width = max(title.width, number.width + block.width)
    result = Image.new("RGB", (width, title.height + number.height), "white")
    title_x = max(0, (width - title.width) // 2)
    result.paste(title, (title_x, 0))
    result.paste(number, (0, title.height))
    result.paste(block, (number.width, title.height))

    # Telegram в превью сильно ужимает узкие вертикальные картинки:
    # добираем белые поля по бокам до пропорции >= 0.6 высоты и центрируем.
    target_width = max(width, int(result.height * MIN_WIDTH_RATIO))
    if target_width > width:
        canvas = Image.new("RGB", (target_width, result.height), "white")
        canvas.paste(result, ((target_width - width) // 2, 0))
        result = canvas

    result.save(out, optimize=True)
    for piece in (title_path, number_path, block_path):
        piece.unlink(missing_ok=True)


def _crop_day(page: fitz.Page, group: str, out: Path) -> bool:
    codes, lefts, rights, _number_x1, table = _columns(page)
    index = _match_group(codes, group)
    if index is None:
        return False
    block_clip = fitz.Rect(lefts[index], table.y0 - 0.5, rights[index], table.y1)
    number_clip = fitz.Rect(table.x0, table.y0 - 0.5, lefts[0], table.y1)
    if block_clip.width < 60:
        return False
    parsed, _day_name = _title_of(page)
    content_width = number_clip.width + block_clip.width
    center = (table.x0 + table.x1) / 2.0
    half = min(content_width / 2.0, (table.x1 - table.x0) / 2.0)
    title_clip = fitz.Rect(center - half, 14.0, center + half, table.y0)
    _compose(page, title_clip, number_clip, block_clip, out)
    return parsed is not None


def build_images(
    data: bytes,
    target: date,
    out_dir: Path,
    *,
    group: str | None = None,
    week: bool = False,
    week_start: date | None = None,
) -> RenderOut:
    """Собирает изображения для дня ``target`` (или всю неделю) по группе."""
    group = group or config.TARGET_GROUP
    out_dir.mkdir(parents=True, exist_ok=True)
    result = RenderOut(ok=False, requested_group=group)
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.needs_pass:
            result.notes.append("PDF защищён паролем и не может быть обработан.")
            return result
        if len(doc) > MAX_PDF_PAGES:
            result.notes.append(
                f"В PDF слишком много страниц ({len(doc)}; максимум {MAX_PDF_PAGES})."
            )
            return result

        titled: list[tuple[date, str, int, fitz.Page]] = []
        for index, page in enumerate(doc):
            if page.rect.width > MAX_PAGE_POINTS or page.rect.height > MAX_PAGE_POINTS:
                result.notes.append("Размер страницы PDF превышает безопасный предел.")
                return result
            scale = DPI / 72.0
            pixels = int(page.rect.width * scale) * int(page.rect.height * scale)
            if pixels > MAX_PAGE_PIXELS:
                result.notes.append("Страница PDF потребует слишком много памяти для рендера.")
                return result
            parsed, day_name = _title_of(page)
            if parsed is not None:
                titled.append((parsed, day_name, index, page))
        if not titled:
            result.notes.append("В PDF не нашлись страницы в формате «День - …, дата».")
            return result

        if week:
            ordered = sorted(titled, key=lambda item: item[0])
            if week_start is not None:
                week_end = week_start + timedelta(days=6)
                chosen = [item for item in ordered if week_start <= item[0] <= week_end]
                if not chosen:
                    first, last = ordered[0][0], ordered[-1][0]
                    result.notes.append(
                        f"В PDF на сайте пока нет страниц на неделю "
                        f"{week_start:%d.%m.%Y}–{week_end:%d.%m.%Y} "
                        f"(сейчас выложена неделя {first:%d.%m.%Y}–{last:%d.%m.%Y})."
                    )
                    return result
            else:
                target_monday = target - timedelta(days=target.weekday())
                target_sunday = target_monday + timedelta(days=6)
                chosen = [
                    item
                    for item in ordered
                    if target_monday <= item[0] <= target_sunday
                ]
                if not chosen:
                    latest = ordered[-1][0]
                    latest_monday = latest - timedelta(days=latest.weekday())
                    latest_sunday = latest_monday + timedelta(days=6)
                    chosen = [
                        item
                        for item in ordered
                        if latest_monday <= item[0] <= latest_sunday
                    ]
        else:
            chosen = [
                item for item in sorted(titled, key=lambda item: item[0]) if item[0] == target
            ]
            if not chosen:
                first, last = titled[0][0], titled[-1][0]
                result.notes.append(
                    f"В файле неделя {first:%d.%m.%Y}–{last:%d.%m.%Y}; "
                    f"страницы «{WEEKDAYS_RU[target.weekday()]} {target:%d.%m.%Y}» нет "
                    "(выходной или расписание ещё не обновили)."
                )
                return result

        for parsed, day_name, index, page in chosen[:MAX_IMAGES]:
            out = out_dir / f"day_{parsed:%d%m}_p{index + 1}.png"
            out.unlink(missing_ok=True)
            ok_crop = _crop_day(page, group, out)
            if not ok_crop or not out.is_file():
                out.unlink(missing_ok=True)
                continue
            result.images.append(
                ImageSlice(
                    page_no=index + 1,
                    path=out,
                    day=parsed,
                    weekday=day_name,
                    week_label=f"{day_name or WEEKDAYS_RU[parsed.weekday()]} {parsed:%d.%m.%Y}",
                )
            )

        if not result.images:
            result.notes.append(
                f"Группу {group} в файле не нашли: проверьте TARGET_GROUP / "
                "SCHEDULE_PDF_PATTERN в .env."
            )
            return result

        result.found_in_file = True
        if not week:
            page_day = result.images[0].day
            result.date_ok = page_day == target
            if not result.date_ok:
                result.notes.append(
                    f"Дата шапки {page_day:%d.%m.%Y} не совпала с запрошенной "
                    f"{target:%d.%m.%Y} — файл, вероятно, за прошлую неделю."
                )
        result.not_cut_off = True
        result.ok = True
    finally:
        doc.close()
    return result


def caption(out: RenderOut, target: date, label: str, file_name: str) -> str:
    """Подпись к альбому/картинке."""
    group = out.requested_group
    head = (
        f"📅 Расписание {group} — {label}"
        if label == "неделя"
        else f"📅 Расписание {group} — {target:%d.%m.%Y} ({WEEKDAYS_RU[target.weekday()]})"
    )
    lines = [head, f"Источник: {file_name}"]
    if out.ok and out.date_ok is False:
        lines.append(
            "⚠️ Дата в файле не совпала с запрошенным днём — сверьте глазами: "
            "файл может быть за прошлую неделю."
        )
    for note in out.notes[:2]:
        lines.append(f"ℹ️ {note}")
    return "\n".join(lines)
