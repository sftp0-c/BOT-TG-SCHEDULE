"""Точка входа Telegram-бота расписания (aiogram 3, polling).

Команды пользователей:
  /расписание | !расписание           — на день; после CUTOFF_HOUR_EKB (13:00 Екб)
                                        автоматически «завтра».
  /расписание завтра                  — следующий день.
  /расписание сегодня                 — сегодняшний день.
  /расписание послезавтра             — через день.
  /расписание 21.10 | 21.10.2026      — на конкретную дату.
  /расписание неделя | все            — столбец группы на всю неделю из PDF.

Команды администратора (чувствительные команды работают только в ЛС):
  /admin                              — сводка и справка по управлению.
  /users                              — список пользователей (ID, @username, статусы).
  /ban <id|@user> [срок] [причина]    — ограничить доступ (например: 30m, 2h, 7d или навсегда).
  /unban <id|@user>                   — снять ограничение доступа.
  /bans                               — список активных ограничений.
  /autoschedule on|off [chat_id]      — вкл/выкл воскресный автопостинг с закрепом.
  /weekly_chats                       — список групп автопостинга и проверка прав закрепа.
  /weekly_now [test|current|all]      — ручной запуск недельной рассылки.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import shutil
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
from aiogram import BaseMiddleware, Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatType
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramUnauthorizedError,
)
from aiogram.filters import Command
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    TelegramObject,
)
from aiogram.utils.media_group import MediaGroupBuilder

import config
from clock import (
    TargetDay,
    format_ban_until,
    next_monday,
    now as ekb_now,
    parse_duration,
    resolve_day,
    week_range,
    weekday_name,
)
from commands import ScheduleArgumentError, parse_command
from config import validate
from schedule_render import RenderOut, build_images
from schedule_render import caption as render_caption
from schedule_source import download_pdf, fetch_schedule_file, validate_pdf_bytes
from state import STATE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("tg-schedule")

dp = Dispatcher()

TEMP_ROOT = Path(tempfile.gettempdir()) / "tg-schedule-bot"
LOCK_PATH = Path(tempfile.gettempdir()) / "tg-schedule-bot.lock"
KEEP_AGE_SECONDS = 15 * 60
COMMAND_RE = r"^\s*[!/](?:расписание|raspisanie|schedule)(?:@[a-zA-Z0-9_]+)?\b"
BOT_LOCK: io.TextIOWrapper | None = None


@dataclass(frozen=True, slots=True)
class AntiSpamLease:
    chat_id: int
    user_id: int
    target_key: str
    token: object


class AntiSpamGuard:
    """Защита от спама командами и кнопками расписания."""

    def __init__(self) -> None:
        self._in_flight: dict[tuple[int, str], object] = {}
        self._user_last_req: dict[int, tuple[float, object]] = {}
        self._user_last_warn: dict[int, float] = {}
        self._chat_last_req: dict[int, tuple[float, object]] = {}
        self._chat_last_target: dict[tuple[int, str], tuple[float, object]] = {}
        self._ban_notice_ts: dict[int, float] = {}

    def should_notify_ban(self, user_id: int, now_ts: float | None = None) -> bool:
        ts = now_ts if now_ts is not None else time.monotonic()
        last = self._ban_notice_ts.get(user_id, 0.0)
        if ts - last >= 300.0:
            self._ban_notice_ts[user_id] = ts
            return True
        return False

    def acquire(
        self,
        *,
        chat_id: int,
        user_id: int,
        target_key: str,
        is_admin: bool = False,
        now_ts: float | None = None,
    ) -> tuple[AntiSpamLease | None, str]:
        """Проверяет лимиты и возвращает lease, который должен быть освобождён."""
        ts = now_ts if now_ts is not None else time.monotonic()
        flight_key = (chat_id, target_key)

        if flight_key in self._in_flight:
            return None, "⏳ Это расписание уже загружается, подождите пару секунд…"

        if not is_admin:
            user_cd = config.USER_COOLDOWN_SEC
            if user_cd > 0:
                last_u = self._user_last_req.get(user_id)
                if last_u is not None and (ts - last_u[0]) < user_cd:
                    wait = max(1, int(round(user_cd - (ts - last_u[0]))))
                    return None, f"⏳ Не так быстро! Повторный запрос через {wait} сек."

            if chat_id < 0:
                same_cd = config.SAME_TARGET_COOLDOWN_SEC
                if same_cd > 0:
                    last_same = self._chat_last_target.get((chat_id, target_key))
                    if last_same is not None and (ts - last_same[0]) < same_cd:
                        wait = max(1, int(round(same_cd - (ts - last_same[0]))))
                        return (
                            None,
                            f"⏳ Это расписание уже отправляли чуть выше 👆 Повтор через {wait} сек.",
                        )

                chat_cd = config.CHAT_COOLDOWN_SEC
                if chat_cd > 0:
                    last_c = self._chat_last_req.get(chat_id)
                    if last_c is not None and (ts - last_c[0]) < chat_cd:
                        wait = max(1, int(round(chat_cd - (ts - last_c[0]))))
                        return None, f"⏳ В чате действует антиспам-пауза ({wait} сек.)."

        token = object()
        self._in_flight[flight_key] = token
        self._user_last_req[user_id] = (ts, token)
        self._chat_last_req[chat_id] = (ts, token)
        self._chat_last_target[(chat_id, target_key)] = (ts, token)
        return AntiSpamLease(chat_id, user_id, target_key, token), ""

    def check_and_acquire(
        self,
        **kwargs: Any,
    ) -> tuple[bool, str]:
        """Совместимая обёртка для старых вызовов; новые используют ``acquire``."""
        lease, reason = self.acquire(**kwargs)
        return lease is not None, reason

    @staticmethod
    def _drop_if_owned(mapping: dict[Any, tuple[float, object]], key: Any, token: object) -> None:
        current = mapping.get(key)
        if current is not None and current[1] is token:
            mapping.pop(key, None)

    def release(self, lease: AntiSpamLease, *, failed: bool = False) -> None:
        flight_key = (lease.chat_id, lease.target_key)
        if self._in_flight.get(flight_key) is not lease.token:
            return
        self._in_flight.pop(flight_key, None)
        if failed:
            self._drop_if_owned(self._chat_last_target, flight_key, lease.token)
            self._drop_if_owned(self._user_last_req, lease.user_id, lease.token)
            self._drop_if_owned(self._chat_last_req, lease.chat_id, lease.token)

    def should_send_text_warning(self, user_id: int, now_ts: float | None = None) -> bool:
        """Не позволяет боту самому спамить предупреждениями при флуде командами."""
        ts = now_ts if now_ts is not None else time.monotonic()
        last = self._user_last_warn.get(user_id, 0.0)
        if ts - last >= 10.0:
            self._user_last_warn[user_id] = ts
            return True
        return False


ANTISPAM = AntiSpamGuard()
RENDER_SEMAPHORE = asyncio.Semaphore(1)
WEEKLY_PUBLISH_LOCK = asyncio.Lock()


async def _build_images_async(
    data: bytes,
    target: date,
    req_dir: Path,
    *,
    week: bool,
    week_start: date | None = None,
) -> RenderOut:
    async with RENDER_SEMAPHORE:
        return await asyncio.to_thread(
            build_images,
            data,
            target,
            req_dir,
            week=week,
            week_start=week_start,
        )


class AccessAndTrackingMiddleware(BaseMiddleware):
    """Перечитывает .env, сохраняет профиль пользователя/чата и блокирует забаненных."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        config.reload()

        user = getattr(event, "from_user", None)
        chat = None
        if isinstance(event, Message):
            chat = event.chat
        elif isinstance(event, CallbackQuery) and event.message is not None:
            chat = event.message.chat

        is_private = bool(chat and chat.type == ChatType.PRIVATE)

        if user is not None and not user.is_bot:
            STATE.touch_user(
                user.id,
                username=user.username,
                full_name=user.full_name,
                is_private=is_private,
            )

        if chat is not None and chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            STATE.touch_chat(chat.id, chat.title or "")

        if user is not None and not config.is_admin(user.id):
            ban = STATE.get_active_ban(user.id)
            if ban is not None:
                until_text = format_ban_until(ban.until)
                reason_part = f" Причина: {ban.reason}" if ban.reason else ""
                if isinstance(event, CallbackQuery):
                    with suppress(Exception):
                        await event.answer(
                            f"⛔ Доступ к боту ограничен ({until_text}).{reason_part}",
                            show_alert=True,
                        )
                    return None
                if isinstance(event, Message) and is_private:
                    if ANTISPAM.should_notify_ban(user.id):
                        with suppress(Exception):
                            await event.answer(
                                f"⛔ Вам ограничен доступ к боту ({until_text}).{reason_part}"
                            )
                return None

        return await handler(event, data)


