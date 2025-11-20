# Миграция с YouGile в Redmine (черновик)

Этот документ описывает текущий рабочий сценарий подготовки локальной БД из YouGile. 
Пока что он **не включает сам Redmine**, только наполнение локальной Postgres-БД и приём вебхуков.

## Предварительные требования

- Docker и Docker Compose установлены на машине.
- В корне репозитория настроен файл `.env` (см. `.env.example`), в том числе:
  - `YOUGILE_EMAIL`, `YOUGILE_PASSWORD`, `YOUGILE_COMPANY_ID`
  - `YOUGILE_API_KEY` (если уже сгенерирован)
  - `YOUGILE_WEBHOOK_PUBLIC_URL` — **полный URL вебхука**, например:
    `https://yg-wh-<env>.teamgram.ru/webhook/yougile`

## Шаг 1. Поднять Postgres и сервер вебхуков

Из корня проекта:

```powershell
docker compose up -d db
docker compose up --build webhooks
```

При успешном старте в логах (`logs/webhook_server.log` и вывод контейнера) должны быть строки:

- инициализация БД вебхуков:
  - `Webhook DB initialized at postgresql+asyncpg://yougile:yougile@db/yougile_webhooks`
- автосоздание подписки (если нет активной):
  - `Created webhook subscription for <YOUGILE_WEBHOOK_PUBLIC_URL> with all events: {...}`
  или
  - `Webhook subscription for <YOUGILE_WEBHOOK_PUBLIC_URL> is present`

## Шаг 2. Проверка приёма вебхуков

1. Сделать любое действие в YouGile (создать/изменить задачу, комментарий и т.п.).
2. Проверить, что события попадают в БД `yougile_webhooks`:

```powershell
docker compose exec db psql -U yougile -d yougile_webhooks -c "SELECT COUNT(*) FROM webhook_events;"
```

Для просмотра последних событий:

```powershell
docker compose exec db psql -U yougile -d yougile_webhooks -c "SELECT id, event_type, entity_type, entity_id, received_at, processed FROM webhook_events ORDER BY received_at DESC LIMIT 10;"
```

Если счётчик растёт и видны новые строки, приём вебхуков работает.

## Шаг 3. Одноразовый импорт полной базы из YouGile

Этот шаг выполняется **вручную**. Он наполняет основную БД `yougile` данными из YouGile.

Из корня проекта:

```powershell
# при необходимости активировать venv
# .\.venv\Scripts\Activate.ps1

# запустить импорт во внутри-docker Postgres
docker compose run --rm cli import all-projects --db "postgresql+asyncpg://yougile:yougile@db/yougile"
```

Импорт:

- создаст схему в БД `yougile` (таблицы проектов, досок, задач и т.д.),
- выкачает все данные из YouGile через API,
- сохранит их в `yougile`.

> **Важно:** импорт на текущем этапе считается **ручной операцией**, 
> его необходимо запускать осознанно. Повторный запуск может повлиять 
> на уже имеющиеся данные (при изменениях в схеме/логике импорта).

## Шаг 4. Догонка изменений после импорта (catch-up)

После завершения импорта необходимо обработать вебхуки, которые прилетели **во время импорта**.  
Для этого используется команда `webhooks catch-up`:

```powershell
# Из корня проекта (локально или в докере)
docker compose run --rm cli webhooks catch-up
```

Эта команда:

- читает все необработанные события из `webhook_events` (где `processed = false`),
- **применяет изменения в локальную БД `yougile`** (создаёт/обновляет задачи, проекты, комментарии и т.д.),
- **автоматически подтягивает недостающие сущности** из YouGile API при FK-ошибках,
- **синхронизирует всех пользователей и стикеры** компании в начале,
- помечает события как обработанные (`processed = true`).

### Возможности catch-up

- **FK-резолвинг**: Если событие ссылается на несуществующую сущность (например, задача на несуществующую колонку), система автоматически запросит её из API и создаст всю цепочку зависимостей (колонка → доска → проект).
- **Prefetch**: В начале синхронизируются все пользователи и стикеры компании, чтобы избежать FK-ошибок.
- **Idempotency**: Повторная обработка событий безопасна — используется upsert (merge).

### Опциональные параметры

- `--since "2025-11-20T10:00:00"` — обработать только события после указанного времени.
- `--no-mark-processed` — режим просмотра без изменения флага `processed` (dry-run).
- `--json` — вывести результат в JSON.

Пример с фильтром по времени:

```powershell
docker compose run --rm cli webhooks catch-up --since "2025-11-20T10:00:00"
```

---

## Шаг 5. Автоматическая синхронизация в реальном времени

После запуска webhook-сервера **все изменения в YouGile автоматически синхронизируются** в локальную БД:

1. **Webhook прилетает** → записывается в `webhook_events`
2. **Сразу обрабатывается** → изменения применяются в БД `yougile`
3. **Помечается `processed = true`**

### Что синхронизируется автоматически

- ✅ Создание/обновление/удаление задач
- ✅ Перемещение задач между колонками
- ✅ Изменение статусов задач (completed/archived)
- ✅ Создание/обновление проектов, досок, колонок
- ✅ Создание комментариев (включая системные)
- ✅ Обновление стикеров
- ✅ Изменения пользователей и департаментов

### Проверка автоматической синхронизации

```powershell
# Переместить задачу в YouGile и сразу проверить в БД
docker compose exec db psql -U yougile -d yougile -c "SELECT id, title, column_id FROM tasks WHERE id = 'TASK_ID';"

# Посмотреть последние обработанные события
docker compose exec db psql -U yougile -d yougile_webhooks -c "SELECT id, event_type, entity_id, processed, processed_at FROM webhook_events ORDER BY processed_at DESC LIMIT 10;"

# Посмотреть задачу с иерархией (проект → доска → колонка)
docker compose exec db psql -U yougile -d yougile -c "
SELECT 
  t.id, t.title, 
  c.title as column, 
  b.title as board, 
  p.title as project
FROM tasks t
LEFT JOIN columns c ON t.column_id = c.id
LEFT JOIN boards b ON c.board_id = b.id
LEFT JOIN projects p ON b.project_id = p.id
WHERE t.id = 'TASK_ID';
"
```

### Логи автоматической обработки

```powershell
docker compose logs -f webhooks
```

Должны быть сообщения:
```
Webhook received: event=task-moved id=...
Processing event #123 immediately...
Event #123 processed successfully
```

---

## Дальнейшие шаги (план)

Следующие шаги пока в разработке и будут добавлены в этот документ позже:

1. **Постоянный live-sync YouGile → локальная БД → Redmine**:
   - применение событий из локальной БД к Redmine.
2. **Сценарий одноразовой миграции в Redmine**:
   - экспорт из локальной БД `yougile` в Redmine (проекты, задачи, пользователи и т.д.),
   - временный период совместной работы по вебхукам до окончательного перехода.
