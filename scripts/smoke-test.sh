#!/usr/bin/env bash
# smoke-test.sh — проверка работающего экземпляра после deploy.
#   scripts/smoke-test.sh [--env FILE]
# Проверяет: frontend, backend (live/ready), PostgreSQL, Redis, LiveKit, ASR,
# и «внутреннюю сессию»: backend создаёт тестовую встречу без реального
# пользователя, выдаёт LiveKit-токен, пропускает синтетическую реплику через
# Redis → ingest → БД и убирает за собой (POST /internal/v1/smoke).
# Ничего не создаёт за пределами текущего проекта.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
while [ $# -gt 0 ]; do case "$1" in --env) ENV_FILE="$2"; shift 2 ;; *) die "Неизвестный аргумент: $1" ;; esac; done
load_env "$ENV_FILE"; validate_project_name
require_vars WEB_PORT LIVEKIT_HTTP_PORT POSTGRES_USER POSTGRES_DB INTERNAL_API_TOKEN

FAILS=0
check() { # check "описание" команда...
  local d="$1"; shift
  if "$@" >/dev/null 2>&1; then ok "$d"; else fail "$d"; FAILS=$((FAILS+1)); fi
}
WEB_ADDR="${WEB_BIND_ADDR:-127.0.0.1}"; [ "$WEB_ADDR" = "0.0.0.0" ] && WEB_ADDR="127.0.0.1"

check "frontend: страница отдаётся" curl -fsS -m 8 "http://${WEB_ADDR}:${WEB_PORT}/"
check "web: healthz" curl -fsS -m 5 "http://${WEB_ADDR}:${WEB_PORT}/healthz"
check "backend: liveness (через web)" curl -fsS -m 5 "http://${WEB_ADDR}:${WEB_PORT}/api/v1/health/live"
check "backend: readiness (PostgreSQL+Redis+LiveKit+ASR)" curl -fsS -m 10 "http://${WEB_ADDR}:${WEB_PORT}/api/v1/health/ready"
check "внутренний API недоступен снаружи (web отдаёт 404)" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -m 5 http://${WEB_ADDR}:${WEB_PORT}/internal/v1/smoke)\" = 404 ]"
check "PostgreSQL: pg_isready" dc exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
redis_ping() { dc exec -T redis redis-cli ping | grep -q PONG; }
check "Redis: PONG" redis_ping
check "LiveKit: HTTP отвечает (loopback ${LIVEKIT_HTTP_PORT})" curl -fsS -m 5 "http://127.0.0.1:${LIVEKIT_HTTP_PORT}/"
check "ASR: процесс жив (/healthz)" dc exec -T asr python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz',timeout=4).status==200 else 1)"

READY="$(dc exec -T asr python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8090/readyz',timeout=4).read().decode())" 2>&1 || true)"
if printf '%s' "$READY" | grep -q '"model_loaded": *true'; then ok "ASR: модель загружена, inference готов"; else fail "ASR: модель не готова ($READY)"; FAILS=$((FAILS+1)); fi

SMOKE="$(dc exec -T -e TOKEN="$INTERNAL_API_TOKEN" backend python -c "
import os,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/smoke',method='POST',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(urllib.request.urlopen(r,timeout=60).read().decode())" 2>&1 || true)"
if printf '%s' "$SMOKE" | grep -q '"ok": *true'; then ok "Внутренняя тестовая сессия: встреча, токен LiveKit, реплика→БД"; else fail "Внутренняя сессия не прошла: $SMOKE"; FAILS=$((FAILS+1)); fi

[ "$FAILS" -eq 0 ] && { ok "SMOKE TEST ПРОЙДЕН"; exit 0; } || { fail "SMOKE TEST: ошибок — $FAILS"; exit 1; }
