#!/usr/bin/env bash
# verifylib.sh — проверка МиГРАЦИЙ и итоговая проверка развёртывания («доказать, что система работает»).
# Только чтение: ничего не меняет в системе. Используется install.sh (этапы migrations и verify) и scripts/verify.sh.
# Секреты не выводятся.

# ------------------------------------------------------------------------- Alembic
# stdin → первая ревизия из вывода `alembic current|heads` (строки вида «0002 (head)» или «0002»). Логи alembic игнорируются.
alembic_rev() { grep -E '^[0-9A-Za-z_]+( \(head\))?[[:space:]]*$' | head -1 | awk '{print $1}'; }

# alembic_verify exec|run — сравнивает текущую ревизию БД с head. Результат: ALEMBIC_CUR, ALEMBIC_HEAD; код 0 — совпадают.
#  exec — в запущенном backend; run — одноразовый контейнер (когда backend ещё не запущен).
alembic_verify() {
  local how="${1:-exec}" cur heads
  if [ "$how" = "run" ]; then
    cur="$(dc run --rm --no-deps -T backend alembic current 2>&1 || true)"; heads="$(dc run --rm --no-deps -T backend alembic heads 2>&1 || true)"
  else
    cur="$(dc exec -T backend alembic current 2>&1 || true)"; heads="$(dc exec -T backend alembic heads 2>&1 || true)"
  fi
  ALEMBIC_CUR="$(printf '%s\n' "$cur" | alembic_rev)"; ALEMBIC_HEAD="$(printf '%s\n' "$heads" | alembic_rev)"
  [ -n "$ALEMBIC_CUR" ] && [ -n "$ALEMBIC_HEAD" ] && [ "$ALEMBIC_CUR" = "$ALEMBIC_HEAD" ]
}

# --------------------------------------------------------------------- итоговая проверка
VERIFY_FAILS=0; VERIFY_WARNINGS=()
v_ok()   { ok "$*"; }
v_warn() { warn "$*"; VERIFY_WARNINGS+=("$*"); }
v_fail() { fail "$*"; VERIFY_FAILS=$((VERIFY_FAILS+1)); }

# svc_http сервис URL → «КОД ТЕЛО» (запрос из контейнера, внутри сети проекта)
svc_http() {
  dc exec -T "$1" python -c "
import urllib.request, urllib.error, sys
u = sys.argv[1]
try:
    r = urllib.request.urlopen(u, timeout=8); print(r.status, r.read().decode())
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode())
" "$2" 2>/dev/null || echo "000 "
}

