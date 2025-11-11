# Руководство по логированию YouGile MCP Server

## Обзор

YouGile MCP Server теперь включает подробную систему логирования, которая записывает все операции API и MCP в файлы для отладки и мониторинга.

## Расположение логов

Логи сохраняются в директории `logs/` в корне проекта:

```
yougile-mcp/
├── logs/
│   ├── yougile_mcp_20241110_203000.log
│   ├── yougile_mcp_20241110_203015.log
│   └── ...
```

Каждый запуск сервера создает новый лог-файл с временной меткой.

## Уровни логирования

### DEBUG (самый подробный)
- Все HTTP запросы и ответы
- Параметры запросов и размеры ответов
- Детали аутентификации
- Внутренние операции

### INFO
- Запуск сервера
- Вызовы MCP инструментов
- Успешные операции
- Общий прогресс

### WARNING
- Неудачные попытки аутентификации
- Повторные попытки запросов
- Отсутствующие конфигурации

### ERROR
- Ошибки API (401, 403, 404, 429, 500)
- Сетевые ошибки
- Ошибки валидации
- Исключения с полным traceback

## Формат логов

### В файле (подробный)
```
2024-11-10 20:30:15 | INFO     | yougile_mcp.server:main:681 | YouGile MCP Server Starting
2024-11-10 20:30:15 | DEBUG    | yougile_mcp.core.client:request:82 | API Request: GET /api-v2/users
2024-11-10 20:30:16 | DEBUG    | yougile_mcp.core.client:request:101 | API Response: 200 for GET /api-v2/users
2024-11-10 20:30:16 | INFO     | yougile_mcp.server:list_users:153 | list_users completed successfully, returned 5 users
```

### В консоли (упрощенный)
```
20:30:15 | INFO     | YouGile MCP Server Starting
20:30:15 | DEBUG    | API Request: GET /api-v2/users
20:30:16 | INFO     | list_users completed successfully, returned 5 users
```

## Что логируется

### 1. Запуск сервера
- Конфигурация сервера
- Наличие учетных данных
- Инициализация аутентификации

### 2. Аутентификация
- Проверка API ключа
- Создание нового API ключа
- Сохранение учетных данных
- Ошибки аутентификации

### 3. API запросы
- Метод и URL
- Параметры запроса (с маскировкой паролей)
- Тело запроса (с маскировкой чувствительных данных)
- Код ответа
- Размер ответа

### 4. MCP операции
- Вызовы инструментов
- Результаты выполнения
- Ошибки и исключения

### 5. Ошибки
- HTTP ошибки с деталями
- Сетевые проблемы
- Таймауты
- Полные stack traces для отладки

## Безопасность

Система логирования автоматически маскирует чувствительные данные:
- Пароли (`***MASKED***`)
- API ключи (`***MASKED***`)
- Токены (`***MASKED***`)
- Секреты (`***MASKED***`)

## Настройка уровня логирования

По умолчанию используется уровень `DEBUG` для максимальной детализации.

Для изменения уровня отредактируйте `src/utils/logger.py`:

```python
logger = setup_logger(
    name="yougile_mcp",
    log_level="INFO",  # Измените на INFO, WARNING, ERROR
    console_output=True
)
```

## Примеры использования

### Просмотр последнего лога
```bash
# Windows PowerShell
Get-Content logs\yougile_mcp_*.log -Tail 50

# Linux/Mac
tail -f logs/yougile_mcp_*.log
```

### Поиск ошибок
```bash
# Windows PowerShell
Select-String -Path "logs\*.log" -Pattern "ERROR"

# Linux/Mac
grep "ERROR" logs/*.log
```

### Фильтрация по MCP инструменту
```bash
# Windows PowerShell
Select-String -Path "logs\*.log" -Pattern "list_users"

# Linux/Mac
grep "list_users" logs/*.log
```

## Отладка проблем

### Проблема: MCP инструмент не работает

1. Найдите лог-файл последнего запуска
2. Найдите строку с именем инструмента (например, `list_users`)
3. Проверьте следующие строки на наличие ошибок
4. Проверьте HTTP запросы и ответы

Пример:
```
20:30:15 | INFO     | MCP Tool called: list_users
20:30:15 | DEBUG    | API Request: GET /api-v2/users
20:30:15 | ERROR    | API Error 401: Invalid API key
20:30:15 | ERROR    | list_users failed: Authentication error
```

### Проблема: Ошибка аутентификации

Ищите в логах:
```
=== Initializing authentication ===
Email: SET/MISSING
Password: SET/MISSING
Company ID: SET/MISSING
```

### Проблема: Сетевые ошибки

Ищите:
```
Network error (attempt 1/4): Connection refused
Request timeout after 4 attempts
```

## Ротация логов

Логи не удаляются автоматически. Для очистки старых логов:

```bash
# Windows PowerShell
Remove-Item logs\*.log -Force

# Linux/Mac
rm logs/*.log
```

Или оставьте только последние N файлов:

```bash
# Windows PowerShell
Get-ChildItem logs\*.log | Sort-Object LastWriteTime -Descending | Select-Object -Skip 10 | Remove-Item

# Linux/Mac
ls -t logs/*.log | tail -n +11 | xargs rm
```

## Производительность

Логирование оптимизировано для минимального влияния на производительность:
- Асинхронная запись в файл
- Буферизация вывода
- Ленивое форматирование строк
- Маскировка данных только при необходимости

## Поддержка

При сообщении о проблемах приложите соответствующие фрагменты логов для быстрой диагностики.
