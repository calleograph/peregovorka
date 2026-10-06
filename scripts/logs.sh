#!/usr/bin/env bash
# logs.sh — журналы сервисов ТЕКУЩЕГО экземпляра.
#   scripts/logs.sh [SERVICE ...] [-f] [--tail N] [--env FILE]
# Сервисы: postgres redis livekit backend asr web. Без аргументов — все.
# Вывод проходит через маскирование секретов (пароли, токены, ключи, access_token в URL).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/mask.sh"
ARGS=(); SVC=(); TAIL=200
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    -f|--follow) ARGS+=(--follow); shift ;;
    --tail) TAIL="$2"; shift 2 ;;
    postgres|redis|livekit|backend|asr|web) SVC+=("$1"); shift ;;
    *) die "Неизвестный аргумент/сервис: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
dc logs --tail "$TAIL" ${ARGS[@]+"${ARGS[@]}"} ${SVC[@]+"${SVC[@]}"} 2>&1 | mask_stream
