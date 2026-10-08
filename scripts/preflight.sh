#!/usr/bin/env bash
# preflight.sh — проверка готовности хоста и конфигурации. НИЧЕГО НЕ МЕНЯЕТ.
#
# Использование:
#   scripts/preflight.sh [--env FILE] [--profile shared-host|standalone]
#                        [--phase pre|post] [--skip-network]
#
#   --phase pre   (по умолчанию) до первого запуска: ОС, ресурсы, Docker, порты,
#                 каталоги, LDAPS+сертификат, Compose-конфиг, конфликты имён, nginx.
#   --phase post  дополнительно проверяет доступность PostgreSQL и Redis
#                 в уже запущенном проекте.
#   --skip-network не обращаться к LDAP (для проверки в изоляции).
#
# Код возврата: 0 — критичных проблем нет (предупреждения возможны); 1 — есть FAIL.

set -uo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PHASE="pre"; SKIP_NET=0; PROFILE_ARG=""; PROBE_BUILD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --profile) PROFILE_ARG="$2"; shift 2 ;;
    --phase) PHASE="$2"; shift 2 ;;
    --skip-network) SKIP_NET=1; shift ;;
    --probe-build) PROBE_BUILD=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

N_OK=0; N_WARN=0; N_FAIL=0
pass()  { ok "$*"; N_OK=$((N_OK+1)); }
pwarn() { warn "$*"; N_WARN=$((N_WARN+1)); }
pfail() { fail "$*"; N_FAIL=$((N_FAIL+1)); }

load_env "$ENV_FILE"
PROFILE="${PROFILE_ARG:-${INSTALL_PROFILE:-}}"

# ---------------------------------------------------------------- конфигурация
log "== Конфигурация ($ENV_FILE) =="
case "$PROFILE" in shared-host|standalone) pass "Профиль: $PROFILE" ;; *) pfail "INSTALL_PROFILE должен быть shared-host или standalone (сейчас: '${PROFILE}')" ;; esac

if [[ "${COMPOSE_PROJECT_NAME:-}" =~ ^[a-z][a-z0-9_-]{2,40}$ ]]; then
  pass "COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME}"
else
  pfail "COMPOSE_PROJECT_NAME некорректен или не задан (a-z0-9_-, с буквы)"
fi

REQUIRED=(APP_PUBLIC_URL DATA_ROOT WEB_BIND_ADDR WEB_PORT LIVEKIT_HTTP_PORT LIVEKIT_BIND_ADDR
          LIVEKIT_TCP_PORT LIVEKIT_UDP_PORT LIVEKIT_NODE_IP LIVEKIT_API_KEY LIVEKIT_API_SECRET
          LIVEKIT_PUBLIC_URL POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD REDIS_PASSWORD
          APP_MASTER_KEY INTERNAL_API_TOKEN)
for v in "${REQUIRED[@]}"; do
  if [ -z "${!v:-}" ]; then pfail "Не задана обязательная переменная $v"; fi
done
ph="$(env_placeholders "$ENV_FILE")"
if [ -n "$ph" ]; then
  pfail "В $ENV_FILE остались значения-заглушки CHANGE_ME (строки: $(printf '%s' "$ph" | cut -d: -f1 | tr '
' ' ')) — замените их"
else
  pass "Значений-заглушек CHANGE_ME нет"
fi
if [ -f "$ENV_FILE" ]; then
  perm="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo '')"
  if [ -n "$perm" ] && [ "$((8#$perm & 8#077))" -ne 0 ]; then
    pfail "Права $ENV_FILE = $perm: файл с секретами должен быть доступен только владельцу (chmod 600)"
  else
    pass "Права на .env ограничены (${perm:-?})"
  fi
fi
if [ -n "${LIVEKIT_API_SECRET:-}" ] && [ "${#LIVEKIT_API_SECRET}" -lt 32 ]; then
  pfail "LIVEKIT_API_SECRET короче 32 символов"
fi
if [ -n "${APP_MASTER_KEY:-}" ]; then
  if [ "$(printf '%s' "$APP_MASTER_KEY" | base64 -d 2>/dev/null | wc -c)" -ne 32 ]; then
    pfail "APP_MASTER_KEY должен быть base64 от ровно 32 байт (openssl rand -base64 32)"
  else
    pass "APP_MASTER_KEY корректного размера"
  fi
fi
case "${LDAP_URIS:-}" in
  *ldap://*) pfail "LDAP_URIS содержит нешифрованный ldap:// — разрешён только ldaps://" ;;
