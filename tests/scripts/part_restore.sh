# shellcheck shell=bash
# Подключается из run.sh: восстановление БД на настоящем PostgreSQL. Compose подменён функцией dc: команды уходят в отдельный контейнер, приложения нет.
# Главная проверка: при любом сбое рабочая база не меняется, приложение запускается. Пропускается, если нет Docker или образа.
if command -v docker >/dev/null 2>&1 && docker image inspect postgres:16-alpine >/dev/null 2>&1; then
  RS="$TMP/restore"; mkdir -p "$RS"; PGC="pgtest-restore-$$"
  docker run -d --rm --name "$PGC" --tmpfs /var/lib/postgresql/data -e POSTGRES_USER=pg -e POSTGRES_PASSWORD=x -e POSTGRES_DB=app postgres:16-alpine >/dev/null
  for _ in $(seq 60); do docker exec "$PGC" pg_isready -U pg -d app >/dev/null 2>&1 && sleep 1 && docker exec "$PGC" psql -U pg -d app -c 'select 1' >/dev/null 2>&1 && break; sleep 1; done
  sql() { docker exec -i "$PGC" psql -U pg -d "${2:-app}" -v ON_ERROR_STOP=1 -tAc "$1"; }
  cat > "$RS/lib.sh" <<EOL
