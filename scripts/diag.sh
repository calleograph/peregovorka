#!/usr/bin/env bash
# diag.sh — безопасный диагностический пакет (bundle) для разбора проблем без «ручного» сбора по docker/sysctl/DevTools.
#   scripts/diag.sh [--out DIR] [--tail N] [--env FILE] [--no-public]
# Только чтение: ничего не перезапускает и не меняет. Собирает: версии (приложение, commit, Docker, LiveKit/SDK), состояние
# контейнеров, `docker compose config` и .env (ТОЛЬКО через маскирование), хвосты журналов сервисов, параметры хоста и ядра
# (UDP-буферы), ресурсы (память и swap, диск и inode, размер каталогов данных, docker stats), состояние Docker, ответ API
# локально и через публичный адрес (версия, commit), цепочку TLS публичного адреса, состояние обновлений, отчёт backend
# (WebSocket /rtc/v1, RTC-порты, LDAP, БД, Redis, ASR, тайминги). --no-public — не обращаться к публичному адресу.
# Содержимое встреч (стенограммы, чаты, записи, документы) не читается: только размеры каталогов. Пароли, токены, ключи,
# access_token в URL, LDAP bind password и креды LiveKit заменяются на ***. В конце пакет ПРОВЕРЯЕТСЯ на утечки значений
# секретов из .env: при находке архив не создаётся. Рядом с архивом — контрольная сумма SHA-256 для передачи поддержке.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/mask.sh"
OUTDIR="$REPO_ROOT/diagnostics"; TAIL=500; PUBLIC=1
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --out) OUTDIR="$2"; shift 2 ;;
    --tail) TAIL="$2"; shift 2 ;;
    --no-public) PUBLIC=0; shift ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
TS="$(date +%Y%m%d-%H%M%S)"; W="$(mktemp -d)"; B="$W/peregovorka-diag-$TS"; mkdir -p "$B/logs" "$OUTDIR"
trap 'rm -rf "$W"' EXIT
section() { printf '== %s ==\n' "$1"; }

{
  section "Peregovorka diagnostics $TS"
  echo "project: $COMPOSE_PROJECT_NAME"; echo "profile: ${INSTALL_PROFILE:-?}"
  echo "git: $(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "LIVEKIT_IMAGE_TAG: ${LIVEKIT_IMAGE_TAG:-?}"
  [ -r "$REPO_ROOT/deployment/compat.env" ] && sed 's/^/compat: /' "$REPO_ROOT/deployment/compat.env" | grep -v '^compat: #'
  echo "docker: $(docker --version 2>&1)"; echo "compose: $(docker compose version 2>&1 | head -1)"
} 2>&1 | mask_stream > "$B/summary.txt"

dc ps 2>&1 | mask_stream > "$B/containers.txt"
dc config 2>&1 | mask_stream > "$B/compose.config.masked.yml"
mask_stream < "$ENV_FILE" > "$B/env.masked"
for s in postgres redis livekit backend asr web llm-local livekit-sip; do dc logs --no-color --tail "$TAIL" "$s" 2>&1 | mask_stream > "$B/logs/$s.log"; done
{
  section "локальная LLM"
  echo "включена: ${LLM_LOCAL_ENABLED:-yes}"; echo "файл: $(llm_file_name)"
  st="$(llm_model_state)"; echo "состояние файла: $st ($(llm_state_text "$st"))"
  [ -f "$(llm_path)" ] && echo "размер: $(stat -c '%s' "$(llm_path)" 2>/dev/null) байт (ожидается $(llm_expected_bytes))"
  echo "образ runtime: $(llm_image) ($(docker image inspect "$(llm_image)" >/dev/null 2>&1 && echo есть || echo нет))"
  echo "контейнер: $(dc ps llm-local 2>/dev/null | tail -n +2 | head -1)"
  echo "сеть llm_internal internal: $(docker network inspect "${COMPOSE_PROJECT_NAME}_llm_internal" -f '{{.Internal}}' 2>/dev/null || echo н/д)"
} 2>&1 | mask_stream > "$B/local-llm.txt"
{
  section "SIP-телефония"
  echo "включена: ${SIP_ENABLED:-no}"; echo "signalling: $(sip_port)/udp+tcp"; echo "RTP: $(sip_rtp)/udp"; echo "адрес привязки: ${SIP_BIND_ADDR:-0.0.0.0}"
  echo "разрешённые адреса АТС: ${SIP_ALLOWED_CIDRS:-не заданы}"; echo "образ: $(sip_image) ($(docker image inspect "$(sip_image)" >/dev/null 2>&1 && echo есть || echo нет))"
  echo "контейнер: $(dc ps livekit-sip 2>/dev/null | tail -n +2 | head -1)"
} 2>&1 | mask_stream > "$B/sip.txt"
{
  section "uname"; uname -a
  section "cpu/ram"; nproc 2>/dev/null; free -m 2>/dev/null || true
  section "load"; cat /proc/loadavg 2>/dev/null || true
  section "disk"; df -h "${DATA_ROOT:-/}" 2>/dev/null || true
  section "ядро (рекомендации WebRTC)"
  for k in net.core.rmem_max net.core.wmem_max net.core.netdev_max_backlog vm.overcommit_memory; do
    f="/proc/sys/$(echo "$k" | tr . /)"; printf '%s = %s\n' "$k" "$(cat "$f" 2>/dev/null || echo н/д)"
  done
  section "порты проекта"
  for p in "${WEB_PORT:-}" "${LIVEKIT_HTTP_PORT:-}" "${LIVEKIT_TCP_PORT:-}" "${LIVEKIT_UDP_PORT:-}"; do
    [ -n "$p" ] && { ss -H -ltnu "sport = :$p" 2>/dev/null | sed "s/^/:$p /" || true; }
  done
} 2>&1 | mask_stream > "$B/host.txt"

