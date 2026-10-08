#!/usr/bin/env bash
# verifylib.sh — проверка МиГРАЦИЙ и итоговая проверка развёртывания («доказать, что система работает»).
# Только чтение: ничего не меняет в системе. Используется install.sh (этапы migrations и verify) и scripts/verify.sh.
# Секреты не выводятся.

# Служебные переменные результата (устанавливаются функциями НАПРЯМУЮ, а не через $(...): значения из подоболочки не возвращаются).
# Инициализация здесь, чтобы `set -u` в вызывающих скриптах никогда не падал из-за необъявленной переменной.
COMPAT_STATUS=""; COMPAT_NOTE=""; WS_STATUS=""; WS_NOTE=""; TLS_STATUS=""; TLS_NOTE=""

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
VERIFY_FAILS=0; VERIFY_WARNINGS=(); VERIFY_STAGES=(); _STAGE_F0=0
# Этапы проверки с отдельным итогом PASS/FAIL: _stage_begin; …проверки…; _stage_end "Название"
_stage_begin() { _STAGE_F0=$VERIFY_FAILS; }
_stage_end() { VERIFY_STAGES+=("$1|$([ "$VERIFY_FAILS" -eq "$_STAGE_F0" ] && echo PASS || echo FAIL)"); }
print_verify_stages() {
  local e
  [ "${#VERIFY_STAGES[@]}" -gt 0 ] || return 0
  printf '
Проверки по этапам:
'
  for e in "${VERIFY_STAGES[@]}"; do printf '  %-5s %s
' "${e##*|}" "${e%|*}"; done
}
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

# ------------------------------------------------------------------ WebSocket / TLS / совместимость
# ws_upgrade_probe URL [ТАЙМАУТ] → WS_CODE (HTTP-код ответа на НАСТОЯЩИЙ WebSocket Upgrade; 101 = сервер и прокси его пропускают) и WS_BODY
# (первые 200 байт тела ответа без переводов строк). Вызывать НАПРЯМУЮ. URL подаётся curl через stdin (-K -), чтобы токен из query
# не попал в список процессов; схема http(s) (curl делает Upgrade сам).
ws_upgrade_probe() {
  local key bf; key="$(openssl rand -base64 16 2>/dev/null || echo dGhlIHNhbXBsZSBub25jZQ==)"
  bf="$(mktemp 2>/dev/null || echo "/tmp/ws-body.$$")"
  WS_CODE="$(printf 'url = "%s"\n' "$1" | curl -s -o "$bf" -m "${2:-6}" -w '%{http_code}' --http1.1 -K - \
      -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H "Sec-WebSocket-Key: $key" 2>/dev/null || true)"
  WS_BODY="$(head -c 200 "$bf" 2>/dev/null | tr -d '\r\n\000')"; rm -f "$bf"
  return 0
}
ws_upgrade_code() { ws_upgrade_probe "$@"; printf '%s' "$WS_CODE"; }