source "\$ROOT/scripts/lib/common.sh" >/dev/null 2>&1
set +e
POSTGRES_USER=pg; POSTGRES_DB=app; DATA_ROOT="$RS"; WAIT=1; UPD_POLL=1
dc() {
  case "\$1" in
    exec) shift 3; docker exec -i "$PGC" "\$@" ;;
    *) echo "\$*" >> "$RS/calls" ;;
  esac
}
upd_wait_healthy() { [ "\${HEALTHY:-1}" = 1 ]; }
EOL
  export RS PGC
  sql "CREATE TABLE users (id int PRIMARY KEY, name text); CREATE TABLE alembic_version (version_num text); INSERT INTO alembic_version VALUES ('0001'); CREATE TABLE posts (id int PRIMARY KEY, u int REFERENCES users(id)); INSERT INTO users VALUES (1, 'в дампе');" >/dev/null
  docker exec "$PGC" pg_dump -U pg -d app -Fc > "$RS/good.dump"
  # после дампа: новая таблица ссылается на старую (именно здесь --clean рвал внешние ключи и останавливался на полпути)
  sql "CREATE TABLE extra (id int PRIMARY KEY, u int REFERENCES users(id)); UPDATE users SET name = 'рабочие данные'; INSERT INTO extra VALUES (1, 1);" >/dev/null
  head -c 2000 "$RS/good.dump" > "$RS/broken.dump"
  head -c 2000 /dev/urandom > "$RS/random.dump"
  run_restore() { bash -c 'source "$RS/lib.sh"; restore_preflight "$1" && restore_from_dump "$1"; rc=$?; echo "$RESTORE_ERROR" > "$RS/err"; echo "$DB_KEPT" > "$RS/kept"; exit $rc' _ "$1"; }
  export -f run_restore 2>/dev/null
  t "restore: мусор вместо дампа отклоняется заранее" bash -c '! run_restore "$RS/random.dump"'
  t "restore: обрезанный дамп — рабочая база цела" bash -c '! run_restore "$RS/broken.dump"'
  t "restore: после неудачи данные рабочей базы на месте" eq "$(sql 'SELECT name FROM users')" "рабочие данные"
  t "restore: после неудачи внешние ключи на месте" eq "$(sql "SELECT count(*) FROM pg_constraint WHERE contype = 'f'")" "2"
  t "restore: вспомогательная база убрана" eq "$(sql "SELECT count(*) FROM pg_database WHERE datname LIKE 'app\_%'" postgres)" "0"
  # нормальное восстановление
  t "restore: копия восстанавливается" run_restore "$RS/good.dump"
  t "restore: приложение запущено после замены базы" grep -q 'up -d --no-build backend asr web' "$RS/calls"
  t "restore: данные из копии" eq "$(sql 'SELECT name FROM users')" "в дампе"
  t "restore: лишней таблицы из новой схемы нет" eq "$(sql "SELECT count(*) FROM pg_tables WHERE tablename = 'extra'")" "0"
  KEPT="$(cat "$RS/kept")"
  t "restore: прежняя база сохранена" eq "$(sql 'SELECT name FROM users' "$KEPT")" "рабочие данные"
  sql "DROP DATABASE \"$KEPT\" WITH (FORCE)" postgres >/dev/null
  # приёмка не пройдена: та же версия схемы, приложение не поднялось
  sql "UPDATE users SET name = 'рабочие 2'" >/dev/null
  t "restore: приложение не поднялось — ошибка" bash -c '! HEALTHY=0 run_restore "$RS/good.dump"'
  t "restore: прежняя база возвращена на место" eq "$(sql 'SELECT name FROM users')" "рабочие 2"
  t "restore: причина названа" grep -q 'Прежняя база возвращена' "$RS/err"
  # файлы: архив распаковывается рядом и заменяет каталог целиком
  mkdir -p "$RS/src/recordings/a" "$RS/recordings"; echo new > "$RS/src/recordings/a/f.wav"; echo old > "$RS/recordings/old.wav"
  tar -C "$RS/src" -czf "$RS/files.tar.gz" recordings
  t "restore: файлы из архива, прежние отложены" bash -c 'source "$RS/lib.sh"; upd_ensure_data_dirs() { :; }; restore_files "$RS/files.tar.gz" && [ "$(cat "$RS/recordings/a/f.wav")" = new ] && [ "$(cat "$RS"/recordings.before-restore-*/old.wav)" = old ]'
  t "restore.sh: после замены файлов контейнеры пересоздаются" bash -c 'grep -q "APP=(backend asr web).*livekit-egress" "$1/scripts/restore.sh" && grep -A1 "if restore_files" "$1/scripts/restore.sh" | grep -q "force-recreate"' _ "$ROOT"
  head -c 100 "$RS/files.tar.gz" > "$RS/cut.tar.gz"
  t "restore: обрезанный архив не трогает каталоги" bash -c 'source "$RS/lib.sh"; ! restore_files "$RS/cut.tar.gz" && [ "$(cat "$RS/recordings/a/f.wav")" = new ] && [ -z "$(ls -d "$RS"/.restore.* 2>/dev/null)" ]'
  docker rm -f "$PGC" >/dev/null 2>&1
fi

# .env при сбое записи
ES="$TMP/envset"; mkdir -p "$ES"
envlib() { bash -c 'source "$1/scripts/lib/common.sh" >/dev/null 2>&1; set +e; shift; "$@"' _ "$ROOT" "$@"; }
export -f envlib 2>/dev/null
printf 'APP_MASTER_KEY=secret\nA=1\n' > "$ES/.env"; chmod 640 "$ES/.env"
t ".env: значение заменяется, права сохраняются" bash -c 'envlib upd_env_set_key "$1/.env" A 2 && grep -q "^A=2$" "$1/.env" && grep -q "^APP_MASTER_KEY=secret$" "$1/.env" && [ "$(stat -c %a "$1/.env")" = 640 ]' _ "$ES"
t ".env: новая переменная дописывается" bash -c 'envlib upd_env_set_key "$1/.env" B 3 && grep -q "^B=3$" "$1/.env" && grep -q "^APP_MASTER_KEY=secret$" "$1/.env"' _ "$ES"
mkdir -p "$ES.bin"; printf '#!/bin/sh\nexit 2\n' > "$ES.bin/awk"; chmod +x "$ES.bin/awk"
t ".env: сбой awk — файл цел, код ошибки" bash -c '! PATH="$1:$PATH" envlib upd_env_set_key "$2/.env" A 9; rc=$?; [ "$rc" = 0 ] && grep -q "^A=2$" "$2/.env" && grep -q "^APP_MASTER_KEY=secret$" "$2/.env"' _ "$ES.bin" "$ES"
t ".env: каталог только для чтения — файл цел" bash -c 'chmod 555 "$1"; ! envlib upd_env_set_key "$1/.env" A 9; rc=$?; chmod 755 "$1"; [ "$rc" = 0 ] && grep -q "^A=2$" "$1/.env"' _ "$ES"
t ".env: временных файлов не осталось" bash -c '[ "$(ls -A "$1" | wc -l)" = 1 ]' _ "$ES"

