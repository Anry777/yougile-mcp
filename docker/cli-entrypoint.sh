#!/bin/bash
set -e

# Run Alembic migrations for local DB
echo "Running Alembic migrations..."
cd /app
alembic upgrade head

# Execute the command passed to the container
exec "$@"
