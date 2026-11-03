# shellcheck shell=bash
# Подключается из run.sh: каталоги видеоподсистемы и секрет подписи HLS — единая подготовка live_dirs_prepare (установка, обновление, «Исправить права каталогов»),
# права секрета (0640, группа 10001), чтение процессом nginx (uid 101 + группа 10001) и отказ постороннему пользователю.
# Владельцев 10001 назначить может только root: без sudo -n часть пропускается (в CI sudo есть).
if sudo -n true 2>/dev/null; then
  LD="$TMP/livedirs"; mkdir -p "$LD"
  lib() { bash -c 'source "$1/scripts/lib/common.sh" >/dev/null 2>&1; shift; "$@"' _ "$ROOT" "$@"; }
  own() { sudo -n stat -c '%u:%g %a' "$1"; }
  export -f lib own

  # 1. новая установка: каталогов нет
  mkdir -p "$LD/a"
  t "live: новая установка — live, live/b, live/acc, live/nginx создаются с владельцем 10001:10001" lib live_dirs_prepare "$LD/a"
  t "live: live/nginx — 2750 (пишет backend, читает группа 10001, setgid)" eq "$(own "$LD/a/live/nginx")" "10001:10001 2750"
  t "live: live/b и live/acc — владелец 10001" bash -c '[ "$(sudo -n stat -c %u:%g "$1/live/b")" = 10001:10001 ] && [ "$(sudo -n stat -c %u:%g "$1/live/acc")" = 10001:10001 ]' _ "$LD/a"
  t "live: после подготовки проблем нет" eq "$(lib live_dirs_problems "$LD/a")" ""

  # 2. Docker уже создал live/nginx от root (точка монтирования web), секрет записан прежней версией с правами 644
  mkdir -p "$LD/b/live"; sudo -n mkdir -p "$LD/b/live/nginx"; sudo -n chmod 755 "$LD/b/live/nginx"
  printf 'set $hls_secret "abc123";\n' | sudo -n tee "$LD/b/live/nginx/secret.conf" >/dev/null; sudo -n chmod 644 "$LD/b/live/nginx/secret.conf"
  t "live: каталог от root и секрет 644 распознаются как проблема" bash -c 'p="$("$@")"; [[ "$p" == *"live/nginx: владелец 0:0"* ]] && [[ "$p" == *"секрет HLS"* ]]' _ lib live_dirs_problems "$LD/b"
  t "live: исправление — успех" lib live_dirs_prepare "$LD/b"
  t "live: каталог от root исправлен (10001:10001 2750)" eq "$(own "$LD/b/live/nginx")" "10001:10001 2750"
  t "live: секрет — 10001:10001 0640, содержимое сохранено" bash -c '[ "$(sudo -n stat -c "%u:%g %a" "$1")" = "10001:10001 640" ] && [ "$(sudo -n cat "$1")" = "set \$hls_secret \"abc123\";" ]' _ "$LD/b/live/nginx/secret.conf"
  t "live: повторное исправление ничего не меняет и проблем не находит" bash -c 'a="$(sudo -n stat -c "%u:%g %a %Y" "$1/live/nginx/secret.conf")"; lib live_dirs_prepare "$1" && [ "$a" = "$(sudo -n stat -c "%u:%g %a %Y" "$1/live/nginx/secret.conf")" ] && [ -z "$(lib live_dirs_problems "$1")" ]' _ "$LD/b"
  t "live: посторонний локальный пользователь секрет не читает" bash -c '! sudo -n -u nobody cat "$1" >/dev/null 2>&1' _ "$LD/b/live/nginx/secret.conf"

  # 3. процессы контейнеров: backend (10001:999 + группа 10001) атомарно заменяет секрет, nginx (101 + группа 10001) читает, без группы — нет
  IMG="$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -m1 -E '^(alpine|nginxinc/nginx-unprivileged):' || true)"
  if [ -n "$IMG" ]; then
    run() { docker run --rm --entrypoint sh -v "$LD/b/live/nginx:/s" "$@"; }
    export -f run; export LD
    t "live: backend (uid 10001, gid 999, группа 10001) атомарно заменяет секрет: 0640, группа 10001" \
      run --user 10001:999 --group-add 10001 "$IMG" -c 'umask 077; printf "set \$hls_secret \"new\";\n" > /s/.t && chgrp 10001 /s/.t && chmod 640 /s/.t && mv -f /s/.t /s/secret.conf && [ "$(stat -c "%g %a" /s/secret.conf)" = "10001 640" ]'
    t "live: nginx (uid 101 с группой 10001) читает секрет" run --user 101:101 --group-add 10001 "$IMG" -c 'grep -q "new" /s/secret.conf'
    t "live: процесс без группы 10001 секрет не читает" bash -c '! "$@"' _ run --user 101:101 "$IMG" -c 'cat /s/secret.conf'
  fi
  sudo -n rm -rf "$LD"
fi
