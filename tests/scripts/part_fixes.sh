# shellcheck shell=bash
# Подключается из run.sh: правки по итогам обновления на реальном сервере — пути резервных копий, проверка WebSocket LiveKit, TLS, ручная сборка.
# Используются: ROOT, TMP, t
VL="$ROOT/scripts/lib/verifylib.sh"; EL="$ROOT/scripts/lib/envlib.sh"; export VL EL

# ---------------------------------------------------------------------------------- BACKUP_DIR / BACKUP_COPY_DIR
vp() { bash -c 'source "$1"; REPO_ROOT=/opt/peregovorka; DATA_ROOT="${2:-/srv/d}"; BACKUP_DIR="$3"; BACKUP_COPY_DIR="${4:-}"; validate_env_paths' _ "$EL" "${2:-/srv/d}" "$1" "${3:-}"; }
export -f vp
t "BACKUP_DIR: обычный локальный путь принимается" vp /srv/d/backups
t "BACKUP_DIR: UNC \\\\Srv11\\tmp/backups отвергается" bash -c '! vp "$1" >/dev/null' _ '\\Srv11\tmp/backups'
t "BACKUP_DIR: сообщение объясняет, как делать копии на сетевой ресурс (BACKUP_COPY_DIR)" bash -c 'vp "$1" | grep -q BACKUP_COPY_DIR' _ '\\Srv11\tmp/backups'
t "BACKUP_DIR: smb:// и относительный путь отвергаются" bash -c '! vp smb://srv/share >/dev/null && ! vp backups >/dev/null && ! vp ./backups >/dev/null'
t "BACKUP_DIR: путь внутри репозитория отвергается" bash -c '! vp /opt/peregovorka/backups >/dev/null'
t "BACKUP_COPY_DIR: локальная точка монтирования принимается, UNC отвергается" bash -c 'vp /srv/d/backups /srv/d /mnt/share/pg >/dev/null && ! vp /srv/d/backups /srv/d "\\\\srv\\share" >/dev/null'

mkbk() { # окружение для backup.sh с подставным docker; $1 — значение BACKUP_DIR
  rm -rf "$TMP/bk"; mkdir -p "$TMP/bk/bin" "$TMP/bk/data" "$TMP/bk/cwd"
  cat > "$TMP/bk/bin/docker" <<'FAKE'
#!/usr/bin/env bash
case "$*" in
  *"ps -q postgres"*) echo abc123 ;;
  *pg_dump*) echo "PGDMP-подставной-дамп" ;;
esac
exit 0
FAKE
  chmod +x "$TMP/bk/bin/docker"
  printf 'COMPOSE_PROJECT_NAME=pg-bk\nDATA_ROOT=%s\nPOSTGRES_DB=app\nPOSTGRES_USER=app\nBACKUP_DIR=%s\n%s' "$TMP/bk/data" "$1" "${2:-}" > "$TMP/bk/env"
}
runbk() { ( cd "$TMP/bk/cwd" && env PATH="$TMP/bk/bin:$PATH" bash "$ROOT/scripts/backup.sh" --env "$TMP/bk/env" ) 2>&1; }

