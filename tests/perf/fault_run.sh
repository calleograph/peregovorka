#!/usr/bin/env bash
# Отказоустойчивость под нагрузкой (профиль client): во время прогона по очереди — kill -9 процесса backend, который ведущий для фоновых задач;
# разрыв всех соединений PostgreSQL и 10 с его недоступности; перезапуск Redis. Результат — доля ошибок по 5-секундным интервалам (timeline_5s в JSON) и смена ведущего.
# Использование: tests/perf/fault_run.sh <python> <каталог результатов> [пользователей=1000] [процессов=4] [длительность=150]
set -uo pipefail
PY="${1:?python}"; OUT="${2:?каталог}"; N="${3:-1000}"; W="${4:-4}"; D="${5:-150}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; mkdir -p "$OUT"
docker rm -f pg-perf rd-perf >/dev/null 2>&1
docker run -d --name pg-perf -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=pg -p 55432:5432 --tmpfs /var/lib/postgresql/data:size=2g postgres:16-alpine -c max_connections=400 >/dev/null
docker run -d --name rd-perf -p 56379:6379 redis:7-alpine >/dev/null
for _ in $(seq 1 30); do docker exec pg-perf pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done; sleep 2
PG_URL="postgresql+asyncpg://postgres:pw@127.0.0.1:55432/pg" REDIS_URL="redis://127.0.0.1:56379/0" USERS=$((N+100)) PORT=18080 LLM_DELAY=4 UVICORN_WORKERS=$W \
  "$PY" "$ROOT/tests/perf/devserver_perf.py" >"$OUT/server.log" 2>&1 &
SRV=$!
for _ in $(seq 1 60); do curl -fs localhost:18080/api/v1/health/db >/dev/null 2>&1 && break; sleep 1; done; sleep 3
"$PY" "$ROOT/tests/perf/loadtest.py" --profile client --ramp 30 --users "$N" --duration "$D" --heavy 4 --pg "postgresql://postgres:pw@127.0.0.1:55432/pg" \
  --out "$OUT/result.json" >"$OUT/run.log" 2>&1 &
LT=$!
log() { echo "$(date +%T) $*" | tee -a "$OUT/faults.log"; }
leader() { docker exec rd-perf redis-cli get peregovorka:background-leader; }
# первые ~40 с: вход людей и установившийся режим (отсчёт шкалы — после входа всех в систему)
until grep -q "вошло" "$OUT/run.log" 2>/dev/null; do sleep 1; done
sleep 40
L=$(leader); P=$(echo "$L" | cut -d: -f2)
log "ведущий: $L — kill -9 процесса $P"; kill -9 "$P"
sleep 30; log "ведущий после: $(leader); процессов backend: $(pgrep -P $SRV | wc -l)"
# сбой PostgreSQL без потери данных (данные стенда в tmpfs): сервер рвёт все соединения — как при перезапуске — и затем 10 с не отвечает
log "PostgreSQL: разрыв всех соединений"; docker exec pg-perf psql -U postgres -tAc "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'" | tee -a "$OUT/faults.log"
sleep 5; log "PostgreSQL: недоступен 10 с"; docker pause pg-perf >/dev/null; sleep 10; docker unpause pg-perf >/dev/null; log "PostgreSQL снова отвечает"
sleep 30
log "перезапуск Redis"; docker restart -t 2 rd-perf >/dev/null; log "Redis снова запущен"
wait $LT; log "нагрузка завершена: код $?"
log "ведущий в конце: $(leader)"
kill -INT "$SRV" 2>/dev/null; sleep 5; kill -9 $(pgrep -P "$SRV") "$SRV" 2>/dev/null
docker rm -f pg-perf rd-perf >/dev/null 2>&1
