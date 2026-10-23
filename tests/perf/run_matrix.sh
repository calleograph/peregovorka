#!/usr/bin/env bash
# Матрица нагрузочных испытаний: для каждой конфигурации свежая база, сервер, прогон. Нужны Docker (postgres, redis) и venv с зависимостями backend + asyncpg + psutil.
# Использование: [PROFILE=heavy|client] [RAMP=20] tests/perf/run_matrix.sh <python> <каталог результатов> "<users:workers:duration> …"   например "100:1:40 500:1:60 1000:4:60 2000:4:60"
set -uo pipefail
PY="${1:?python}"; OUT="${2:?каталог результатов}"; PLAN="${3:-100:1:40 500:1:60}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; mkdir -p "$OUT"
docker rm -f pg-perf rd-perf >/dev/null 2>&1
docker run -d --name pg-perf -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=pg -p 55432:5432 --tmpfs /var/lib/postgresql/data:size=2g postgres:16-alpine -c max_connections=${PG_MAX_CONNECTIONS:-400} -c shared_buffers=256MB >/dev/null
docker run -d --name rd-perf -p 56379:6379 redis:7-alpine >/dev/null
for _ in $(seq 1 30); do docker exec pg-perf pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for item in $PLAN; do
  IFS=: read -r N W D <<<"$item"
  docker exec pg-perf psql -U postgres -c "DROP DATABASE IF EXISTS pg" -c "CREATE DATABASE pg" >/dev/null 2>&1; docker exec rd-perf redis-cli flushall >/dev/null
  rm -rf /tmp/pg-perf-data; mkdir -p /tmp/pg-perf-data
  PG_URL="postgresql+asyncpg://postgres:pw@127.0.0.1:55432/pg" REDIS_URL="redis://127.0.0.1:56379/0" USERS=$((N+100)) PORT=18080 LLM_DELAY=4 UVICORN_WORKERS=$W \
    "$PY" "$ROOT/tests/perf/devserver_perf.py" >"$OUT/server-$N-$W.log" 2>&1 &
  SRV=$!
  for _ in $(seq 1 60); do curl -fs localhost:18080/api/v1/health/db >/dev/null 2>&1 && break; sleep 1; done
  sleep 3
  PIDS="$(pgrep -P "$SRV" | tr '\n' ',')$SRV"
  "$PY" "$ROOT/tests/perf/loadtest.py" --users "$N" --duration "$D" --profile "${PROFILE:-heavy}" --ramp "${RAMP:-20}" --heavy 8 --pg "postgresql://postgres:pw@127.0.0.1:55432/pg" --pids "$PIDS" --out "$OUT/result-$N-$W.json" >"$OUT/run-$N-$W.log" 2>&1
  echo "== $N пользователей, процессов backend: $W → код $?"
  kill -INT "$SRV" 2>/dev/null; sleep 4; kill -9 $(pgrep -P "$SRV") "$SRV" 2>/dev/null
done
docker rm -f pg-perf rd-perf >/dev/null 2>&1
