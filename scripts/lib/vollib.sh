#!/usr/bin/env bash
# vollib.sh — внешние тома: каталог сервера (смонтированный NFS, SMB, диск) становится доступен контейнеру backend без правки compose и без пересоздания контейнеров.
#
# Устройство. В контейнер один раз подключён каталог $DATA_ROOT/ext (/data/ext) с распространением rslave. Чтобы открыть том, исполнитель (служба от root, updater.sh) делает
# `mount --bind ПУТЬ $DATA_ROOT/ext/ИД` на хосте, и том сразу появляется в работающем контейнере как /data/ext/ИД. Сокет Docker приложению не нужен.
# Запрос из веб-интерфейса — только операция и путь; путь проверяется здесь по белому списку корней (VOLUME_ALLOWED_ROOTS) и без символьных ссылок.
#
# Защита от записи на локальный диск:
#   • точка подключения ИД — пустой каталог root:root 0555: пока том не подключён, пользователь приложения писать в него не может;
#   • запомнено, какая файловая система подключалась (тип и источник). vol_reconcile (раз в 15 с и при запуске) подключает том заново только к ней и отключает, если её не стало;
#   • backend сверяет фактическое монтирование и файл-метку перед каждой записью (services/storage.py).
# Состояние: $DATA_ROOT/state/volumes.tsv (ид, путь, тип, источник), для веб-интерфейса — $DATA_ROOT/updater/volumes.json. Только функции; зависит от common.sh (load_env выполнен).

VOLUME_UID="${BACKEND_UID:-10001}"
VOLUME_DEFAULT_ROOTS="/mnt:/media:/srv"

vol_dir() { echo "$DATA_ROOT/ext"; }
vol_list() { echo "$DATA_ROOT/state/volumes.tsv"; }

# Откуда разрешено подключать. Меняется только в .env на сервере: из веб-интерфейса расширить нельзя.
vol_roots() { echo "${VOLUME_ALLOWED_ROOTS:-$VOLUME_DEFAULT_ROOTS}" | tr ':' '\n'; }

