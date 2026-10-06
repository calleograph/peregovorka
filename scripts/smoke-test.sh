#!/usr/bin/env bash
# smoke-test.sh — сквозная проверка работающего экземпляра (не только Docker healthcheck).
#   scripts/smoke-test.sh [--env FILE] [--login ЛОГИН]
#
# Всегда: frontend; backend API (live/ready/version); PostgreSQL; Redis; LDAP (bind сервисной учётки);
# LiveKit (HTTP и сигналинг через web); ASR: процесс, загрузка модели GigaAM и короткий тестовый инференс
# (POST /selftest на 1 с синтетической тишины — пользовательских данных нет); «внутренняя сессия»: backend
# создаёт тестовые комнату/встречу без реального пользователя, выдаёт LiveKit-токен, пропускает синтетическую
# реплику Redis → БД и убирает за собой.
# С --login ЛОГИН: реальная аутентификация через AD — пароль запрашивается без эха, нигде не сохраняется и не
# логируется; затем /auth/me, список комнат, выход. Комнаты/встречи в этом режиме НЕ создаются.
# Пароли, токены и ключи в вывод не попадают.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
LOGIN_USER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --login) LOGIN_USER="$2"; shift 2 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
require_vars WEB_PORT LIVEKIT_HTTP_PORT POSTGRES_USER POSTGRES_DB INTERNAL_API_TOKEN

FAILS=0
check() { local d="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$d"; else fail "$d"; FAILS=$((FAILS+1)); fi; }
bad() { fail "$*"; FAILS=$((FAILS+1)); }
WEB_ADDR="${WEB_BIND_ADDR:-127.0.0.1}"; [ "$WEB_ADDR" = "0.0.0.0" ] && WEB_ADDR="127.0.0.1"
BASE="http://${WEB_ADDR}:${WEB_PORT}"

log "-- frontend и API --"
check "frontend: страница отдаётся" curl -fsS -m 8 "$BASE/"
check "web: healthz" curl -fsS -m 5 "$BASE/healthz"
check "backend: liveness (через web)" curl -fsS -m 5 "$BASE/api/v1/health/live"
check "backend: readiness (PostgreSQL+Redis+LiveKit+ASR)" curl -fsS -m 10 "$BASE/api/v1/health/ready"
check "backend: версия/commit" bash -c "curl -fsS -m 5 $BASE/api/v1/version | grep -q '\"commit\"'"
check "внутренний API снаружи закрыт (404)" bash -c "[ \"\$(curl -s -o /dev/null -w '%{http_code}' -m 5 $BASE/internal/v1/smoke)\" = 404 ]"

log "-- данные --"
check "PostgreSQL: pg_isready" dc exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
redis_ping() { dc exec -T redis redis-cli ping | grep -q PONG; }
check "Redis: PONG" redis_ping
alembic_ok() { alembic_verify exec; }
check "Alembic: ревизия БД = head" alembic_ok

