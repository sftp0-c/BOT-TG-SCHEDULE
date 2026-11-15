"""Поиск и скачивание актуального PDF расписания.

Ссылка нестабильна: базовый файл может называться `25-27-25-31.pdf`, а позже
`25-27-25-31 изм.pdf`, `25-27-25-31 (изм).pdf` и т.п. Бот ищет файл по шаблону
(SCHEDULE_PDF_PATTERN / TARGET_GROUP), а не по одной жёсткой ссылке.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

import config

USER_AGENT = "tg-schedule-bot/0.2"
MAX_PDF_BYTES = 40 * 1024 * 1024
PDF_MAGIC = b"%PDF"
HREF_RE = re.compile(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', re.IGNORECASE)
HREF_ANY_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
# Страница расписаний в меню сайта: /studentam/raspisanie-zanyatiy.php, но php
# могли переименовать (и «rasspisanie…» с двумя «с» тоже встречалось).
DISCOVER_RE = re.compile(r"raspisan|расписан", re.IGNORECASE)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif")
RANGE_TOKEN_RE = re.compile(r"\d{2}-\d{2}")
URL_SAFE = re.compile(r"[^A-Za-z0-9._~:/-]")


def _group_number(group: str) -> int | None:
    """«25-28» → 2528; «26-29(П)» → 2629; другое — None."""
    match = re.match(r"(\d{2})-(\d{2})", group.strip())
    if not match:
        return None
    return int(match.group(1) + match.group(2))


def _normalize_name(name: str) -> str:
    lowered = name.lower()
    lowered = lowered.replace("(", " ").replace(")", " ")
    lowered = re.sub(r"\s+", " ", lowered).strip()
    # Пробелы вокруг дефисов ломают поиск подстроки: «25-27 - 25-31 изм»
    # должно приравняться к «25-27-25-31 изм».
    return re.sub(r"\s*[-–—]\s*", "-", lowered)


def _matches(url: str, patterns: tuple[str, ...]) -> bool:
    path = unquote(urlsplit(url).path)
    name = path.rsplit("/", 1)[-1] if "/" in path else path
    norm = _normalize_name(name)
    for pattern in patterns:
        base = _normalize_name(pattern)
        if base and base in norm:
            return True
    return False


@dataclass
class ScheduleFile:
    url: str
    name: str
    path: Path | None = None  # задан для локального файла (тест без сайта)

    @property
    def changed_mark(self) -> bool:
        norm = _normalize_name(self.name)
        return "изм" in norm or "измен" in norm


def _patterns_for(group: str) -> tuple[str, ...]:
    # Имя файла расписания: список групп через дефис, напр. «25-27-25-31».
    codes = [code for code in re.split(r"[-\s]+", group.strip()) if code]
    base = "-".join(codes) if codes else group
    return (base, config.SCHEDULE_PDF_PATTERN) if base != config.SCHEDULE_PDF_PATTERN else (base,)


def candidate_direct_urls(base_url: str) -> list[str]:
    """Варианты прямой ссылки: без пометки и с разными начинками «изм».

    Сис-админ пишет то «(изм)», то «изм», иногда с пробелами — пробуем все.
    """
    raw = (base_url or "").strip()
    if not raw or not raw.lower().startswith(("http://", "https://")):
        return []
    if "расписание" not in unquote(raw).lower() and not raw.lower().endswith(".pdf"):
        return []  # это страница, не файл
    prefix = raw[:-4].rstrip() if raw.lower().endswith(".pdf") else raw
    if prefix.lower().endswith(".pdf"):
        prefix = prefix[:-4]
    marks = ["", " изм", " (изм)", " изм.", "-изм", "-изм(изм)"]
    dedup: list[str] = []
    for mark in marks:
        candidate = prefix + mark + ".pdf"
        encoded = quote(candidate, safe=":/?&=%#[]@!$'()*+,;=~-._")
        if encoded not in dedup:
            dedup.append(encoded)
    return dedup


def _range_covers(url: str, group: str) -> bool:
    """Файл-диапазон: «25-27-25-31» — это группы от 25-27 до 25-31.

    Группа 25-28 сидит именно в таком файле, а имя может быть написано и как
    «25-31-25-27», и с «(изм)». Разбираем числовые пары и проверяем границами.
    """
    codes = _group_number(group)
    if codes is None:
        return False
    numbers = [int(token.replace("-", "")) for token in RANGE_TOKEN_RE.findall(unquote(url))]
    if len(numbers) < 2:
        return False
    return min(numbers) <= codes <= max(numbers)


def pick_schedule_url_from_html(html: str, page_url: str, patterns: tuple[str, ...],
                                group: str = "") -> ScheduleFile | None:
    """Чистая функция: выбрать подходящий PDF из HTML страницы.

    Приоритет: точное совпадение с шаблоном (или «изм» внутри него), потом
    файл-диапазон, накрывающий группу.
    """
    group = group or config.TARGET_GROUP
    candidates: list[ScheduleFile] = []
    for href in HREF_RE.findall(html or ""):
        full = urljoin(page_url, unescape(href.strip()))
        if _matches(full, patterns) or _range_covers(full, group):
            name = unquote(urlsplit(full).path.rsplit("/", 1)[-1])
            candidates.append(ScheduleFile(url=full, name=name))
    if not candidates:
        return None

    def rank(item: ScheduleFile) -> tuple[int, int, int, str]:
        exact = 0 if _matches(item.url, patterns) else 1
        ranged = 0 if _range_covers(item.url, group) else 1
        return (exact, ranged, int(item.changed_mark), _normalize_name(item.name))

    candidates.sort(key=rank)
    return candidates[-1]


def take_local_pdf() -> ScheduleFile | None:
    """Тестовый режим: SCHEDULE_LOCAL_PDF указывает файл на диске.

    Пока сайт колледжа недоступен, конвейер можно проверить на PDF из архива:
    бот возьмёт этот файл вместо скачивания. Если путь указан, но файла нет —
    ошибка, а не тихое переключение на сеть (иначе опечатка была бы незаметна).
    """
    raw = config.SCHEDULE_LOCAL_PDF
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return ScheduleFile(url=str(path), name=path.name, path=path)


def validate_pdf_bytes(data: bytes) -> bytes:
    """Общая проверка PDF (magic и размер): и для сети, и для локального файла."""
    if not data.startswith(PDF_MAGIC):
        raise ValueError("по ссылке/файлу не PDF (возможно, страница с ошибкой)")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("файл больше 40 МБ — похоже, это не расписание")
    return data


def schedule_pages() -> list[str]:
    """SCHEDULE_PAGE_URL — можно список через запятую: пробуем по порядку."""
    return [page for page in (part.strip() for part in config.SCHEDULE_PAGE_URL.split(","))
            if page]


async def fetch_schedule_file() -> ScheduleFile | None:
    """Найти актуальный PDF: локальный (если задан) → страницы → прямая ссылка.

    Порядок (каждый следующий — только если предыдущий не дал файл):
      1. страницы из SCHEDULE_PAGE_URL (можно список через запятую);
      2. автопоиск: в меню раздела SCHEDULE_MENU_URL ищем ссылку «Расписание
         занятий» — php-страницу могли переименовать/перенести, а ссылка в
         меню укажет на её новое имя;
      3. прямая ссылка SCHEDULE_DIRECT_URL и её варианты («изм»/«(изм)»).
    """
    local = take_local_pdf()
    if local is not None:
        return local

    patterns = _patterns_for(config.TARGET_GROUP)
    tried: set[str] = set()

    async def page_pdf(url: str) -> ScheduleFile | None:
        """Скачать страницу и выбрать на ней подходящий PDF (None — не вышло)."""
        if not url or url in tried:
            return None
        tried.add(url)
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0,
                                         headers={"User-Agent": USER_AGENT}) as client:
                response = await client.get(url)
                response.raise_for_status()
                if len(response.content) > 4 * 1024 * 1024:
                    raise ValueError("страница расписаний слишком большая")
            return pick_schedule_url_from_html(
                response.text, str(response.url), patterns, config.TARGET_GROUP)
        except httpx.HTTPError:
            return None

    for page in schedule_pages():
        found = await page_pdf(page)
        if found is not None:
            return found

    # Автопоиск по меню: ссылки, похожие на страницу расписания (не pdf/картинки).
    if config.SCHEDULE_MENU_URL:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30.0,
                                         headers={"User-Agent": USER_AGENT}) as client:
                menu_response = await client.get(config.SCHEDULE_MENU_URL)
                menu_response.raise_for_status()
            guesses: list[str] = []
            for href in HREF_ANY_RE.findall(menu_response.text):
                full = urljoin(str(menu_response.url), unescape(href.strip()))
                path = unquote(urlsplit(full).path)
                if not DISCOVER_RE.search(path):
                    continue
                if path.lower().endswith(IMAGE_EXT) or path.lower().endswith(".pdf"):
                    continue
                clean = full.split("#", 1)[0]
                if clean not in guesses:
                    guesses.append(clean)
            for guess in guesses[:3]:
                found = await page_pdf(guess)
                if found is not None:
                    return found
        except httpx.HTTPError:
            pass

    if config.SCHEDULE_DIRECT_URL:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0,
                                     headers={"User-Agent": USER_AGENT}) as client:
            for url in candidate_direct_urls(config.SCHEDULE_DIRECT_URL):
                try:
                    response = await client.get(url)
                except httpx.HTTPError:
                    continue
                if response.status_code == 200 and response.content.startswith(PDF_MAGIC):
                    name = unquote(urlsplit(str(response.url)).path.rsplit("/", 1)[-1])
                    return ScheduleFile(url=str(response.url), name=name)

    return None


async def download_pdf(url: str) -> bytes:
    """Скачивает PDF с проверкой сигнатуры и размера (иначе в кэш попадёт HTML 404)."""
    async with httpx.AsyncClient(follow_redirects=True, timeout=60.0,
                                 headers={"User-Agent": USER_AGENT}) as client:
        response = await client.get(url)
        response.raise_for_status()
    return validate_pdf_bytes(response.content)
