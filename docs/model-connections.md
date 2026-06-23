# Model connections design

Цель вкладки Mini App — дать владельцу простой экран подключения LLM/vision-моделей без редактирования `.env` и без ручных перезапусков для каждого переключения.

## Пользовательский сценарий

1. Пользователь открывает вкладку `Модели`.
2. Видит карточки:
   - `OpenAI API` — облачная модель по ключу.
   - `Local OpenAI-compatible` — локальный сервер с endpoint вида `/v1/models` и `/v1/responses` или `/v1/chat/completions`.
   - `Manual/offline` — без модели, бот использует ручные шаблоны.
3. В каждой карточке есть свернутая инструкция:
   - что установить/где взять ключ;
   - какие поля заполнить;
   - как проверить соединение;
   - что будет отправляться в модель.
4. Пользователь сохраняет подключение.
5. Backend проверяет соединение и получает список доступных моделей.
6. Пользователь выбирает модель для задач:
   - `vision_trade_extraction` — распознавание сделки по скрину;
   - `journal_summary` — разбор дневника;
   - `obsidian_report` — генерация расширенных отчетов;
   - `trade_review` — LLM-комментарий к сетапу.
7. Сохраненные подключения можно включать/выключать и переключать активную модель.

## Минимальная структура данных

```text
model_connections
  id
  user_id
  name
  provider              openai | openai_compatible | offline
  base_url
  api_key_encrypted
  status                active | disabled
  created_at
  updated_at

model_catalog
  id
  connection_id
  model
  capabilities          vision,json,text
  last_seen_at

model_task_bindings
  user_id
  task
  connection_id
  model
```

## Безопасность

- API keys нельзя отдавать обратно в Mini App.
- UI показывает только маску: `sk-...abcd`.
- Ключи хранятся на backend в зашифрованном виде или через системное secret-хранилище.
- Все операции требуют Telegram auth текущего пользователя.
- Проверка `/v1/models` не должна логировать ключ, полный base URL с секретами или prompt.
- Для локальных моделей base URL разрешается только из allowlist-схем: `http://127.0.0.1`, `http://localhost`, private LAN или явно настроенный HTTPS.

## API

```text
GET  /api/model-connections
POST /api/model-connections
POST /api/model-connections/{id}/test
POST /api/model-connections/{id}/refresh-models
POST /api/model-connections/{id}/activate
POST /api/model-connections/{id}/disable
PUT  /api/model-task-bindings/{task}
```

## Первый релиз

- Страница `Модели` в Mini App.
- Добавление OpenAI API connection.
- Добавление OpenAI-compatible connection по `base_url`.
- Тест соединения.
- Получение списка моделей.
- Выбор активной модели для `vision_trade_extraction`.
- Telegram `/open` использует выбранную модель, а если её нет — текущий ручной fallback.

## Второй релиз

- Поддержка нескольких задач и отдельных моделей под каждую задачу.
- Статус здоровья модели.
- История последних ошибок подключения.
- Кнопка “проверить скрин на тестовом изображении”.
- Расширенные инструкции для локальных runtimes.
