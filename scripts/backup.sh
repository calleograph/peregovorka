#!/usr/bin/env bash
# backup.sh — резервная копия PostgreSQL ТЕКУЩЕГО экземпляра.
#   scripts/backup.sh [--env FILE] [--keep N] [--with-env] [--with-recordings]
#
# По умолчанию: pg_dump (custom format) -> ${BACKUP_DIR}/<проект>-db-<время>.dump
# --with-env         добавить копию .env (СОДЕРЖИТ СЕКРЕТЫ, включая APP_MASTER_KEY;
#                    храните архив с ограниченными правами) — без APP_MASTER_KEY
#                    зашифрованные настройки после восстановления нечитаемы.
# --with-recordings  добавить tar каталога записей и экспортов.
# --keep N           оставить N последних дампов этого проекта (по умолчанию 14).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

KEEP=14; WITH_ENV=0; WITH_REC=0; LABEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    --with-env) WITH_ENV=1; shift ;;
    --with-recordings) WITH_REC=1; shift ;;
    --label) LABEL="-$2"; shift 2 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
require_vars DATA_ROOT POSTGRES_DB POSTGRES_USER
DEST="${BACKUP_DIR:-$DATA_ROOT/backups}"
mkdir -p "$DEST"; chmod 700 "$DEST" 2>/dev/null || true
TS="$(date +%Y%m%d-%H%M%S)"
OUT="$DEST/${COMPOSE_PROJECT_NAME}-db-${TS}${LABEL}.dump"

dc ps -q postgres | grep -q . || die "Контейнер postgres проекта ${COMPOSE_PROJECT_NAME} не запущен"
info "pg_dump → $OUT"
umask 077
if ! dc exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$OUT.part"; then
  rm -f "$OUT.part"; die "pg_dump завершился с ошибкой"
fi
[ -s "$OUT.part" ] || { rm -f "$OUT.part"; die "Дамп пустой"; }
mv "$OUT.part" "$OUT"
ok "Дамп БД: $OUT ($(du -h "$OUT" | cut -f1))"

if [ "$WITH_ENV" -eq 1 ]; then
  cp "$ENV_FILE" "$DEST/${COMPOSE_PROJECT_NAME}-env-${TS}${LABEL}.env"; chmod 600 "$DEST/${COMPOSE_PROJECT_NAME}-env-${TS}${LABEL}.env"
  warn "Копия .env содержит секреты: $DEST/${COMPOSE_PROJECT_NAME}-env-${TS}${LABEL}.env"
fi
if [ "$WITH_REC" -eq 1 ]; then
  tar -C "$DATA_ROOT" -czf "$DEST/${COMPOSE_PROJECT_NAME}-files-${TS}${LABEL}.tar.gz" recordings exports
  ok "Файлы: $DEST/${COMPOSE_PROJECT_NAME}-files-${TS}${LABEL}.tar.gz"
fi

# Ротация: только файлы ЭТОГО проекта по шаблону имени.
mapfile -t OLD < <(ls -1t "$DEST"/"${COMPOSE_PROJECT_NAME}"-db-*.dump 2>/dev/null | tail -n +$((KEEP+1)))
for f in "${OLD[@]:-}"; do [ -n "$f" ] && { rm -f -- "$f"; info "удалён старый дамп: $f"; }; done
ok "Резервное копирование завершено."
