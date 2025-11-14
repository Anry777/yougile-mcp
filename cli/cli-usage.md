# YouGile CLI — руководство и примеры

Этот документ описывает все команды CLI из каталога `cli` и примеры их использования в среде Windows с локальным виртуальным окружением проекта.

## Запуск

Рекомендуемый способ (через локальный venv):

```powershell
E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli <команда> [подкоманда] [опции]
```

---

## Команда: projects — операции с проектами

Общие флаги группы:

- --json

Подкоманды:

- list — вывести список проектов компании
  - Параметры:
    - --json
  - Примеры:
  ```powershell
  E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli projects list
  E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli projects list --json
  ```

CLI автоматически подхватывает переменные из файла `.env` (формат KEY=VALUE) в корне проекта, если их нет в окружении.

## Требуемые переменные окружения

- YOUGILE_API_KEY — API-ключ YouGile
- YOUGILE_COMPANY_ID — ID компании
- YOUGILE_PROJECT_ID — ID проекта (опционально, можно переопределять флагом `--project-id`)
- YOUGILE_EMAIL / YOUGILE_PASSWORD — для команд auth (получение ключей) или автосоздания ключа
- YOUGILE_AUTO_CREATE_API_KEY=1 — опционально, для автосоздания ключа при запуске CLI (если заданы логин/пароль/компания)

Во многих командах доступен флаг `--json` для машинно-читаемого вывода.

---

## Команда: tasks — операции с задачами

Общие флаги группы:

- --project-id <UUID>
- --json

Подкоманды:

- list — вывести задачи проекта (агрегация по всем колонкам проекта)
  - Параметры:
    - --limit <int> (по умолчанию 50)
    - --offset <int> (0)
    - --column-id <str>
    - --assigned-to <str>
    - --title <str>
    - --include-deleted
    - --json
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli tasks --project-id <PROJECT_ID> list --limit 100
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli tasks --project-id <PROJECT_ID> list --assigned-to <USER_ID> --json
    ```

- get — получить задачу по ID (с проверкой принадлежности проекту)
  - Параметры:
    - --id <UUID> (обязательно)
    - --json
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli tasks --project-id <PROJECT_ID> get --id <TASK_ID>
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli tasks --project-id <PROJECT_ID> get --id <TASK_ID> --json
    ```

- comments-by-title — получить комментарии (сообщения чата) задачи по названиям доски/колонки/задачи
  - Параметры:
    - --board <str> (обязательно) — название доски
    - --column <str> (обязательно) — название колонки
    - --task <str> (обязательно) — название задачи
    - --json
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli tasks --project-id <PROJECT_ID> comments-by-title --board "aand86@gmail.com" --column "Закупки" --task "РБ Создание доп расходов на приход" --json
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli tasks comments-by-title --board "aand86@gmail.com" --column "Закупки" --task "РБ Создание доп расходов на приход"
    ```

---

## Команда: boards — операции с досками

Общие флаги группы:

- --project-id <UUID>
- --json

Подкоманды:

- sync-unfinished — создать/обновить целевую доску и скопировать незавершённые задачи, синхронизируя колонки (названия и цвета)
  - Параметры:
    - --source-title <str> (по умолчанию "Все задачи")
    - --target-title <str> (по умолчанию "Незавершенные")
    - --dry-run
    - --json
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli boards --project-id <PROJECT_ID> sync-unfinished --dry-run
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli boards --project-id <PROJECT_ID> sync-unfinished
    ```

- ensure-user-boards — создать доски для пользователей, у которых есть задачи на целевой доске (имя доски = имя пользователя)
  - Параметры:
    - --target-title <str> (по умолчанию "Незавершенные")
    - --dry-run
    - --json
  - Пример:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli boards --project-id <PROJECT_ID> ensure-user-boards --json
    ```

- distribute-unfinished-by-user — разложить незавершённые задачи с целевой доски по личным доскам пользователей, сохраняя соответствующие колонки
  - Параметры:
    - --target-title <str> (по умолчанию "Незавершенные")
    - --dry-run
    - --json
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli boards --project-id <PROJECT_ID> distribute-unfinished-by-user --dry-run
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli boards --project-id <PROJECT_ID> distribute-unfinished-by-user
    ```

Примечания:

- Идемпотентность по ключу (columnId, title) — дубликаты не создаются.
- Цвет колонок синхронизируется, если значение валидно (1–16).

---

## Команда: webhooks — управление вебхуками

Общие флаги группы:

- --json

Подкоманды:

- create — создать подписку
  - Параметры:
    - --url <str> (обязательно)
    - --event <str> (обязательно), например `task-*` или `.*`
    - --json
  - Пример:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli webhooks create --url https://example.com/webhook/yougile --event task-* --json
    ```

- list — список подписок
  - Параметры:
    - --json
  - Пример:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli webhooks list
    ```

- delete — пометить подписку удалённой
  - Параметры:
    - --id <UUID> (обязательно)
    - --json
  - Пример:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli webhooks delete --id <WEBHOOK_ID>
    ```

- delete-all — пометить удалёнными все подписки
  - Параметры:
    - --json
  - Пример:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli webhooks delete-all --json
    ```