# ws_verdict КОД [direct|proxy] → WS_STATUS (OK|WARNING|FAIL) и WS_NOTE для WebSocket Upgrade на /rtc. Вызывать НАПРЯМУЮ (не через $(...)).
# Второй аргумент говорит, чей это ответ: «direct» — самого LiveKit (без прокси), «proxy» — через web/публичный адрес. Для proxy при известном
# результате прямого запроса (DIRECT_WS_CODE) вывод сравнивает: виноват прокси только если напрямую проходит (101), а через прокси нет.
ws_verdict() {
  local where="${2:-}" body="${WS_BODY:+ («${WS_BODY}»)}"
  WS_STATUS=FAIL; WS_NOTE="HTTP ${1:-?}"
  case "${1:-}" in
    101) WS_STATUS=OK; WS_NOTE="WebSocket Upgrade выполнен (101)" ;;
    401|403) WS_STATUS=WARNING; WS_NOTE="HTTP $1: сервер достигнут, но токен отклонён — проверьте LIVEKIT_API_KEY/SECRET" ;;
    404) WS_NOTE="HTTP 404: путь не найден${body} — для /rtc/v1 это LiveKit устаревшей версии (SDK уйдёт в медленный запасной путь /rtc), для проксированного адреса — прокси не передаёт /livekit/" ;;
    400|426|200|301|302)
      case "$where" in
        direct) WS_NOTE="HTTP $1 вместо 101${body}: это ответ самого LiveKit при обращении напрямую (без прокси) — искать в токене/ключах/версии LiveKit, а не в прокси" ;;
        proxy)
          if [ "${DIRECT_WS_CODE:-}" = 101 ]; then WS_NOTE="HTTP $1 вместо 101${body}, а напрямую LiveKit отвечает 101 — проблема в прокси (нужны proxy_http_version 1.1, Upgrade, Connection upgrade, путь /livekit/)"
          elif [ -n "${DIRECT_WS_CODE:-}" ]; then WS_NOTE="HTTP $1 вместо 101${body}; напрямую LiveKit тоже не даёт 101 (HTTP ${DIRECT_WS_CODE}) — это ответ самого LiveKit, прокси ни при чём"
          else WS_NOTE="HTTP $1 вместо 101${body}: результат прямого запроса неизвестен, поэтому нельзя сказать, виноват ли прокси (проверьте Upgrade/Connection на прокси и ответ LiveKit напрямую)"; fi ;;
        *) WS_NOTE="HTTP $1 вместо 101${body}: ответ LiveKit или прокси — сравните с запросом напрямую (scripts/smoke-test.sh это делает)" ;;
      esac ;;
    000|"") WS_STATUS=WARNING; WS_NOTE="нет ответа (адрес не разрешается/порт закрыт/таймаут) — с этого сервера проверить не удалось" ;;
  esac
  return 0
}

# ws_route_verdict КОД [direct|proxy] → проверка СУЩЕСТВОВАНИЯ маршрута /rtc/v1 (запрос без join_request, который передаёт только настоящий SDK).
# LiveKit 1.13 отвечает на него 400 «join_request is required» — это значит «маршрут есть»; устаревший сервер ответил бы 404 (SDK тратил бы секунды на /rtc).
ws_route_verdict() {
  local body="${WS_BODY:+ («${WS_BODY}»)}"
  case "${1:-}" in
    101) WS_STATUS=OK; WS_NOTE="маршрут /rtc/v1 существует (101)" ;;
    400) if printf '%s' "${WS_BODY:-}" | grep -qi 'join_request'; then WS_STATUS=OK; WS_NOTE="маршрут /rtc/v1 существует (LiveKit ответил 400 «join_request is required» — так он отвечает на запрос без параметров SDK)"
         else WS_STATUS=FAIL; WS_NOTE="HTTP 400${body} на /rtc/v1 — ответ без признака LiveKit; сравните с запросом напрямую"; fi ;;
    *) ws_verdict "${1:-}" "${2:-}" ;;
  esac
  return 0
}

