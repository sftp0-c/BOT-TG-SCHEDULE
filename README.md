# TG-бот расписания группы 25-28

Telegram-бот на `aiogram 3`: находит актуальный PDF расписания колледжа,
выбирает страницу нужной даты, вырезает колонку группы и отправляет результат
картинкой. Работает через polling и автоматически публикует недельное
расписание только в подключённые группы.

## Пользовательские команды

- `/расписание` или `!расписание` — расписание на день; после заданного часа
  автоматически выбирается завтра.
- `/расписание сегодня`, `завтра`, `послезавтра` — явный день.
- `/расписание 21.10` или `/расписание 21.10.2026` — конкретная дата.
- `/расписание неделя` или `все` — календарная неделя альбомом.

В личном чате можно писать без команды: `сегодня`, `завтра`, `послезавтра`,
`неделя` или дату. В группах bare-текст игнорируется.

## Администрирование

Чувствительные команды работают только в ЛС настроенного администратора:

- `/admin`, `/users` — панель и список пользователей;
- `/ban`, `/unban`, `/bans` — ограничения доступа;
- `/weekly_chats`, `/weekly_now` — состояние и ручной запуск недели.

`/autoschedule on|off` можно выполнять администратору прямо в настраиваемой
группе. Из ЛС требуется отрицательный Telegram ID группы.

## Конфигурация

Создайте `.env` по `.env.example`. Минимально нужен `TG_BOT_TOKEN`.
Администраторы и прокси не имеют встроенных значений и задаются явно:

```ini
TG_BOT_TOKEN=...
ADMIN_IDS=123456789
TG_PROXY=
TARGET_GROUP=25-28
```

Файл перечитывается между событиями. `TG_BOT_TOKEN` и `TG_PROXY` применяются к
Telegram-сессии после перезапуска контейнера; остальные настройки — сразу.

Для офлайн-проверки источника можно задать:

```ini
SCHEDULE_LOCAL_PDF=/app/samples/schedules/25-27-25-31_00e8da94d0354de8.pdf
```

## Локальный запуск

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-test.txt
python -m pytest
python bot.py
```

## Docker

Изолированные тесты не монтируют production `.env` и `data`:

```bash
docker build --network host --target test -t bot-tg-schedule:test .
```

Сборка и запуск runtime:

```bash
docker compose up -d --build
```

Контейнер работает от непривилегированного пользователя с read-only root FS;
для записи доступны только `/app/data` и временный `/tmp`.

## Production

На сервере проект размещается в `/opt/bot-tg-schedule`. Каноническая команда —
`tg-schedule-ctl`, совместимый alias — `bot-schedule-ctl`:

```bash
bot-schedule-ctl status
bot-schedule-ctl logs 200
bot-schedule-ctl test
bot-schedule-ctl backup
bot-schedule-ctl rebuild
```

`test` строит отдельный Docker target без боевых volumes. `rebuild` сначала
создаёт backup и пропускает обновление только после зелёных тестов. Подробный
runbook: `docs/deploy.md`.

## Структура

- `bot.py` — Telegram handlers, антиспам, групповые публикации и scheduler;
- `config.py`, `clock.py`, `commands.py` — настройки, время и разбор команд;
- `schedule_source.py` — ограниченная загрузка и поиск PDF;
- `schedule_render.py` — безопасный PDF → PNG;
- `state.py` — атомарное JSON-состояние и backup;
- `tests/` — офлайн regression suite;
- `ops/` — серверная команда управления;
- `analysis/` — ручные диагностические скрипты, не pytest-тесты.
