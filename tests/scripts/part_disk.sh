# shellcheck shell=bash
# Подключается из run.sh: обслуживание диска перед обновлением (upd_disk_maintenance, scripts/lib/autorepair.sh).
# Подставные docker и df моделируют место, кэш сборки и образы; каждая команда docker пишется в журнал — проверяется, что удаляется и что нет.
# Используются: ROOT, TMP, t.

DK="$TMP/disk"; mkdir -p "$DK/bin"
cat > "$DK/bin/docker" <<'DOCK'
#!/usr/bin/env bash
S="$FAKE_DISK"; echo "$*" >> "$S/log"
free="$(cat "$S/free")"; cache="$(cat "$S/cache")"
case "$1 $2" in
  "info -f") echo "$S/root" ;;
  "system df") echo "Images|10GB|0B"; echo "Build Cache|${cache}GB|${cache}GB" ;;
  "builder prune")
    case "$*" in
      *--help*) echo "      --keep-storage bytes   Amount of disk space to keep for cache" ;;
      *until=*) old="$(cat "$S/old")"; [ "$old" -gt "$cache" ] && old="$cache"; echo $((free + old)) > "$S/free"; echo $((cache - old)) > "$S/cache"; echo 0 > "$S/old" ;;
      *--keep-storage*) k="$(sed -E 's/.*--keep-storage ([0-9]+)gb.*/\1/' <<<"$*")"; [ "$cache" -gt "$k" ] && { echo $((free + cache - k)) > "$S/free"; echo "$k" > "$S/cache"; } ;;
      *) echo $((free + cache)) > "$S/free"; echo 0 > "$S/cache" ;;
    esac ;;
  "image prune") echo $((free + $(cat "$S/dangling"))) > "$S/free"; echo 0 > "$S/dangling" ;;
  "ps -a") cat "$S/running" ;;
  "images --format")
    if [ -n "${4:-}" ]; then grep "^$4:" "$S/images" | awk -F'[:|]' '{print $3"|"$2}'      # «CreatedAt|Tag» одного репозитория
    else cut -d'|' -f1 "$S/images"; fi ;;
  "rmi "*) grep -qxF "$2" <(cut -d'|' -f1 "$S/images") || exit 1
    grep -vF "$2|" "$S/images" > "$S/images.n"; mv "$S/images.n" "$S/images"; echo $((free + 3)) > "$S/free" ;;
  *) exit 0 ;;
esac
DOCK
cat > "$DK/bin/df" <<'DF'
#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted"; echo "/dev/x 999999999 1 $(( $(cat "$FAKE_DISK/free") * 1048576 )) 1% /"
DF
chmod +x "$DK/bin/docker" "$DK/bin/df"

# disk_case ИМЯ свободно кэш старый_кэш висячие — новое состояние; образы: «репо:тег|создан»
disk_case() {
  local d="$DK/$1"; rm -rf "$d"; mkdir -p "$d/root" "$d/data/state"
  echo "$2" > "$d/free"; echo "$3" > "$d/cache"; echo "$4" > "$d/old"; echo "$5" > "$d/dangling"; : > "$d/log"
  printf '%s\n' "pg-backend:aaaaaaaaaaaa" "postgres:16-alpine" > "$d/running"
  printf '%s\n' "pg-backend:aaaaaaaaaaaa|2026-10-30" "pg-asr:aaaaaaaaaaaa|2026-10-30" \
    "pg-backend:prev-bbbbbbbbbbbb|2026-10-20" "pg-asr:prev-bbbbbbbbbbbb|2026-10-20" \
    "pg-backend:cccccccccccc|2026-10-10" "pg-asr:cccccccccccc|2026-10-10" "pg-backend:prev-dddddddddddd|2026-09-01" "pg-asr:dddddddddddd|2026-09-01" \
    "postgres:16-alpine|2026-08-01" "livekit/livekit-server:v1.13.10|2026-08-01" > "$d/images"
  echo "previous_git_commit=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" > "$d/data/state/last-update.state"
}
disk_run() { # ИМЯ [переменные…] → код возврата upd_disk_maintenance; вывод — в $d/out
  local d="$DK/$1"; shift
  env PATH="$DK/bin:$PATH" FAKE_DISK="$d" DATA_ROOT="$d/data" COMPOSE_PROJECT_NAME=pg IMAGE_TAG=eeeeeeeeeeee MIN_FREE_DISK_GB=30 "$@" \
    bash -c 'source "$1"; upd_disk_maintenance' _ "$ROOT/scripts/lib/common.sh" > "$d/out" 2>&1
}
export -f disk_run disk_case; export DK ROOT
DANGER='system prune|volume|rm -rf|container prune|network prune|rmi postgres|rmi livekit'

disk_case ok 40 5 0 0
t "диск: места хватает и кэш небольшой — ничего не чистится" bash -c 'disk_run ok; ! grep -q prune "$1/log" && ! grep -q "^rmi" "$1/log"' _ "$DK/ok"

