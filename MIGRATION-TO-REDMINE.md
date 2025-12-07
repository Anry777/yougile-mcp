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

---

## Состояние синхронизации с Redmine (фактическое)

### Что сделано

- **Общий пайплайн синка**
  - CLI-команда `sync redmine` поддерживает `--dry-run` и `--apply`.
  - Порядок сущностей: `users → projects → boards → memberships → tasks`.

- **Пользователи (users)**
  - Создаются/обновляются в Redmine по данным из локальной БД YouGile.
  - Идемпотентность: повторный запуск не создаёт дублей.
  - В локальной БД хранится роль (`admin` / `user`) из поля `isAdmin` YouGile.

- **Проекты и доски (projects, boards)**
  - Проект YouGile → корневой проект Redmine.
  - Доска YouGile → подпроект Redmine.
  - Для подпроектов включено наследование участников (`inherit_members = true`) как при создании, так и для уже существующих.

- **Участники проектов (memberships)**
  - Есть отдельный sync memberships.
  - Роли в Redmine берутся из настроек (`REDMINE_ADMIN_ROLE_NAME`, `REDMINE_USER_ROLE_NAME`).
  - Учитываются все пользователи, когда-либо назначавшиеся на задачи проекта (даже если помечены deleted/archived).

- **Задачи (tasks → issues в Redmine)**
  - Используется отдельный сервис `redmine_task_sync`.
  - Добавлена таблица `task_issue_links` (миграция `0006_add_task_issue_links`), которая хранит связь `YouGile task.id ↔ Redmine issue.id`.
  - Идемпотентность:
    - если линк есть → обновляем существующий issue;
    - если нет → создаём новый issue и записываем линк;
    - если связаный issue был удалён в Redmine → при 404 создаём новый и обновляем линк.
  - Маппинг полей:
    - Статус определяется из колонки задачи через `get_redmine_status` с эвристиками по подстрокам.
    - Ограничение длины темы: `subject` обрезается до 255 символов, «хвост» переносится в начало `description`.
    - `Task.created_at` маппится в `start_date` (формат `YYYY-MM-DD`).
    - Для завершённых задач (`task.completed == True` или финальный статус: `Решена`, `Согласовано`, `Закрыта` и т.п.):
      - `done_ratio = 100`;
      - `due_date = Task.completed_at` (если есть, формат `YYYY-MM-DD`).

### Что пока не реализовано

- Синхронизация **комментариев** задач в Redmine (журналы / notes).
- Синхронизация **файлов и вложений**.
- Обратная синхронизация изменений из **Redmine → YouGile**.
- Запись данных в кастомные поля Redmine (например, отдельное поле с ID задачи YouGile).
- Полноценный live-sync изменений через вебхуки напрямую в Redmine (сейчас синк задач выполняется батчевой CLI-командой).
- Перенос и отображение **спринтов** YouGile в Redmine (версии, кастомные поля или иная модель).
- Маппинг **автора задачи** из YouGile в автора issue Redmine (сейчас автором становится пользователь по API-ключу).
- Перенос **плановых дедлайнов** (due date/estimate) из YouGile, помимо `completed_at` для завершённых задач.
- Перенос **иерархии подзадач** и **связанных задач** (parent / relations между issues в Redmine).
- Обработка **удаления/архивации задач** в YouGile (соответствующие issues в Redmine сейчас остаются как есть и не закрываются/не удаляются автоматически).

### План задач по доработке синхронизации с Redmine

#### Высокий приоритет
- [ ] Реализовать синхронизацию комментариев задач (журналы / notes) в Redmine.
- [ ] Настроить запись ключевых данных в кастомные поля Redmine (в т.ч. ID задачи YouGile).
- [ ] Добавить полноценный live-sync изменений в Redmine через вебхуки (без ручных батчевых запусков).
- [ ] Реализовать маппинг автора задачи YouGile в автора issue Redmine.
- [ ] Реализовать обработку удаления/архивации задач в YouGile с отражением на связанные issues в Redmine (закрытие/удаление).

#### Средний приоритет
- [ ] Реализовать синхронизацию файлов и вложений задач.
- [ ] Реализовать обратную синхронизацию изменений из Redmine в YouGile (минимальный набор полей).
- [ ] Реализовать перенос плановых дедлайнов и оценок (estimate) из YouGile в Redmine.
- [ ] Реализовать перенос иерархии подзадач и связей задач (parent / relations между issues).

#### Низкий приоритет
- [ ] Реализовать перенос и отображение спринтов YouGile в Redmine (версии, кастомные поля или другая модель).

---

## Деплой yougile-mcp на VPS через Ansible (черновик)

Исходные файлы:

- `inventory.ini` — описывает VPS с yougile-mcp:

  ```ini
  [yougile_mcp]
  yougile-mcp-vps ansible_host=10.1.2.124 ansible_user=root
  ```

- `deploy-redmine.yml` — плейбук деплоя yougile-mcp (название файла историческое).

### Шаги деплоя

1. Установить Ansible на локальную машину (см. скрипт `install-ansible.sh` в корне репозитория).
2. Убедиться, что по SSH можно зайти на VPS:

   ```bash
   ssh root@10.1.2.124
   ```

3. Запустить плейбук из корня репозитория:

   ```bash
   ansible-playbook -i inventory.ini deploy-redmine.yml
   ```

При первом запуске плейбук:

- поставит Docker и зависимости на VPS,
- создаст пользователя `deploy` и структуру каталогов,
- сгенерирует для `deploy` SSH-ключ (`~deploy/.ssh/id_ed25519`) и выведет публичный ключ в вывод Ansible,
- склонирует репозиторий `yougile-mcp` в `/home/deploy/apps/yougile-mcp`,
- выполнит `docker compose pull` и `docker compose up -d` в этом каталоге.
