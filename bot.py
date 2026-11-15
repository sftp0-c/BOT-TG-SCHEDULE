"""Точка входа Telegram-бота (aiogram 3).

Команды:
  /расписание | !расписание           — расписание на день; после 13:00 (Екб)
                                        автоматически «завтра».
  /расписание завтра                  — следующий день.
  /расписание сегодня                 — сегодняшний день.
  /расписание 21.10 | 21.10.2026      — на конкретный день.
  /расписание неделя                  — столбец группы на всю неделю из PDF.

Поиск файла: сначала прямой SCHEDULE_DIRECT_URL с вариантами пометки «изм»,
иначе страница SCHEDULE_PAGE_URL, где ссылка ищется по шаблону группы.
Для проверки без сайта есть SCHEDULE_LOCAL_PDF — путь к PDF на диске.
"""
from __future__ import annotations

import asyncio
import io
import logging
import msvcrt  # noqa: F401 — файл-замок одной копии бота (Windows)
import tempfile
import time
from pathlib import Path

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.filters import Command
from aiogram.types import FSInputFile, Message

import config
from clock import weekday_name
from commands import parse_command
from config import TG_BOT_TOKEN, TG_PROXY, validate
from schedule_render import build_images
from schedule_render import caption as render_caption
from schedule_source import (download_pdf, fetch_schedule_file,
                             validate_pdf_bytes)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("tg-schedule")

dp = Dispatcher()

HELP = (
    "📅 Расписание группы 25-28.\n"
    "Команды:\n"
    "• /расписание — на день (после 13:00 по Екб автоматически «завтра»)\n"
    "• /расписание завтра — на следующий день\n"
    "• /расписание сегодня — на сегодняшний день\n"
    "• /расписание 21.10 — на конкретный день (можно 21.10.2026)\n"
    "• /расписание неделя (или все) — вся неделя из PDF, альбомом\n"
    "Вместо «/» можно писать «!»: !расписание завтра."
)

TEMP_ROOT = Path(tempfile.gettempdir()) / "tg-schedule-bot"
LOCK_PATH = Path(tempfile.gettempdir()) / "tg-schedule-bot.lock"
KEEP_AGE_SECONDS = 15 * 60
COMMAND_RE = r"^\s*[!/](?:расписание|raspisanie|schedule)\b"
BOT_LOCK: "io.TextIOWrapper | None" = None  # держим файл-замок живым всё время работы


def acquire_single_instance_lock() -> "io.TextIOWrapper | None":
    """Одна копия бота: файл-замок в temp держится открытым, пока бот живёт.

    Вторая копия не сможет закрыть замок и тут же выйдет с понятной ошибкой.
    """
    try:
        lock = open(LOCK_PATH, "a+", encoding="utf-8")
        lock.seek(0)  # замок всегда на первый байт, независимо от режима 'a'
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        # Замок занят: другая копия уже работает (или этот процесс владеет им).
        done = locals().get("lock")  # файл мог не открыться вовсе
        if done is not None:
            done.close()
        raise SystemExit("Бот уже запущен — вторая копия не нужна (файл-замок занят).")
    return lock


def cleanup(out_dir: Path) -> None:
    """Удаляем старые картинки: данные в них критичными не являются."""
    if not out_dir.exists():
        return
    now = time.time()
    for stale in out_dir.glob("*.png"):
        try:
            if now - stale.stat().st_mtime > KEEP_AGE_SECONDS:
                stale.unlink()
        except OSError:
            continue