# install.sh: параметры доходят до перезапуска через sudo
IS="$TMP/inst"; mkdir -p "$IS/bin"
printf '#!/bin/bash\n[ "$1" = -E ] && shift\necho "$@" > "%s/args"\n' "$IS" > "$IS/bin/sudo"; chmod +x "$IS/bin/sudo"
t "install.sh: параметры не теряются при перезапуске через sudo" bash -c '[ "$(id -u)" -ne 0 ] || exit 0; PATH="$1/bin:$PATH" bash "$2/install.sh" --data /big --ref v1.2.3 >/dev/null 2>&1; grep -q -- "--data /big --ref v1.2.3" "$1/args"' _ "$IS" "$ROOT"

# backup.sh: набор, копия на другой ресурс, ротация целиком. dc подменён: дамп — фиктивный файл
BK="$TMP/bk"; mkdir -p "$BK/data/recordings/r" "$BK/repo/scripts/lib" "$BK/copy"; echo wav > "$BK/data/recordings/r/a.wav"
cp "$ROOT"/scripts/backup.sh "$BK/repo/scripts/"; cp "$ROOT"/scripts/lib/* "$BK/repo/scripts/lib/"
printf 'dc() { case "$1" in ps) echo id ;; exec) case "$*" in *pg_dump*) echo dump ;; esac ;; esac; }\n' >> "$BK/repo/scripts/lib/common.sh"
printf 'COMPOSE_PROJECT_NAME=bkp\nDATA_ROOT=%s\nPOSTGRES_DB=a\nPOSTGRES_USER=b\nBACKUP_COPY_DIR=%s\nAPP_MASTER_KEY=k\n' "$BK/data" "$BK/copy" > "$BK/.env"
backup() { bash "$BK/repo/scripts/backup.sh" --env "$BK/.env" "$@"; }
export -f backup; export BK
t "backup: полный набор создан" backup --full --keep 2
t "backup: в копии на другом ресурсе весь набор" bash -c 'ls "$BK"/copy/bkp-db-*.dump && ls "$BK"/copy/bkp-env-*.env && ls "$BK"/copy/bkp-files-*.tar.gz && ls "$BK"/copy/bkp-files-*.sha256'
t "backup: копия .env закрыта от посторонних" bash -c '[ "$(stat -c %a "$BK"/data/backups/bkp-env-*.env)" = 600 ]'
t "backup: контрольные суммы верны" bash -c 'cd "$BK/copy" && sha256sum -c ./*.sha256'
t "backup: временных файлов нет" bash -c '[ -z "$(find "$BK" -name "*.part")" ]'
sleep 1; backup --full --keep 2 >/dev/null 2>&1; sleep 1; backup --full --keep 2 >/dev/null 2>&1
t "backup: ротация убирает весь набор, а не только дамп" bash -c 'for d in "$BK/data/backups" "$BK/copy"; do [ "$(ls "$d"/bkp-db-*.dump | wc -l)" = 2 ] && [ "$(ls "$d"/bkp-env-*.env | wc -l)" = 2 ] && [ "$(ls "$d"/bkp-files-*.tar.gz | wc -l)" = 2 ] || exit 1; done'