log "-- LDAP, LiveKit, ASR --"
DIAG="$(dc exec -T -e TOKEN="$INTERNAL_API_TOKEN" backend python -c "
import os,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/diag',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(urllib.request.urlopen(r,timeout=30).read().decode())" 2>/dev/null || true)"
if printf '%s' "$DIAG" | grep -q '"ldap":{"ok":true'; then ok "LDAP: bind сервисной учётки по LDAPS выполнен (сертификат проверен)"; else bad "LDAP: нет связи/bind не удался (подробности: scripts/logs.sh backend)"; fi
printf '%s' "$DIAG" | grep -q '"livekit":{"ok":true' && ok "LiveKit: backend видит сервер (HTTP)" || bad "LiveKit: backend не видит сервер"
code="$(curl -s -o /dev/null -m 8 -w '%{http_code}' -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "$BASE/livekit/rtc" 2>/dev/null || echo 000)"
case "$code" in 101|400|401|403|426) ok "LiveKit: сигналинг через web достижим (HTTP $code)" ;; *) bad "LiveKit: сигналинг через web недоступен (HTTP $code)" ;; esac
check "LiveKit: HTTP (loopback ${LIVEKIT_HTTP_PORT})" curl -fsS -m 5 "http://127.0.0.1:${LIVEKIT_HTTP_PORT}/"
check "ASR: процесс жив (/healthz)" dc exec -T asr python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz',timeout=4).status==200 else 1)"
read -r acode abody <<<"$(svc_http asr http://127.0.0.1:8090/readyz)"
if [ "$acode" = 200 ] && printf '%s' "$abody" | grep -q '"model_loaded":true'; then ok "ASR: модель загружена ($(printf '%s' "$abody" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4))"; else bad "ASR: модель не загружена/не готова (HTTP $acode)"; fi
ST="$(dc exec -T asr python -c "
import urllib.request
r=urllib.request.Request('http://127.0.0.1:8090/selftest',method='POST')
print(urllib.request.urlopen(r,timeout=120).read().decode())" 2>/dev/null || true)"
if printf '%s' "$ST" | grep -q '"ok":true'; then ok "ASR: тестовый инференс выполнен ($(printf '%s' "$ST" | grep -o '"ms":[0-9]*' | cut -d: -f2) мс)"; else bad "ASR: тестовый инференс не выполнен"; fi

log "-- внутренняя сессия (без реального пользователя) --"
SMOKE="$(dc exec -T -e TOKEN="$INTERNAL_API_TOKEN" backend python -c "
import os,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/smoke',method='POST',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(urllib.request.urlopen(r,timeout=60).read().decode())" 2>&1 || true)"
if printf '%s' "$SMOKE" | grep -q '"ok": *true'; then ok "Тестовые комната и встреча, токен LiveKit с identity пользователя, реплика → БД"; else bad "Внутренняя сессия не прошла: $(printf '%s' "$SMOKE" | cut -c1-200)"; fi

if [ -n "$LOGIN_USER" ]; then
  log "-- реальный вход через AD (пользователь: $LOGIN_USER) --"
  read -r -s -p "Пароль (не сохраняется и не логируется): " PW; echo
  jar="$(mktemp)"; trap 'rm -f "$jar"' EXIT
  esc() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
  resp="$(printf '{"login":"%s","password":"%s"}' "$(esc "$LOGIN_USER")" "$(esc "$PW")" \
          | curl -s -m 20 -c "$jar" -H 'Content-Type: application/json' -H "Origin: ${APP_PUBLIC_URL:-}" --data-binary @- -w '\n%{http_code}' "$BASE/api/v1/auth/login")"
  unset PW
  lcode="$(printf '%s' "$resp" | tail -1)"; lbody="$(printf '%s' "$resp" | sed '$d')"
  if [ "$lcode" = 200 ]; then
    ok "Аутентификация через AD выполнена (HTTP 200)"
    csrf="$(printf '%s' "$lbody" | grep -o '"csrf_token":"[^"]*"' | cut -d'"' -f4)"
    c="$(curl -s -o /dev/null -m 10 -b "$jar" -w '%{http_code}' "$BASE/api/v1/auth/me")"; [ "$c" = 200 ] && ok "Сессия действует (/auth/me)" || bad "/auth/me: HTTP $c"
    c="$(curl -s -m 10 -b "$jar" -w '\n%{http_code}' "$BASE/api/v1/rooms" | tail -1)"; [ "$c" = 200 ] && ok "Список доступных комнат получен" || bad "/rooms: HTTP $c"
    curl -s -o /dev/null -m 10 -b "$jar" -X POST -H "X-CSRF-Token: $csrf" -H "Origin: ${APP_PUBLIC_URL:-}" "$BASE/api/v1/auth/logout"; ok "Выход выполнен"
  else
    bad "Аутентификация не удалась (HTTP $lcode): $(printf '%s' "$lbody" | grep -o '"message":"[^"]*"' | head -1)"
  fi
fi

[ "$FAILS" -eq 0 ] && { ok "SMOKE TEST ПРОЙДЕН"; exit 0; } || { fail "SMOKE TEST: ошибок — $FAILS"; exit 1; }