async def handle_schedule(message: Message, text: str) -> None:
    config.reload()  # .env правят на ходу — перечитываем на каждую команду
    target = parse_command(text)
    if target is None:
        return

    if target.label == "неделя":
        await message.answer(f"Собираю расписание {config.TARGET_GROUP} на всю неделю…")
    else:
        day = target.day
        await message.answer(
            f"Смотрю расписание {config.TARGET_GROUP} на {target.label} "
            f"({weekday_name(day)}, {day:%d.%m.%Y})…")

    try:
        found = await fetch_schedule_file()
    except FileNotFoundError as exc:  # SCHEDULE_LOCAL_PDF указан, но файла нет
        log.warning("SCHEDULE_LOCAL_PDF не найден: %s", exc)
        await message.answer(
            "В .env включён тестовый файл (SCHEDULE_LOCAL_PDF), но по этому пути файла нет:\n"
            f"{exc}\n\nИсправьте путь или закомментируйте строку символом # — "
            "бот снова будет читать сайт.")
        return
    except httpx.HTTPError:  # noqa: BLE001 — сайт/сеть: понятное сообщение вместо типа ошибки
        log.warning("Сайт расписаний недоступен")
        await message.answer(
            "Сайт колледжа сейчас недоступен (не отвечает или лежит).\n"
            "Как только он заработает — просто пришли команду ещё раз.\n"
            "Можно указать прямую ссылку SCHEDULE_DIRECT_URL в .env."
        )
        return
    except Exception as exc:  # noqa: BLE001 — черновик: честно показываем причину
        log.warning("Не удалось найти файл расписания: %s", type(exc).__name__)
        await message.answer(
            "Не удалось получить ссылку на расписание.\n"
            f"Причина: {type(exc).__name__}\n"
            "Проверьте SCHEDULE_PAGE_URL / SCHEDULE_DIRECT_URL в .env."
        )
        return

    if not found:
        await message.answer(
            "Подходящий PDF не нашёлся.\n"
            "Укажите SCHEDULE_DIRECT_URL в .env (прямую ссылку на файл) — "
            "бот сам попробует варианты «изм»/«(изм)»."
        )
        return

    if found.path is not None:  # тестовый режим: файл с диска
        try:
            data = validate_pdf_bytes(found.path.read_bytes())
        except (OSError, ValueError) as exc:
            log.warning("Локальный файл %s не читается: %s", found.name, type(exc).__name__)
            await message.answer(
                f"Тестовый файл есть, но читать его не удалось:\n{found.url}\n\nПричина: {exc}")
            return
    else:
        try:
            data = await download_pdf(found.url)
        except Exception as exc:  # noqa: BLE001 — отправка: показываем причину
            log.warning("Не удалось скачать PDF %s: %s", found.name, type(exc).__name__)
            await message.answer(
                f"Файл найден, но скачать его не удалось: {found.name}\n"
                f"{found.url}\n\nПричина: {type(exc).__name__}"
            )
            return

    is_week = target.label == "неделя"
    cleanup(TEMP_ROOT)
    out = build_images(data, target.day, TEMP_ROOT, week=is_week)
    if not out.ok or not out.images:
        reasons = "; ".join(out.notes) or "не удалось подготовить изображение"
        await message.answer(
            f"Пока не получилось собрать картинку: {reasons}.\n"
            f"Файл: {found.name} — {found.url}"
        )
        return

    try:
        text_out = render_caption(out, target.day, target.label, found.name)
        photos = [FSInputFile(slice.path) for slice in out.images]
        if len(photos) == 1:
            await message.answer_photo(photos[0], caption=text_out)
            return
        from aiogram.media_group import MediaGroupBuilder
        builder = MediaGroupBuilder(caption=text_out)
        for photo in photos:
            builder.add_photo(photo)
        await message.answer_media_group(builder.build())
    except Exception as exc:  # noqa: BLE001 — отправка: показываем причину
        log.warning("Не удалось отправить изображение: %s", type(exc).__name__)
        await message.answer(
            f"Картинки собраны, но отправить их не получилось ({type(exc).__name__}).\n"
            f"Файлы лежат в: {TEMP_ROOT}"
        )


@dp.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@dp.message(Command("расписание", "raspisanie", "schedule"))
async def cmd_schedule(message: Message) -> None:
    await handle_schedule(message, message.text or "")


@dp.message(F.text.regexp(COMMAND_RE, flags=2))  # 2 = re.IGNORECASE
async def bang_command(message: Message) -> None:
    await handle_schedule(message, message.text or "")


async def main() -> None:
    errors = validate()
    if errors:
        raise SystemExit("Ошибки конфигурации:\n- " + "\n- ".join(errors))
    global BOT_LOCK
    BOT_LOCK = acquire_single_instance_lock()
    log.info("Запуск TG-бота расписания (группа %s)", config.TARGET_GROUP)
    # Связь с api.telegram.org бывает недоступна (и тогда бот падал на старте).
    # Теперь просто ждём и повторяем, пока сеть не появится.
    while True:
        # Сессия новая на каждый цикл: старую в finally закрываем.
        # TG_PROXY — только для api.telegram.org; сайт колледжа ходит напрямую.
        session = AiohttpSession(proxy=TG_PROXY) if TG_PROXY else None
        bot = Bot(TG_BOT_TOKEN, session=session)
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
            return
        except TelegramNetworkError as exc:
            log.warning("Нет связи с api.telegram.org (%s). Повтор через 15 с…",
                        type(exc).__name__)
            await asyncio.sleep(15)
        finally:
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