# tls_check URL → TLS_STATUS (OK|WARNING|FAIL|SKIP) и TLS_NOTE. Без -k: проверяется доверие к цепочке и имя хоста; затем срок
# и полнота цепочки (openssl). Только чтение.
tls_check() {
  local url="$1" host port rc out vr
  TLS_STATUS=OK; TLS_NOTE=""
  case "$url" in
    https://*) ;;
    http://*) TLS_STATUS=WARNING; TLS_NOTE="публичный URL не HTTPS: браузер не даст доступ к микрофону/экрану (нужен защищённый контекст)"; return 0 ;;
    *) TLS_STATUS=SKIP; TLS_NOTE="URL не задан"; return 0 ;;
  esac
  command -v curl >/dev/null 2>&1 || { TLS_STATUS=SKIP; TLS_NOTE="curl не найден"; return 0; }
  host="${url#https://}"; host="${host%%/*}"; port=443
  case "$host" in *:*) port="${host##*:}"; host="${host%%:*}" ;; esac
  out="$(curl -sS -o /dev/null -m 10 -w '%{ssl_verify_result}' "https://${host}:${port}/" 2>&1)"; rc=$?
  vr="$(printf '%s' "$out" | grep -oE '[0-9]+$' | tail -1)"
  if [ "$rc" -eq 6 ] || [ "$rc" -eq 7 ] || [ "$rc" -eq 28 ]; then TLS_STATUS=WARNING; TLS_NOTE="не удалось подключиться к ${host}:${port} с этого сервера (curl код $rc)"; return 0; fi
  if [ "$rc" -eq 60 ] || [ "$rc" -eq 35 ] || [ "$rc" -eq 51 ] || [ "$rc" -eq 58 ]; then
    case "$vr" in
      10) TLS_STATUS=FAIL; TLS_NOTE="сертификат не принят: истёк срок действия (curl код $rc) — браузеры тоже его отвергнут" ;;
      62) TLS_STATUS=FAIL; TLS_NOTE="сертификат не принят: имя хоста ${host} не совпадает с сертификатом (curl код $rc) — браузеры тоже его отвергнут" ;;
      *) # цепочка не доверена САМИМ СЕРВЕРОМ (нет корневого/промежуточного сертификата в его хранилище или неполная цепочка): это не значит, что она плоха для браузеров
         TLS_STATUS=WARNING
         TLS_NOTE="этот сервер не доверяет цепочке сертификата (curl код $rc, проверка $vr): нет корневого/промежуточного сертификата в системном хранилище сервера или сервер отдаёт неполную цепочку. Если адрес открывается в браузере без предупреждений — это допустимо; для ASR/curl/мобильных клиентов может понадобиться fullchain" ;;
    esac
  fi
  if command -v openssl >/dev/null 2>&1; then
    local pem; pem="$(echo | openssl s_client -connect "${host}:${port}" -servername "$host" -showcerts 2>/dev/null)"
    local leaf; leaf="$(printf '%s\n' "$pem" | awk '/BEGIN CERTIFICATE/{f=1} f{print} /END CERTIFICATE/{exit}')"
    if [ -n "$leaf" ]; then
      if ! printf '%s\n' "$leaf" | openssl x509 -noout -checkend 0 >/dev/null 2>&1; then TLS_STATUS=FAIL; TLS_NOTE="${TLS_NOTE:+$TLS_NOTE; }срок действия сертификата истёк"
      elif ! printf '%s\n' "$leaf" | openssl x509 -noout -checkend $((14*86400)) >/dev/null 2>&1; then
        [ "$TLS_STATUS" = OK ] && TLS_STATUS=WARNING; TLS_NOTE="${TLS_NOTE:+$TLS_NOTE; }сертификат истекает менее чем через 14 дней"; fi
      if ! printf '%s\n' "$leaf" | openssl x509 -noout -checkhost "$host" 2>/dev/null | grep -q "does match"; then
        TLS_STATUS=FAIL; TLS_NOTE="${TLS_NOTE:+$TLS_NOTE; }сертификат не выдан для имени ${host}"; fi
      local ncerts; ncerts="$(printf '%s\n' "$pem" | grep -c 'BEGIN CERTIFICATE')"
      if [ "$ncerts" -le 1 ] && ! printf '%s\n' "$leaf" | openssl x509 -noout -issuer -subject 2>/dev/null | awk -F'= ' '/issuer/{i=$2} /subject/{s=$2} END{exit !(i==s)}'; then
        [ "$TLS_STATUS" = OK ] && TLS_STATUS=WARNING; TLS_NOTE="${TLS_NOTE:+$TLS_NOTE; }сервер отдаёт только свой сертификат без промежуточных (fullchain): браузеры могут догрузить цепочку, а ASR/curl/мобильные клиенты — нет"
      fi
    fi
  fi
  [ "$TLS_STATUS" = OK ] && TLS_NOTE="цепочка доверена, имя совпадает, срок действия в порядке"
  return 0
}

