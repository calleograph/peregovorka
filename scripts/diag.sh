#!/usr/bin/env bash
# diag.sh — безопасный диагностический пакет (bundle) для разбора проблем без «ручного» сбора по docker/sysctl/DevTools.
#   scripts/diag.sh [--out DIR] [--tail N] [--env FILE]
# Собирает: версии (приложение, commit, Docker, LiveKit/SDK), состояние контейнеров, `docker compose config` и .env
# (ТОЛЬКО через маскирование), хвосты журналов сервисов, параметры хоста и ядра (UDP-буферы), отчёт backend (WebSocket
# /rtc/v1, RTC-порты, LDAP, БД, Redis, ASR, тайминги). Пароли, токены, ключи, access_token в URL, LDAP bind password и
# креды LiveKit заменяются на ***. В конце пакет ПРОВЕРЯЕТСЯ на утечки значений секретов из .env: при находке архив удаляется.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/mask.sh"
OUTDIR="$REPO_ROOT/diagnostics"; TAIL=500
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --out) OUTDIR="$2"; shift 2 ;;
    --tail) TAIL="$2"; shift 2 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
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
for s in postgres redis livekit backend asr web; do dc logs --no-color --tail "$TAIL" "$s" 2>&1 | mask_stream > "$B/logs/$s.log"; done
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

# отчёт backend: глубокая проверка (реальный WebSocket Upgrade на /rtc/v1, RTC TCP, LDAP, БД, Redis, ASR)
dc exec -T -e TOKEN="${INTERNAL_API_TOKEN:-}" backend python -c "
import os,urllib.request
r=urllib.request.Request('http://127.0.0.1:8000/internal/v1/diag?deep=1',headers={'Authorization':'Bearer '+os.environ['TOKEN']})
print(urllib.request.urlopen(r,timeout=60).read().decode())" 2>&1 | mask_stream > "$B/backend-diag.json"
dc exec -T asr python -c "
import urllib.request
print(urllib.request.urlopen('http://127.0.0.1:8090/readyz',timeout=8).read().decode())" 2>&1 | mask_stream > "$B/asr-readyz.json"

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
ok "Диагностический пакет: $ARCHIVE (секреты замаскированы, проверка на утечки пройдена)"
