"""Персистентное состояние бота (data/state.json): пользователи, баны, рассылка и чаты."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import config

log = logging.getLogger("tg-schedule")

BUTTON_PRESS_HISTORY_MAX = 500


@dataclass
class UserRecord:
    user_id: int
    username: str = ""
    full_name: str = ""
    private_started: bool = False
    dispatch_enabled: bool = True
    dispatch_muted_by_admin: bool = False
    last_seen: float = 0.0

    @property
    def display_name(self) -> str:
        parts: list[str] = []
        if self.full_name:
            parts.append(self.full_name)
        if self.username:
            parts.append(f"@{self.username}")
        parts.append(f"[{self.user_id}]")
        return " ".join(parts)


@dataclass
class BanRecord:
    user_id: int
    until: float | None = None  # None = навсегда
    reason: str = ""
    banned_by: int = 0
    created_at: float = 0.0


@dataclass
class ChatRecord:
    chat_id: int
    title: str = ""
    weekly_enabled: bool = False
    last_pinned_message_id: int | None = None
    last_sent_week_monday: str | None = None


@dataclass
class ButtonPressRecord:
    """Факт нажатия inline-кнопки бота (кто, куда, когда)."""

    ts: float
    user_id: int
    username: str = ""
    full_name: str = ""
    chat_id: int = 0
    kind: str = ""
    owner_id: int | None = None
    allowed: bool = False
    deny_reason: str = ""


class BotState:
    """Потокобезопасное хранилище состояния в JSON с атомарным сохранением."""

    def __init__(self, path: Path | None = None) -> None:
        self._explicit_path = path
        self._lock = threading.RLock()
        self._loaded_path: Path | None = None
        self.users: dict[int, UserRecord] = {}
        self.bans: dict[int, BanRecord] = {}
        self.chats: dict[int, ChatRecord] = {}
        self.weekly_sent_monday: str | None = None
        self.button_presses: list[ButtonPressRecord] = []

    @property
    def path(self) -> Path:
        return self._explicit_path if self._explicit_path is not None else config.STATE_PATH

    def _ensure_loaded(self) -> None:
        target = self.path
        if self._loaded_path == target:
            return
        self.users.clear()
        self.bans.clear()
        self.chats.clear()
        self.weekly_sent_monday = None
        self.button_presses = []
        self._loaded_path = target

        if not target.is_file():
            return
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("Не удалось прочитать %s: %s", target, exc)
            return

        if not isinstance(raw, dict):
            return

        self.weekly_sent_monday = raw.get("weekly_sent_monday") or None

        for uid_str, item in (raw.get("users") or {}).items():
            try:
                uid = int(uid_str)
                self.users[uid] = UserRecord(
                    user_id=uid,
                    username=str(item.get("username") or ""),
                    full_name=str(item.get("full_name") or ""),
                    private_started=bool(item.get("private_started", False)),
                    dispatch_enabled=bool(item.get("dispatch_enabled", True)),
                    dispatch_muted_by_admin=bool(item.get("dispatch_muted_by_admin", False)),
                    last_seen=float(item.get("last_seen") or 0.0),
                )
            except (TypeError, ValueError):
                continue

        for uid_str, item in (raw.get("bans") or {}).items():
            try:
                uid = int(uid_str)
                until_raw = item.get("until")
                until_val = float(until_raw) if until_raw is not None else None
                self.bans[uid] = BanRecord(
                    user_id=uid,
                    until=until_val,
                    reason=str(item.get("reason") or ""),
                    banned_by=int(item.get("banned_by") or 0),
                    created_at=float(item.get("created_at") or 0.0),
                )
            except (TypeError, ValueError):
                continue

        for cid_str, item in (raw.get("chats") or {}).items():
            try:
                cid = int(cid_str)
                pin_raw = item.get("last_pinned_message_id")
                pin_val = int(pin_raw) if pin_raw is not None else None
                self.chats[cid] = ChatRecord(
                    chat_id=cid,
                    title=str(item.get("title") or ""),
                    weekly_enabled=bool(item.get("weekly_enabled", False)),
                    last_pinned_message_id=pin_val,
                    last_sent_week_monday=item.get("last_sent_week_monday") or None,
                )
            except (TypeError, ValueError):
                continue

        for item in raw.get("button_presses") or []:
            try:
                if not isinstance(item, dict):
                    continue
                owner_raw = item.get("owner_id")
                self.button_presses.append(
                    ButtonPressRecord(
                        ts=float(item.get("ts") or 0.0),
                        user_id=int(item.get("user_id") or 0),
                        username=str(item.get("username") or ""),
                        full_name=str(item.get("full_name") or ""),
                        chat_id=int(item.get("chat_id") or 0),
                        kind=str(item.get("kind") or ""),
                        owner_id=int(owner_raw) if owner_raw is not None else None,
                        allowed=bool(item.get("allowed", False)),
                        deny_reason=str(item.get("deny_reason") or ""),
                    )
                )
            except (TypeError, ValueError):
                continue
        if len(self.button_presses) > BUTTON_PRESS_HISTORY_MAX:
            del self.button_presses[:-BUTTON_PRESS_HISTORY_MAX]

    def _save(self) -> None:
        target = self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "version": 1,
            "weekly_sent_monday": self.weekly_sent_monday,
            "users": {str(uid): asdict(rec) for uid, rec in sorted(self.users.items())},
            "bans": {str(uid): asdict(rec) for uid, rec in sorted(self.bans.items())},
            "chats": {str(cid): asdict(rec) for cid, rec in sorted(self.chats.items())},
            "button_presses": [asdict(rec) for rec in self.button_presses],
        }
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    # --- Пользователи ---

    def touch_user(
        self,
        user_id: int,
        username: str | None = None,
        full_name: str | None = None,
        *,
        is_private: bool = False,
    ) -> UserRecord:
        with self._lock:
            self._ensure_loaded()
            now_ts = time.time()
            rec = self.users.get(user_id)
            clean_username = (username or "").lstrip("@").strip()
            clean_name = (full_name or "").strip()

            if rec is None:
                rec = UserRecord(
                    user_id=user_id,
                    username=clean_username,
                    full_name=clean_name,
                    private_started=is_private,
                    dispatch_enabled=True,
                    dispatch_muted_by_admin=False,
                    last_seen=now_ts,
                )
                self.users[user_id] = rec
                self._save()
                return rec

            changed = False
            if clean_username != rec.username:
                rec.username = clean_username
                changed = True
            if clean_name and clean_name != rec.full_name:
                rec.full_name = clean_name
                changed = True
            if is_private and not rec.private_started:
                rec.private_started = True
                changed = True
            if now_ts - rec.last_seen >= 60:
                rec.last_seen = now_ts
                changed = True
            if changed:
                self._save()
            return rec

    def get_user(self, user_id: int) -> UserRecord | None:
        with self._lock:
            self._ensure_loaded()
            return self.users.get(user_id)

    def resolve_user(self, query: str) -> tuple[int | None, str]:
        """Ищет user_id по числовому ID или @username среди известных боту пользователей."""
        token = (query or "").strip()
        if not token:
            return None, "Не указан ID или @username пользователя."
        if token.lstrip("-").isdigit():
            uid = int(token)
            with self._lock:
                self._ensure_loaded()
                rec = self.users.get(uid)
            label = rec.display_name if rec else f"[{uid}]"
            return uid, label

        needle = token.lstrip("@").lower()
        with self._lock:
            self._ensure_loaded()
            for rec in self.users.values():
                if rec.username and rec.username.lower() == needle:
                    return rec.user_id, rec.display_name

        return None, (
            f"Пользователь @{needle} не найден в базе бота. "
            "Укажите его числовой Telegram ID (посмотреть список: /users)."
        )

    def list_users(self, limit: int = 40) -> list[UserRecord]:
        with self._lock:
            self._ensure_loaded()
            ordered = sorted(self.users.values(), key=lambda u: u.last_seen, reverse=True)
            return ordered[:limit]

    # --- Отслеживание нажатий inline-кнопок ---

    def record_button_press(
        self,
        *,
        user_id: int,
        kind: str,
        chat_id: int = 0,
        owner_id: int | None = None,
        allowed: bool = False,
        deny_reason: str = "",
        username: str | None = None,
        full_name: str | None = None,
        ts: float | None = None,
    ) -> ButtonPressRecord:
        """Сохраняет факт нажатия inline-кнопки: кто нажал, куда, результат."""
        with self._lock:
            self._ensure_loaded()
            now_ts = ts if ts is not None else time.time()
            rec = ButtonPressRecord(
                ts=now_ts,
                user_id=user_id,
                username=(username or "").lstrip("@").strip(),
                full_name=(full_name or "").strip(),
                chat_id=chat_id,
                kind=kind,
                owner_id=owner_id,
                allowed=allowed,
                deny_reason=deny_reason,
            )
            self.button_presses.append(rec)
            if len(self.button_presses) > BUTTON_PRESS_HISTORY_MAX:
                del self.button_presses[:-BUTTON_PRESS_HISTORY_MAX]
            self._save()
            return rec

    def list_button_presses(self, limit: int = 20) -> list[ButtonPressRecord]:
        with self._lock:
            self._ensure_loaded()
            return list(self.button_presses[-limit:])

    # --- Ограничение доступа (баны) ---

    def ban_user(
        self,
        user_id: int,
        *,
        duration: timedelta | None = None,
        reason: str = "",
        banned_by: int = 0,
    ) -> BanRecord:
        with self._lock:
            self._ensure_loaded()
            now_ts = time.time()
            until_ts = (now_ts + duration.total_seconds()) if duration is not None else None
            rec = BanRecord(
                user_id=user_id,
                until=until_ts,
                reason=reason.strip(),
                banned_by=banned_by,
                created_at=now_ts,
            )
            self.bans[user_id] = rec
            self._save()
            return rec

    def unban_user(self, user_id: int) -> bool:
        with self._lock:
            self._ensure_loaded()
            if user_id in self.bans:
                del self.bans[user_id]
                self._save()
                return True
            return False

    def get_active_ban(self, user_id: int, *, now_ts: float | None = None) -> BanRecord | None:
        with self._lock:
            self._ensure_loaded()
            rec = self.bans.get(user_id)
            if rec is None:
                return None
            current = now_ts if now_ts is not None else time.time()
            if rec.until is not None and current >= rec.until:
                del self.bans[user_id]
                self._save()
                return None
            return rec

    def list_active_bans(self, *, now_ts: float | None = None) -> list[BanRecord]:
        with self._lock:
            self._ensure_loaded()
            current = now_ts if now_ts is not None else time.time()
            expired = [
                uid
                for uid, rec in self.bans.items()
                if rec.until is not None and current >= rec.until
            ]
            if expired:
                for uid in expired:
                    del self.bans[uid]
                self._save()
            return sorted(self.bans.values(), key=lambda b: b.created_at, reverse=True)

    # --- Управление рассылкой ---

    def set_dispatch_admin(self, user_id: int, *, enabled: bool) -> UserRecord:
        with self._lock:
            self._ensure_loaded()
            rec = self.users.get(user_id)
            if rec is None:
                rec = UserRecord(user_id=user_id)
                self.users[user_id] = rec
            rec.dispatch_enabled = enabled
            rec.dispatch_muted_by_admin = not enabled
            self._save()
            return rec

    def set_dispatch_self(self, user_id: int, *, enabled: bool) -> tuple[bool, str]:
        with self._lock:
            self._ensure_loaded()
            rec = self.users.get(user_id)
            if rec is None:
                rec = UserRecord(user_id=user_id, private_started=True)
                self.users[user_id] = rec
            if enabled and rec.dispatch_muted_by_admin:
                return (
                    False,
                    "⛔ Рассылка для вас отключена администратором. Обратитесь к админу.",
                )
            rec.dispatch_enabled = enabled
            self._save()
            if enabled:
                return True, "✅ Еженедельная рассылка расписания включена."
            return True, "🔕 Еженедельная рассылка расписания остановлена."

    def get_dispatch_recipients(self, *, now_ts: float | None = None) -> list[UserRecord]:
        with self._lock:
            self._ensure_loaded()
            # Очищаем просроченные баны перед выборкой
            self.list_active_bans(now_ts=now_ts)
            recipients: list[UserRecord] = []
            for uid, rec in sorted(self.users.items()):
                if not rec.private_started:
                    continue
                if not rec.dispatch_enabled or rec.dispatch_muted_by_admin:
                    continue
                if uid in self.bans:
                    continue
                recipients.append(rec)
            return recipients

    # --- Чаты для воскресного автопостинга с закрепом ---

    def touch_chat(self, chat_id: int, title: str = "") -> ChatRecord | None:
        if chat_id >= 0:
            return None
        with self._lock:
            self._ensure_loaded()
            rec = self.chats.get(chat_id)
            clean_title = (title or "").strip()
            if rec is None:
                rec = ChatRecord(
                    chat_id=chat_id,
                    title=clean_title,
                    weekly_enabled=(chat_id in config.WEEKLY_CHAT_IDS),
                )
                self.chats[chat_id] = rec
                self._save()
                return rec
            if clean_title and clean_title != rec.title:
                rec.title = clean_title
                self._save()
            return rec

    def set_chat_weekly(self, chat_id: int, *, enabled: bool, title: str = "") -> ChatRecord:
        with self._lock:
            self._ensure_loaded()
            rec = self.chats.get(chat_id)
            if rec is None:
                rec = ChatRecord(chat_id=chat_id, title=title.strip(), weekly_enabled=enabled)
                self.chats[chat_id] = rec
            else:
                rec.weekly_enabled = enabled
                if title.strip():
                    rec.title = title.strip()
            self._save()
            return rec

    def get_weekly_chat_ids(self) -> list[int]:
        with self._lock:
            self._ensure_loaded()
            result: list[int] = list(config.WEEKLY_CHAT_IDS)
            for cid, rec in sorted(self.chats.items()):
                if rec.weekly_enabled and cid not in result:
                    result.append(cid)
            return result

    def record_chat_pin(
        self, chat_id: int, pinned_message_id: int | None, week_monday_iso: str
    ) -> int | None:
        """Сохраняет новый закреп и возвращает ID предыдущего закрепа (если был)."""
        with self._lock:
            self._ensure_loaded()
            rec = self.chats.get(chat_id)
            if rec is None:
                rec = ChatRecord(chat_id=chat_id, weekly_enabled=True)
                self.chats[chat_id] = rec
            old_pin = rec.last_pinned_message_id
            if pinned_message_id is not None:
                rec.last_pinned_message_id = pinned_message_id
            rec.last_sent_week_monday = week_monday_iso
            self._save()
            return old_pin

    def get_weekly_sent_monday(self) -> str | None:
        with self._lock:
            self._ensure_loaded()
            return self.weekly_sent_monday

    def set_weekly_sent_monday(self, monday_iso: str) -> None:
        with self._lock:
            self._ensure_loaded()
            self.weekly_sent_monday = monday_iso
            self._save()


STATE = BotState()