# compat_check — сравнение версии LiveKit с проверенной (deployment/compat.env). Результат: COMPAT_STATUS (OK|WARNING), COMPAT_NOTE.
# Вызывать НАПРЯМУЮ. Только информирование: решающая проверка — реальный WebSocket на /rtc/v1 (scripts/smoke-test.sh).
compat_check() {
  local f="${REPO_ROOT:-.}/deployment/compat.env" want have
  COMPAT_STATUS=WARNING; COMPAT_NOTE=""
  [ -r "$f" ] || { COMPAT_NOTE="deployment/compat.env не найден"; return 0; }
  want="$(grep -E '^TESTED_LIVEKIT_SERVER=' "$f" | cut -d= -f2)"; have="${LIVEKIT_IMAGE_TAG:-}"
  if [ "$have" = "$want" ]; then COMPAT_STATUS=OK; COMPAT_NOTE="LiveKit Server ${have} = проверенная версия"
  elif [ "$have" = "latest" ] || [ -z "$have" ]; then COMPAT_NOTE="LIVEKIT_IMAGE_TAG=${have:-не задан}: в production нужен проверенный тег ${want} (scripts/update.sh выставит его сам)"
  elif semver_lt "$have" "$want" 2>/dev/null; then COMPAT_NOTE="LiveKit Server ${have} СТАРШЕ проверенной ${want}: вход в комнату может замедляться (/rtc/v1 → 404). Обновите: scripts/update.sh"
  else COMPAT_NOTE="LiveKit Server ${have} новее проверенной ${want}: допустимо, но не проверялось; решающая проверка — scripts/smoke-test.sh"; fi
  return 0
}

# check_build_versions — сравнивает git HEAD с commit, «запечённым» в образах backend/asr/web (build ARG → ENV/version.json).
# Несовпадение или commit=unknown → предупреждение (образ собран вручную или не из этого commit): scripts/rebuild.sh.
check_build_versions() {
  local head b a w
  head="$(git_head_commit "$REPO_ROOT")"
  [ -n "$head" ] || { v_warn "git HEAD не определён — сравнение версий образов пропущено"; return 0; }
  read -r _ b <<<"$(svc_http backend http://127.0.0.1:8000/api/v1/version)"
  read -r _ a <<<"$(svc_http asr http://127.0.0.1:8090/readyz)"
  w="$(dc exec -T web wget -qO- http://127.0.0.1:8080/version.json 2>/dev/null || true)"
  local name val raw want ver; want="$(version_file_read "$REPO_ROOT/VERSION")"
  for name in backend asr web; do
    case "$name" in backend) raw="$b" ;; asr) raw="$a" ;; web) raw="$w" ;; esac
    val="$(printf '%s' "$raw" | grep -o '"commit": *"[^"]*"' | head -1 | cut -d'"' -f4)"
    ver="$(printf '%s' "$raw" | grep -o '"version": *"[^"]*"' | head -1 | cut -d'"' -f4)"
    if [ -n "$ver" ] && [ -n "$want" ] && [ "$ver" != "$want" ]; then v_warn "Образ $name показывает версию $ver, а в VERSION $want — пересоберите: scripts/rebuild.sh"; fi
    if [ -z "$val" ]; then v_warn "Версия образа $name не определена (старый образ без version.json/commit?) — пересоберите: scripts/rebuild.sh"
    elif [ "$val" = unknown ]; then v_warn "Образ $name: commit=unknown (собран без данных Git, например вручную командой docker compose build) — пересоберите: scripts/rebuild.sh"
    elif [ "$val" != "$head" ]; then v_warn "Образ $name собран из commit $val, а git HEAD = $head — пересоберите: scripts/rebuild.sh"
    else v_ok "Образ $name: версия ${ver:-?} · commit ${val:0:7} = git HEAD"; fi
  done
}

verify_deployment() {
  VERIFY_FAILS=0; VERIFY_WARNINGS=(); VERIFY_STAGES=()
  local s st code body web_addr="${WEB_BIND_ADDR:-127.0.0.1}" lk_bind="${LIVEKIT_BIND_ADDR:-0.0.0.0}"
  [ "$web_addr" = "0.0.0.0" ] && web_addr="127.0.0.1"

  log "-- контейнеры и Docker healthcheck --"
  _stage_begin
  for s in postgres redis backend asr livekit web; do
    st="$(_svc_state "$s")"
    case "$st" in
      healthy) v_ok "$s: контейнер healthy" ;;
      *) v_fail "$s: состояние «$st» (ожидалось healthy) — scripts/logs.sh $s" ;;
    esac
  done

  _stage_end "Контейнеры (healthcheck всех сервисов)"

  log "-- конфигурация web nginx --"
  _stage_begin
  if st="$(dc exec -T web nginx -t 2>&1)"; then v_ok "web nginx: конфигурация корректна (nginx -t)"
  else v_fail "web nginx: nginx -t не прошёл: $(printf '%s' "$st" | tr '
