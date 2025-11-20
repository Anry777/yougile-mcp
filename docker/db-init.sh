#!/bin/bash
set -e

echo "Running Alembic migrations for yougile database..."
cd /app
alembic upgrade head

echo "Migrations complete!"