{
  section "память и swap"; free -m 2>/dev/null || true; swapon --show 2>/dev/null || echo "swap: н/д"
  section "давление памяти (PSI)"; cat /proc/pressure/memory 2>/dev/null || echo н/д
  section "OOM в журнале ядра (последние)"; (journalctl -k --no-pager -n 400 2>/dev/null || dmesg 2>/dev/null) | grep -iE "out of memory|oom-kill|killed process" | tail -10 || true
  section "диск и inode DATA_ROOT"; df -h "${DATA_ROOT:-/}" 2>/dev/null || true; df -i "${DATA_ROOT:-/}" 2>/dev/null || true
  section "размер каталогов данных (только размеры, без содержимого)"
  for d in postgres redis recordings exports backups models chat-files avatars state updater; do
    [ -d "${DATA_ROOT:-/nonexistent}/$d" ] || continue
    out="$(du -sh "$DATA_ROOT/$d" 2>/dev/null)"; rc=$?; sz="${out%%[[:space:]]*}"
    [ "$rc" -eq 0 ] || sz="${sz:-?} (подсчёт неполный: нет прав на часть файлов — запустите от root)"
    printf '%-12s %s\n' "$d" "${sz:-?}"
  done
  printf '%-12s %s\n' "база" "$(dc exec -T postgres psql -U "${POSTGRES_USER:-peregovorka}" -d "${POSTGRES_DB:-peregovorka}" -tAc 'select pg_size_pretty(pg_database_size(current_database()))' 2>/dev/null || echo н/д)"
  section "Docker"; docker info --format 'server={{.ServerVersion}} storage={{.Driver}} logging={{.LoggingDriver}} cgroup={{.CgroupVersion}} root={{.DockerRootDir}}' 2>&1 | head -1
  echo "buildx: $(docker buildx version 2>&1 | head -1)"
  docker system df 2>&1 | head -8
  section "ресурсы контейнеров проекта"
  ids="$(dc ps -q 2>/dev/null | tr '\n' ' ')"; [ -n "$ids" ] && docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.PIDs}}' $ids 2>&1 || echo "контейнеры не запущены"
  section "перезапуски контейнеров"
  for id in $ids; do docker inspect -f '{{.Name}} restarts={{.RestartCount}} started={{.State.StartedAt}} oom={{.State.OOMKilled}} exit={{.State.ExitCode}}' "$id" 2>/dev/null; done
} 2>&1 | mask_stream > "$B/resources.txt"

{
  section "обновления и развёртывания"
  for f in deploy-history.log last-update.state last-verify.state; do
    [ -r "${DATA_ROOT:-/nonexistent}/state/$f" ] && { echo "-- $f"; tail -5 "$DATA_ROOT/state/$f"; }
  done
  [ -r "${DATA_ROOT:-/nonexistent}/updater/report.txt" ] && { echo "-- updater/report.txt"; tail -60 "$DATA_ROOT/updater/report.txt"; }
  [ -r "${DATA_ROOT:-/nonexistent}/updater/status.json" ] && { echo "-- updater/status.json"; head -c 2000 "$DATA_ROOT/updater/status.json"; echo; }
  echo "git HEAD: $(git -C "$REPO_ROOT" describe --tags --always 2>/dev/null || echo н/д); изменённых файлов: $(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | wc -l)"
} 2>&1 | mask_stream > "$B/updates.txt"