' ' ' | cut -c1-250)"; fi
  _stage_end "Конфигурация nginx (nginx -t)"

  log "-- данные и миграции --"
  if dc exec -T postgres pg_isready -U "${POSTGRES_USER:-}" -d "${POSTGRES_DB:-}" >/dev/null 2>&1; then v_ok "PostgreSQL принимает подключения"; else v_fail "PostgreSQL не отвечает на pg_isready"; fi
  if dc exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then v_ok "Redis отвечает PONG"; else v_fail "Redis не отвечает"; fi
  if alembic_verify exec; then v_ok "Alembic: ${ALEMBIC_CUR} (head)"
  else v_fail "Alembic: текущая ревизия «${ALEMBIC_CUR:-?}», head «${ALEMBIC_HEAD:-?}» — миграции не применены/не подтверждены"; fi

  log "-- права на каталоги данных (запись от имени сервисов) --"
  _stage_begin
  wbad="$(writable_probe backend "${WRITABLE_BACKEND_DIRS[@]}"; writable_probe asr "${WRITABLE_ASR_DIRS[@]}")"
  if [ -z "$wbad" ]; then v_ok "Каталоги данных доступны сервисам на запись (backend: ${WRITABLE_BACKEND_DIRS[*]##*/}; asr: ${WRITABLE_ASR_DIRS[*]##*/})"
  else v_warn "Сервис не может писать в: $(printf '%s' "$wbad" | tr '