vol_id() { # vol_id ПУТЬ → имя каталога: читаемое и уникальное для этого пути
  local slug sum
  slug="$(printf '%s' "${1#/}" | tr 'A-Z' 'a-z' | sed 's/[^a-z0-9]\+/-/g; s/^-//; s/-$//' | cut -c1-40)"
  sum="$(printf '%s' "$1" | sha256sum | cut -c1-6)"
  echo "${slug:-vol}-$sum"
}

vol_path_error() { # vol_path_error ПУТЬ → пусто или причина отказа. Путь приходит из веб-интерфейса: это граница доверия.
  local p="$1" real r ok=0
  [[ "$p" =~ ^/[A-Za-z0-9._/-]{1,200}$ ]] || { echo "Путь: абсолютный, только латиница, цифры и знаки . _ - /"; return; }
  [[ "$p" == *"/../"* || "$p" == *"/.." || "$p" == *"//"* || "$p" == *"/./"* || "$p" == */ ]] && { echo "Путь должен быть без «..», двойных и завершающих косых черт"; return; }
  [ -d "$p" ] || { echo "Каталог $p на сервере не найден"; return; }
  real="$(readlink -f "$p")"
  [ "$real" = "$p" ] || { echo "Путь содержит символьную ссылку: укажите настоящий путь ($real)"; return; }
  case "$p/" in "$DATA_ROOT"/*|"$REPO_ROOT"/*) echo "Этот каталог уже принадлежит приложению"; return ;; esac
  case "$DATA_ROOT/" in "$p"/*) echo "Каталог содержит данные приложения: укажите отдельный каталог"; return ;; esac      # родитель открыл бы .env и ключи
  case "$REPO_ROOT/" in "$p"/*) echo "Каталог содержит программу сервера: укажите отдельный каталог"; return ;; esac
  while IFS= read -r r; do
    [ -n "$r" ] || continue
    [ "$p" != "$r" ] && [[ "$p" == "$r"/* ]] && ok=1
  done < <(vol_roots)
  [ "$ok" = 1 ] || { echo "Разрешены каталоги внутри: $(vol_roots | paste -sd' ' -). Другие корни задаются в .env на сервере (VOLUME_ALLOWED_ROOTS)"; return; }
}

vol_mount_of() { # vol_mount_of ПУТЬ → «точка тип источник». Только таблица монтирований: файловую систему не трогаем, зависший NFS здесь не повесит исполнитель.
  findmnt -nrUo TARGET,FSTYPE,SOURCE 2>/dev/null | awk -v p="$1" '{ t=$1; if (t=="/" || p==t || index(p, t "/")==1) { if (length(t)>=length(b)) { b=t; r=$0 } } } END { print r }'
}

vol_bound() { [ -n "$(findmnt -nrUo TARGET --mountpoint "$1" 2>/dev/null)" ]; }

vol_alive() { timeout -k 1 "${VOLUME_PROBE_S:-5}" stat -f -c %T "$1" >/dev/null 2>&1; }

vol_write_error() { # vol_write_error КАТАЛОГ → пусто или причина: создать, прочитать и удалить файл от имени пользователя приложения
  local d="$1" f=".peregovorka-attach-test-$$"
  timeout -k 1 10 setpriv --reuid="$VOLUME_UID" --regid="$VOLUME_UID" --clear-groups sh -c 'f="$1/$2"; echo ok > "$f" && [ "$(cat "$f")" = ok ] && rm -f "$f"' _ "$d" "$f" 2>/dev/null \
    || echo "Пользователь приложения (uid $VOLUME_UID) не может записать в $d: выдайте ему права на ресурс или настройте соответствие пользователей на сервере хранения"
}

vol_propagates() { findmnt -no PROPAGATION -T "$DATA_ROOT" 2>/dev/null | grep -Eq 'shared|slave'; }

vol_state_json() { # всё, что нужно веб-интерфейсу: backend сам не видит хост
  local id path fstype source first=1 mp state free total
  printf '{"ts":%s,"propagates":%s,"roots":"%s","items":[' "$(date +%s)" "$(vol_propagates && echo true || echo false)" "$(vol_roots | paste -sd' ' -)"
  [ -f "$(vol_list)" ] && while IFS=$'\t' read -r id path fstype source; do
    [ -n "$id" ] || continue
    mp="$(vol_dir)/$id"; state=attached; free=null; total=null
    if ! vol_bound "$mp"; then state=detached
    elif ! vol_alive "$mp"; then state=unreachable
    else read -r total free < <(timeout -k 1 5 df -B1 --output=size,avail "$mp" 2>/dev/null | tail -1); fi
    [ "$first" = 1 ] || printf ','
    first=0
    printf '{"id":"%s","host_path":"%s","container_path":"/data/ext/%s","fstype":"%s","source":"%s","state":"%s","free":%s,"total":%s}' "$id" "$path" "$id" "$fstype" "$(jesc "$source")" "$state" "${free:-null}" "${total:-null}"
  done < "$(vol_list)"
  printf ']}\n'
}

vol_publish() { [ -d "$DATA_ROOT/updater" ] && vol_state_json | atomic_write "$DATA_ROOT/updater/volumes.json"; }

# Проверка пути и монтирование разделены: если каталог лежит на томе, куда пишет приложение, его могут заменить ссылкой на / между ними. safefs.py заново проходит путь без ссылок
# и монтирует уже открытый каталог, поэтому подмена после проверки не работает.
vol_bind() { timeout -k 1 20 python3 -I "$REPO_ROOT/scripts/safefs.py" bind "$1" "$2"; }

vol_mountpoint() { # пустая точка подключения: пока том не подключён, приложение не может писать в неё
  local mp="$1"
  mkdir -p "$(vol_dir)" "$mp" && chown root:root "$mp" && chmod 0555 "$mp"
}

vol_attach() { # vol_attach ПУТЬ: 0 — подключён; иначе причина в VOL_ERROR
  local p="$1" id mp info target fstype source err
  VOL_ERROR="$(vol_path_error "$p")"; [ -z "$VOL_ERROR" ] || return 1
  id="$(vol_id "$p")"; mp="$(vol_dir)/$id"; VOL_ID="$id"
  info="$(vol_mount_of "$p")"; read -r target fstype source <<<"$info"
  [ -n "$fstype" ] || { VOL_ERROR="Не удалось определить файловую систему каталога $p"; return 1; }
  vol_alive "$p" || { VOL_ERROR="Каталог $p не отвечает: ресурс недоступен или завис"; return 1; }
  err="$(vol_write_error "$p")"; [ -z "$err" ] || { VOL_ERROR="$err"; return 1; }
  if ! vol_bound "$mp"; then
    vol_mountpoint "$mp" || { VOL_ERROR="Не удалось подготовить каталог $mp"; return 1; }
    vol_bind "$p" "$mp" || { VOL_ERROR="Не удалось подключить $p к приложению"; return 1; }
  fi
  [ -f "$(vol_list)" ] && grep -v "^$id"$'\t' "$(vol_list)" > "$(vol_list).tmp"; touch "$(vol_list).tmp"
  printf '%s\t%s\t%s\t%s\n' "$id" "$p" "$fstype" "$source" >> "$(vol_list).tmp"
  mv "$(vol_list).tmp" "$(vol_list)"
  # сервер без общих монтирований (некоторые контейнерные хосты): работающий контейнер новый том не увидит, нужен пересоздание backend
  vol_propagates || dc up -d --no-build --no-deps --force-recreate backend >/dev/null 2>&1
  VOL_NOTE="$fstype $source (точка $target)"
  return 0
}

vol_detach() { # vol_detach ИД: данные на ресурсе не трогаются, снимается только подключение к приложению
  local id="$1" mp
  [[ "$id" =~ ^[a-z0-9-]{1,60}$ ]] || { VOL_ERROR="Некорректный идентификатор тома"; return 1; }
  mp="$(vol_dir)/$id"
  vol_bound "$mp" && { umount "$mp" 2>/dev/null || umount -l "$mp"; }
  [ -d "$mp" ] && rmdir "$mp" 2>/dev/null
  [ -f "$(vol_list)" ] && { grep -v "^$id"$'\t' "$(vol_list)" > "$(vol_list).tmp"; mv "$(vol_list).tmp" "$(vol_list)"; }
  return 0
}

vol_reconcile() { # запуск и раз в 15 с: том подключён тогда и только тогда, когда на месте та же файловая система
  [ -f "$(vol_list)" ] || return 0
  local id path fstype source mp cur_t cur_f cur_s same changed=0
  while IFS=$'\t' read -r id path fstype source; do
    [ -n "$id" ] || continue
    mp="$(vol_dir)/$id"
    read -r cur_t cur_f cur_s <<<"$(vol_mount_of "$path")"
    same=0; [ "$cur_f" = "$fstype" ] && [ "$cur_s" = "$source" ] && same=1
    if vol_bound "$mp"; then
      if [ "$same" = 0 ]; then
        warn "Том $path отключён на сервере ($fstype $source): снимаю подключение к приложению"
        umount -l "$mp" 2>/dev/null; changed=1
      fi
    elif [ "$same" = 1 ] && [ -d "$path" ]; then
      if [ -n "$(vol_path_error "$path")" ]; then warn "Том $path не подключён заново: путь больше не проходит проверку ($(vol_path_error "$path"))"
      else vol_mountpoint "$mp" && vol_bind "$path" "$mp" && { info "Том $path подключён заново"; changed=1; }; fi
    else
      vol_mountpoint "$mp"
    fi
  done < "$(vol_list)"
  [ "${1:-}" = quiet ] && [ "$changed" = 0 ] && return 0
  vol_publish
}
