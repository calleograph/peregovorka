#!/usr/bin/env bash
# ctl.sh — штатное управление сервисами ТОЛЬКО этого экземпляра (compose-проекта).
#   scripts/ctl.sh status|restart|stop|start [сервис …]     сервисы: postgres redis livekit backend asr web
# stop — останавливает контейнеры (данные, тома, образы НЕ удаляются); start — запускает без пересборки.
# Чужие контейнеры не затрагиваются; Docker/containerd не перезапускаются.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
ACTION="${1:-}"; [ -n "$ACTION" ] || die "Использование: scripts/ctl.sh status|restart|stop|start [сервис …]"; shift
SVC=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    postgres|redis|livekit|backend|asr|web) SVC+=("$1"); shift ;;
    *) die "Неизвестный сервис/аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
case "$ACTION" in
  status)  exec "$REPO_ROOT/scripts/status.sh" --env "$ENV_FILE" ;;
  restart) dc restart ${SVC[@]+"${SVC[@]}"} ;;
  stop)    dc stop ${SVC[@]+"${SVC[@]}"} ;;
  start)   dc up -d --no-build ${SVC[@]+"${SVC[@]}"} ;;
  *) die "Неизвестное действие: $ACTION" ;;
esac
ok "Готово: $ACTION ${SVC[*]:-все сервисы проекта ${COMPOSE_PROJECT_NAME}}"
