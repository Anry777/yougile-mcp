#!/bin/bash
set -e

echo "Running Alembic migrations for yougile database..."
cd /app

# Основной кейс: нормальный alembic upgrade head
if alembic upgrade head; then
  echo "Migrations complete!"
  exit 0
fi

echo "Alembic upgrade failed, trying to stamp existing schema as 0001_initial and retry..."

# Для уже существующей схемы без alembic_version:
# помечаем базовый ревижн как применённый и ещё раз пробуем upgrade head
alembic stamp 0001_initial
alembic upgrade head

echo "Migrations complete after stamp!"
