"""Автотесты для TG-бота расписания."""
from __future__ import annotations

from datetime import date, datetime, timedelta
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import threading
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
    assert clock.parse_duration("2часа") == timedelta(hours=2)
    assert clock.parse_duration("7дней") == timedelta(days=7)
    assert clock.parse_duration("30минут") == timedelta(minutes=30)
    assert clock.parse_duration("спам") is None
    assert clock.parse_duration("0m") is None
    assert clock.parse_duration("999999999999999999999d") is None
    assert clock.parse_duration("6y") is None
    assert clock.parse_duration("") is None


def test_config_reload_removes_deleted_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.delenv("SCHEDULE_LOCAL_PDF", raising=False)
    monkeypatch.delenv("ADMIN_IDS", raising=False)
    monkeypatch.delenv("TG_PROXY", raising=False)

    env_path.write_text(
        "SCHEDULE_LOCAL_PDF=/tmp/schedule.pdf\n"
        "ADMIN_IDS=111,222\n"
        "TG_PROXY=http://127.0.0.1:7890\n",
        encoding="utf-8",
    )
    config.reload()
    assert config.SCHEDULE_LOCAL_PDF == "/tmp/schedule.pdf"
    assert config.ADMIN_IDS == (111, 222)
    assert config.TG_PROXY == "http://127.0.0.1:7890"

    env_path.write_text(
        "WEEKLY_CHAT_IDS=-100123,-100456\n",
        encoding="utf-8",
    )
    config.reload()
    assert config.WEEKLY_CHAT_IDS == (-100123, -100456)

    env_path.write_text("# настройки удалены\n", encoding="utf-8")
    config.reload()
    assert config.SCHEDULE_LOCAL_PDF == ""
    assert config.ADMIN_IDS == ()
    assert config.TG_PROXY == ""