# стенд владельца: 28 ГБ кэша, свободно 12 ГБ, DATA_ROOT на том же диске — Preflight требует 30
disk_case cache 12 28 12 1
disk_run cache; echo $? > "$DK/cache/rc"
t "диск: 28 ГБ кэша — старый кэш, висячие образы и подрезка кэша до 8 ГБ дают ≥ 30 ГБ; цель достигнута" bash -c '[ "$(cat "$1/rc")" = 0 ] && [ "$(cat "$1/free")" -ge 30 ] && [ "$(cat "$1/cache")" = 8 ]' _ "$DK/cache"
t "диск: ступени по порядку — сначала кэш старше 7 дней, затем образы без тега, затем подрезка; полной очистки кэша нет" bash -c '
  l="$1/log"; a=$(grep -n "until=168h" "$l" | cut -d: -f1); b=$(grep -n "^image prune" "$l" | cut -d: -f1); c=$(grep -n "keep-storage 8gb" "$l" | cut -d: -f1)
  [ -n "$a" ] && [ -n "$b" ] && [ -n "$c" ] && [ "$a" -lt "$b" ] && [ "$b" -lt "$c" ] && ! grep -qxE "builder prune -f" "$l"' _ "$DK/cache"
t "диск: когда кэша хватило, образы проекта не удаляются" bash -c '! grep -q "^rmi" "$1/log"' _ "$DK/cache"
t "диск: в журнале обновления — было/стало по каждой ступени и итог" bash -c 'grep -q "ступень 1" "$1/out" && grep -q "ступень 3" "$1/out" && grep -q "свободно 12 ГБ, кэш сборки 28 ГБ" "$1/out" && grep -q "Диск: свободно .* (было 12)" "$1/out"' _ "$DK/cache"

# мало места и почти нет кэша — доходит до старых образов проекта
disk_case images 5 9 0 0
disk_run images; echo $? > "$DK/images/rc"
t "диск: образ отката (prev- версии из last-update.state), работающий и две самые новые версии остаются" bash -c '
  i="$1/images"; grep -q "^pg-backend:aaaaaaaaaaaa|" "$i" && grep -q "^pg-asr:aaaaaaaaaaaa|" "$i" && grep -q "^pg-backend:prev-bbbbbbbbbbbb|" "$i" && grep -q "^pg-asr:prev-bbbbbbbbbbbb|" "$i"' _ "$DK/images"
t "диск: старее — удаляются (prev- и обычный тег одинаково)" bash -c '! grep -q "dddddddddddd" "$1/images" && grep -q "^rmi pg-backend:prev-dddddddddddd" "$1/log"' _ "$DK/images"
t "диск: чужие образы (postgres, LiveKit) не трогаются; томов, контейнеров и system prune нет" bash -c 'grep -q "^postgres:16-alpine|" "$1/images" && grep -q "^livekit/" "$1/images" && ! grep -qE "$2" "$1/log"' _ "$DK/images" "$DANGER"
t "диск: не хватило и после всего — весь неиспользуемый кэш, код 1 и понятное сообщение (данные и откат не удаляются автоматически)" bash -c '
  [ "$(cat "$1/rc")" = 1 ] && grep -qxE "builder prune -f" "$1/log" && grep -q "освободите место на сервере" "$1/out"' _ "$DK/images"

# место есть, но кэш разросся — гигиена без удаления образов
disk_case hyg 60 26 3 0
t "диск: места достаточно, кэш 26 ГБ > 20 — подрезается, образы не трогаются" bash -c 'disk_run hyg; [ "$(cat "$1/cache")" -le 20 ] && ! grep -q "^rmi" "$1/log"' _ "$DK/hyg"

disk_case off 10 28 10 0
t "диск: UPD_DISK_MAINTENANCE=0 — только замер и предупреждение" bash -c 'disk_run off UPD_DISK_MAINTENANCE=0; [ $? = 1 ] && ! grep -q prune "$1/log" && grep -q "выключено" "$1/out"' _ "$DK/off"

t "update.sh: обслуживание диска — до Preflight и перед сборкой" bash -c '
  f="$1/scripts/update.sh"; a=$(grep -n "upd_disk_maintenance ||" "$f" | head -1 | cut -d: -f1); p=$(grep -n "scripts/preflight.sh\" --env" "$f" | cut -d: -f1); b=$(grep -n "^  build_images ||" "$f" | cut -d: -f1)
  [ -n "$a" ] && [ "$a" -lt "$p" ] && [ "$(grep -c "upd_disk_maintenance" "$f")" = 2 ] && [ "$p" -lt "$b" ]' _ "$ROOT"
t "автопочинка: в обслуживании диска нет docker system/volume prune и удаления каталогов" bash -c '! grep -nE "system prune|volume (rm|prune)|rm -rf" "$1/scripts/lib/autorepair.sh" | grep -vE "^[0-9]+:[[:space:]]*#"' _ "$ROOT"
