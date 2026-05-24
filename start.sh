#!/usr/bin/env bash
# Local dev startup — spins up Redis via Docker, then runs uvicorn with hot reload.
# For production use docker-compose up instead.
set -e

REDIS_CONTAINER="pawlog-redis"

# Start Redis if not already running
if ! docker ps --format '{{.Names}}' | grep -q "^${REDIS_CONTAINER}$"; then
  echo "Starting Redis container..."
  docker run -d --name "$REDIS_CONTAINER" -p 6379:6379 redis:7-alpine
else
  echo "Redis already running."
fi

# Wait for Redis to be ready
echo "Waiting for Redis to be ready..."
until docker exec "$REDIS_CONTAINER" redis-cli ping 2>/dev/null | grep -q PONG; do
  sleep 0.5
done
echo "Redis ready."

# Activate virtualenv if present
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
  source venv/bin/activate
fi

# Start FastAPI with hot reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