mkbk '\\Srv11\tmp/backups'
OUTB="$(runbk)"; RCB=$?
t "backup.sh с UNC-путём в BACKUP_DIR отказывает и НИЧЕГО не создаёт (ни в текущем каталоге, ни в репозитории)" bash -c '[ "$1" -ne 0 ] && printf "%s" "$2" | grep -q "BACKUP_DIR" && [ -z "$(ls -A "$3/bk/cwd")" ] && [ ! -e "$4/scripts/Srv11" ] && [ ! -d "$4/\\\\Srv11" ]' _ "$RCB" "$OUTB" "$TMP" "$ROOT"
mkbk "$TMP/bk/data/backups"
OUTB="$(runbk)"; RCB=$?
t "backup.sh с нормальным путём: дамп создан" bash -c '[ "$1" -eq 0 ] && ls "$2"/data/backups/pg-bk-db-*.dump >/dev/null' _ "$RCB" "$TMP/bk"
mkbk "$TMP/bk/data/backups" "BACKUP_COPY_DIR=$TMP/bk/share
"
OUTB="$(runbk)"; RCB=$?
t "BACKUP_COPY_DIR: после локального дампа копия лежит и во втором каталоге" bash -c 'a=$(ls "$1"/data/backups/pg-bk-db-*.dump | head -1); b=$(ls "$1"/share/pg-bk-db-*.dump | head -1); [ -n "$a" ] && cmp -s "$a" "$b"' _ "$TMP/bk"
mkbk "$TMP/bk/data/backups" "BACKUP_COPY_DIR=$TMP/bk/notadir/sub
"
: > "$TMP/bk/notadir"   # обычный файл на месте каталога: создать подкаталог невозможно
OUTB="$(runbk)"; RCB=$?
t "BACKUP_COPY_DIR недоступен: предупреждение, а локальная копия остаётся (код 0)" bash -c '[ "$1" -eq 0 ] && ls "$2"/data/backups/pg-bk-db-*.dump >/dev/null && printf "%s" "$3" | grep -q "удалось скопировать"' _ "$RCB" "$TMP/bk" "$OUTB"

# ---------------------------------------------------------------------------------- WebSocket LiveKit: маршрут и Upgrade
t "ws_route_verdict: 400 «join_request is required» = маршрут /rtc/v1 существует (OK)" bash -c 'set -u; source "$1"; WS_BODY="join_request is required"; ws_route_verdict 400 direct; [ "$WS_STATUS" = OK ] && [[ "$WS_NOTE" == *существует* ]]' _ "$VL"
t "ws_route_verdict: 400 без join_request — FAIL, 404 — FAIL с указанием на устаревший LiveKit, 101 — OK" bash -c 'set -u; source "$1"; WS_BODY="что-то другое"; ws_route_verdict 400; a=$WS_STATUS; WS_BODY=""; ws_route_verdict 404; b=$WS_STATUS; n=$WS_NOTE; ws_route_verdict 101; [ "$a$b$WS_STATUS" = FAILFAILOK ] && [[ "$n" == *rtc/v1* ]]' _ "$VL"
t "ws_verdict 400 напрямую: это ответ самого LiveKit, прокси не обвиняется" bash -c 'set -u; source "$1"; WS_BODY="boom"; ws_verdict 400 direct; [ "$WS_STATUS" = FAIL ] && [[ "$WS_NOTE" == *"самого LiveKit"* ]] && [[ "$WS_NOTE" != *"проблема в прокси"* ]] && [[ "$WS_NOTE" == *boom* ]]' _ "$VL"
t "ws_verdict через прокси: напрямую 101 → виноват прокси" bash -c 'set -u; source "$1"; DIRECT_WS_CODE=101; WS_BODY=""; ws_verdict 400 proxy; [[ "$WS_NOTE" == *"проблема в прокси"* ]] && [[ "$WS_NOTE" == *Upgrade* ]]' _ "$VL"
t "ws_verdict через прокси: напрямую тоже 400 → «прокси ни при чём»" bash -c 'set -u; source "$1"; DIRECT_WS_CODE=400; WS_BODY=""; ws_verdict 400 proxy; [[ "$WS_NOTE" == *"прокси ни при чём"* ]] && [[ "$WS_NOTE" != *"проблема в прокси"* ]]' _ "$VL"
t "ws_verdict через прокси без результата прямой проверки — не делает вывод о вине прокси" bash -c 'set -u; unset DIRECT_WS_CODE; source "$1"; WS_BODY=""; ws_verdict 400 proxy; [[ "$WS_NOTE" == *"нельзя сказать"* ]]' _ "$VL"

