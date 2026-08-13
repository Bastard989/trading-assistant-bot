# Crisis Radar production runbook

## Перед выпуском

1. Сделать SQLite backup и SHA-256 sidecar.
2. Проверить восстановление backup в отдельный файл.
3. Выполнить shadow migration в PostgreSQL/pgvector и сверить row count/checksum.
4. Запустить полный pytest, Ruff, JS syntax, dependency audit и secret scan.
5. Убедиться, что все `CRISIS_RADAR_*_V2_ENABLED` можно выключить без изменения торговли.

## Проверки после запуска

- `/health/live` и `/health/ready` возвращают 200;
- authenticated `/api/crisis-radar/operations`: snapshot lag, coverage, sync errors и queue depth приемлемы;
- Mini App открывается через Telegram, RU/EN переводит всё приложение;
- уровни `Главное / Разобрать / Методика` работают на мобильной ширине;
- первая синхронизация не создаёт рыночное уведомление;
- деградация источника создаёт только data-health alert;
- Telegram retry не дублирует уже отправленное сообщение.

## Canary

Режимы: internal → shadow → visible beta → production. Минимум 14 дней фиксировать snapshot lag, coverage, source errors, alert duplicates, restarts, backup status и security incidents. Нельзя объявлять canary пройденным заранее.

## Откат

1. Выключить `CRISIS_RADAR_V2_ENABLED` или конкретный component flag.
2. Перезапустить API и bot; торговые функции продолжают работать на прежнем контуре.
3. При миграционной проблеме остановить запись, сохранить проблемную БД, восстановить проверенный backup.
4. PostgreSQL shadow не удалять: он не является рабочим источником до отдельного подтверждённого cutover.

## Инциденты

- `insufficient_data`: проверить missing regions/groups и source sync errors;
- stale snapshot: проверить job queue, API limits и сетевую доступность;
- duplicate alerts: проверить unique event key, delivery status и cooldown;
- ungrounded agent response: сохранить rejection evidence, не показывать как факт;
- GDELT 429: bounded retry выполняется автоматически; при исчерпании попыток
  `discovery_source_failures` остаётся warning, а официальный числовой и
  official-news coverage продолжают работу.
- New York Fed GSCPI: `research_source_failures` остаётся отдельным warning;
  отключённый candidate-v15 не должен маскироваться под отказ обязательного
  числового контура и не меняет live stage.
- Binance/Bybit USDC/USDT: оба сборщика имеют собственный sync-run с
  `access_type=research_candidate`; их сбои попадают в
  `research_source_failures`, не ухудшают статус обязательных BTC/ETH
  price/OI/funding запросов и не теряются после перезапуска. Оба candidate-v16
  ряда выключены и не запускают snapshot.
