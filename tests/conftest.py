"""Общие pytest-фикстуры: тесты не используют рабочие .env, state и temp-файлы."""
from __future__ import annotations

from pathlib import Path

import pytest

import bot
import config
from state import BotState


@pytest.fixture(autouse=True)
def isolate_runtime_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / ".env"
    state_path = tmp_path / "state.json"
    temp_root = tmp_path / "requests"
    lock_path = tmp_path / "bot.lock"

    for name in config._CONFIG_KEYS:
        monkeypatch.setattr(config, name, getattr(config, name))
        monkeypatch.delenv(name, raising=False)

    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setattr(config, "STATE_PATH", state_path)
    monkeypatch.setattr(bot, "STATE", BotState(state_path))
    monkeypatch.setattr(bot, "TEMP_ROOT", temp_root)
    monkeypatch.setattr(bot, "LOCK_PATH", lock_path)