esac
case "${APP_PUBLIC_URL:-}" in
  https://*) pass "APP_PUBLIC_URL использует https" ;;
  http://localhost*|http://127.0.0.1*) pwarn "APP_PUBLIC_URL по http на localhost — допустимо только для разработки" ;;
  *) pfail "APP_PUBLIC_URL должен начинаться с https:// (микрофон в браузере требует secure context)" ;;
esac

# ------------------------------------------------------------------------ хост
log; log "== Хост =="
if [ -r /etc/os-release ]; then
  . /etc/os-release
  if [ "${ID:-}" = "ubuntu" ]; then pass "ОС: ${PRETTY_NAME:-ubuntu}"; else pwarn "ОС ${PRETTY_NAME:-?}: проверялось на Ubuntu 24.04"; fi
else
  pfail "Не удалось определить ОС (/etc/os-release)"
fi
arch="$(uname -m)"
case "$arch" in x86_64|amd64) pass "Архитектура: $arch" ;; *) pwarn "Архитектура $arch не проверялась (ожидается x86_64)" ;; esac

CPUS="$(nproc 2>/dev/null || echo 0)"
if [ "$CPUS" -ge 4 ]; then pass "CPU: $CPUS ядер"; else pwarn "CPU: $CPUS ядер — для ASR рекомендуется >= 4"; fi

mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null)"
if [ -n "$mem_kb" ]; then
  avail_kb="$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null)"
  mem_h="$(awk -v k="$mem_kb" 'BEGIN{printf "%.1f", k/1048576}')"; avail_h="$(awk -v k="${avail_kb:-$mem_kb}" 'BEGIN{printf "%.1f", k/1048576}')"
  case "$(ram_verdict "$mem_kb" "${MIN_RAM_GB:-8}")" in
    ok)   pass "RAM: ${mem_h} ГиБ (доступно ~${avail_h} ГиБ; требование ${MIN_RAM_GB:-8} ГиБ с допуском на накладные расходы ВМ)" ;;
    warn) pwarn "RAM: ${mem_h} ГиБ — меньше рекомендуемых ${MIN_RAM_GB:-8} ГиБ; установка возможна, но ASR на CPU может работать медленно" ;;
    *)    pfail "RAM: ${mem_h} ГиБ — меньше 75% от требуемых ${MIN_RAM_GB:-8} ГиБ" ;;
  esac
  if [ "${avail_kb:-$mem_kb}" -lt 4194304 ]; then pwarn "Свободно RAM всего ~${avail_h} ГиБ: на общем сервере модель ASR может не поместиться рядом с другими сервисами"; fi
else
  pwarn "Нет /proc/meminfo — RAM не проверена"
fi

if [ "${ASR_DEVICE:-cpu}" = "cuda" ]; then
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    pass "GPU: $(nvidia-smi -L | head -1)"
  else
    pfail "ASR_DEVICE=cuda, но nvidia-smi недоступен/GPU не найден"
  fi
  if command -v docker >/dev/null 2>&1 && docker info 2>/dev/null | grep -qi nvidia; then
    pass "Docker runtime NVIDIA обнаружен"
  else
    pfail "ASR_DEVICE=cuda: в docker info не найден nvidia runtime (NVIDIA Container Toolkit)"
  fi
else
  pass "Режим ASR: CPU"
fi

# ----------------------------------------------------------------- каталоги/диск
log; log "== Параметры ядра (только предупреждения; установщик sysctl не меняет) =="
kernel_tuning_check pass pwarn

log; log "== Рекомендации реального времени (только предупреждения) =="
realtime_config_check pass pwarn
info "Если перед проектом стоят внешние прокси (например панель вида Nginx Proxy Manager → системный nginx → nginx проекта): WebSocket Upgrade, X-Forwarded-Proto и отсутствие буферизации нужны на КАЖДОМ слое — чек-лист в DEPLOYMENT.md §3.1"

log; log "== Каталоги и диск =="
check_dir_parent() {
  local d="$1" p="$1"
  while [ ! -e "$p" ] && [ "$p" != "/" ]; do p="$(dirname "$p")"; done
  [ -w "$p" ] || [ "$(id -u)" -eq 0 ]
}
if [ -n "${DATA_ROOT:-}" ] && ! dr_msg="$(validate_local_dir "$DATA_ROOT" DATA_ROOT)"; then
  pfail "$dr_msg"
