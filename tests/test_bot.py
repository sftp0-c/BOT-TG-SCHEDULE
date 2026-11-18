"""Автотесты для TG-бота расписания."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

import bot
import clock
import commands
import config
import schedule_render as render
import schedule_source as source
from state import BotState

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "schedules"


def test_clock_cutoff_before_and_after_13() -> None:
    tz = ZoneInfo("Asia/Yekaterinburg")
    morning = datetime(2026, 10, 19, 12, 59, tzinfo=tz)
    afternoon = datetime(2026, 10, 19, 13, 0, tzinfo=tz)

    res_morning = clock.resolve_day("auto", current_dt=morning)
    assert res_morning.day == date(2026, 10, 19)
    assert res_morning.label == "сегодня"

    res_afternoon = clock.resolve_day("auto", current_dt=afternoon)
    assert res_afternoon.day == date(2026, 10, 20)
    assert "завтра" in res_afternoon.label


def test_next_monday_and_duration_parser() -> None:
    # Воскресенье 22.11.2026 -> понедельник 23.11.2026
    assert clock.next_monday(date(2026, 11, 22)) == date(2026, 11, 23)
    # Среда 18.11.2026 -> понедельник 23.11.2026
    assert clock.next_monday(date(2026, 11, 18)) == date(2026, 11, 23)
    # Понедельник 16.11.2026 -> следующий понедельник 23.11.2026
    assert clock.next_monday(date(2026, 11, 16)) == date(2026, 11, 23)

    assert clock.parse_duration("30m") == timedelta(minutes=30)
    assert clock.parse_duration("2h") == timedelta(hours=2)
    assert clock.parse_duration("7d") == timedelta(days=7)
    assert clock.parse_duration("1w") == timedelta(weeks=1)
    assert clock.parse_duration("1d12h") == timedelta(days=1, hours=12)
    assert clock.parse_duration("2ч") == timedelta(hours=2)
    assert clock.parse_duration("спам") is None
    assert clock.parse_duration("") is None


def test_commands_parsing() -> None:
    assert commands.parse_command("/расписание") is not None
    assert commands.parse_command("!расписание завтра").label == "завтра"
    assert commands.parse_command("/raspisanie@my_sched_bot сегодня").label == "сегодня"
    assert commands.parse_command("/schedule неделя").label == "неделя"
    assert commands.parse_command("!расписание все").label == "неделя"

    explicit = commands.parse_command("/расписание 21.10.2026")
    assert explicit is not None
    assert explicit.day == date(2026, 10, 21)

    # В группах голый текст без префикса не срабатывает, в ЛС — срабатывает
    assert commands.parse_command("завтра", allow_bare=False) is None
    assert commands.parse_command("завтра", allow_bare=True).label == "завтра"
    assert commands.parse_command("21.10", allow_bare=True) is not None


def test_source_ranking_and_range() -> None:
    page = "https://collegelan.ru/studentam/raspisanie-zanyatiy.php"
    html = (
        '<a href="/studentam/Расписание/23-27-23-29.pdf">1</a>'
        '<a href="/studentam/Расписание/25-27-25-31.pdf">2</a>'
        '<a href="/studentam/Расписание/25-27-25-31 1.pdf">3</a>'
        '<a href="/studentam/Расписание/25-27-25-31 (изм).pdf">4</a>'
        '<a href="/studentam/Расписание/25-25-25-30.pdf">5</a>'
    )
    picked = source.pick_schedule_url_from_html(html, page, ("25-27-25-31",), "25-28")
    assert picked is not None
    assert picked.name == "25-27-25-31 (изм).pdf"

    html_rev = (
        '<a href="/studentam/Расписание/25-27-25-31.pdf">1</a>'
        '<a href="/studentam/Расписание/25-27-25-31 1.pdf">2</a>'
    )
    picked_rev = source.pick_schedule_url_from_html(html_rev, page, ("25-27-25-31",), "25-28")
    assert picked_rev is not None
    assert picked_rev.name == "25-27-25-31 1.pdf"

    html_rev_range = '<a href="/studentam/Расписание/25-31-25-27 (изм).pdf">x</a>'
    picked_rr = source.pick_schedule_url_from_html(
        html_rev_range, page, ("25-27-25-31",), "25-28"
    )
    assert picked_rr is not None
    assert picked_rr.name == "25-31-25-27 (изм).pdf"

    assert (
        source.pick_schedule_url_from_html(
            '<a href="/studentam/Расписание/26-20-26-27.pdf">x</a>',
            page,
            ("25-27-25-31",),
            "25-28",
        )
        is None
    )


@pytest.mark.parametrize(
    ("pdf_name", "group", "target", "week", "expected_ok", "expected_count"),
    [
        ("25-27-25-31_00e8da94d0354de8.pdf", "25-28", date(2026, 9, 29), False, True, 1),
        ("25-27-25-31_00e8da94d0354de8.pdf", "25-28", date(2026, 9, 28), True, True, 5),
        ("26-31_adc562d352096b24.pdf", "26-28", date(2026, 10, 21), False, True, 1),
        ("26-31_adc562d352096b24.pdf", "26-29(П)", date(2026, 10, 19), False, True, 1),
        ("26-31_adc562d352096b24.pdf", "26-31", date(2026, 10, 24), False, False, 0),
        ("26-27_b2db108310b4fe32.pdf", "26-27", date(2026, 10, 23), False, True, 1),
        ("24-25_bd23aa0aff67f963.pdf", "24-25", date(2026, 10, 20), False, True, 1),
        ("26-22_251bf070347c9439.pdf", "25-32", date(2026, 10, 20), True, True, 5),
    ],
)
def test_render_samples(
    tmp_path: Path,
    pdf_name: str,
    group: str,
    target: date,
    week: bool,
    expected_ok: bool,
    expected_count: int,
) -> None:
    pdf_path = SAMPLES_DIR / pdf_name
    assert pdf_path.is_file(), f"Отсутствует образец {pdf_path}"
    out = render.build_images(pdf_path.read_bytes(), target, tmp_path, group=group, week=week)
    assert out.ok is expected_ok
    assert len(out.images) == expected_count
    for img in out.images:
        assert img.path.is_file()
        assert img.path.stat().st_size > 1000


def test_render_week_start_validation(tmp_path: Path) -> None:
    pdf_path = SAMPLES_DIR / "25-27-25-31_00e8da94d0354de8.pdf"
    # В файле неделя 28.09.2026–02.10.2026
    ok_out = render.build_images(
        pdf_path.read_bytes(),
        date(2026, 9, 28),
        tmp_path / "ok",
        group="25-28",
        week=True,
        week_start=date(2026, 9, 28),
    )
    assert ok_out.ok is True
    assert len(ok_out.images) == 5

    # Если просим следующую неделю (05.10.2026), бот должен отклонить старый PDF
    old_out = render.build_images(
        pdf_path.read_bytes(),
        date(2026, 10, 5),
        tmp_path / "old",
        group="25-28",
        week=True,
        week_start=date(2026, 10, 5),
    )
    assert old_out.ok is False
    assert any("пока нет страниц на неделю" in note for note in old_out.notes)


def test_state_bans_and_dispatch(tmp_path: Path) -> None:
    st = BotState(tmp_path / "state.json")
    st.touch_user(111, username="student_one", full_name="Иван", is_private=True)
    st.touch_user(222, username="student_two", full_name="Пётр", is_private=True)

    # Поиск по @username
    uid, _ = st.resolve_user("@student_one")
    assert uid == 111

    # Временный бан и автоистечение
    st.ban_user(111, duration=timedelta(seconds=60), reason="спам", banned_by=1075591078)
    assert st.get_active_ban(111, now_ts=1000.0) is not None
    # Через 100 секунд бан должен автоматически сняться
    ban_rec = st.get_active_ban(111)
    assert ban_rec is not None
    assert st.get_active_ban(111, now_ts=ban_rec.created_at + 120) is None

    # Остановка рассылки админом запрещает пользователю включить её обратно самому
    st.set_dispatch_admin(222, enabled=False)
    ok, _msg = st.set_dispatch_self(222, enabled=True)
    assert ok is False
    recipients = [r.user_id for r in st.get_dispatch_recipients()]
    assert 222 not in recipients
    assert 111 in recipients

    # Возврат в рассылку админом
    st.set_dispatch_admin(222, enabled=True)
    assert 222 in [r.user_id for r in st.get_dispatch_recipients()]


def test_antispam_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "USER_COOLDOWN_SEC", 20.0)
    monkeypatch.setattr(config, "CHAT_COOLDOWN_SEC", 8.0)
    monkeypatch.setattr(config, "SAME_TARGET_COOLDOWN_SEC", 30.0)

    guard = bot.AntiSpamGuard()
    ok1, _ = guard.check_and_acquire(chat_id=-1001, user_id=500, target_key="2026-11-19", now_ts=100.0)
    assert ok1 is True
    # Пока выполняется in-flight — второй запрос отклоняется
    ok_inflight, _ = guard.check_and_acquire(
        chat_id=-1001, user_id=501, target_key="2026-11-19", now_ts=100.5
    )
    assert ok_inflight is False
    guard.release(chat_id=-1001, target_key="2026-11-19")

    # Тот же пользователь через 5 секунд получает отказ по USER_COOLDOWN_SEC
    ok2, reason2 = guard.check_and_acquire(
        chat_id=-1001, user_id=500, target_key="week", now_ts=105.0
    )
    assert ok2 is False
    assert "Не так быстро" in reason2

    # Другой пользователь через 10 секунд запрашивает тот же день — отказ по SAME_TARGET_COOLDOWN_SEC
    ok3, reason3 = guard.check_and_acquire(
        chat_id=-1001, user_id=501, target_key="2026-11-19", now_ts=110.0
    )
    assert ok3 is False
    assert "чуть выше" in reason3

    # Админ пропускается без кулдауна
    ok_admin, _ = guard.check_and_acquire(
        chat_id=-1001, user_id=1075591078, target_key="2026-11-19", is_admin=True, now_ts=111.0
    )
    assert ok_admin is True


def test_callback_owner_restriction() -> None:
    import asyncio

    kb = bot.schedule_keyboard(owner_id=100)
    assert kb.inline_keyboard[0][0].callback_data == "sched:today:100"

    # Чужой пользователь (id=200) жмёт на кнопку пользователя 100
    cb = SimpleNamespace(
        data="sched:today:100",
        from_user=SimpleNamespace(id=200),
        message=None,
        answer=AsyncMock(),
    )
    asyncio.run(bot.cb_schedule(cb))  # type: ignore[arg-type]
    cb.answer.assert_awaited_once()
    args, kwargs = cb.answer.call_args
    assert "другой пользователь" in args[0]
    assert kwargs.get("show_alert") is True


def test_single_instance_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_file = tmp_path / "bot.lock"
    monkeypatch.setattr(bot, "LOCK_PATH", lock_file)
    handle = bot.acquire_single_instance_lock()
    try:
        with pytest.raises(SystemExit):
            bot.acquire_single_instance_lock()
    finally:
        handle.close()
