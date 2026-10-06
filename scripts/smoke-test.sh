#!/usr/bin/env bash
# smoke-test.sh — сквозная проверка работающего экземпляра (не только Docker healthcheck) с итоговой таблицей.
#   scripts/smoke-test.sh [--env FILE] [--login ЛОГИН] [--no-public]
#
# Проверяется: Frontend; Backend; PostgreSQL; Redis; LDAP/LDAPS (bind сервисной учётки); ASR (процесс, модель, тестовый
# инференс на 1 с тишины); LiveKit HTTP; LiveKit /rtc/v1 — НАСТОЯЩИЙ WebSocket Upgrade (101) напрямую, через web и через
# публичный адрес (404 = устаревший LiveKit, SDK уйдёт в медленный запасной путь; 400/426 = прокси не передаёт Upgrade);
# RTC TCP (подключение); RTC UDP (статус «configured»: наличие слушающего порта; доступность с клиентов проверить нельзя);
# параметры ядра (UDP-буферы); TLS публичного URL (без -k: доверие к цепочке, имя хоста, срок, полнота цепочки);
# тестовая комната (внутренняя сессия без реального пользователя).
# С --login ЛОГИН: реальная аутентификация через AD (пароль без эха, нигде не сохраняется/не логируется).
# Токены и пароли в вывод не попадают. Код возврата: 0 — ошибок нет (предупреждения допустимы), 1 — есть FAIL.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/verifylib.sh"
LOGIN_USER=""; NO_PUBLIC=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --login) LOGIN_USER="$2"; shift 2 ;;
    --no-public) NO_PUBLIC=1; shift ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
require_vars WEB_PORT LIVEKIT_HTTP_PORT POSTGRES_USER POSTGRES_DB INTERNAL_API_TOKEN

T_NAME=(); T_ST=(); T_NOTE=(); FAILS=0; WARNS=0
rec() { # rec ИМЯ СТАТУС [ПОЯСНЕНИЕ]
  T_NAME+=("$1"); T_ST+=("$2"); T_NOTE+=("${3:-}")
  case "$2" in OK|configured) ok "$1: ${3:-$2}" ;; WARNING) WARNS=$((WARNS+1)); warn "$1: ${3:-}" ;; SKIP) info "$1: ${3:-пропущено}" ;; *) FAILS=$((FAILS+1)); fail "$1: ${3:-}" ;; esac
}
WEB_ADDR="${WEB_BIND_ADDR:-127.0.0.1}"; [ "$WEB_ADDR" = "0.0.0.0" ] && WEB_ADDR="127.0.0.1"
BASE="http://${WEB_ADDR}:${WEB_PORT}"
http_code() { curl -s -o /dev/null -m 8 -w '%{http_code}' "$@" 2>/dev/null || echo 000; }
bexec() { dc exec -T -e TOKEN="$INTERNAL_API_TOKEN" backend python -c "$1" 2>/dev/null; }