_svc_state() { # → healthy|running|unhealthy|missing|<state>
  local cid; cid="$(dc ps -q "$1" 2>/dev/null | head -1)"
  [ -n "$cid" ] || { echo missing; return; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo missing
}

_listening() { # _listening tcp|udp порт → 0 если слушается
  command -v ss >/dev/null 2>&1 || return 2
  if [ "$1" = tcp ]; then ss -H -ltn "sport = :$2" 2>/dev/null | grep -q .; else ss -H -lun "sport = :$2" 2>/dev/null | grep -q .; fi
}

_curl_code() { curl -s -o /dev/null -m 8 -w '%{http_code}' "$@" 2>/dev/null || echo 000; }

verify_deployment() {
  VERIFY_FAILS=0; VERIFY_WARNINGS=()
  local s st code body web_addr="${WEB_BIND_ADDR:-127.0.0.1}" lk_bind="${LIVEKIT_BIND_ADDR:-0.0.0.0}"
  [ "$web_addr" = "0.0.0.0" ] && web_addr="127.0.0.1"

  log "-- контейнеры и Docker healthcheck --"
  for s in postgres redis backend asr livekit web; do
    st="$(_svc_state "$s")"
    case "$st" in
      healthy) v_ok "$s: контейнер healthy" ;;
      *) v_fail "$s: состояние «$st» (ожидалось healthy) — scripts/logs.sh $s" ;;
    esac
  done

  log "-- данные и миграции --"
  if dc exec -T postgres pg_isready -U "${POSTGRES_USER:-}" -d "${POSTGRES_DB:-}" >/dev/null 2>&1; then v_ok "PostgreSQL принимает подключения"; else v_fail "PostgreSQL не отвечает на pg_isready"; fi
  if dc exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then v_ok "Redis отвечает PONG"; else v_fail "Redis не отвечает"; fi
  if alembic_verify exec; then v_ok "Alembic: ${ALEMBIC_CUR} (head)"
  else v_fail "Alembic: текущая ревизия «${ALEMBIC_CUR:-?}», head «${ALEMBIC_HEAD:-?}» — миграции не применены/не подтверждены"; fi

  log "-- backend, ASR, версия --"
  read -r code body <<<"$(svc_http backend http://127.0.0.1:8000/api/v1/health/ready)"
  case "$body" in
    *'"status":"ready"'*) v_ok "Backend ready (PostgreSQL, Redis, LiveKit, ASR доступны)" ;;
    *'"status":"degraded"'*) v_warn "Backend degraded: ASR недоступен (транскрибация не работает): $(printf '%s' "$body" | cut -c1-200)" ;;
    *) v_fail "Backend не ready (HTTP $code): $(printf '%s' "$body" | cut -c1-200)" ;;
  esac
  read -r code body <<<"$(svc_http backend http://127.0.0.1:8000/api/v1/version)"
  if [ "$code" = 200 ]; then
    v_ok "Версия backend: $(printf '%s' "$body" | grep -o '"version":"[^"]*"' | cut -d'"' -f4) commit=$(printf '%s' "$body" | grep -o '"commit":"[^"]*"' | cut -d'"' -f4) built_at=$(printf '%s' "$body" | grep -o '"built_at":"[^"]*"' | cut -d'"' -f4)"
    case "$body" in *'"commit":"unknown"'*) v_warn "commit=unknown: образ собран без данных Git — неясно, какой код работает (пересоберите через install.sh/deploy.sh)" ;; esac
  else v_fail "Не удалось получить версию backend (HTTP $code)"; fi
  read -r code body <<<"$(svc_http asr http://127.0.0.1:8090/readyz)"
  if [ "$code" = 200 ] && printf '%s' "$body" | grep -q '"model_loaded":true'; then
    v_ok "ASR healthy; модель загружена: $(printf '%s' "$body" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4) ($(printf '%s' "$body" | grep -o '"device":"[^"]*"' | head -1 | cut -d'"' -f4))"
  else v_fail "ASR не готов или модель не загружена (HTTP $code): $(printf '%s' "$body" | cut -c1-200)"; fi

  log "-- HTTP-цепочка --"
  if command -v curl >/dev/null 2>&1; then
    code="$(_curl_code "http://${web_addr}:${WEB_PORT}/")"; [ "$code" = 200 ] && v_ok "Internal HTTP (web ${web_addr}:${WEB_PORT}): $code" || v_fail "Web ${web_addr}:${WEB_PORT} вернул $code"
    code="$(_curl_code "http://${web_addr}:${WEB_PORT}/api/v1/health/live")"; [ "$code" = 200 ] && v_ok "Backend через web (/api/v1/health/live): $code" || v_fail "Backend через web: $code"
    code="$(_curl_code "http://${web_addr}:${WEB_PORT}/internal/v1/smoke")"; [ "$code" = 404 ] && v_ok "Внутренний API снаружи закрыт (404)" || v_fail "/internal/ доступен снаружи (код $code) — должен быть 404"
    code="$(_curl_code -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "http://${web_addr}:${WEB_PORT}/livekit/rtc")"
    case "$code" in 101|400|401|403|426) v_ok "Сигналинг LiveKit через web (/livekit/) достижим (HTTP $code)" ;; *) v_fail "Сигналинг LiveKit через web недоступен (HTTP $code) — проверьте прокси /livekit/" ;; esac
    code="$(_curl_code "http://127.0.0.1:${LIVEKIT_HTTP_PORT}/")"; [ "$code" = 200 ] && v_ok "LiveKit HTTP healthy ($code)" || v_fail "LiveKit HTTP 127.0.0.1:${LIVEKIT_HTTP_PORT}: $code"
    if [ "${NGINX_MANAGE:-no}" = "yes" ]; then
      code="$(_curl_code -H "Host: ${NGINX_SERVER_NAME}" "http://127.0.0.1:${NGINX_LISTEN_PORT}/healthz")"
      [ "$code" = 200 ] && v_ok "Локальный reverse proxy (host nginx :${NGINX_LISTEN_PORT}) → web: $code" || v_fail "host nginx :${NGINX_LISTEN_PORT} → web вернул $code (nginx-site не активен или не перезагружен)"
    fi
  else v_warn "curl не найден — HTTP-проверки пропущены"; fi

  log "-- порты хоста --"
  _listening tcp "$LIVEKIT_HTTP_PORT"; case $? in 0) v_ok "LiveKit HTTP: 127.0.0.1:${LIVEKIT_HTTP_PORT} (только localhost)" ;; 2) v_warn "ss недоступен — порты не проверены" ;; *) v_fail "LiveKit HTTP 127.0.0.1:${LIVEKIT_HTTP_PORT} не слушается" ;; esac
  _listening tcp "$LIVEKIT_TCP_PORT"; case $? in 0) v_ok "LiveKit RTC TCP: ${lk_bind}:${LIVEKIT_TCP_PORT}" ;; 2) : ;; *) v_fail "LiveKit RTC TCP ${LIVEKIT_TCP_PORT} не слушается" ;; esac
  _listening udp "$LIVEKIT_UDP_PORT"; case $? in 0) v_ok "LiveKit RTC UDP: ${lk_bind}:${LIVEKIT_UDP_PORT}" ;; 2) : ;; *) v_fail "LiveKit RTC UDP ${LIVEKIT_UDP_PORT} не слушается" ;; esac
  v_warn "HTTP reverse proxy НЕ заменяет доступность RTC TCP/UDP: ${LIVEKIT_TCP_PORT}/tcp и ${LIVEKIT_UDP_PORT}/udp должны быть открыты клиентам напрямую (проверьте с клиентского компьютера, например: nc -vz <сервер> ${LIVEKIT_TCP_PORT})."

  log "-- параметры хоста для production --"
  kernel_tuning_check v_ok v_warn

  return "$VERIFY_FAILS"
}