elif [ -n "${DATA_ROOT:-}" ]; then
  if [ -d "$DATA_ROOT" ]; then pass "DATA_ROOT существует: $DATA_ROOT"
  elif check_dir_parent "$DATA_ROOT"; then pass "DATA_ROOT будет создан: $DATA_ROOT"
  else pfail "DATA_ROOT=$DATA_ROOT не существует и родитель недоступен для записи"; fi
  case "$(cd "$REPO_ROOT" && pwd)/" in
    "$DATA_ROOT"/*) pfail "Git checkout находится внутри DATA_ROOT — данные и код должны быть раздельно" ;;
  esac
  case "$DATA_ROOT/" in
    "$(cd "$REPO_ROOT" && pwd)"/*) pfail "DATA_ROOT находится внутри Git checkout ($REPO_ROOT) — вынесите данные" ;;
  esac
  probe="$DATA_ROOT"; while [ ! -e "$probe" ] && [ "$probe" != "/" ]; do probe="$(dirname "$probe")"; done
  free_gb=$(( $(df -Pk "$probe" | awk 'NR==2 {print $4}') / 1024 / 1024 ))
  if [ "$free_gb" -ge "${MIN_FREE_DISK_GB:-30}" ]; then pass "Свободно на диске DATA_ROOT: ${free_gb} ГБ"; else pfail "Свободно ${free_gb} ГБ < MIN_FREE_DISK_GB=${MIN_FREE_DISK_GB:-30}"; fi
fi
if [ -n "${BACKUP_DIR:-}${BACKUP_COPY_DIR:-}" ]; then
  # BACKUP_DIR/BACKUP_COPY_DIR — только локальные абсолютные пути (UNC и smb:// отвергаются), BACKUP_DIR — вне репозитория
  if bk_msg="$(DATA_ROOT="${DATA_ROOT:-}" REPO_ROOT="$REPO_ROOT" validate_env_paths)"; then pass "BACKUP_DIR: ${BACKUP_DIR:-по умолчанию в DATA_ROOT}"
  else pfail "$bk_msg"; fi
fi
if [ -n "${LDAP_CA_FILE:-}" ]; then
  if [ -r "$LDAP_CA_FILE" ]; then pass "CA-файл AD найден: $LDAP_CA_FILE"; else pfail "CA-файл AD не найден/не читается: $LDAP_CA_FILE"; fi
fi
if [ -n "${GIGAAM_MODEL_DIR_CHECK:-}" ] || [ -n "${DATA_ROOT:-}" ]; then
  mdl="${DATA_ROOT:-}/models/gigaam/${ASR_MODEL_NAME:-v3_e2e_rnnt}.ckpt"
  if [ -f "$mdl" ]; then pass "Модель ASR на месте: $mdl"; else pwarn "Модель ASR не подготовлена ($mdl) — выполните scripts/models.sh"; fi
fi

# --------------------------------------------------------------------- порты
log; log "== Порты хоста =="
check_port() {
  local proto="$1" port="$2" label="$3" rc
  if ! [[ "$port" =~ ^[0-9]+$ ]] || [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    pfail "$label: некорректный порт '$port'"; return
  fi
  port_busy "$proto" "$port"; rc=$?
  if [ "$rc" -eq 2 ]; then pwarn "$label ($proto/$port): утилита ss недоступна, проверка пропущена"
  elif [ "$rc" -eq 0 ]; then
    if port_owned_by_project "$port"; then pass "$label $proto/$port занят контейнером этого проекта (повторный запуск)"
    else pfail "$label $proto/$port УЖЕ ЗАНЯТ на хосте (выберите другой порт в .env)"; fi
  else pass "$label $proto/$port свободен"; fi
}
[ -n "${WEB_PORT:-}" ] && check_port tcp "$WEB_PORT" "WEB_PORT"
[ -n "${LIVEKIT_HTTP_PORT:-}" ] && check_port tcp "$LIVEKIT_HTTP_PORT" "LIVEKIT_HTTP_PORT"
[ -n "${LIVEKIT_TCP_PORT:-}" ] && check_port tcp "$LIVEKIT_TCP_PORT" "LIVEKIT_TCP_PORT (ICE/TCP)"
[ -n "${LIVEKIT_UDP_PORT:-}" ] && check_port udp "$LIVEKIT_UDP_PORT" "LIVEKIT_UDP_PORT (ICE/UDP mux)"
# уникальность портов между собой
dups="$(printf '%s\n' "${WEB_PORT:-}" "${LIVEKIT_HTTP_PORT:-}" "${LIVEKIT_TCP_PORT:-}" | grep -v '^$' | sort | uniq -d)"
[ -n "$dups" ] && pfail "Совпадающие TCP-порты в .env: $dups"
# host nginx
if [ "${NGINX_MANAGE:-no}" = "yes" ] && [ -n "${NGINX_LISTEN_PORT:-}" ]; then
  port_busy tcp "$NGINX_LISTEN_PORT"; rc=$?
  if [ "$rc" -eq 0 ]; then
    if own_nginx_site_ok; then
      pass "NGINX_LISTEN_PORT tcp/$NGINX_LISTEN_PORT занят НАШИМ nginx-site «$NGINX_SITE_NAME» (marker, listen и symlink совпадают) — повторный запуск"
    else
      ssinfo="$(ss -H -ltnp "sport = :$NGINX_LISTEN_PORT" 2>/dev/null || true)"
      if printf '%s' "$ssinfo" | grep -q 'users:'; then
        if printf '%s' "$ssinfo" | grep -q nginx; then
          pwarn "NGINX_LISTEN_PORT $NGINX_LISTEN_PORT уже слушает чужой nginx-site — допустимо только при уникальном server_name (проверяется ниже)"
        else
          pfail "NGINX_LISTEN_PORT tcp/$NGINX_LISTEN_PORT занят не nginx: $(printf '%s' "$ssinfo" | grep -o 'users:(([^)]*)' | head -1)"
        fi
      else
        pfail "NGINX_LISTEN_PORT tcp/$NGINX_LISTEN_PORT занят, владелец не определён (нет прав видеть процессы) и это не наш установленный site. Проверьте: sudo ss -ltnp 'sport = :$NGINX_LISTEN_PORT'"
      fi
    fi
  elif [ "$rc" -eq 1 ]; then pass "NGINX_LISTEN_PORT tcp/$NGINX_LISTEN_PORT свободен"; fi
fi

# ---------------------------------------------------------------------- Docker
log; log "== Docker / Compose =="
if ! command -v docker >/dev/null 2>&1; then
  if [ "$PROFILE" = "shared-host" ]; then pfail "Docker не установлен. В профиле shared-host установка Docker запрещена — установите его вручную и повторите."
  else pwarn "Docker не установлен — install.sh --profile standalone установит его"; fi
else
  docker_diag pass pwarn pfail || true
  if [ "$PROBE_BUILD" -eq 1 ]; then
    if select_build_mode; then pass "Сборка образов: builder=$SELECTED_BUILD_MODE (проверено пробной сборкой)"; else pfail "Сборка образов на этом сервере невозможна (subsystem=docker-build/buildkit) — см. сообщения выше"; fi
  else
    info "Реальная пробная сборка выполняется на этапе build установщика (или: preflight.sh --probe-build)."
  fi

  if [ "$N_FAIL" -eq 0 ] || docker compose version >/dev/null 2>&1; then
    if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
      if dc config -q >/dev/null 2>/tmp/vm_preflight_cfg.$$; then pass "Конфигурация compose валидна"; else pfail "docker compose config: $(head -3 /tmp/vm_preflight_cfg.$$ | tr '\n' ' ')"; fi
      rm -f /tmp/vm_preflight_cfg.$$

      # Конфликты имён: контейнеры/сети с префиксом нашего проекта, принадлежащие другому проекту.
      conflict=0
      while IFS='|' read -r cname cproj; do
        [ -z "$cname" ] && continue
        case "$cname" in "${COMPOSE_PROJECT_NAME}-"*|"${COMPOSE_PROJECT_NAME}_"*)
          if [ "$cproj" != "$COMPOSE_PROJECT_NAME" ]; then pfail "Контейнер '$cname' (проект '${cproj:-нет}') конфликтует по имени"; conflict=1; fi ;;
        esac
      done < <(docker ps -a --format '{{.Names}}|{{.Label "com.docker.compose.project"}}' 2>/dev/null)
      while IFS='|' read -r nname nproj; do
        [ -z "$nname" ] && continue
        case "$nname" in "${COMPOSE_PROJECT_NAME}_"*|"${COMPOSE_PROJECT_NAME}-"*)
          if [ "$nproj" != "$COMPOSE_PROJECT_NAME" ]; then pfail "Docker-сеть '$nname' (проект '${nproj:-нет}') конфликтует по имени"; conflict=1; fi ;;
        esac
      done < <(docker network ls --format '{{.Name}}|{{.Label "com.docker.compose.project"}}' 2>/dev/null)
      [ "$conflict" -eq 0 ] && pass "Конфликтующих контейнеров и сетей не найдено"
    fi
  fi
fi

# ------------------------------------------------------------------ nginx (хост)
if [ "${NGINX_MANAGE:-no}" = "yes" ]; then
  log; log "== Host nginx =="
  if ! command -v nginx >/dev/null 2>&1; then
    if [ "$PROFILE" = "shared-host" ]; then pfail "nginx не найден (NGINX_MANAGE=yes). Отключите NGINX_MANAGE или установите nginx."
    else pwarn "nginx не установлен — standalone-установка установит его"; fi
  else
    pass "nginx: $(nginx -v 2>&1 | head -1)"
    if nginx -t >/dev/null 2>&1 || sudo -n nginx -t >/dev/null 2>&1; then pass "Текущая конфигурация nginx валидна (до наших изменений)"; else pwarn "nginx -t не прошёл без изменений (или нет прав) — исправьте ДО установки"; fi
    for d in "${NGINX_SITES_AVAILABLE:-}" "${NGINX_SITES_ENABLED:-}"; do
      [ -d "$d" ] && pass "Каталог nginx есть: $d" || pfail "Нет каталога nginx: $d"
    done
    site="${NGINX_SITES_AVAILABLE:-}/${NGINX_SITE_NAME:-}"
    if [ -e "$site" ] && ! grep -q "managed-by: peregovorka:${COMPOSE_PROJECT_NAME}" "$site" 2>/dev/null; then
      pfail "Файл $site уже существует и создан не этим проектом — выберите другой NGINX_SITE_NAME"
    else pass "Имя site-файла свободно/наше: ${NGINX_SITE_NAME:-?}"; fi
    if [ -d "${NGINX_SITES_ENABLED:-/nonexistent}" ] && [ -n "${NGINX_SERVER_NAME:-}" ]; then
      other="$(grep -rlsE "server_name[^;]*[[:space:]]${NGINX_SERVER_NAME}[[:space:];]" "${NGINX_SITES_ENABLED}" 2>/dev/null | grep -v "/${NGINX_SITE_NAME}$" || true)"
      [ -n "$other" ] && pfail "server_name ${NGINX_SERVER_NAME} уже используется в: $other" || pass "server_name ${NGINX_SERVER_NAME} не занят другими site-файлами"
    fi
  fi
fi

# ------------------------------------------------------------------------ LDAPS
log; log "== Active Directory (LDAPS) =="
if [ "$SKIP_NET" -eq 1 ]; then
  pwarn "Сетевые проверки LDAP пропущены (--skip-network)"
elif [ -z "${LDAP_URIS:-}" ]; then
  pass "LDAP в .env не задан — каталог подключается в веб-интерфейсе после установки (вход локальным администратором)"
else
  IFS=',' read -ra URIS <<< "$LDAP_URIS"
  reachable=0
  for uri in "${URIS[@]}"; do
    uri="$(printf '%s' "$uri" | tr -d '[:space:]')"
    [ -n "$uri" ] || continue
    if ! parse_ldap_uri "$uri"; then pfail "Некорректный LDAP URI: '$uri' (нужен ldaps://host[:port])"; continue; fi
    host="$LDAP_HOST"; port="$LDAP_PORT"
    if timeout 5 bash -c "exec 3<>/dev/tcp/$host/$port" 2>/dev/null; then
      pass "TCP до $host:$port доступен"
      if command -v openssl >/dev/null 2>&1 && [ -r "${LDAP_CA_FILE:-/nonexistent}" ]; then
        if out="$(timeout 10 openssl s_client -connect "$host:$port" -servername "$host" -CAfile "$LDAP_CA_FILE" -verify_return_error -verify_hostname "$host" </dev/null 2>&1)"; then
          pass "Сертификат $host проверен по $LDAP_CA_FILE (цепочка и имя хоста)"; reachable=$((reachable+1))
        else
          pfail "Сертификат $host НЕ прошёл проверку по $LDAP_CA_FILE: $(printf '%s' "$out" | grep -iE 'verif|error' | head -2 | tr '\n' ' ')"
        fi
      else
        pwarn "openssl или CA-файл недоступны — проверка сертификата $host пропущена"
      fi
    else
      pfail "Нет TCP-связи с $host:$port"
    fi
  done
  [ "$reachable" -eq 0 ] && pfail "Ни один LDAP-сервер не прошёл проверку сертификата"
fi

# -------------------------------------------------------- PostgreSQL / Redis (post)
if [ "$PHASE" = "post" ]; then
  log; log "== Запущенные сервисы =="
  if dc exec -T postgres pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then pass "PostgreSQL принимает подключения"; else pfail "PostgreSQL недоступен"; fi
  if dc exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then pass "Redis отвечает PONG"; else pfail "Redis недоступен"; fi
fi

log
log "Итог: ok=$N_OK warn=$N_WARN fail=$N_FAIL"
if [ "$N_FAIL" -gt 0 ]; then fail "Preflight НЕ пройден — установка/обновление запрещены до устранения FAIL."; exit 1; fi
ok "Preflight пройден."
exit 0