# локальный и публичный ответ API: расхождение версии/commit — признак «прокси ведёт на другой экземпляр» или незавершённого обновления
{
  section "API: локально и через публичный адрес"
  printf 'локально (web 127.0.0.1:%s): ' "${WEB_PORT:-?}"
  curl -sS -m 8 "http://127.0.0.1:${WEB_PORT:-0}/api/v1/version" 2>&1 | head -c 400; echo
  if [ "$PUBLIC" = 1 ] && [ -n "${APP_PUBLIC_URL:-}" ]; then
    printf 'публично (%s): ' "$APP_PUBLIC_URL"
    curl -sS -m 12 -o "$W/pub.json" -w 'http=%{http_code} tls_verify=%{ssl_verify_result} ip=%{remote_ip} t=%{time_total}s\n' "${APP_PUBLIC_URL%/}/api/v1/version" 2>&1 | head -3
    head -c 400 "$W/pub.json" 2>/dev/null; echo
    host="$(printf '%s' "$APP_PUBLIC_URL" | sed -E 's#^[a-z]+://##; s#/.*$##; s#:[0-9]+$##')"
    port="$(printf '%s' "$APP_PUBLIC_URL" | sed -nE 's#^https://[^/:]+:([0-9]+).*#\1#p')"; port="${port:-443}"
    section "DNS"; getent ahosts "$host" 2>&1 | awk '{print $1}' | sort -u | head -6 || true
    case "$APP_PUBLIC_URL" in https://*)
      section "TLS публичного адреса ($host:$port)"
      echo | timeout 12 openssl s_client -connect "$host:$port" -servername "$host" -showcerts 2>/dev/null > "$W/tls.txt" || true
      echo "сертификатов в цепочке, которую отдаёт сервер: $(grep -c 'BEGIN CERTIFICATE' "$W/tls.txt" 2>/dev/null || true)"
      grep -E '^ *[0-9]+ s:|^ *i:|Verify return code' "$W/tls.txt" 2>/dev/null | head -12
      awk '/BEGIN CERTIFICATE/{p=1} p{print} /END CERTIFICATE/{exit}' "$W/tls.txt" 2>/dev/null | openssl x509 -noout -subject -enddate -ext subjectAltName 2>/dev/null || echo "сертификат не получен" ;;
    esac
  else echo "публичная проверка пропущена (--no-public или APP_PUBLIC_URL не задан)"; fi
} 2>&1 | mask_stream > "$B/network.txt"

# отчёт backend: глубокая проверка (реальный WebSocket Upgrade на /rtc/v1, RTC TCP, LDAP, БД, Redis, ASR)
dc exec -T -e TOKEN="${INTERNAL_API_TOKEN:-}" backend python -c "
import os,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/diag?deep=1',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(urllib.request.urlopen(r,timeout=60).read().decode())" 2>&1 | mask_stream > "$B/backend-diag.json"
dc exec -T asr python -c "
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8090/readyz',timeout=8).read().decode())" 2>&1 | mask_stream > "$B/asr-readyz.json"

{
  section "строк уровня ERROR/FATAL в журналах (последние $TAIL строк каждого сервиса; предупреждения не считаются)"
  for f in "$B"/logs/*.log; do printf '%-14s %s\n' "$(basename "$f" .log)" "$(grep -cE '"level": ?"(ERROR|CRITICAL)"|	(ERROR|FATAL|PANIC)	|Traceback \(most recent call last\)|\[(error|crit|alert|emerg)\]| (ERROR|FATAL|PANIC):  ' "$f" 2>/dev/null || true)"; done
} >> "$B/summary.txt" 2>&1
cat > "$B/README.txt" <<'TXT'
Диагностический пакет Peregovorka (scripts/diag.sh). Собран в режиме чтения: система не менялась.
summary.txt        версии, Docker/Compose, число строк с ошибками по сервисам
containers.txt     docker compose ps
resources.txt      память, swap, OOM, диск и inode, размеры каталогов данных, docker stats, перезапуски контейнеров
network.txt        ответ API локально и через публичный адрес, DNS, цепочка TLS публичного адреса
updates.txt        история развёртываний, последнее обновление, отчёт помощника обновлений
host.txt           ядро, нагрузка, порты проекта; local-llm.txt, sip.txt — дополнительные компоненты
backend-diag.json  глубокая проверка backend (LiveKit /rtc/v1, RTC, LDAP, БД, Redis, ASR); asr-readyz.json — готовность ASR
compose.config.masked.yml, env.masked — конфигурация с замаскированными секретами
logs/*.log         последние строки журналов сервисов (маскированы)
Не содержит: паролей, токенов, ключей (заменены на ***), содержимого встреч (стенограмм, чатов, записей, документов).
TXT
# финальная проверка: ни одно значение секрета из .env не должно присутствовать в пакете
LEAK=0
while IFS= read -r secret; do
  [ -n "$secret" ] || continue
  if grep -rqF -- "$secret" "$B" 2>/dev/null; then LEAK=1; fi
done < <(env_secret_values "$ENV_FILE")
if [ "$LEAK" -ne 0 ]; then
  fail "В диагностическом пакете обнаружено значение секрета — архив НЕ создан (сообщите об этом разработчикам, приложите НЕ файл, а описание проблемы)."
  exit 2
fi
ARCHIVE="$OUTDIR/peregovorka-diag-$TS.tar.gz"
tar -C "$W" -czf "$ARCHIVE" "peregovorka-diag-$TS" || die "Не удалось создать архив"
chmod 600 "$ARCHIVE" 2>/dev/null || true
SUM="$(sha256sum "$ARCHIVE" 2>/dev/null | cut -d' ' -f1)"; [ -n "$SUM" ] && printf '%s  %s\n' "$SUM" "$(basename "$ARCHIVE")" > "$ARCHIVE.sha256"
ok "Диагностический пакет: $ARCHIVE (секреты замаскированы, проверка на утечки пройдена)"
[ -n "$SUM" ] && info "SHA-256: $SUM (сообщите поддержке отдельно от файла, чтобы проверить целостность при передаче)"
