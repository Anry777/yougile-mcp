-- БД yougile создаётся самим образом через POSTGRES_DB, здесь создаём только БД для вебхуков
CREATE DATABASE yougile_webhooks;

-- даём пользователю yougile права на БД вебхуков
GRANT ALL PRIVILEGES ON DATABASE yougile_webhooks TO yougile;