dp.message.outer_middleware(AccessAndTrackingMiddleware())
dp.callback_query.outer_middleware(AccessAndTrackingMiddleware())


def help_text(*, is_admin_user: bool = False) -> str:
    cutoff = config.CUTOFF_HOUR_EKB
    group = config.TARGET_GROUP
    base = (
        f"📅 Расписание группы {group}.\n\n"
        "Команды:\n"
        f"• /расписание — на день (после {cutoff:02d}:00 по Екб автоматически «завтра»)\n"
        "• /расписание сегодня — на сегодняшний день\n"
        "• /расписание завтра — на следующий день\n"
        "• /расписание послезавтра — через день\n"
        "• /расписание 21.10 — на конкретную дату (можно 21.10.2026)\n"
        "• /расписание неделя (или все) — вся неделя из PDF альбомом\n\n"
        "Вместо «/» можно писать «!»: !расписание завтра.\n"
        "В личных сообщениях можно писать просто: сегодня, завтра, неделя или 21.10."
    )
    if is_admin_user:
        base += (
            "\n\n👑 Админ-команды (работают прямо здесь в ЛС):\n"
            "• /admin — сводка по боту и чатам\n"
            "• /users — список пользователей (ID и @username)\n"
            "• /ban <id|@user> [срок: 30m/2h/7d] [причина] — ограничить доступ\n"
            "• /unban <id|@user> — снять ограничение\n"
            "• /bans — список активных ограничений\n"
            "• /autoschedule on|off [chat_id] — вкл/выкл воскресный пост + закреп\n"
            "• /weekly_chats — группы воскресной рассылки и статус прав закрепа\n"
            "• /weekly_now [test|current|all] — ручной запуск недельной рассылки"
        )
    return base


def schedule_keyboard(owner_id: int | None = None) -> InlineKeyboardMarkup:
    suffix = f":{owner_id}" if owner_id is not None else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Сегодня", callback_data=f"sched:today{suffix}"),
                InlineKeyboardButton(text="➡️ Завтра", callback_data=f"sched:tomorrow{suffix}"),
                InlineKeyboardButton(text="🗓 Неделя", callback_data=f"sched:week{suffix}"),
            ]
        ]
    )


