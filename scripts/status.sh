#!/usr/bin/env bash
# status.sh — состояние ТЕКУЩЕГО экземпляра (read-only).
#   scripts/status.sh [--env FILE] [--wait SECONDS]   # --wait: ждать, пока все сервисы healthy
# Код возврата 0 — все сервисы running и (где есть healthcheck) healthy.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

WAIT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --wait) WAIT="$2"; shift 2 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name

SERVICES=(postgres redis livekit backend asr web)
all_healthy() {
  local s cid st h bad=0
  for s in "${SERVICES[@]}"; do
    cid="$(dc ps -q "$s" 2>/dev/null | head -1)"
    if [ -z "$cid" ]; then bad=1; continue; fi
    st="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"
    h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null)"
    if [ "$st" != "running" ] || { [ "$h" != "healthy" ] && [ "$h" != "none" ]; }; then bad=1; fi
  done
  return $bad
}

if [ "$WAIT" -gt 0 ]; then
  end=$(( $(date +%s) + WAIT ))
  until all_healthy; do
    [ "$(date +%s)" -ge "$end" ] && break
    sleep 5
  done
fi

log "== Экземпляр: ${COMPOSE_PROJECT_NAME} =="
dc ps
log
for s in "${SERVICES[@]}"; do
  cid="$(dc ps -q "$s" 2>/dev/null | head -1)"
  if [ -n "$cid" ]; then
    printf '%-10s %-10s health=%s\n' "$s" "$(docker inspect -f '{{.State.Status}}' "$cid")" \
      "$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' "$cid")"
  else
    printf '%-10s %s\n' "$s" "не запущен"
  fi
done
log
v="$(dc exec -T backend python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/version',timeout=4).read().decode())" 2>/dev/null || true)"
[ -n "$v" ] && log "Версия backend: $v"
if [ -f "${DATA_ROOT:-/nonexistent}/state/deploy-history.log" ]; then
  log "Последние деплои:"; tail -3 "$DATA_ROOT/state/deploy-history.log" | sed 's/^/  /'
fi
all_healthy && { ok "Все сервисы работают"; exit 0; } || { fail "Есть неработающие/нездоровые сервисы"; exit 1; }