- update — обновить параметры подписки (отправляются только указанные поля)
  - Параметры:
    - --id <UUID> (обязательно)
    - --url <str>
    - --event <str>
    - --disabled | --enabled
    - --deleted | --restore
    - --json
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli webhooks update --id <WEBHOOK_ID> --url https://new.example/webhook/yougile
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli webhooks update --id <WEBHOOK_ID> --disabled
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli webhooks update --id <WEBHOOK_ID> --restore
    ```

---

## Команда: auth — утилиты аутентификации

Общие флаги группы:

- --json

Подкоманды:

- keys — получить список API-ключей через POST /auth/keys/get
  - Параметры:
    - --login <email> (если не задан — берётся из YOUGILE_EMAIL)
    - --password <str> (если не задан — берётся из YOUGILE_PASSWORD)
    - --company-id <UUID> (если не задан — берётся из YOUGILE_COMPANY_ID)
    - --json
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli auth keys --login user@example.com --password **** --company-id <COMPANY_ID>
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli auth keys --login user@example.com --password **** --company-id <COMPANY_ID> --json
    ```

- set-api-key — записать YOUGILE_API_KEY в .env
  - Взаимоисключающие режимы:
    - --key <str> — записать явное значение ключа
    - --from-latest — получить самый свежий ключ и записать его
      - Доп. параметры для --from-latest: --login, --password, --company-id
  - Примеры:
    ```powershell
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli auth set-api-key --key <API_KEY>
    E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli auth set-api-key --from-latest --login user@example.com --password **** --company-id <COMPANY_ID>
    ```

---

## Подсказки и диагностика

- Используйте `--json` для интеграций/скриптов.
- При ошибке учётных данных убедитесь, что переменные окружения заданы или присутствуют в `.env`.
- Для сетевых сценариев (вебхуки) убедитесь, что сервер доступен по публичному URL и путь совпадает (`/webhook/yougile`).

---

## Файлы реализации CLI

- Точка входа: `cli/__main__.py`
- Реализация подкоманд: `cli/tasks.py`, `cli/boards.py`, `cli/webhooks.py`, `cli/auth.py`
- Дефолты CLI: `cli/config.py`

## Автосправка (--help)

Для синхронизации с текущей версией CLI ниже приведены выдержки из `--help`.

### yougile-cli --help

```text
usage: yougile-cli [-h] [--json] {tasks,boards,webhooks,auth,projects,import} ...

YouGile CLI utilities

positional arguments:
  {tasks,boards,webhooks,auth,projects,import}
    tasks               Task operations
    boards              Board operations
    webhooks            Webhooks operations
    auth                Authentication utilities
    projects            Project operations
    import              Import data into local DB

options:
  -h, --help            show this help message and exit
  --json                Output JSON
```

---

## Команда: import — импорт проекта в локальную БД

Общие флаги группы:

- --json

Подкоманды:

- project — импорт всего проекта в локальную SQLite БД
  - Параметры:
    - --project-id <UUID> — ID проекта (если не указан, берётся из окружения/дефолта)
    - --db <path> — путь к SQLite файлу (по умолчанию ./yougile_local.db)
    - --reset — полностью пересоздать данные проекта в БД перед импортом
    - --prune — удалить локальные записи, которых нет в облаке (синхронизация)
    - --json
  - Примеры:
  ```powershell
  E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli import project --project-id <PROJECT_ID> --db .\yougile_local.db --reset --prune --json
  E:\Python\yougile-mcp\venv\Scripts\python.exe -m cli import project --db .\yougile_local.db
  ```

### yougile-cli tasks --help

```text
usage: yougile-cli tasks [-h] [--json] [--project-id PROJECT_ID] {list,get,comments-by-title} ...

positional arguments:
  {list,get,comments-by-title}
    list                List tasks
    get                 Get task by id
    comments-by-title   Get task comments by board/column/task titles

options:
  -h, --help            show this help message and exit
  --json                Output JSON
  --project-id PROJECT_ID
                        Project UUID to scope all operations
```

### yougile-cli boards --help

```text
usage: yougile-cli boards [-h] [--json] [--project-id PROJECT_ID] {sync-unfinished,ensure-user-boards,distribute-unfinished-by-user} ...

positional arguments:
  {sync-unfinished,ensure-user-boards,distribute-unfinished-by-user}
    sync-unfinished     Ensure 'Незавершенные' mirrors columns from 'Все задачи' and copy unfinished tasks
    ensure-user-boards  Create boards for each user having tasks on target board (title = user name)
    distribute-unfinished-by-user
                        Copy unfinished tasks from target board to per-user boards, preserving columns

options:
  -h, --help            show this help message and exit
  --json                Output JSON
  --project-id PROJECT_ID
                        Project UUID to scope all operations
```

### yougile-cli webhooks --help

```text
usage: yougile-cli webhooks [-h] [--json] {create,list,delete,delete-all,update} ...

positional arguments:
  {create,list,delete,delete-all,update}
    create              Create webhook subscription
    list                List webhook subscriptions
    delete              Delete webhook subscription
    delete-all          Delete all webhook subscriptions (mark deleted=true)
    update              Update webhook subscription

options:
  -h, --help            show this help message and exit
  --json                Output JSON
```

### yougile-cli auth --help

```text
usage: yougile-cli auth [-h] [--json] {keys,set-api-key} ...

positional arguments:
  {keys,set-api-key}
    keys              List API keys via POST /auth/keys/get
    set-api-key       Write YOUGILE_API_KEY to .env

options:
  -h, --help          show this help message and exit
  --json              Output JSON
