#!/usr/bin/env bash
# repair.sh — «Исправить автоматически» из терминала (то же, что кнопка в браузере): найти известные проблемы установки и исправить выбранную.
#
#   sudo scripts/repair.sh --scan                 показать найденные проблемы (ничего не меняет)
#   sudo scripts/repair.sh ID [--env FILE]        выполнить одно исправление из белого списка (ID — из --scan; нужен root)
#   scripts/repair.sh --list                      допустимые ID
#
# Допустимы только ID из scripts/lib/repairlib.sh (REPAIR_IDS); произвольных команд скрипт не выполняет.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

ID=""; MODE="apply"
while [ $# -gt 0 ]; do
  case "$1" in
    --scan) MODE=scan; shift ;;
    --list) MODE=list; shift ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    -*) die "Неизвестный аргумент: $1" ;;
    *) ID="$1"; shift ;;
  esac
done

[ "$MODE" = list ] && { printf '%s\n' "${REPAIR_IDS[@]}"; exit 0; }
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name; require_vars DATA_ROOT

if [ "$MODE" = scan ]; then
  repair_scan
  if [ "${#REPAIR_FOUND[@]}" -eq 0 ]; then ok "Известных проблем не найдено."; exit 0; fi
  for i in "${!REPAIR_FOUND[@]}"; do
    IFS='|' read -r title meaning fix <<<"${REPAIR_DETAILS[$i]}"
    log "• ${REPAIR_FOUND[$i]}: $title"; log "    что это значит: $meaning"; log "    что будет сделано: $fix"
  done
  log; info "Исправить: sudo scripts/repair.sh <id>"
  exit 1
fi

[ -n "$ID" ] || die "Укажите ID исправления (scripts/repair.sh --list) или --scan"
repair_id_valid "$ID" || die "Неизвестное исправление: $ID (допустимы: ${REPAIR_IDS[*]})"
[ "$(id -u)" -eq 0 ] || die "Нужны права root: sudo scripts/repair.sh $ID"
repair_apply "$ID" && ok "Исправление «$ID» выполнено" || die "Исправление «$ID» не удалось (подробности выше)"