def admin_menu_kb(admin_id: int) -> InlineKeyboardMarkup:
    """Меню управления ботом администратора через inline-кнопки (в личке админа)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅 Сегодня", callback_data=f"sched:today:{admin_id}"),
                InlineKeyboardButton(text="➡️ Завтра", callback_data=f"sched:tomorrow:{admin_id}"),
                InlineKeyboardButton(text="🗓 Неделя", callback_data=f"sched:week:{admin_id}"),
            ],
            [
                InlineKeyboardButton(text="📋 Панель админа", callback_data="admin:panel"),
                InlineKeyboardButton(text="👥 Пользователи", callback_data="admin:users"),
                InlineKeyboardButton(text="⛔ Баны", callback_data="admin:bans"),
            ],
        ]
    )


def acquire_single_instance_lock() -> io.TextIOWrapper:
    """Кроссплатформенный lock-файл одной копии бота (Linux fcntl / Windows msvcrt)."""
    lock = open(LOCK_PATH, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock.close()
        raise SystemExit(
            "Бот уже запущен — вторая копия не нужна (файл-замок занят)."
        ) from exc
    return lock


def cleanup_stale(root: Path) -> None:
    """Удаляет зависшие временные подкаталоги старше KEEP_AGE_SECONDS."""
    if not root.exists():
        return
    now = time.time()
    for entry in root.iterdir():
        try:
            if now - entry.stat().st_mtime > KEEP_AGE_SECONDS:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
        except OSError:
            continue


def _target_key(target: TargetDay) -> str:
    return "week" if target.label == "неделя" else target.day.isoformat()


def _admit_schedule_request(
    message: Message,
    target: TargetDay,
    *,
    requester_id: int | None = None,
) -> tuple[AntiSpamLease | None, str]:
    config.reload()
    uid = requester_id if requester_id is not None else (
        message.from_user.id if message.from_user else 0
    )
    return ANTISPAM.acquire(
        chat_id=message.chat.id,
        user_id=uid,
        target_key=_target_key(target),
        is_admin=config.is_admin(uid),
    )


async def _send_schedule(
    message: Message,
    target: TargetDay,
    *,
    requester_id: int | None = None,
) -> tuple[bool, str]:
    """Получает anti-spam lease и отправляет расписание обычному сообщению."""
    lease, deny_reason = _admit_schedule_request(
        message,
        target,
        requester_id=requester_id,
    )
    uid = requester_id if requester_id is not None else (
        message.from_user.id if message.from_user else 0
    )
    if lease is None:
        if uid and ANTISPAM.should_send_text_warning(uid):
            with suppress(TelegramBadRequest):
                await message.reply(deny_reason)
        return False, deny_reason

    await _send_schedule_admitted(message, target, lease)
    return True, ""


async def _send_schedule_admitted(
    message: Message,
    target: TargetDay,
    lease: AntiSpamLease,
) -> None:
    """Отправляет расписание по уже полученному anti-spam lease."""
    uid = lease.user_id
    kb = schedule_keyboard(owner_id=uid if uid else None)
    failed = True

    try:
        if target.label == "неделя":
            status_text = f"⏳ Собираю расписание {config.TARGET_GROUP} на всю неделю…"
        else:
            day = target.day
            status_text = (
                f"⏳ Смотрю расписание {config.TARGET_GROUP} на {target.label} "
                f"({weekday_name(day)}, {day:%d.%m.%Y})…"
            )
        status_msg = await message.answer(status_text)

        async def finish_error(text: str) -> None:
            with suppress(TelegramBadRequest):
                await status_msg.edit_text(text, reply_markup=kb)
                return
            await message.answer(text, reply_markup=kb)

        try:
            found = await fetch_schedule_file()
        except FileNotFoundError as exc:
            log.warning("SCHEDULE_LOCAL_PDF не найден: %s", exc)
            await finish_error(
                "В .env включён тестовый файл (SCHEDULE_LOCAL_PDF), но по этому пути файла нет:\n"
                f"{exc}\n\nИсправьте путь или закомментируйте строку символом # — "
                "бот снова будет читать сайт."
            )
            return True, ""
        except httpx.HTTPError as exc:
            log.warning("Сайт расписаний недоступен: %s", exc)
            await finish_error(
                "Сайт колледжа сейчас недоступен (не отвечает или лежит).\n"
                "Как только он заработает — просто нажми кнопку ниже или повтори команду.\n"
                "При необходимости можно указать прямую ссылку SCHEDULE_DIRECT_URL в .env."
            )
            return True, ""
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось найти файл расписания")
            await finish_error(
                "Не удалось получить ссылку на расписание.\n"
                f"Причина: {type(exc).__name__}\n"
                "Проверьте SCHEDULE_PAGE_URL / SCHEDULE_DIRECT_URL в .env."
            )
            return True, ""

        if not found:
            await finish_error(
                "Подходящий PDF для группы "
                f"{config.TARGET_GROUP} не нашёлся на сайте.\n"
                "Проверьте TARGET_GROUP / SCHEDULE_PDF_PATTERN или укажите "
                "SCHEDULE_DIRECT_URL в .env."
            )
            return True, ""

        if found.path is not None:
            try:
                data = validate_pdf_bytes(await asyncio.to_thread(found.path.read_bytes))
            except (OSError, ValueError) as exc:
                log.warning("Локальный файл %s не читается: %s", found.name, exc)
                await finish_error(
                    f"Тестовый файл есть, но прочитать его не удалось:\n{found.url}\n\nПричина: {exc}"
                )
                return True, ""
        else:
            try:
                data = await download_pdf(found.url)
            except Exception as exc:  # noqa: BLE001
                log.exception("Не удалось скачать PDF %s", found.url)
                await finish_error(
                    f"Файл найден, но скачать его не удалось: {found.name}\n"
                    f"{found.url}\n\nПричина: {type(exc).__name__}"
                )
                return True, ""

        await asyncio.to_thread(cleanup_stale, TEMP_ROOT)
        req_dir = TEMP_ROOT / f"req_{uuid.uuid4().hex[:8]}"
        try:
            is_week = target.label == "неделя"
            out = await _build_images_async(
                data,
                target.day,
                req_dir,
                week=is_week,
            )
            if not out.ok or not out.images:
                reasons = "; ".join(out.notes) or "не удалось подготовить изображение"
                await finish_error(
                    f"Пока не получилось собрать картинку: {reasons}\n"
                    f"Файл: {found.name} — {found.url}"
                )
                return True, ""

            text_out = render_caption(out, target.day, target.label, found.name)
            photos = [FSInputFile(item.path) for item in out.images]
            if len(photos) == 1:
                await message.answer_photo(
                    photos[0], caption=text_out, reply_markup=kb
                )
            else:
                builder = MediaGroupBuilder(caption=text_out)
                for photo in photos:
                    builder.add_photo(photo)
                await message.answer_media_group(builder.build())
                await message.answer("Быстрый выбор:", reply_markup=kb)

            with suppress(TelegramBadRequest):
                await status_msg.delete()
            failed = False
            return True, ""
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка при сборке или отправке изображения")
            await finish_error(
                f"Картинки собраны, но отправить их не получилось ({type(exc).__name__})."
            )
            return True, ""
        finally:
            await asyncio.to_thread(shutil.rmtree, req_dir, ignore_errors=True)
    finally:
        ANTISPAM.release(lease, failed=failed)


# --- Воскресная рассылка на следующую неделю + закреп ---


async def publish_weekly_schedule(
    bot: Bot,
    *,
    target_monday: date | None = None,
    require_target_week: bool = True,
    notify_chat_id: int | None = None,
    test_only: bool = False,
) -> tuple[bool, str]:
    """Сериализует ручную и автоматическую недельную публикацию."""
    if WEEKLY_PUBLISH_LOCK.locked():
        return False, "Недельная рассылка уже выполняется."
    async with WEEKLY_PUBLISH_LOCK:
        return await _publish_weekly_schedule(
            bot,
            target_monday=target_monday,
            require_target_week=require_target_week,
            notify_chat_id=notify_chat_id,
            test_only=test_only,
        )


async def _publish_weekly_schedule(
    bot: Bot,
    *,
    target_monday: date | None = None,
    require_target_week: bool = True,
    notify_chat_id: int | None = None,
    test_only: bool = False,
) -> tuple[bool, str]:
    """Собирает расписание и публикует его в подключённые группы с закрепом.

    Если ``require_target_week=True``, проверяет, что даты в PDF попадают в неделю ``target_monday``.
    """
    config.reload()
    monday = target_monday if target_monday is not None else next_monday()
    sunday = monday + timedelta(days=6)

    try:
        found = await fetch_schedule_file()
    except Exception as exc:  # noqa: BLE001
        log.warning("Недельная рассылка: ошибка поиска PDF: %s", exc)
        return False, f"Ошибка поиска PDF на сайте: {type(exc).__name__}"

    if not found:
        return False, "Подходящий PDF не найден на сайте."

    try:
        if found.path is not None:
            data = validate_pdf_bytes(found.path.read_bytes())
        else:
            data = await download_pdf(found.url)
    except Exception as exc:  # noqa: BLE001
        log.warning("Недельная рассылка: ошибка скачивания PDF: %s", exc)
        return False, f"Ошибка скачивания PDF ({found.name}): {type(exc).__name__}"

    await asyncio.to_thread(cleanup_stale, TEMP_ROOT)
    req_dir = TEMP_ROOT / f"weekly_{uuid.uuid4().hex[:8]}"
    try:
        out = await _build_images_async(
            data,
            monday,
            req_dir,
            week=True,
            week_start=(monday if require_target_week else None),
        )
        if not out.ok or not out.images:
            reason = "; ".join(out.notes) or "не удалось собрать расписание недели"
            return False, reason

        first_day = out.images[0].day
        last_day = out.images[-1].day
        actual_monday = first_day - timedelta(days=first_day.weekday())
        is_next_week = actual_monday >= monday
        title_prefix = (
            "📅 Расписание на следующую неделю"
            if is_next_week
            else "📅 Расписание на неделю"
        )
        caption_text = (
            f"{title_prefix} ({first_day:%d.%m.%Y}–{last_day:%d.%m.%Y}) — "
            f"группа {out.requested_group}\n"
            f"Источник: {found.name}"
        )

        # Режим проверки только админу в ЛС
        if test_only and notify_chat_id is not None:
            photos = [FSInputFile(item.path) for item in out.images]
            if len(photos) == 1:
                await bot.send_photo(notify_chat_id, photos[0], caption=caption_text)
            else:
                builder = MediaGroupBuilder(caption=caption_text)
                for photo in photos:
                    builder.add_photo(photo)
                await bot.send_media_group(notify_chat_id, builder.build())
            return True, f"Тестовое расписание ({first_day:%d.%m}–{last_day:%d.%m}) отправлено в ЛС."

        chat_ids = [
            cid
            for cid in STATE.get_weekly_chat_ids()
            if STATE.chats.get(cid) is None
            or STATE.chats[cid].last_sent_week_monday != actual_monday.isoformat()
        ]

        group_ok = 0
        pin_ok = 0
        group_errors: list[str] = []

        for cid in chat_ids:
            try:
                photos = [FSInputFile(item.path) for item in out.images]
                if len(photos) == 1:
                    sent = await bot.send_photo(cid, photos[0], caption=caption_text)
                    pin_msg_id = sent.message_id
                else:
                    builder = MediaGroupBuilder(caption=caption_text)
                    for photo in photos:
                        builder.add_photo(photo)
                    msgs = await bot.send_media_group(cid, builder.build())
                    pin_msg_id = msgs[0].message_id if msgs else None

                group_ok += 1
                STATE.record_chat_pin(
                    cid,
                    None,
                    actual_monday.isoformat(),
                )
                old_pin = None
                known_chat = STATE.chats.get(cid)
                if known_chat is not None:
                    old_pin = known_chat.last_pinned_message_id
                if pin_msg_id is not None:
                    try:
                        await bot.pin_chat_message(
                            chat_id=cid,
                            message_id=pin_msg_id,
                            disable_notification=True,
                        )
                        pin_ok += 1
                        STATE.record_chat_pin(
                            cid,
                            pin_msg_id,
                            actual_monday.isoformat(),
                        )
                        if old_pin and old_pin != pin_msg_id:
                            with suppress(Exception):
                                await bot.unpin_chat_message(
                                    chat_id=cid,
                                    message_id=old_pin,
                                )
                    except Exception as pin_exc:  # noqa: BLE001
                        log.warning("Не удалось закрепить сообщение в чате %s: %s", cid, pin_exc)
                        group_errors.append(f"чат {cid}: отправлено, но нет прав на закреп")
            except Exception as send_exc:  # noqa: BLE001
                log.warning("Ошибка отправки расписания в чат %s: %s", cid, send_exc)
                group_errors.append(f"чат {cid}: {type(send_exc).__name__}")

        unresolved = len(chat_ids) - group_ok
        all_delivered = unresolved == 0
        if require_target_week and all_delivered:
            STATE.set_weekly_sent_monday(monday.isoformat())

        status_icon = "✅" if all_delivered else ("⚠️" if group_ok else "❌")
        summary = (
            f"{status_icon} Недельная публикация ({first_day:%d.%m.%Y}–{last_day:%d.%m.%Y}) завершена.\n"
            f"• Групп: {group_ok}/{len(chat_ids)} (закреплено: {pin_ok})"
        )
        if not chat_ids:
            summary += "\nℹ️ Для этой недели нет новых групп: все уже получили расписание или автопостинг не подключён."
        elif not all_delivered:
            summary += "\n⏳ Не все группы получили расписание — автоматическая попытка будет повторена."
        if group_errors:
            summary += "\n⚠️ Замечания: " + "; ".join(group_errors)
        return all_delivered, summary
    finally:
        await asyncio.to_thread(shutil.rmtree, req_dir, ignore_errors=True)


async def weekly_scheduler_loop(bot: Bot) -> None:
    """Фоновая задача: по воскресеньям в 18:00 публикует следующую неделю и ставит в закреп."""
    last_attempt_ts = 0.0
    last_not_ready_monday: str | None = None

    while True:
        try:
            await asyncio.sleep(30)
            config.reload()
            current = ekb_now()
            if current.weekday() != config.WEEKLY_SEND_WEEKDAY:
                continue
            if (current.hour, current.minute) < (
                config.WEEKLY_SEND_HOUR,
                config.WEEKLY_SEND_MINUTE,
            ):
                continue

            target_mon = next_monday(current.date())
            mon_iso = target_mon.isoformat()
            if STATE.get_weekly_sent_monday() == mon_iso:
                continue

            now_mono = time.monotonic()
            # Если файл на следующую неделю ещё не выложили — повторяем раз в 15 минут
            if now_mono - last_attempt_ts < 900:
                continue
            last_attempt_ts = now_mono

            log.info("Запуск воскресной рассылки на неделю с %s…", mon_iso)
            ok, report = await publish_weekly_schedule(
                bot, target_monday=target_mon, require_target_week=True
            )
            if ok:
                log.info("Воскресная рассылка завершена: %s", report.replace("\n", " | "))
                for admin_id in config.ADMIN_IDS:
                    with suppress(Exception):
                        await bot.send_message(admin_id, f"🗓 Авторассылка в воскресенье:\n{report}")
            else:
                log.info("Воскресная рассылка отложена (повтор через 15 мин): %s", report)
                if last_not_ready_monday != mon_iso:
                    last_not_ready_monday = mon_iso
                    for admin_id in config.ADMIN_IDS:
                        with suppress(Exception):
                            await bot.send_message(
                                admin_id,
                                f"⏳ Воскресная рассылка на {target_mon:%d.%m.%Y}: расписание на сайте ещё не обновили.\n"
                                f"Причина: {report}\n"
                                "Бот будет проверять сайт каждые 15 минут и отправит автоматически.",
                            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка в планировщике воскресной рассылки: %s", exc)


# --- Вспомогательный разбор целей для админ-команд ---


def _extract_target_user(message: Message, args_text: str) -> tuple[int | None, str, list[str]]:
    """Извлекает целевого пользователя из аргументов или из reply_to_message.

    Возвращает (user_id, display_name_или_ошибка, оставшиеся_токены_аргументов).
    """
    tokens = [tok for tok in (args_text or "").strip().split() if tok]

    if tokens:
        first = tokens[0]
        looks_like_target = first.lstrip("-").isdigit() or first.startswith("@")
        if looks_like_target:
            uid, label = STATE.resolve_user(first)
            return uid, label, tokens[1:]

    if message.reply_to_message and message.reply_to_message.from_user:
        target_u = message.reply_to_message.from_user
        if not target_u.is_bot:
            rec = STATE.touch_user(
                target_u.id,
                username=target_u.username,
                full_name=target_u.full_name,
            )
            return target_u.id, rec.display_name, tokens

    if not tokens:
        return None, "Укажите ID или @username пользователя (например: /ban 123456789 2h или /ban @username).", []

    uid, label = STATE.resolve_user(tokens[0])
    return uid, label, tokens[1:]


def _command_args(message: Message) -> str:
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


# --- Пользовательские команды ---


@dp.message(Command("start", "help"))
async def cmd_help(message: Message) -> None:
    uid = message.from_user.id if message.from_user else None
    keyboard = schedule_keyboard(owner_id=uid)
    if message.chat.type == ChatType.PRIVATE and _is_admin_user(uid):
        keyboard = admin_menu_kb(uid or 0)
    await message.answer(
        help_text(is_admin_user=_is_admin_user(uid)),
        reply_markup=keyboard,
    )


# --- Админ-команды (удобно вызывать в ЛС боту) ---


def _is_admin_user(user_id: int | None) -> bool:
    return bool(user_id) and config.is_admin(user_id)


def _is_admin_message(message: Message) -> bool:
    """Проверяет только Telegram ID администратора, независимо от типа чата."""
    return bool(message.from_user and config.is_admin(message.from_user.id))


def _check_admin_message(message: Message) -> bool:
    """Чувствительные админ-команды доступны только в личном чате."""
    return bool(_is_admin_message(message) and message.chat.type == ChatType.PRIVATE)


def _admin_panel_text() -> str:
    users = STATE.list_users(limit=500)
    bans = STATE.list_active_bans()
    weekly_chats = STATE.get_weekly_chat_ids()
    mon = next_monday()
    sent_mon = STATE.get_weekly_sent_monday()

    lines = [
        "👑 Панель администратора",
        f"• Группа: {config.TARGET_GROUP}",
        f"• Известно пользователей: {len(users)}",
        f"• Активных ограничений доступа: {len(bans)}",
        f"• Чатов для воскресного закрепа (Вс {config.WEEKLY_SEND_HOUR:02d}:{config.WEEKLY_SEND_MINUTE:02d}): {len(weekly_chats)}",
        f"• Ближайший понедельник: {mon:%d.%m.%Y} (рассылка: {'выполнена' if sent_mon == mon.isoformat() else 'ожидается'})",
        f"• Антиспам: юзер {int(config.USER_COOLDOWN_SEC)}с / чат {int(config.CHAT_COOLDOWN_SEC)}с / повтор {int(config.SAME_TARGET_COOLDOWN_SEC)}с",
        "",
        "Команды управления в ЛС:",
        "• /users — список пользователей с ID и @username",
        "• /ban <id|@user> [30m|2h|7d] [причина] — ограничить доступ",
        "• /unban <id|@user> — снять ограничение доступа",
        "• /bans — список заблокированных",
        "• /weekly_chats — список групп и статус закрепа",
        "• /autoschedule on|off [chat_id] — настроить автопостинг группы",
        "• /weekly_now [test|current|all] — запустить недельную рассылку вручную",
    ]

    presses = STATE.list_button_presses(limit=10)
    lines.append("")
    lines.append("🔘 Последние нажатия inline-кнопок:")
    if not presses:
        lines.append("• (пока нет — кнопки ещё никто не нажимал)")
    for p in presses:
        actor = f"@{p.username}" if p.username else (p.full_name or f"[{p.user_id}]")
        result = "✅ OK" if p.allowed else f"⛔ {p.deny_reason or 'откл.'}"
        lines.append(
            f"• {time.strftime('%H:%M:%S', time.localtime(p.ts))} — кнопка «{p.kind}», "
            f"кто: {actor} (id {p.user_id}), чат {p.chat_id}, owner {p.owner_id} → {result}"
        )

    return "\n".join(lines)


@dp.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not _check_admin_message(message):
        return
    await message.answer(_admin_panel_text())


def _admin_users_text(limit: int = 40) -> str:
    users = STATE.list_users(limit=limit)
    if not users:
        return "Пока в базе бота нет пользователей."

    lines = [f"👥 Пользователи бота (последние {limit}):"]
    for u in users:
        ban = STATE.get_active_ban(u.user_id)
        if ban:
            status = f"⛔ БАН ({format_ban_until(ban.until)})"
        elif u.private_started:
            status = "💬 писал боту в ЛС"
        else:
            status = "💬 только в группе"
        admin_tag = " 👑" if config.is_admin(u.user_id) else ""
        lines.append(f"• {u.display_name}{admin_tag} — {status}")

    return "\n".join(lines)


@dp.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not _check_admin_message(message):
        return
    await message.answer(_admin_users_text())


@dp.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    if not _check_admin_message(message) or not message.from_user:
        return
    args_text = _command_args(message)
    uid, label, rest = _extract_target_user(message, args_text)
    if uid is None:
        await message.answer(f"⚠️ {label}")
        return
    if config.is_admin(uid):
        await message.answer("⚠️ Нельзя ограничить доступ администратору бота.")
        return

    duration = None
    reason_tokens = rest
    if rest:
        first = rest[0].lower()
        if first in {"навсегда", "forever", "permanent"}:
            reason_tokens = rest[1:]
        else:
            parsed_dur = parse_duration(first)
            if parsed_dur is not None:
                duration = parsed_dur
                reason_tokens = rest[1:]
            elif first[:1].isdigit():
                await message.answer(
                    "⚠️ Некорректный срок. Примеры: 30m, 2h, 7d, 1w или «навсегда»."
                )
                return
    reason = " ".join(reason_tokens).strip()

    rec = STATE.ban_user(
        uid,
        duration=duration,
        reason=reason,
        banned_by=message.from_user.id,
    )
    until_str = format_ban_until(rec.until)
    reason_str = f"\nПричина: {rec.reason}" if rec.reason else ""
    await message.answer(
        f"⛔ Доступ к боту ограничен для {label}.\n"
        f"Срок: {until_str}{reason_str}"
    )


@dp.message(Command("unban"))
async def cmd_unban(message: Message) -> None:
    if not _check_admin_message(message):
        return
    args_text = _command_args(message)
    uid, label, _rest = _extract_target_user(message, args_text)
    if uid is None:
        await message.answer(f"⚠️ {label}")
        return
    removed = STATE.unban_user(uid)
    if removed:
        await message.answer(f"✅ Ограничение доступа снято для {label}.")
    else:
        await message.answer(f"ℹ️ У пользователя {label} не было активного ограничения.")


def _admin_bans_text() -> str:
    bans = STATE.list_active_bans()
    if not bans:
        return "✅ Активных ограничений доступа нет."
    lines = ["⛔ Активные ограничения доступа:"]
    for b in bans:
        user_rec = STATE.get_user(b.user_id)
        name = user_rec.display_name if user_rec else f"[{b.user_id}]"
        reason_part = f" — {b.reason}" if b.reason else ""
        lines.append(f"• {name}: {format_ban_until(b.until)}{reason_part}")
    return "\n".join(lines)


@dp.message(Command("bans"))
async def cmd_bans(message: Message) -> None:
    if not _check_admin_message(message):
        return
    await message.answer(_admin_bans_text())


@dp.message(Command("autoschedule"))
async def cmd_autoschedule(message: Message, bot: Bot) -> None:
    if not _is_admin_message(message):
        return
    tokens = [t for t in _command_args(message).split() if t]
    if not tokens or tokens[0].lower() not in {"on", "off", "вкл", "выкл"}:
        await message.answer(
            "Использование:\n"
            "• В группе: /autoschedule on или /autoschedule off\n"
            "• В ЛС боту: /autoschedule on -1001234567890"
        )
        return

    enable = tokens[0].lower() in {"on", "вкл"}
    target_chat_id: int
    target_title = ""

    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        if len(tokens) != 1:
            await message.answer(
                "⚠️ В группе команда настраивает только текущий чат: "
                "/autoschedule on или /autoschedule off"
            )
            return
        target_chat_id = message.chat.id
        target_title = message.chat.title or ""
    else:
        if len(tokens) != 2:
            await message.answer(
                "⚠️ В ЛС укажите отрицательный ID группы: "
                "/autoschedule on -1001234567890"
            )
            return
        try:
            target_chat_id = int(tokens[1])
        except ValueError:
            await message.answer("⚠️ ID группы должен быть целым отрицательным числом.")
            return
        if target_chat_id >= 0:
            await message.answer("⚠️ Telegram ID группы должен быть отрицательным.")
            return

        if enable:
            try:
                target_chat = await bot.get_chat(target_chat_id)
            except (TelegramBadRequest, TelegramForbiddenError):
                await message.answer(
                    "⚠️ Бот не видит этот чат. Сначала добавьте бота в группу."
                )
                return
            if target_chat.type not in {ChatType.GROUP, ChatType.SUPERGROUP}:
                await message.answer("⚠️ Указанный ID не является группой.")
                return
            target_title = target_chat.title or ""

    rec = STATE.set_chat_weekly(target_chat_id, enabled=enable, title=target_title)
    title_str = f"«{rec.title}» " if rec.title else ""
    if enable:
        await message.answer(
            f"✅ Воскресная рассылка (в {config.WEEKLY_SEND_HOUR:02d}:{config.WEEKLY_SEND_MINUTE:02d}) "
            f"с закрепом ВКЛЮЧЕНА для чата {title_str}[{target_chat_id}].\n"
            "Убедитесь, что бот является администратором группы с правом закреплять сообщения."
        )
    else:
        await message.answer(
            f"🔕 Воскресная рассылка ВЫКЛЮЧЕНА для чата {title_str}[{target_chat_id}]."
        )


@dp.message(Command("weekly_chats"))
async def cmd_weekly_chats(message: Message, bot: Bot) -> None:
    if not _check_admin_message(message):
        return
    active_ids = set(STATE.get_weekly_chat_ids())
    known_chats = dict(STATE.chats)
    for cid in active_ids:
        if cid not in known_chats:
            STATE.touch_chat(cid, "")
    known_chats = dict(STATE.chats)

    if not known_chats:
        await message.answer(
            "ℹ️ Бот пока не добавлен ни в одну группу.\n"
            "Добавьте бота в группу (с правом закреплять сообщения) и напишите там /autoschedule on."
        )
        return

    lines = [
        f"💬 Группы (воскресенье {config.WEEKLY_SEND_HOUR:02d}:{config.WEEKLY_SEND_MINUTE:02d}):"
    ]
    me = await bot.get_me()
    for cid, rec in sorted(known_chats.items()):
        enabled = cid in active_ids
        status = "✅ ВКЛ" if enabled else "⚪ ВЫКЛ"
        pin_status = ""
        try:
            member = await bot.get_chat_member(cid, me.id)
            can_pin = bool(getattr(member, "can_pin_messages", False)) or member.status == "creator"
            pin_status = " | закреп: ОК" if can_pin else " | ⚠️ нет прав на закреп"
        except Exception:  # noqa: BLE001
            pin_status = " | ⚠️ нет доступа к чату"
        title = rec.title or "Без названия"
        lines.append(f"• {title} [{cid}] — {status}{pin_status}")

    lines.append("\nВключить/выключить из ЛС: /autoschedule on <chat_id>")
    await message.answer("\n".join(lines))


@dp.message(Command("weekly_now"))
async def cmd_weekly_now(message: Message, bot: Bot) -> None:
    if not _check_admin_message(message):
        return
    mode = (_command_args(message).strip().lower() or "test")
    if mode not in {"test", "current", "all"}:
        await message.answer(
            "Использование:\n"
            "• /weekly_now test — прислать пробный сбор недели вам в ЛС\n"
            "• /weekly_now current — опубликовать текущую неделю в группы с закрепом\n"
            "• /weekly_now all — опубликовать следующую неделю, если она выложена"
        )
        return

    await message.answer("⏳ Запускаю сборку недельного расписания…")
    if mode == "test":
        _ok, report = await publish_weekly_schedule(
            bot,
            target_monday=next_monday(),
            require_target_week=False,
            notify_chat_id=message.chat.id,
            test_only=True,
        )
        await message.answer(report)
    elif mode == "current":
        _ok, report = await publish_weekly_schedule(
            bot,
            target_monday=next_monday(),
            require_target_week=False,
        )
        await message.answer(report)
    else:
        _ok, report = await publish_weekly_schedule(
            bot,
            target_monday=next_monday(),
            require_target_week=True,
        )
        await message.answer(report)


# --- Основные команды расписания ---


async def _parse_and_send_schedule(
    message: Message,
    *,
    allow_bare: bool,
) -> bool:
    try:
        target = parse_command(message.text or "", allow_bare=allow_bare)
    except ScheduleArgumentError as exc:
        await message.reply(
            f"⚠️ {exc}\n\nПример: /расписание завтра или /расписание 21.10"
        )
        return True
    if target is None:
        return False
    await _send_schedule(message, target)
    return True


@dp.message(Command("расписание", "raspisanie", "schedule"))
async def cmd_schedule(message: Message) -> None:
    await _parse_and_send_schedule(message, allow_bare=False)


@dp.message(F.text.regexp(COMMAND_RE, flags=2))
async def bang_command(message: Message) -> None:
    await _parse_and_send_schedule(message, allow_bare=False)


@dp.message(F.chat.type == ChatType.PRIVATE, F.text)
async def private_bare_schedule(message: Message) -> None:
    await _parse_and_send_schedule(message, allow_bare=True)


@dp.callback_query(F.data.startswith("sched:"))
async def cb_schedule(callback: CallbackQuery) -> None:
    parts = (callback.data or "").split(":")
    kind = parts[1] if len(parts) >= 2 else ""
    if kind not in {"today", "tomorrow", "week"}:
        await callback.answer("Неизвестная команда")
        return

    owner_id: int | None = None
    if len(parts) >= 3 and parts[2].lstrip("-").isdigit():
        owner_id = int(parts[2])

    clicker = callback.from_user
    clicker_id = clicker.id if clicker else 0
    chat_id = callback.message.chat.id if callback.message is not None else 0

    if owner_id is not None and clicker_id != owner_id:
        await callback.answer(
            "⛔ Это меню вызвал другой пользователь.\nНапишите /расписание, чтобы открыть своё.",
            show_alert=True,
        )
        STATE.record_button_press(
            user_id=clicker_id,
            username=clicker.username if clicker else None,
            full_name=clicker.full_name if clicker else None,
            chat_id=chat_id,
            kind=kind,
            owner_id=owner_id,
            allowed=False,
            deny_reason="menu_owner_mismatch",
        )
        return

    if callback.message is None:
        await callback.answer()
        return

    target = resolve_day(kind)
    lease, deny_reason = _admit_schedule_request(
        callback.message,
        target,
        requester_id=clicker_id,
    )
    allowed = lease is not None
    if lease is None:
        await callback.answer(deny_reason, show_alert=False)
        STATE.record_button_press(
            user_id=clicker_id,
            username=clicker.username if clicker else None,
            full_name=clicker.full_name if clicker else None,
            chat_id=chat_id,
            kind=kind,
            owner_id=owner_id,
            allowed=False,
            deny_reason=deny_reason,
        )
        return

    try:
        await callback.answer()
        STATE.record_button_press(
            user_id=clicker_id,
            username=clicker.username if clicker else None,
            full_name=clicker.full_name if clicker else None,
            chat_id=chat_id,
            kind=kind,
            owner_id=owner_id,
            allowed=True,
            deny_reason="",
        )
        log.info(
            "INLINE_BUTTON kind=%s clicker=%s (%s) owner=%s chat=%s allowed=%s",
            kind,
            clicker_id,
            (clicker.username or clicker.full_name or "") if clicker else "",
            owner_id,
            chat_id,
            allowed,
        )
        await _send_schedule_admitted(callback.message, target, lease)
    finally:
        # Идемпотентная страховка на случай ошибки до входа в admitted-обработчик.
        ANTISPAM.release(lease, failed=True)


@dp.callback_query(F.data.startswith("admin:"))
async def cb_admin_menu(callback: CallbackQuery, bot: Bot) -> None:
    """Inline-кнопки меню управления ботом администратора (в личных сообщениях)."""
    user = callback.from_user
    uid = user.id if user else 0
    if not _is_admin_user(uid):
        await callback.answer("⛔ Только администратор бота может вызывать это меню.", show_alert=True)
        return
    chat = callback.message
    if chat is None or chat.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "⛔ Админское меню доступно только в личном чате с ботом.",
            show_alert=True,
        )
        return
    action = (callback.data or "").split(":", 1)[1] if callback.data else ""
    text: str | None = None
    if action == "panel":
        text = _admin_panel_text()
    elif action == "users":
        text = _admin_users_text()
    elif action == "bans":
        text = _admin_bans_text()
    if text is None:
        await callback.answer("Неизвестное действие меню.", show_alert=True)
        return
    await callback.answer()
    await bot.send_message(chat_id=chat.chat.id, text=text)


async def setup_bot_commands(bot: Bot) -> None:
    public_commands = [
        BotCommand(command="raspisanie", description="Расписание (авто: сегодня/завтра)"),
        BotCommand(command="schedule", description="Расписание на день или неделю"),
        BotCommand(command="help", description="Справка и кнопки выбора"),
    ]
    private_commands = list(public_commands)
    with suppress(Exception):
        await bot.set_my_commands(public_commands, scope=BotCommandScopeDefault())
        await bot.set_my_commands(
            private_commands,
            scope=BotCommandScopeAllPrivateChats(),
        )

    admin_commands = private_commands + [
        BotCommand(command="admin", description="👑 Панель администратора"),
        BotCommand(command="users", description="👑 Список пользователей (ID/@username)"),
        BotCommand(command="ban", description="👑 Ограничить доступ пользователю"),
        BotCommand(command="unban", description="👑 Снять ограничение доступа"),
        BotCommand(command="bans", description="👑 Активные ограничения доступа"),
        BotCommand(command="weekly_chats", description="👑 Группы воскресного закрепа"),
        BotCommand(command="autoschedule", description="👑 Вкл/выкл автопостинг группы"),
        BotCommand(command="weekly_now", description="👑 Ручной запуск недельной рассылки"),
    ]
    for admin_id in config.ADMIN_IDS:
        with suppress(Exception):
            await bot.set_my_commands(
                admin_commands, scope=BotCommandScopeChat(chat_id=admin_id)
            )


async def main() -> None:
    global BOT_LOCK
    BOT_LOCK = acquire_single_instance_lock()

    warned_missing_token = False
    while True:
        config.reload()
        errors = validate()
        if errors:
            if not warned_missing_token:
                log.warning(
                    "Ожидаю корректную конфигурацию .env (%s). Проверка каждые 15 с…",
                    "; ".join(errors),
                )
                warned_missing_token = True
            await asyncio.sleep(15)
            continue

        warned_missing_token = False
        log.info(
            "Запуск TG-бота расписания (группа %s, админы: %s)",
            config.TARGET_GROUP,
            config.ADMIN_IDS,
        )
        session = AiohttpSession(proxy=config.TG_PROXY) if config.TG_PROXY else None
        bot = Bot(config.TG_BOT_TOKEN, session=session)
        scheduler_task: asyncio.Task[None] | None = None
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await setup_bot_commands(bot)
            scheduler_task = asyncio.create_task(weekly_scheduler_loop(bot))
            await dp.start_polling(bot)
            return
        except TelegramUnauthorizedError:
            log.error(
                "Telegram отклонил TG_BOT_TOKEN (Unauthorized). Исправьте токен в .env — "
                "повтор через 30 с…"
            )
            await asyncio.sleep(30)
        except TelegramNetworkError as exc:
            log.warning(
                "Нет связи с api.telegram.org (%s). Повтор через 15 с…",
                type(exc).__name__,
            )
            await asyncio.sleep(15)
        finally:
            if scheduler_task is not None:
                scheduler_task.cancel()
                with suppress(asyncio.CancelledError):
                    await scheduler_task
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit) as exc:
        if isinstance(exc, SystemExit) and exc.code:
            raise
        log.info("Бот остановлен.")