mkdir -p "$TMP/fakecurl"
cat > "$TMP/fakecurl/curl" <<'FAKE'
#!/usr/bin/env bash
# подставной curl: ответ задаётся переменными FAKE_CODE / FAKE_BODY (WebSocket-проба) либо FAKE_TLS_RC / FAKE_TLS_VR (проверка сертификата)
out=""; wfmt=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; -w) wfmt="$2"; shift 2 ;; *) shift ;; esac; done
if [ "$wfmt" = '%{ssl_verify_result}' ]; then printf '%s' "${FAKE_TLS_VR:-0}"; exit "${FAKE_TLS_RC:-0}"; fi
[ -n "$out" ] && [ "$out" != /dev/null ] && printf '%s' "${FAKE_BODY:-}" > "$out"
printf '%s' "${FAKE_CODE:-000}"
FAKE
printf '#!/usr/bin/env bash\nexit 1\n' > "$TMP/fakecurl/openssl"; chmod +x "$TMP/fakecurl/curl" "$TMP/fakecurl/openssl"
t "ws_upgrade_probe: код и тело ответа попадают в WS_CODE/WS_BODY (токен в аргументах не передаётся)" bash -c 'source "$1"; FAKE_CODE=400 FAKE_BODY="join_request is required" PATH="$2:$PATH" ws_upgrade_probe "http://x/rtc/v1?access_token=SECRET"; [ "$WS_CODE" = 400 ] && [ "$WS_BODY" = "join_request is required" ]' _ "$VL" "$TMP/fakecurl"
t "ws_upgrade_code остаётся совместимой: печатает только код" bash -c 'source "$1"; [ "$(FAKE_CODE=101 PATH="$2:$PATH" ws_upgrade_code http://x/rtc)" = 101 ]' _ "$VL" "$TMP/fakecurl"

# ---------------------------------------------------------------------------------- TLS: цепочка не доверена сервером — предупреждение
tlsr() { bash -c 'source "$1"; FAKE_TLS_RC="$3" FAKE_TLS_VR="$4" PATH="$2:$PATH" tls_check https://meet.example.org; printf "%s|%s" "$TLS_STATUS" "$TLS_NOTE"' _ "$VL" "$TMP/fakecurl" "$1" "$2"; }
export -f tlsr
t "TLS: сервер не доверяет цепочке (проверка 20/21) → WARNING, а не FAIL" bash -c 'r="$(tlsr 60 20)"; [ "${r%%|*}" = WARNING ] && [[ "$r" == *"в браузере"* ]]'
t "TLS: неполная цепочка (проверка 2) → WARNING" bash -c 'r="$(tlsr 60 2)"; [ "${r%%|*}" = WARNING ]'
t "TLS: истёк срок (проверка 10) → FAIL" bash -c 'r="$(tlsr 60 10)"; [ "${r%%|*}" = FAIL ] && [[ "$r" == *"истёк срок"* ]]'
t "TLS: имя хоста не совпадает (проверка 62) → FAIL" bash -c 'r="$(tlsr 60 62)"; [ "${r%%|*}" = FAIL ]'
t "TLS: всё в порядке (код 0) → OK" bash -c 'r="$(tlsr 0 0)"; [ "${r%%|*}" = OK ]'
t "TLS: нет связи с этого сервера → WARNING" bash -c 'r="$(tlsr 7 0)"; [ "${r%%|*}" = WARNING ]'

# ---------------------------------------------------------------------------------- ручная пересборка
t "scripts/rebuild.sh существует, исполняем и использует тот же путь сборки, что update.sh (build_images: BuildKit → legacy, APP_GIT_COMMIT)" bash -c '[ -x "$1/scripts/rebuild.sh" ] && grep -q "build_images" "$1/scripts/rebuild.sh" && grep -q "host_version_info" "$1/scripts/rebuild.sh"' _ "$ROOT"
t "документация: обычный «docker compose build» не рекомендуется, указан rebuild.sh" bash -c 'grep -q "rebuild.sh" "$1/docs/INSTALL_AND_UPDATE.md" && grep -qi "docker compose build" "$1/docs/INSTALL_AND_UPDATE.md" && grep -q "rebuild.sh" "$1/DEPLOYMENT.md"' _ "$ROOT"
t "веб-обновление использует update.sh (а не собственную сборку)" bash -c 'grep -q "scripts/update.sh" "$1/scripts/updater.sh" && ! grep -nE "docker compose.*build|dc build|build_images" "$1/scripts/updater.sh" | grep -v "^[0-9]*:[[:space:]]*#"' _ "$ROOT"