log "-- frontend и backend --"
c="$(http_code "$BASE/")"; [ "$c" = 200 ] && rec "Frontend" OK "страница отдаётся (HTTP 200)" || rec "Frontend" FAIL "HTTP $c на $BASE/"
c="$(http_code "$BASE/api/v1/health/live")"; r="$(curl -s -m 10 "$BASE/api/v1/health/ready" 2>/dev/null)"
if [ "$c" = 200 ] && printf '%s' "$r" | grep -Eq '"status": ?"(ready|degraded)"'; then
  v="$(curl -s -m 5 "$BASE/api/v1/version" 2>/dev/null)"
  rec "Backend" OK "live/ready; версия $(printf '%s' "$v" | grep -o '"version": *"[^"]*"' | cut -d'"' -f4) commit $(printf '%s' "$v" | grep -o '"commit": *"[^"]*"' | cut -d'"' -f4)"
else rec "Backend" FAIL "live=$c ready=$(printf '%s' "$r" | cut -c1-120)"; fi
[ "$(http_code "$BASE/internal/v1/smoke")" = 404 ] || rec "Внутренний API снаружи" FAIL "/internal/ доступен снаружи — должен быть 404"

log "-- данные --"
dc exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 && rec "PostgreSQL" OK "pg_isready" || rec "PostgreSQL" FAIL "не отвечает"
dc exec -T redis redis-cli ping 2>/dev/null | grep -q PONG && rec "Redis" OK "PONG" || rec "Redis" FAIL "не отвечает"
if alembic_verify exec; then rec "Миграции (Alembic)" OK "ревизия ${ALEMBIC_CUR} = head"; else rec "Миграции (Alembic)" FAIL "текущая «${ALEMBIC_CUR:-?}», head «${ALEMBIC_HEAD:-?}»"; fi

log "-- LDAP, ASR --"
DIAG="$(bexec "
import os,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/diag?deep=1',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(urllib.request.urlopen(r,timeout=60).read().decode())" || true)"
printf '%s' "$DIAG" | grep -Eq '"ldap": ?\{"ok": ?true' && rec "LDAP/LDAPS" OK "bind сервисной учётки выполнен (сертификат проверен)" || rec "LDAP/LDAPS" FAIL "нет связи или bind не удался (scripts/logs.sh backend)"
read -r acode abody <<<"$(svc_http asr http://127.0.0.1:8090/readyz)"
if [ "$acode" = 200 ] && printf '%s' "$abody" | grep -q '"model_loaded": *true'; then
  ST="$(dc exec -T asr python -c "
import urllib.request
r=urllib.request.Request('http://127.0.0.1:8090/selftest',method='POST')
print(urllib.request.urlopen(r,timeout=120).read().decode())" 2>/dev/null || true)"
  if printf '%s' "$ST" | grep -q '"ok": *true'; then rec "ASR" OK "модель $(printf '%s' "$abody" | grep -o '"name": *"[^"]*"' | head -1 | cut -d'"' -f4) загружена; тест-инференс $(printf '%s' "$ST" | grep -o '"ms": *[0-9]*' | grep -o '[0-9]*$') мс; потоки torch: $(printf '%s' "$abody" | grep -o '"torch_threads": *[0-9]*' | grep -o '[0-9]*$' || echo '?')"
  else rec "ASR" FAIL "модель загружена, но тестовый инференс не выполнен"; fi
else rec "ASR" WARNING "модель не загружена/не готова (HTTP $acode): звонки работают, транскрибации нет"; fi

log "-- LiveKit --"
c="$(http_code "http://127.0.0.1:${LIVEKIT_HTTP_PORT}/")"; [ "$c" = 200 ] && rec "LiveKit HTTP" OK "127.0.0.1:${LIVEKIT_HTTP_PORT}" || rec "LiveKit HTTP" FAIL "HTTP $c"
LKV="$(dc exec -T livekit livekit-server --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
cv="$(compat_check)"; [ -n "$LKV" ] && COMPAT_NOTE="$COMPAT_NOTE; фактически запущен ${LKV}"
[ "$cv" = OK ] && rec "Версия LiveKit" OK "$COMPAT_NOTE" || rec "Версия LiveKit" WARNING "$COMPAT_NOTE"
# 1) изнутри: backend → LiveKit (реальный WebSocket Upgrade на /rtc/v1)
IN_STATUS="$(printf '%s' "$DIAG" | grep -o '"rtc_v1_internal": *{[^}]*}' | grep -o '"status": *[0-9]*' | grep -o '[0-9]*$')"
case "${IN_STATUS:-}" in
  101) rec "LiveKit /rtc/v1 (напрямую)" OK "WebSocket Upgrade 101 — без 404 и запасного пути /rtc" ;;
  404) rec "LiveKit /rtc/v1 (напрямую)" FAIL "404: LiveKit Server устарел и не знает /rtc/v1 — SDK тратит секунды на запасной путь; обновите LIVEKIT_IMAGE_TAG (см. docs/COMPATIBILITY.md)" ;;
  "") rec "LiveKit /rtc/v1 (напрямую)" FAIL "WebSocket не установлен (нет ответа)" ;;
  *) rec "LiveKit /rtc/v1 (напрямую)" FAIL "HTTP ${IN_STATUS} вместо 101" ;;
esac
# 2) с хоста: через web-контейнер и через публичный адрес — тем же способом, каким ходит браузер
TOKEN="$(bexec "
import os,json,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/diag/token',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(json.load(urllib.request.urlopen(r,timeout=15))['token'])" | tr -d '\r\n')"
if [ -n "$TOKEN" ]; then
  Q="?access_token=${TOKEN}&auto_subscribe=0&sdk=js&protocol=15&version=smoke"
  code="$(ws_upgrade_code "${BASE}/livekit/rtc/v1${Q}")"; v="$(ws_verdict "$code")"
  rec "LiveKit WebSocket (через web)" "$v" "$WS_NOTE"
  PUB="${LIVEKIT_PUBLIC_URL:-}"
  if [ "$NO_PUBLIC" = 1 ] || [ -z "$PUB" ]; then rec "LiveKit WebSocket (публичный URL)" SKIP "не проверялся"
  else
    purl="$(printf '%s' "$PUB" | sed -e 's#^wss://#https://#' -e 's#^ws://#http://#')"
    code="$(ws_upgrade_code "${purl%/}/rtc/v1${Q}" 8)"; v="$(ws_verdict "$code")"
    rec "LiveKit WebSocket (публичный URL)" "$v" "${PUB}: $WS_NOTE"
  fi
