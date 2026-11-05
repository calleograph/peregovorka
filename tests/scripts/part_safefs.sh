# shellcheck shell=bash
# Подключается из run.sh: scripts/safefs.py — root не следует за ссылками, подложенными приложением. Без root проверяются отказы и обычная смена режима; настоящий mount и гонка — tests/live/volumes/swap_race.sh.
SF="$TMP/safefs"; mkdir -p "$SF/base/live/nginx" "$SF/outside"; echo x > "$SF/outside/victim"; chmod 600 "$SF/outside/victim"; echo s > "$SF/base/live/nginx/secret.conf"; chmod 600 "$SF/base/live/nginx/secret.conf"
safefs() { python3 -I "$ROOT/scripts/safefs.py" "$@" 2>/dev/null; }
me="$(id -u) $(id -g)"
rc() { local want="$1"; shift; "$@"; [ $? -eq "$want" ]; }
export -f safefs rc 2>/dev/null
# shellcheck disable=SC2086
t "safefs: обычный файл получает режим" bash -c 'safefs fix "$1" live/nginx/secret.conf $2 640 file && [ "$(stat -c %a "$1/live/nginx/secret.conf")" = 640 ]' _ "$SF/base" "$me"
ln -s "$SF/outside/victim" "$SF/base/live/nginx/link.conf"
# shellcheck disable=SC2086
t "safefs: ссылка вместо файла — отказ, цель не тронута" bash -c 'rc 3 safefs fix "$1" live/nginx/link.conf $3 640 file; r=$?; [ $r -eq 0 ] && [ "$(stat -c %a "$2/victim")" = 600 ]' _ "$SF/base" "$SF/outside" "$me"
ln -s "$SF/outside" "$SF/base/live/lnk"
# shellcheck disable=SC2086
t "safefs: ссылка в середине пути — отказ" bash -c 'rc 3 safefs fix "$1" live/lnk/victim $3 640 file && [ "$(stat -c %a "$2/victim")" = 600 ]' _ "$SF/base" "$SF/outside" "$me"
# shellcheck disable=SC2086
t "safefs: ссылка вместо каталога — отказ, цель не тронута" bash -c 'rc 3 safefs fix "$1" live/lnk $3 2750 dir && [ "$(stat -c %a "$2")" != 2750 ]' _ "$SF/base" "$SF/outside" "$me"
ln "$SF/outside/victim" "$SF/base/live/nginx/hard.conf"
# shellcheck disable=SC2086
t "safefs: жёсткая ссылка на чужой файл — отказ" bash -c 'rc 3 safefs fix "$1" live/nginx/hard.conf $3 640 file && [ "$(stat -c %a "$2/victim")" = 600 ]' _ "$SF/base" "$SF/outside" "$me"
rm -f "$SF/base/live/nginx/hard.conf"; mkfifo "$SF/base/live/nginx/f.conf"
# shellcheck disable=SC2086
t "safefs: канал вместо файла — отказ без зависания" bash -c 'rc 3 timeout 5 python3 -I "$0/scripts/safefs.py" fix "$1" live/nginx/f.conf $2 640 file' "$ROOT" "$SF/base" "$me"
# shellcheck disable=SC2086
t "safefs: «..» в пути — отказ" bash -c 'rc 3 safefs fix "$1" live/../live/nginx/secret.conf $2 640 file' _ "$SF/base" "$me"
mkdir -p "$SF/bin" "$SF/vol/real" "$SF/mp"; ln -s /etc "$SF/vol/lnk"
printf '#!/bin/sh\necho "$*" > "%s/mount.args"\nls "$(echo "$*" | sed -n "s/.*\\(\\/proc[^ ]*\\).*/\\1/p")/" > "%s/mount.list" 2>&1\n' "$SF" "$SF" > "$SF/bin/mount"; chmod +x "$SF/bin/mount"
touch "$SF/vol/real/marker"
t "safefs bind: подставной mount получает открытый каталог и --no-canonicalize" bash -c 'PATH="$1/bin:$PATH" safefs bind "$1/vol/real" "$1/mp" && grep -q -- "--no-canonicalize --bind /proc/self/fd/" "$1/mount.args" && grep -q marker "$1/mount.list"' _ "$SF"
rm -f "$SF/mount.args"
t "safefs bind: ссылка в пути — mount не вызывается" bash -c 'PATH="$1/bin:$PATH" rc 3 safefs bind "$1/vol/lnk" "$1/mp" && [ ! -e "$1/mount.args" ]' _ "$SF"
t "vollib: родитель каталога данных отклоняется" bash -c 'DATA_ROOT=/srv/data/pg REPO_ROOT=/opt/repo; source "$1/scripts/lib/vollib.sh"; mkdir -p "$2/d"; vol_roots() { echo /; }; DATA_ROOT="$2/d/pg"; vol_path_error "$2/d" | grep -q "данные приложения"' _ "$ROOT" "$SF"
t "vollib: родитель каталога программы отклоняется" bash -c 'DATA_ROOT=/nonexistent; source "$1/scripts/lib/vollib.sh"; mkdir -p "$2/r"; vol_roots() { echo /; }; REPO_ROOT="$2/r/repo"; vol_path_error "$2/r" | grep -q "программу сервера"' _ "$ROOT" "$SF"
t "vollib: повторное подключение идёт только через vol_bind" bash -c '! grep -nE "^[^#]*[^_]mount --bind" "$1/scripts/lib/vollib.sh"' _ "$ROOT"
t "updatelib: каталоги трансляций меняются только через safefs" bash -c '! sed -n "/^live_dirs_prepare/,/^}/p" "$1/scripts/lib/updatelib.sh" | grep -E "_ld_run (chown|chmod)"' _ "$ROOT"
