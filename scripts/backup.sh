#!/usr/bin/env bash
# backup.sh — резервная копия PostgreSQL ТЕКУЩЕГО экземпляра.
#   scripts/backup.sh [--env FILE] [--keep N] [--with-env] [--with-recordings]
#
# По умолчанию: pg_dump (custom format) -> ${BACKUP_DIR}/<проект>-db-<время>.dump
# --with-env         добавить копию .env (СОДЕРЖИТ СЕКРЕТЫ, включая APP_MASTER_KEY;
#                    храните архив с ограниченными правами) — без APP_MASTER_KEY
#                    зашифрованные настройки после восстановления нечитаемы.
# --with-recordings  добавить tar файлов встреч из DATA_ROOT: записи, выгрузки, вложения чата, аватарки, оформление, сертификаты CA, сертификат TLS
#                    (файлы во внешнем SMB-хранилище сюда не входят — их копирует владелец хранилища).
# --full             всё для восстановления на чистом сервере: дамп, .env, файлы.
# Дамп проверяется чтением (pg_restore --list); рядом пишется контрольная сумма <файл>.sha256 (копируется и в BACKUP_COPY_DIR).
# --keep N           оставить N последних наборов этого проекта (по умолчанию 14); набор удаляется целиком.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

KEEP=14; WITH_ENV=0; WITH_REC=0; LABEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --keep) KEEP="$2"; shift 2 ;;
    --with-env) WITH_ENV=1; shift ;;
    --full) WITH_ENV=1; WITH_REC=1; shift ;;
    --with-recordings) WITH_REC=1; shift ;;
    --label) LABEL="-$2"; shift 2 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
require_vars DATA_ROOT POSTGRES_DB POSTGRES_USER
if ! pmsg="$(validate_env_paths)"; then printf '%s\n' "$pmsg" >&2; die "Некорректный путь в .env — резервная копия не создана (ничего не записано на диск)."; fi
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
# проверка читаемости: pg_restore читает оглавление всего архива; повреждённый или обрезанный дамп здесь не пройдёт
if ! dc exec -T postgres pg_restore --list < "$OUT" > /dev/null 2>&1; then
  mv -f "$OUT" "$OUT.bad"; die "Дамп не читается pg_restore — копия повреждена и отложена как $OUT.bad (места на диске достаточно? см. журнал postgres)"
fi
SET_KEY="${TS}${LABEL}"                # по этой части имени файлы одного набора находятся при ротации
SET_FILES=()
add_to_set() { # контрольная сумма рядом с файлом; файл входит в копию на другой ресурс
  ( cd "$DEST" && sha256sum "$(basename "$1")" > "$(basename "$1").sha256" ) 2>/dev/null || warn "Контрольная сумма не записана (нет sha256sum): $1"
  SET_FILES+=("$1")
}
add_to_set "$OUT"
ok "Дамп БД: $OUT ($(du -h "$OUT" | cut -f1)), проверен pg_restore --list"

if [ "$WITH_ENV" -eq 1 ]; then
  F="$DEST/${COMPOSE_PROJECT_NAME}-env-${SET_KEY}.env"
  cp "$ENV_FILE" "$F.part" && chmod 600 "$F.part" && mv -f "$F.part" "$F" || { rm -f "$F.part"; die "Копия .env не записана"; }
  add_to_set "$F"
  warn "Копия .env содержит секреты: $F"
fi
if [ "$WITH_REC" -eq 1 ]; then
  FILES=(); for d in recordings exports chat-files avatars branding ca tls; do [ -d "$DATA_ROOT/$d" ] && FILES+=("$d"); done
  if [ "${#FILES[@]}" -gt 0 ]; then
    F="$DEST/${COMPOSE_PROJECT_NAME}-files-${SET_KEY}.tar.gz"
    rc=0; tar -C "$DATA_ROOT" -czf "$F.part" "${FILES[@]}" || rc=$?
    # код 1 у tar — файл изменился при чтении (идёт встреча): архив цельный, просто этот файл мог попасть не последней версией
    [ "$rc" -le 1 ] || { rm -f "$F.part"; die "Архив файлов не создан (tar, код $rc): места на диске достаточно?"; }
    [ "$rc" -eq 0 ] || warn "Часть файлов менялась во время архивации: идёт встреча?"
    gzip -t "$F.part" 2>/dev/null || { rm -f "$F.part"; die "Архив файлов не читается: копия не сохранена"; }
    mv -f "$F.part" "$F"
    add_to_set "$F"
    ok "Файлы (${FILES[*]}): $F ($(du -h "$F" | cut -f1))"
  else warn "В $DATA_ROOT нет каталогов файлов встреч — архив файлов не создан"; fi
fi

# Копия на другой локальный путь (обычно — смонтированный сетевой ресурс). Едет весь набор: дамп без .env при потере сервера бесполезен, ключи шифрования настроек лежат в .env.
copy_to_dir() { # имя файла .part до полной записи: оборванная копия не выглядит настоящей
  local f b
  for f in "${SET_FILES[@]}"; do
    b="$(basename "$f")"
    cp -f "$f" "$BACKUP_COPY_DIR/$b.part" && mv -f "$BACKUP_COPY_DIR/$b.part" "$BACKUP_COPY_DIR/$b" || { rm -f "$BACKUP_COPY_DIR/$b.part"; return 1; }
    [ ! -f "$f.sha256" ] || cp -f "$f.sha256" "$BACKUP_COPY_DIR/" || return 1
  done
}
if [ -n "${BACKUP_COPY_DIR:-}" ]; then
  if mkdir -p "$BACKUP_COPY_DIR" 2>/dev/null && copy_to_dir; then
    ok "Копия набора (${#SET_FILES[@]} файлов): $BACKUP_COPY_DIR"
    [ "$WITH_ENV" -eq 1 ] || warn "В $BACKUP_COPY_DIR нет .env: без него зашифрованные настройки не прочитать. Полный набор — backup.sh --full"
  else warn "Не удалось скопировать набор в BACKUP_COPY_DIR=$BACKUP_COPY_DIR (смонтирован ли ресурс? права?). Локальная копия сохранена: $DEST"; fi
fi

# Ротация: только файлы ЭТОГО проекта по шаблону имени, и весь набор целиком, иначе архивы файлов копились бы без предела.
rotate() { # rotate КАТАЛОГ
  local d="$1" f key
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    key="${f##*"${COMPOSE_PROJECT_NAME}"-db-}"; key="${key%.dump}"
    rm -f -- "$f" "$f.sha256" "$d/${COMPOSE_PROJECT_NAME}-env-$key.env" "$d/${COMPOSE_PROJECT_NAME}-env-$key.env.sha256" \
      "$d/${COMPOSE_PROJECT_NAME}-files-$key.tar.gz" "$d/${COMPOSE_PROJECT_NAME}-files-$key.tar.gz.sha256"
    info "удалён старый набор: $key"
  done < <(ls -1t "$d"/"${COMPOSE_PROJECT_NAME}"-db-*.dump 2>/dev/null | tail -n +$((KEEP+1)))
}
rotate "$DEST"
[ -z "${BACKUP_COPY_DIR:-}" ] || [ ! -d "$BACKUP_COPY_DIR" ] || rotate "$BACKUP_COPY_DIR"
ok "Резервное копирование завершено."