def test_config_process_environment_has_priority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("TARGET_GROUP=from-file\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setenv("TARGET_GROUP", "from-process")
    config.reload()
    assert config.TARGET_GROUP == "from-process"


def test_config_validation_rejects_timezone_and_bad_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TG_BOT_TOKEN=test-token\n"
        "TIMEZONE=Not/AZone\n"
        "SCHEDULE_PAGE_URL=not-a-url\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.delenv("TIMEZONE", raising=False)
    monkeypatch.delenv("SCHEDULE_PAGE_URL", raising=False)
    config.reload()
    errors = config.validate()
    assert any("часовой пояс" in item for item in errors)
    assert any("URL страницы" in item for item in errors)


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
    with pytest.raises(commands.ScheduleArgumentError):
        commands.parse_command("/расписание 31.02.2026")
    with pytest.raises(commands.ScheduleArgumentError):
        commands.parse_command("/расписание неизвестно")


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


def test_render_reused_output_does_not_return_stale_image(tmp_path: Path) -> None:
    pdf_path = SAMPLES_DIR / "25-27-25-31_00e8da94d0354de8.pdf"
    data = pdf_path.read_bytes()
    first = render.build_images(
        data,
        date(2026, 9, 29),
        tmp_path,
        group="25-28",
    )
    assert first.ok is True
    second = render.build_images(
        data,
        date(2026, 9, 29),
        tmp_path,
        group="99-99",
    )
    assert second.ok is False
    assert second.images == []


def test_state_users_bans_and_v1_migration(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        """{
  "version": 1,
  "users": {
    "111": {
      "username": "student_one",
      "full_name": "Иван",
      "private_started": true,
      "dispatch_enabled": false,
      "dispatch_muted_by_admin": true,
      "last_sent_week_monday": "2026-11-23"
    },
    "222": {
      "username": "student_two",
      "full_name": "Пётр",
      "private_started": false
    }
  }
} """,
        encoding="utf-8",
    )
    st = BotState(path)

    uid, _ = st.resolve_user("@student_one")
    assert uid == 111
    assert st.get_user(111).private_started is True
    assert st.get_user(222).private_started is False

    st.ban_user(111, duration=timedelta(seconds=60), reason="спам", banned_by=1075591078)
    ban_rec = st.get_active_ban(111)
    assert ban_rec is not None
    assert st.get_active_ban(111, now_ts=ban_rec.created_at + 120) is None

    # Любое штатное сохранение мигрирует state v1 в v2 без legacy DM-полей.
    st.touch_user(333, username="new_user", full_name="Новый")
    raw = path.read_text(encoding="utf-8")
    assert '"version": 2' in raw
    assert "dispatch_enabled" not in raw
    assert "dispatch_muted_by_admin" not in raw
    assert "last_sent_week_monday" not in raw.split('"chats"', 1)[0]

    reloaded = BotState(path)
    assert reloaded.get_user(111).username == "student_one"
    assert reloaded.get_user(333).username == "new_user"


def test_group_only_help_and_command_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    removed = {"stop_mail", "start_mail", "mute_dispatch", "unmute_dispatch"}
    assert all(command not in bot.help_text(is_admin_user=True) for command in removed)
    assert "ЛС-рассыл" not in bot._admin_panel_text()

    monkeypatch.setattr(config, "ADMIN_IDS", (123,))
    api = SimpleNamespace(set_my_commands=AsyncMock())
    asyncio.run(bot.setup_bot_commands(api))  # type: ignore[arg-type]
    assert api.set_my_commands.await_count == 3
    for call in api.set_my_commands.await_args_list:
        commands_arg = call.args[0]
        names = {item.command for item in commands_arg}
        assert not names & removed
    admin_names = {item.command for item in api.set_my_commands.await_args_list[-1].args[0]}
    assert {"users", "ban", "bans", "weekly_chats", "autoschedule", "weekly_now"} <= admin_names


def test_group_only_user_statuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    test_state = BotState(tmp_path / "state.json")
    test_state.touch_user(111, username="private_user", is_private=True)
    test_state.touch_user(222, username="group_user", is_private=False)
    monkeypatch.setattr(bot, "STATE", test_state)
    text = bot._admin_users_text()
    assert "писал боту в ЛС" in text
    assert "только в группе" in text
    assert "рассылка" not in text.lower()


def test_weekly_publisher_targets_groups_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    pdf_path = SAMPLES_DIR / "25-27-25-31_00e8da94d0354de8.pdf"
    found = source.ScheduleFile(
        url=str(pdf_path),
        name=pdf_path.name,
        path=pdf_path,
    )
    test_state = BotState(tmp_path / "state.json")
    test_state.touch_user(111, username="private_user", is_private=True)
    test_state.set_chat_weekly(-1001, enabled=True, title="Учебная группа")
    monkeypatch.setattr(bot, "STATE", test_state)
    monkeypatch.setattr(bot, "TEMP_ROOT", tmp_path / "render")
    monkeypatch.setattr(bot, "fetch_schedule_file", AsyncMock(return_value=found))
    monkeypatch.setattr(bot, "WEEKLY_PUBLISH_LOCK", asyncio.Lock())

    api = SimpleNamespace(
        send_photo=AsyncMock(),
        send_media_group=AsyncMock(
            return_value=[SimpleNamespace(message_id=700)]
        ),
        pin_chat_message=AsyncMock(),
        unpin_chat_message=AsyncMock(),
    )
    ok, report = asyncio.run(
        bot.publish_weekly_schedule(
            api,  # type: ignore[arg-type]
            target_monday=date(2026, 9, 28),
            require_target_week=True,
        )
    )
    assert ok is True
    api.send_media_group.assert_awaited_once()
    assert api.send_media_group.await_args.args[0] == -1001
    assert all(
        call.args[0] != 111 for call in api.send_photo.await_args_list
    )
    assert "Пользователей" not in report
    assert test_state.get_weekly_sent_monday() == "2026-09-28"
    assert test_state.chats[-1001].last_sent_week_monday == "2026-09-28"


def test_antispam_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "USER_COOLDOWN_SEC", 20.0)
    monkeypatch.setattr(config, "CHAT_COOLDOWN_SEC", 8.0)
    monkeypatch.setattr(config, "SAME_TARGET_COOLDOWN_SEC", 30.0)

    guard = bot.AntiSpamGuard()
    lease1, _ = guard.acquire(chat_id=-1001, user_id=500, target_key="2026-11-19", now_ts=100.0)
    assert lease1 is not None
    # Пока выполняется in-flight — второй запрос отклоняется
    lease_inflight, _ = guard.acquire(
        chat_id=-1001, user_id=501, target_key="2026-11-19", now_ts=100.5
    )
    assert lease_inflight is None
    guard.release(lease1)

    # Тот же пользователь через 5 секунд получает отказ по USER_COOLDOWN_SEC
    lease2, reason2 = guard.acquire(
        chat_id=-1001, user_id=500, target_key="week", now_ts=105.0
    )
    assert lease2 is None
    assert "Не так быстро" in reason2

    # Другой пользователь через 10 секунд запрашивает тот же день — отказ по SAME_TARGET_COOLDOWN_SEC
    lease3, reason3 = guard.acquire(
        chat_id=-1001, user_id=501, target_key="2026-11-19", now_ts=110.0
    )
    assert lease3 is None
    assert "чуть выше" in reason3

    # Админ пропускается без кулдауна
    lease_admin, _ = guard.acquire(
        chat_id=-1001, user_id=1075591078, target_key="2026-11-19", is_admin=True, now_ts=111.0
    )
    assert lease_admin is not None


