"""Поиск и скачивание актуального PDF расписания.

Ссылка нестабильна: базовый файл может называться ``25-27-25-31.pdf``, а позже
``25-27-25-31 1.pdf``, ``25-27-25-31 изм.pdf``, ``25-27-25-31 (изм).pdf`` и т.п.
Бот ищет файл по шаблону (SCHEDULE_PDF_PATTERN / TARGET_GROUP), а не по одной
жёсткой ссылке.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx

import config

log = logging.getLogger("tg-schedule")

USER_AGENT = "tg-schedule-bot/1.0"
MAX_PDF_BYTES = 40 * 1024 * 1024
PDF_MAGIC = b"%PDF"
HREF_RE = re.compile(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', re.IGNORECASE)
HREF_ANY_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
DISCOVER_RE = re.compile(r"raspisan|расписан", re.IGNORECASE)
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg")
RANGE_TOKEN_RE = re.compile(r"\d{2}-\d{2}")
REVISION_NUM_RE = re.compile(r"(?:^|\s|\()(\d+)(?:\))?\.pdf$", re.IGNORECASE)


def _filename_from_url(url: str) -> str:
    path = unquote(urlsplit(url).path)
    return path.rsplit("/", 1)[-1] if "/" in path else path


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
    return re.sub(r"\s*[-–—]\s*", "-", lowered)


def _revision_number(name: str) -> int:
    """Извлекает номер версии из конца имени файла (например «25-27-25-31 2.pdf» → 2)."""
    match = REVISION_NUM_RE.search(name.strip())
    return int(match.group(1)) if match else 0


def _matches(url: str, patterns: tuple[str, ...]) -> bool:
    name = _filename_from_url(url)
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
    codes = [code for code in re.split(r"[-\s]+", group.strip()) if code]
    base = "-".join(codes) if codes else group
    pattern = config.SCHEDULE_PDF_PATTERN.strip()
    if pattern and base and base != pattern:
        return (pattern, base)
    return (pattern,) if pattern else (base,)


def candidate_direct_urls(base_url: str) -> list[str]:
    """Варианты прямой ссылки: без пометки и с разными вариантами «изм»."""
    raw = (base_url or "").strip()
    if not raw or not raw.lower().startswith(("http://", "https://")):
        return []
    if "расписание" not in unquote(raw).lower() and not raw.lower().endswith(".pdf"):
        return []
    prefix = raw[:-4].rstrip() if raw.lower().endswith(".pdf") else raw
    if prefix.lower().endswith(".pdf"):
        prefix = prefix[:-4]
    marks = ["", " изм", " (изм)", " изм.", "-изм", " 1", " 2"]
    dedup: list[str] = []
    for mark in marks:
        candidate = prefix + mark + ".pdf"
        encoded = quote(candidate, safe=":/?&=%#[]@!$'()*+,;=~-._")
        if encoded not in dedup:
            dedup.append(encoded)
    return dedup


def _range_covers(url: str, group: str) -> bool:
    """Файл-диапазон: «25-27-25-31» — группы от 25-27 до 25-31 (включая 25-28)."""
    codes = _group_number(group)
    if codes is None:
        return False
    filename = _filename_from_url(url)
    numbers = [int(token.replace("-", "")) for token in RANGE_TOKEN_RE.findall(filename)]
    if len(numbers) < 2:
        return False
    return min(numbers) <= codes <= max(numbers)


def pick_schedule_url_from_html(
    html: str, page_url: str, patterns: tuple[str, ...], group: str = ""
) -> ScheduleFile | None:
    """Выбирает наиболее актуальный PDF из HTML-страницы.

    Приоритет (чем выше кортеж — тем лучше):
      1. точное совпадение с шаблоном ``patterns``;
      2. диапазон в имени файла накрывает группу ``group``;
      3. пометка «изм» / «измен» (исправленное расписание важнее исходного);
      4. номер ревизии в конце имени (``... 2.pdf`` > ``... 1.pdf`` > ``....pdf``);
      5. нормализованное имя файла.
    """
    group = group or config.TARGET_GROUP
    candidates: list[ScheduleFile] = []
    seen_urls: set[str] = set()
    for href in HREF_RE.findall(html or ""):
        full = urljoin(page_url, unescape(href.strip()))
        if full in seen_urls:
            continue
        seen_urls.add(full)
        if _matches(full, patterns) or _range_covers(full, group):
            candidates.append(ScheduleFile(url=full, name=_filename_from_url(full)))
    if not candidates:
        return None

    def rank(item: ScheduleFile) -> tuple[int, int, int, int, str]:
        exact = 1 if _matches(item.url, patterns) else 0
        ranged = 1 if _range_covers(item.url, group) else 0
        changed = 1 if item.changed_mark else 0
        rev = _revision_number(item.name)
        return (exact, ranged, changed, rev, _normalize_name(item.name))

    return max(candidates, key=rank)


def take_local_pdf() -> ScheduleFile | None:
    """Тестовый режим: SCHEDULE_LOCAL_PDF указывает файл на диске."""
    raw = config.SCHEDULE_LOCAL_PDF
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    return ScheduleFile(url=str(path), name=path.name, path=path)


def validate_pdf_bytes(data: bytes) -> bytes:
    """Проверка сигнатуры PDF (%PDF) и разумного размера."""
    if not data.startswith(PDF_MAGIC):
        raise ValueError("по ссылке/файлу не PDF (возможно, страница с ошибкой)")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("файл больше 40 МБ — похоже, это не расписание")
    return data


def schedule_pages() -> list[str]:
    """SCHEDULE_PAGE_URL — можно список через запятую: пробуем по порядку."""
    return [
        page
        for page in (part.strip() for part in config.SCHEDULE_PAGE_URL.split(","))
        if page
    ]


async def _get_with_retry(
    client: httpx.AsyncClient, url: str, *, attempts: int = 2
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            response = await client.get(url)
            if response.status_code >= 500 and attempt + 1 < attempts:
                await asyncio.sleep(0.6 * (attempt + 1))
                continue
            return response
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.6 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


async def fetch_schedule_file() -> ScheduleFile | None:
    """Найти актуальный PDF: локальный (если задан) → страницы → меню → прямая ссылка."""
    local = take_local_pdf()
    if local is not None:
        return local

    patterns = _patterns_for(config.TARGET_GROUP)
    tried: set[str] = set()

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=30.0,
        headers={"User-Agent": USER_AGENT},
    ) as client:

        async def page_pdf(url: str) -> ScheduleFile | None:
            if not url or url in tried:
                return None
            tried.add(url)
            try:
                response = await _get_with_retry(client, url)
                response.raise_for_status()
                if len(response.content) > 4 * 1024 * 1024:
                    raise ValueError("страница расписаний слишком большая")
                return pick_schedule_url_from_html(
                    response.text, str(response.url), patterns, config.TARGET_GROUP
                )
            except httpx.HTTPError as exc:
                log.debug("Не удалось загрузить страницу %s: %s", url, exc)
                return None

        for page in schedule_pages():
            found = await page_pdf(page)
            if found is not None:
                return found

        # Автопоиск по меню раздела, если адрес php-страницы поменяли
        if config.SCHEDULE_MENU_URL:
            try:
                menu_response = await _get_with_retry(client, config.SCHEDULE_MENU_URL)
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
                for guess in guesses[:4]:
                    found = await page_pdf(guess)
                    if found is not None:
                        return found
            except httpx.HTTPError as exc:
                log.debug("Не удалось загрузить меню %s: %s", config.SCHEDULE_MENU_URL, exc)

        # Запасной вариант: прямая ссылка и её варианты («изм», «(изм)», « 1»)
        if config.SCHEDULE_DIRECT_URL:
            for url in candidate_direct_urls(config.SCHEDULE_DIRECT_URL):
                try:
                    response = await _get_with_retry(client, url, attempts=1)
                except httpx.HTTPError:
                    continue
                if response.status_code == 200 and response.content.startswith(PDF_MAGIC):
                    final_url = str(response.url)
                    return ScheduleFile(url=final_url, name=_filename_from_url(final_url))

    return None


async def download_pdf(url: str) -> bytes:
    """Скачивает PDF с проверкой сигнатуры %PDF и размера."""
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=60.0,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        response = await _get_with_retry(client, url, attempts=2)
        response.raise_for_status()
    return validate_pdf_bytes(response.content)