' ' ') — права на каталоги данных неверны (сертификаты, вложения, записи не сохранятся). В браузере: Состояние системы → «Исправить автоматически»; на сервере: sudo scripts/repair.sh data_dirs"; fi
  _stage_end "Права на каталоги данных"

  log "-- backend, ASR, версия --"
  _stage_begin
  read -r code body <<<"$(svc_http backend http://127.0.0.1:8000/api/v1/health/ready)"
  case "$body" in
    *'"status":"ready"'*) v_ok "Backend ready (PostgreSQL, Redis, LiveKit, ASR доступны)" ;;
    *'"status":"degraded"'*) v_warn "Backend degraded: ASR недоступен (транскрибация не работает): $(printf '%s' "$body" | cut -c1-200)" ;;
    *) v_fail "Backend не ready (HTTP $code): $(printf '%s' "$body" | cut -c1-200)" ;;
  esac
  _stage_end "API /api/v1/health/ready"
  read -r code body <<<"$(svc_http backend http://127.0.0.1:8000/api/v1/version)"
  if [ "$code" = 200 ]; then
    v_ok "Версия backend: $(printf '%s' "$body" | grep -o '"version":"[^"]*"' | cut -d'"' -f4) commit=$(printf '%s' "$body" | grep -o '"commit":"[^"]*"' | cut -d'"' -f4) built_at=$(printf '%s' "$body" | grep -o '"built_at":"[^"]*"' | cut -d'"' -f4)"
    case "$body" in *'"commit":"unknown"'*) v_warn "commit=unknown: образ собран без данных Git — неясно, какой код работает (пересоберите через install.sh/deploy.sh)" ;; esac
  else v_fail "Не удалось получить версию backend (HTTP $code)"; fi
  read -r code body <<<"$(svc_http asr http://127.0.0.1:8090/readyz)"
  if [ "$code" = 200 ] && printf '%s' "$body" | grep -q '"model_loaded":true'; then
    v_ok "ASR healthy; модель загружена: $(printf '%s' "$body" | grep -o '"name":"[^"]*"' | head -1 | cut -d'"' -f4) ($(printf '%s' "$body" | grep -o '"device":"[^"]*"' | head -1 | cut -d'"' -f4))"
  else v_fail "ASR не готов или модель не загружена (HTTP $code): $(printf '%s' "$body" | cut -c1-200)"; fi

  check_build_versions

  log "-- локальная LLM (Qwen3 0.6B) --"
  verify_local_llm

  log "-- SIP-телефония --"
  verify_sip

  log "-- HTTP-цепочка --"
  if command -v curl >/dev/null 2>&1; then
    code="$(_curl_code "http://${web_addr}:${WEB_PORT}/")"; [ "$code" = 200 ] && v_ok "Internal HTTP (web ${web_addr}:${WEB_PORT}): $code" || v_fail "Web ${web_addr}:${WEB_PORT} вернул $code"
    _stage_begin
    code="$(_curl_code "http://${web_addr}:${WEB_PORT}/api/v1/health/live")"; [ "$code" = 200 ] && v_ok "Backend через web (/api/v1/health/live): $code" || v_fail "Backend через web: $code"
    # Запрос ТАК, как его присылает внешний прокси: со схемой https и цепочкой адресов. Ошибка в обработке X-Forwarded-* (например, цикл переменной
    # $xfp в map) проявляется только при непустом заголовке и пустым запросом без него не ловится.
    code="$(_curl_code -H 'X-Forwarded-Proto: https' -H 'X-Forwarded-For: 203.0.113.7, 10.0.0.1' "http://${web_addr}:${WEB_PORT}/api/v1/health/live")"
    [ "$code" = 200 ] && v_ok "Backend через web со схемой https и X-Forwarded-For: $code" || v_fail "Backend через web при X-Forwarded-Proto: https вернул $code (ошибка обработки заголовков прокси в frontend/nginx.conf?)"
    _stage_end "Прокси web → backend (как через внешний proxy, https)"
    code="$(_curl_code "http://${web_addr}:${WEB_PORT}/internal/v1/smoke")"; [ "$code" = 404 ] && v_ok "Внутренний API снаружи закрыт (404)" || v_fail "/internal/ доступен снаружи (код $code) — должен быть 404"
    _stage_begin
    code="$(_curl_code -H 'X-Forwarded-Proto: https' -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "http://${web_addr}:${WEB_PORT}/livekit/rtc")"
    case "$code" in 101|400|401|403|426) v_ok "Сигналинг LiveKit через web (/livekit/) достижим (HTTP $code)" ;; *) v_fail "Сигналинг LiveKit через web недоступен (HTTP $code) — проверьте прокси /livekit/" ;; esac
    _stage_end "Сигналинг LiveKit (WebSocket через web)"
    compat_check; if [ "$COMPAT_STATUS" = OK ]; then v_ok "$COMPAT_NOTE"; else v_warn "$COMPAT_NOTE"; fi
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
  realtime_config_check v_ok v_warn

  return "$VERIFY_FAILS"
}

# ---- запись в каталоги данных от имени сервисов. Владелец каталога на хосте — лишь косвенный признак: решает, может ли записать ПРОЦЕСС контейнера (uid 10001).
WRITABLE_BACKEND_DIRS=(/data/ca /data/chat-files /data/exports /data/recordings /data/updater)
WRITABLE_ASR_DIRS=(/data/recordings)
# writable_probe СЕРВИС КАТАЛОГ… — печатает каталоги, в которые процесс сервиса записать не смог (пробный файл создаётся и сразу удаляется).
# Остановленный сервис не считается нарушением (проверять нечем): такие каталоги не печатаются.
writable_probe() {
  local svc="$1" d; shift
  [ -n "$(dc ps -q "$svc" 2>/dev/null | head -1)" ] || return 0
  for d in "$@"; do
    dc exec -T "$svc" sh -c 'f="$1/.write-probe.$$"; : > "$f" && rm -f "$f"' _ "$d" >/dev/null 2>&1 || printf '%s:%s
' "$svc" "$d"
  done
}

# Локальная LLM — необязательная возможность: любые проблемы здесь — предупреждения (не отказ), чтобы отсутствие интернета при установке не ломало остальное.
verify_local_llm() {
  local st cid net code
  if ! llm_local_enabled; then v_ok "Локальная LLM отключена (LLM_LOCAL_ENABLED=no)"; return 0; fi
  st="$(llm_model_state)"
  if [ "$st" != ok ]; then
    v_warn "Локальная LLM: $(llm_state_text "$st"). Загрузить: Администрирование → Языковая модель (LLM) → «Скачать модель» или sudo scripts/models.sh --llm-only (нужен интернет). Остальная система работает."
    return 0
  fi
  v_ok "Локальная LLM: файл $(llm_file_name) на месте, размер и SHA-256 проверены"
  cid="$(dc ps -q llm-local 2>/dev/null | head -1)"
  if [ -z "$cid" ]; then v_warn "Контейнер llm-local не запущен (образ $(llm_image) не скачан или запуск отключён). Повторите scripts/update.sh при наличии интернета."; return 0; fi
  if [ "$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)" != running ]; then v_warn "Контейнер llm-local не работает — scripts/logs.sh llm-local"; return 0; fi
  code="$(dc exec -T backend python -c "
import urllib.request
try:
    print(urllib.request.urlopen('http://llm-local:8080/health', timeout=6).status)
except Exception as e:
    print(type(e).__name__)" 2>/dev/null | tr -d '\r\n')"
  if [ "$code" = 200 ]; then v_ok "llama.cpp отвечает (модель загружена)"; else v_warn "llama.cpp не готов (${code:-нет ответа}); сразу после запуска модель загружается до минуты — повторите проверку"; fi
  net="$(docker network inspect "${COMPOSE_PROJECT_NAME}_llm_internal" -f '{{.Internal}}' 2>/dev/null)"
  if [ "$net" = true ]; then v_ok "Сеть локальной LLM внутренняя (internal): выхода наружу нет, данные не покидают сервер"
  else v_warn "Сеть ${COMPOSE_PROJECT_NAME}_llm_internal не внутренняя или не найдена — проверьте compose.yml"; fi
  return 0
}

# SIP-телефония — необязательная возможность: «не включена» — это SKIP (v_ok с пометкой), а не отказ; проблемы включённой службы — предупреждения.
verify_sip() {
  local cid st
  if ! sip_enabled; then v_ok "SIP-телефония не включена (пропущено): служба и порты SIP/RTP не создавались"; return 0; fi
  cid="$(dc ps -q livekit-sip 2>/dev/null | head -1)"
  if [ -z "$cid" ]; then v_warn "SIP-телефония включена, но контейнер livekit-sip не запущен (образ $(sip_image) не скачан?). Администрирование → SIP-телефония → «Состояние»"; return 0; fi
  st="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"
  if [ "$st" != running ]; then v_warn "Контейнер livekit-sip: состояние «$st» — scripts/logs.sh livekit-sip"; return 0; fi
  v_ok "livekit-sip запущен: SIP $(sip_port)/udp+tcp, RTP $(sip_rtp)/udp"
  if [ "$(dc exec -T backend python -c "
import urllib.request
try:
    print(urllib.request.urlopen('http://livekit-sip:8081/', timeout=5).status)
except Exception as e:
    print(type(e).__name__)" 2>/dev/null | tr -d ' ')" = 200 ]; then v_ok "Служба SIP отвечает (связана с LiveKit и Redis)"; else v_warn "Служба SIP не отвечает на проверку работоспособности — scripts/logs.sh livekit-sip"; fi
  return 0
}
