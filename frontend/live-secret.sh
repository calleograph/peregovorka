#!/bin/sh
# live-secret.sh — секрет подписи HLS для nginx этого контейнера (/docker-entrypoint.d: выполняется перед запуском nginx).
# Backend пишет /etc/nginx/live/secret.conf (том только на чтение; файл 0640, группа 10001 — контейнер входит в неё через group_add).
# nginx подключает КОПИЮ из /tmp/hls-live: если исходный файл ещё не создан или недоступен, nginx всё равно запускается (раздача HLS закрыта, 503),
# а не падает весь веб-сервер. Фоновая проверка раз в LIVE_SECRET_POLL секунд: файл изменился → копия → nginx -t → перечитывание (nginx -s reload);
# ошибка проверки — прежняя копия возвращается, nginx продолжает с прежней конфигурацией. Итог — /tmp/hls-live-state («ok|missing|unreadable|test_failed|
# reload_failed <поколение>»), его отдаёт location /__hls/state, а backend сверяет поколение (sha256 файла, 12 знаков) со своим: это и есть
# «актуальная конфигурация применена», а не время запуска контейнера.
SRC=/etc/nginx/live/secret.conf
DIR=/tmp/hls-live
DST=$DIR/secret.conf
STATE=/tmp/hls-live-state
mkdir -p "$DIR" && chmod 700 "$DIR"

gen() { sha256sum "$1" 2>/dev/null | cut -c1-12; }
say() { printf '%s %s\n' "$1" "${2:-}" > "$STATE.tmp" && mv -f "$STATE.tmp" "$STATE"; }
note() { [ "$(cat "$DIR/last" 2>/dev/null)" = "$1" ] && return 0; printf '%s' "$1" > "$DIR/last"; echo "live-secret: $1" >&2; }     # в журнал — только смена состояния

# $1 = start (nginx ещё не запущен: только копия) | reload
sync_secret() {
  if [ ! -e "$SRC" ]; then [ -e "$DST" ] || say missing; return 0; fi
  if ! cat "$SRC" > "$DIR/new" 2>/dev/null; then say unreadable "$(gen "$DST")"; note "$SRC недоступен для чтения (нужна группа 10001)"; return 0; fi
  if [ -e "$DST" ] && cmp -s "$DIR/new" "$DST"; then rm -f "$DIR/new"; say ok "$(gen "$DST")"; return 0; fi
  if [ "$(gen "$DIR/new")" = "$(cat "$DIR/bad" 2>/dev/null)" ]; then rm -f "$DIR/new"; return 0; fi     # этот файл nginx -t уже отклонил: ждём исправленный
  [ -e "$DST" ] && cp -p "$DST" "$DIR/prev"
  chmod 600 "$DIR/new" && mv -f "$DIR/new" "$DST"
  g="$(gen "$DST")"
  if [ "$1" = start ]; then say ok "$g"; return 0; fi
  if ! nginx -t -q 2>"$DIR/test.err"; then
    note "nginx -t отклонил конфигурацию с новым секретом (работает прежняя): $(head -c 300 "$DIR/test.err")"
    if [ -e "$DIR/prev" ]; then mv -f "$DIR/prev" "$DST"; else rm -f "$DST"; fi
    printf '%s' "$g" > "$DIR/bad"; say test_failed "$g"; return 0
  fi
  rm -f "$DIR/bad"
  if nginx -s reload 2>"$DIR/reload.err"; then say ok "$g"; note "секрет подписи HLS применён (поколение $g)"
  else       # nginx остался с прежней конфигурацией: копию возвращаем, чтобы следующий проход повторил перечитывание
    if [ -e "$DIR/prev" ]; then mv -f "$DIR/prev" "$DST"; else rm -f "$DST"; fi
    say reload_failed "$g"; note "nginx -s reload: $(head -c 300 "$DIR/reload.err")"; fi
  rm -f "$DIR/prev"
}

sync_secret start
( while sleep "${LIVE_SECRET_POLL:-5}"; do sync_secret reload; done ) &
