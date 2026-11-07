#!/usr/bin/env bash
# restore.sh — восстановление PostgreSQL ТЕКУЩЕГО экземпляра из дампа backup.sh.
#   scripts/restore.sh DUMP_FILE [--files АРХИВ] [--env FILE] [--yes]
#
# Копия разворачивается в ОТДЕЛЬНУЮ базу и проверяется (таблицы, внешние ключи, версия схемы); рабочая база до этого не тронута. Затем базы меняются именами.
# Прежняя база остаётся рядом до приёмки. Если приложение не заработало на восстановленной базе той же версии схемы — прежняя возвращается автоматически.
# Без --yes требуется ввести имя проекта. Перед восстановлением делается страховочный дамп. Останавливаются и запускаются только backend/asr/web ЭТОГО проекта.
# --files  архив файлов (backup.sh --full): распаковка рядом и замена каталогов, прежние остаются как <каталог>.before-restore-<время>.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

FILE=""; YES=0; FILES=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --files) FILES="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    -*) die "Неизвестный аргумент: $1" ;;
    *) FILE="$1"; shift ;;
  esac
done
[ -n "$FILE" ] && [ -f "$FILE" ] || die "Укажите существующий файл дампа"
[ -z "$FILES" ] || [ -f "$FILES" ] || die "Архив файлов не найден: $FILES"
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
require_vars POSTGRES_DB POSTGRES_USER

warn "База '${POSTGRES_DB}' проекта '${COMPOSE_PROJECT_NAME}' будет заменена копией из файла: $FILE"
[ -z "$FILES" ] || warn "Файлы встреч в ${DATA_ROOT:-?} будут заменены содержимым архива: $FILES"
if [ "$YES" -ne 1 ]; then
  read -r -p "Для подтверждения введите имя проекта (${COMPOSE_PROJECT_NAME}): " ans
  [ "$ans" = "$COMPOSE_PROJECT_NAME" ] || die "Подтверждение не получено — отмена."
fi

restore_preflight "$FILE" || die "$RESTORE_ERROR"
"$REPO_ROOT/scripts/backup.sh" --env "$ENV_FILE" --label pre-restore --keep 50
# Рабочая база до переключения не тронута, но приложение на это время остановлено: при любом выходе оно должно подняться снова.
app_back() { local rc=$?; if [ "$rc" -ne 0 ]; then warn "Восстановление не выполнено, запускаю приложение"; dc up -d --no-build backend asr web >/dev/null 2>&1 || true; fi; }
trap app_back EXIT
info "Остановка backend/asr/web проекта…"
dc stop web backend asr
restore_from_dump "$FILE" || die "$RESTORE_ERROR"
if [ -n "$FILES" ]; then
  # приложение уже принято на новой базе: каталоги заменяются под ним, поэтому контейнеры пересоздаются и видят новые файлы
  if restore_files "$FILES"; then dc up -d --no-build --force-recreate backend asr web $([ "${VIDEO_ENABLED:-no}" = yes ] && echo livekit-egress); else warn "Файлы не восстановлены: $RESTORE_ERROR (база уже восстановлена)"; fi
fi
trap - EXIT
ok "Восстановление завершено. Прежняя база сохранена как $DB_KEPT. После проверки удалите её: docker exec $(dc ps -q postgres | head -1) dropdb -U $POSTGRES_USER $DB_KEPT"
ok "Проверка: scripts/status.sh && scripts/smoke-test.sh"
