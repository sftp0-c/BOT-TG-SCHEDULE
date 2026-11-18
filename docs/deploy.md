# Production deployment

## Перед обновлением

```bash
cd /opt/bot-tg-schedule
bot-schedule-ctl status
bot-schedule-ctl backup
bot-schedule-ctl test
```

Проверьте, что `.env` и `data/state.json` не отслеживаются Git. Backup хранится
в `/opt/bot-tg-schedule/.backups` с закрытыми правами.

## Установка controller

```bash
sudo ln -sfn /opt/bot-tg-schedule/ops/tg-schedule-ctl /usr/local/bin/tg-schedule-ctl
sudo ln -sfn /usr/local/bin/tg-schedule-ctl /usr/local/bin/bot-schedule-ctl
```

Обе команды должны вести на tracked-скрипт проекта.

## Обновление

```bash
cd /opt/bot-tg-schedule
git fetch origin
git pull --ff-only
bot-schedule-ctl rebuild
bot-schedule-ctl status
bot-schedule-ctl logs 200
```

`rebuild` делает backup, строит target `test`, и только после успешного suite
пересобирает runtime. Массовую `/weekly_now all` для smoke-test не запускайте.

## Проверка

В логах должны появиться:

- запуск для нужной группы;
- `Start polling`;
- имя и ID бота;
- отсутствие traceback и постоянных рестартов.

Дополнительно:

```bash
bot-schedule-ctl exec python -c \
  'import config; config.reload(); print(config.validate())'
```

Команда должна вывести `[]`, не печатая секреты.

## Откат

1. Остановите неуспешный контейнер.
2. Верните предыдущий Git commit или сохранённый image ID.
3. Восстановите `.env` и `data` из последнего архива только если они были
   повреждены; обычный rollback кода не требует отката state.
4. Запустите контейнер и проверьте polling logs.

Не используйте `git reset --hard` в dirty production tree без предварительного
архива/patch: рабочая копия может содержать незакоммиченный релиз.

## State и права

Процесс контейнера работает как UID `10001`. Каталог `data` должен быть доступен
этому UID. `state.json` и `.bak` создаются с режимом `0600`. Храните backups вне
публичных каталогов: они содержат Telegram ID, имена и настройки рассылки.
