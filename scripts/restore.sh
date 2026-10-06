#!/usr/bin/env bash
# restore.sh — восстановление PostgreSQL ТЕКУЩЕГО экземпляра из дампа backup.sh.
#   scripts/restore.sh DUMP_FILE [--env FILE] [--yes]
#
# ОПАСНО: заменяет содержимое БД проекта. Без --yes требуется ввести имя проекта.
# Перед восстановлением автоматически делается страховочный дамп текущего состояния.
# Останавливаются и затем запускаются только backend/asr/web ЭТОГО проекта.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

FILE=""; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    -*) die "Неизвестный аргумент: $1" ;;
    *) FILE="$1"; shift ;;
  esac
done
[ -n "$FILE" ] && [ -f "$FILE" ] || die "Укажите существующий файл дампа"
load_env "$ENV_FILE"; validate_project_name
require_vars POSTGRES_DB POSTGRES_USER

warn "Будет ПЕРЕЗАПИСАНА база '${POSTGRES_DB}' проекта '${COMPOSE_PROJECT_NAME}' из файла: $FILE"
if [ "$YES" -ne 1 ]; then
  read -r -p "Для подтверждения введите имя проекта (${COMPOSE_PROJECT_NAME}): " ans
  [ "$ans" = "$COMPOSE_PROJECT_NAME" ] || die "Подтверждение не получено — отмена."
fi

"$REPO_ROOT/scripts/backup.sh" --env "$ENV_FILE" --label pre-restore --keep 50
info "Остановка backend/asr/web проекта…"
dc stop web backend asr
info "pg_restore…"
dc exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists --no-owner --exit-on-error < "$FILE" \
  || die "pg_restore завершился с ошибкой. Страховочный дамп 'pre-restore' сохранён в каталоге резервных копий."
info "Запуск сервисов…"
dc up -d --no-build backend asr web
ok "Восстановление завершено. Проверьте: scripts/status.sh && scripts/smoke-test.sh"