else rec "LiveKit WebSocket" WARNING "не удалось получить тестовый токен у backend"; fi
TOKEN=""
# RTC TCP / UDP
if _listening tcp "${LIVEKIT_TCP_PORT:-0}"; then
  if timeout 3 bash -c "exec 3<>/dev/tcp/${LIVEKIT_NODE_IP:-127.0.0.1}/${LIVEKIT_TCP_PORT}" 2>/dev/null; then rec "RTC TCP" OK "${LIVEKIT_NODE_IP:-127.0.0.1}:${LIVEKIT_TCP_PORT} принимает подключения"
  else rec "RTC TCP" WARNING "порт слушается, но ${LIVEKIT_NODE_IP:-?}:${LIVEKIT_TCP_PORT} не отвечает с этого сервера (NAT/файрвол?) — проверьте с компьютера пользователя"; fi
else rec "RTC TCP" FAIL "порт ${LIVEKIT_TCP_PORT:-?} не слушается"; fi
if _listening udp "${LIVEKIT_UDP_PORT:-0}"; then rec "RTC UDP" configured "порт ${LIVEKIT_UDP_PORT} слушается на хосте; доступность UDP с клиентов автоматически проверить нельзя"
else rec "RTC UDP" FAIL "порт ${LIVEKIT_UDP_PORT:-?} не слушается"; fi
kv=""; kern_warn() { kv="$kv; $1"; }
kernel_tuning_check : kern_warn 2>/dev/null
if [ -z "$kv" ]; then rec "Параметры ядра (UDP-буферы)" OK "рекомендации выполнены"; else rec "Параметры ядра (UDP-буферы)" WARNING "ниже рекомендаций (scripts/tune-kernel.sh покажет команды; sysctl на shared-host вручную)"; fi

log "-- TLS --"
tls_check "${APP_PUBLIC_URL:-}"; rec "TLS (публичный URL)" "$TLS_STATUS" "$TLS_NOTE"

log "-- тестовая комната (внутренняя сессия без реального пользователя) --"
SMOKE="$(bexec "
import os,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/smoke',method='POST',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(urllib.request.urlopen(r,timeout=60).read().decode())" || true)"
printf '%s' "$SMOKE" | grep -q '"ok": *true' && rec "Тестовая комната" OK "встреча → токен LiveKit с identity пользователя → реплика Redis → БД (всё удалено)" || rec "Тестовая комната" FAIL "$(printf '%s' "$SMOKE" | cut -c1-160)"

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
    csrf="$(printf '%s' "$lbody" | grep -o '"csrf_token": *"[^"]*"' | cut -d'"' -f4)"
    c="$(curl -s -o /dev/null -m 10 -b "$jar" -w '%{http_code}' "$BASE/api/v1/auth/me")"
    c2="$(curl -s -m 10 -b "$jar" -w '\n%{http_code}' "$BASE/api/v1/rooms" | tail -1)"
    curl -s -o /dev/null -m 10 -b "$jar" -X POST -H "X-CSRF-Token: $csrf" -H "Origin: ${APP_PUBLIC_URL:-}" "$BASE/api/v1/auth/logout"
    [ "$c" = 200 ] && [ "$c2" = 200 ] && rec "Вход через AD" OK "аутентификация, /auth/me, список комнат, выход" || rec "Вход через AD" FAIL "/auth/me=$c /rooms=$c2"
  else rec "Вход через AD" FAIL "HTTP $lcode: $(printf '%s' "$lbody" | grep -o '"message": *"[^"]*"' | head -1)"; fi
fi

# ----------------------------------------------------------------------------- итоговая таблица
log; log "================ Итог проверки ================"
pad="................................"
for i in "${!T_NAME[@]}"; do
  n="${T_NAME[$i]}"; printf '%s %s %s\n' "$n" "${pad:${#n}}" "${T_ST[$i]}"
done
log "==============================================="
if [ "$FAILS" -eq 0 ]; then
  [ "$WARNS" -gt 0 ] && warn "Предупреждений: $WARNS (не блокируют; см. пояснения выше)"
  ok "SMOKE TEST ПРОЙДЕН"; exit 0
fi
fail "SMOKE TEST: ошибок — $FAILS"; exit 1