def test_failed_release_only_rolls_back_its_own_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config, "USER_COOLDOWN_SEC", 20.0)
    monkeypatch.setattr(config, "CHAT_COOLDOWN_SEC", 0.0)
    monkeypatch.setattr(config, "SAME_TARGET_COOLDOWN_SEC", 0.0)
    guard = bot.AntiSpamGuard()

    first, _ = guard.acquire(
        chat_id=10,
        user_id=500,
        target_key="first",
        now_ts=100.0,
    )
    assert first is not None
    guard.release(first)

    # Имитируем более новый успешный admission того же пользователя.
    newer_token = object()
    guard._user_last_req[500] = (110.0, newer_token)
    guard.release(first, failed=True)
    assert guard._user_last_req[500] == (110.0, newer_token)


def test_build_images_async_keeps_event_loop_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    started = threading.Event()
    finish = threading.Event()
    expected = render.RenderOut(ok=False)

    def blocking_build(*args: object, **kwargs: object) -> render.RenderOut:
        started.set()
        assert finish.wait(timeout=5)
        return expected

    monkeypatch.setattr(bot, "build_images", blocking_build)

    async def scenario() -> None:
        task = asyncio.create_task(
            bot._build_images_async(b"pdf", date(2026, 11, 18), tmp_path, week=False)
        )
        assert await asyncio.to_thread(started.wait, 2)
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        assert await task is expected

    asyncio.run(scenario())


def test_state_retries_corrupt_file_after_repair(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{broken", encoding="utf-8")
    st = BotState(path)
    assert st.list_users() == []

    path.write_text(
        '{"version": 1, "users": {"42": {"username": "fixed"}}}',
        encoding="utf-8",
    )
    users = st.list_users()
    assert len(users) == 1
    assert users[0].user_id == 42


def test_weekly_chat_disable_overrides_env_seed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "WEEKLY_CHAT_IDS", (-1001,))
    st = BotState(tmp_path / "state.json")
    assert st.get_weekly_chat_ids() == [-1001]
    st.set_chat_weekly(-1001, enabled=False)
    assert st.get_weekly_chat_ids() == []


def test_callback_owner_restriction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    test_state = BotState(tmp_path / "state.json")
    monkeypatch.setattr(bot, "STATE", test_state)

    kb = bot.schedule_keyboard(owner_id=100)
    assert kb.inline_keyboard[0][0].callback_data == "sched:today:100"

    # Чужой пользователь (id=200) жмёт на кнопку пользователя 100
    cb = SimpleNamespace(
        data="sched:today:100",
        from_user=SimpleNamespace(
            id=200,
            username="another_user",
            full_name="Другой пользователь",
            is_bot=False,
        ),
        message=None,
        answer=AsyncMock(),
    )
    asyncio.run(bot.cb_schedule(cb))  # type: ignore[arg-type]
    cb.answer.assert_awaited_once()
    args, kwargs = cb.answer.call_args
    assert "другой пользователь" in args[0]
    assert kwargs.get("show_alert") is True

    presses = test_state.list_button_presses()
    assert len(presses) == 1
    assert presses[0].user_id == 200
    assert presses[0].allowed is False


def test_callback_releases_lease_when_answer_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    guard = bot.AntiSpamGuard()
    monkeypatch.setattr(bot, "ANTISPAM", guard)
    monkeypatch.setattr(bot, "STATE", BotState(tmp_path / "state.json"))
    monkeypatch.setattr(config, "ADMIN_IDS", ())
    cb = SimpleNamespace(
        data="sched:today:200",
        from_user=SimpleNamespace(
            id=200,
            username="owner",
            full_name="Owner",
            is_bot=False,
        ),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=200, type="private"),
            from_user=SimpleNamespace(id=200),
        ),
        answer=AsyncMock(side_effect=RuntimeError("callback expired")),
    )
    with pytest.raises(RuntimeError):
        asyncio.run(bot.cb_schedule(cb))  # type: ignore[arg-type]
    assert guard._in_flight == {}


def test_single_instance_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock_file = tmp_path / "bot.lock"
    monkeypatch.setattr(bot, "LOCK_PATH", lock_file)
    handle = bot.acquire_single_instance_lock()
    try:
        with pytest.raises(SystemExit):
            bot.acquire_single_instance_lock()
    finally:
        handle.close()
