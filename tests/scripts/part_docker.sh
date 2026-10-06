# shellcheck shell=bash
# Подключается из run.sh: Docker build-подсистема, возобновление, свой nginx-site, порядок этапов установщика.
# Использует переменные/функции run.sh: ROOT, TMP, t.

# ---- подставной docker
BIN="$TMP/bin"; mkdir -p "$BIN"
cat > "$BIN/docker" <<'DOCK'
#!/usr/bin/env bash
case "$1" in
  version) echo 29.1.3 ;;
  compose)
    if [ "$2" = version ]; then echo 2.40.3; exit 0; fi
    case "$*" in
      *"alembic current"*)
        echo "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl."
        if [ "${FAKE_ALEMBIC_CUR:-0002}" = "0002" ]; then echo "0002 (head)"; else echo "${FAKE_ALEMBIC_CUR}"; fi ;;
      *"alembic heads"*) echo "0002 (head)" ;;
      *) echo "compose $*" ;;
    esac ;;
  buildx) echo "buildx: command not found" >&2; exit 1 ;;
  info) echo overlayfs ;;
  image) case "$2" in
           inspect) [ "${FAKE_IMAGE:-none}" = exist ] ;;
           rm) exit 0 ;;
         esac ;;
  build)
    if [ "${DOCKER_BUILDKIT:-}" = 0 ]; then
      if [ "${FAKE_LEGACY:-ok}" = fail ]; then echo "legacy builder broke" >&2; exit 1; fi
      exit 0
    fi
    if [ "${FAKE_BK:-ok}" = fail ]; then
      echo "#12 exporting to image" >&2
      echo "ERROR: failed to solve: mount callback failed on /var/lib/containerd/tmpmounts/containerd-mount123: failed to open writer: ref moby/1/abc locked: unavailable" >&2
      exit 1
    fi
    exit 0 ;;
  *) exit 0 ;;
esac
DOCK
chmod +x "$BIN/docker"
printf "#!/usr/bin/env bash
exit 0
" > "$BIN/nginx"; chmod +x "$BIN/nginx"
LIB="$ROOT/scripts/lib/common.sh"

# dk ENV=… [ENV=…] 'код' — выполняет код с подставным docker и библиотеками проекта
dk() {
  local code="${!#}"; local -a envs=("${@:1:$#-1}")
  env PATH="$BIN:$PATH" COMPOSE_PROJECT_NAME=pg-test DATA_ROOT="$TMP/dk-data" "${envs[@]}" bash -c 'source "$1"; shift; '"$code" _ "$LIB"
}

INFRA_LOG='#12 exporting to image
ERROR: failed to solve: mount callback failed on /var/lib/containerd/tmpmounts/containerd-mount4242: failed to open writer: ref moby/1/x locked for 10s: unavailable'
t "лог экспорта containerd → infra" bash -c 'source "$1"; [ "$(printf "%s" "$2" | classify_build_error)" = infra ]' _ "$ROOT/scripts/lib/dockerlib.sh" "$INFRA_LOG"
t "упавший RUN → project" bash -c 'source "$1"; [ "$(printf "%s" "$2" | classify_build_error)" = project ]' _ "$ROOT/scripts/lib/dockerlib.sh" 'ERROR: process "/bin/sh -c pip install x" did not complete successfully: exit code: 1'
t "нехватка места → infra" bash -c 'source "$1"; [ "$(printf "%s" "$2" | classify_build_error)" = infra ]' _ "$ROOT/scripts/lib/dockerlib.sh" 'write /var/lib/docker/x: no space left on device'

t "BuildKit работает → buildkit" dk FAKE_BK=ok 'select_build_mode >/dev/null && [ "$SELECTED_BUILD_MODE" = buildkit ]'
t "BuildKit сломан, legacy ок → fallback на legacy" dk FAKE_BK=fail 'select_build_mode >/dev/null 2>&1; [ "$SELECTED_BUILD_MODE" = legacy ]'
t "fallback явно виден в логе" dk FAKE_BK=fail 'select_build_mode 2>&1 | grep -q "FALLBACK"'
t "fallback называет причину: Docker, а не проект" dk FAKE_BK=fail 'select_build_mode 2>&1 | grep -q "ошибка Docker/containerd, а не проекта"'
t "оба builder'а сломаны → отказ" dk FAKE_BK=fail FAKE_LEGACY=fail '! select_build_mode >/dev/null 2>&1'
t "BUILD_MODE=buildkit — без fallback" dk FAKE_BK=fail BUILD_MODE=buildkit '! select_build_mode >/dev/null 2>&1 && [ -z "$SELECTED_BUILD_MODE" ]'
t "BUILD_MODE=legacy" dk FAKE_BK=fail BUILD_MODE=legacy 'select_build_mode >/dev/null && [ "$SELECTED_BUILD_MODE" = legacy ]'
t "docker_diag: нет buildx — предупреждение, не отказ" dk FAKE_BK=ok 'w=0; o(){ :; }; wn(){ w=$((w+1)); }; f(){ exit 9; }; docker_diag o wn f; [ "$w" -ge 1 ]'

# ---- возобновление: образ не пересобирается, пока исходники не менялись
FP="$TMP/fp-repo"; mkdir -p "$FP/backend" "$FP/asr-service" "$FP/frontend" "$FP/deployment/livekit" "$TMP/dk-data/state"
for d in backend asr-service frontend deployment/livekit; do echo "v1" > "$FP/$d/file.txt"; done
t "нет образа → нужна сборка" dk FAKE_IMAGE=none "REPO_ROOT='$FP'; IMAGE_TAG=dev; build_needed backend"
t "образ есть, отпечатка нет → сборка" dk FAKE_IMAGE=exist "REPO_ROOT='$FP'; IMAGE_TAG=dev; build_needed backend"
t "образ есть, исходники те же → пропуск" dk FAKE_IMAGE=exist "REPO_ROOT='$FP'; IMAGE_TAG=dev; fp_set backend \"\$(src_fingerprint backend)\"; ! build_needed backend"
t "исходники изменились → сборка" dk FAKE_IMAGE=exist "REPO_ROOT='$FP'; IMAGE_TAG=dev; fp_set backend \"\$(src_fingerprint backend)\"; echo v2 > '$FP/backend/file.txt'; build_needed backend"
t "--force-build → сборка" dk FAKE_IMAGE=exist FORCE_BUILD=1 "REPO_ROOT='$FP'; IMAGE_TAG=dev; fp_set backend \"\$(src_fingerprint backend)\"; build_needed backend"
t "adopt_images принимает вручную собранные образы" dk FAKE_IMAGE=exist "REPO_ROOT='$FP'; IMAGE_TAG=dev; adopt_images >/dev/null; ! build_needed web && ! build_needed livekit"

# ---- свой nginx-site распознаётся без прав на ss -p
NG="$TMP/ng"; mkdir -p "$NG/av" "$NG/en"
ngenv() { env COMPOSE_PROJECT_NAME=pg-test NGINX_SITE_NAME=pg-test NGINX_LISTEN_PORT=18400 NGINX_SERVER_NAME=meet.x WEB_PORT=18480 NGINX_SITES_AVAILABLE="$NG/av" NGINX_SITES_ENABLED="$NG/en" "$@"; }
own() { ngenv bash -c 'source "$1"; REPO_ROOT="$2"; shift 2; '"$1" _ "$ROOT/scripts/lib/envlib.sh" "$ROOT"; }
t "site отсутствует → не наш" own '! own_nginx_site_ok'
ngenv bash -c 'source "$1"; REPO_ROOT="$2"; render_nginx_site > "$3/av/pg-test"; ln -s "$3/av/pg-test" "$3/en/pg-test"' _ "$ROOT/scripts/lib/envlib.sh" "$ROOT" "$NG"
t "marker+listen+symlink → наш (повторный запуск)" own 'own_nginx_site_ok'
t "и строго совпадает с шаблоном" own 'own_nginx_site_ok strict'
t "другой порт → не наш" ngenv NGINX_LISTEN_PORT=18401 bash -c 'source "$1"; ! own_nginx_site_ok' _ "$ROOT/scripts/lib/envlib.sh"
t "другой проект (marker) → не наш" ngenv COMPOSE_PROJECT_NAME=other bash -c 'source "$1"; ! own_nginx_site_ok' _ "$ROOT/scripts/lib/envlib.sh"
echo "# чужая правка" >> "$NG/av/pg-test"; [ -L "$NG/en/pg-test" ] || cp "$NG/av/pg-test" "$NG/en/pg-test"   # на Windows ln -s делает копию
t "правка файла: обычная проверка ок, strict — нет" own 'own_nginx_site_ok && ! own_nginx_site_ok strict'
rm -f "$NG/en/pg-test"
t "нет symlink в sites-enabled → не наш" own '! own_nginx_site_ok'

# ---- порядок этапов установщика (dry-run, подставной docker): nginx — последним
IW="$TMP/inst"; mkdir -p "$IW"; cp -r "$ROOT/scripts" "$ROOT/deployment" "$ROOT/.env.example" "$IW/"
mkdir -p "$IW/backend" "$IW/asr-service" "$IW/frontend"
cat > "$IW/.env" <<ENVF
COMPOSE_PROJECT_NAME=pg-test
INSTALL_PROFILE=shared-host
DATA_ROOT=$TMP/inst-data
WEB_PORT=18480
LIVEKIT_HTTP_PORT=17880
LIVEKIT_TCP_PORT=17881
LIVEKIT_UDP_PORT=17882
NGINX_MANAGE=yes
NGINX_SITE_NAME=pg-test
NGINX_SERVER_NAME=meet.x
NGINX_LISTEN_PORT=18400
NGINX_SITES_AVAILABLE=$NG/av
NGINX_SITES_ENABLED=$NG/en
ENVF
env PATH="$BIN:$PATH" FAKE_IMAGE=none bash "$IW/scripts/install.sh" --profile shared-host --dry-run --skip-preflight 2>&1 | sed 's/\x1b\[[0-9;]*m//g' > "$TMP/install-dry.out"
pos() { grep -n "== stage: $1 ==" "$TMP/install-dry.out" | head -1 | cut -d: -f1; }
t "dry-run установщика завершился" grep -q "Dry-run завершён" "$TMP/install-dry.out"
t "порядок: build < database < migrations < services < healthcheck < nginx" bash -c '[ "$1" -lt "$2" ] && [ "$2" -lt "$3" ] && [ "$3" -lt "$4" ] && [ "$4" -lt "$5" ] && [ "$5" -lt "$6" ]' _ "$(pos build)" "$(pos database)" "$(pos migrations)" "$(pos services)" "$(pos healthcheck)" "$(pos nginx)"
t "в плане нет prune/удаления /var/lib/docker|containerd/umount" bash -c '! grep -qiE "prune|rm -rf /var/lib|umount" "$1"' _ "$TMP/install-dry.out"
t "up идёт с --no-build (без скрытой сборки BuildKit)" grep -q "up -d --no-build" "$TMP/install-dry.out"
t "неизвестный этап отвергается" bash -c 'env PATH="$2:$PATH" bash "$1/scripts/install.sh" --profile shared-host --dry-run --skip-preflight --from nonexistent 2>&1 | grep -q "Неизвестный этап"' _ "$IW" "$BIN"


# ---- параметры ядра: только предупреждения, с командами администратору
PS="$TMP/procsys"; mkdir -p "$PS/vm" "$PS/net/core"
kt() { env PROC_SYS_ROOT="$PS" bash -c 'source "$1"; o=""; w=""; ko(){ o+="$*"$'"'"'\n'"'"'; }; kw(){ w+="$*"$'"'"'\n'"'"'; }; kernel_tuning_check ko kw; eval "$2"' _ "$ROOT/scripts/lib/envlib.sh" "$1"; }
echo 0 > "$PS/vm/overcommit_memory"; echo 425984 > "$PS/net/core/rmem_max"; echo 5000000 > "$PS/net/core/wmem_max"; echo 5000 > "$PS/net/core/netdev_max_backlog"
t "overcommit=0 → WARN с командой sysctl, не отказ" kt 'grep -q "sysctl -w vm.overcommit_memory=1" <<<"$w" && grep -q "Cannot allocate memory" <<<"$w"'
t "rmem_max=425984 → WARN с текущим и рекомендуемым" kt 'grep -q "425984" <<<"$w" && grep -q "5000000" <<<"$w" && grep -q "net.core.rmem_max=5000000" <<<"$w"'
t "предупреждение объясняет влияние на медиатрафик" kt 'grep -qi "потер" <<<"$w"'
t "установщик sysctl не меняет (в коде нет sysctl -w вне текста подсказок)" bash -c '! grep -rnE "^[[:space:]]*(sudo[[:space:]]+)?sysctl[[:space:]]+-w" "$1/scripts" --include=*.sh | grep -v "echo\|warn\|\"\$warn\"\|#" ' _ "$ROOT"
echo 1 > "$PS/vm/overcommit_memory"; echo 5000000 > "$PS/net/core/rmem_max"
t "оба параметра в норме → ok без WARN" kt '[ -z "$w" ] && grep -q "overcommit_memory = 1" <<<"$o" && grep -q "rmem_max = 5000000" <<<"$o"'
rm -f "$PS/vm/overcommit_memory"
t "параметр недоступен → WARN, не падение" kt 'grep -q "недоступно" <<<"$w"'

# ---- сетевые сбои registry/CDN: повтор, но не для ошибок проекта
t "тайм-аут pull → network" bash -c 'source "$1"; [ "$(printf "%s" "failed to copy: read tcp 10.0.0.1:443: read: connection timed out" | classify_build_error)" = network ]' _ "$ROOT/scripts/lib/dockerlib.sh"
t "pip Read timed out при сборке → network" bash -c 'source "$1"; [ "$(printf "%s" "pip._vendor.urllib3.exceptions.ReadTimeoutError: Read timed out. process did not complete successfully" | classify_build_error)" = network ]' _ "$ROOT/scripts/lib/dockerlib.sh"
RC="$TMP/retry"; mkdir -p "$RC"
cat > "$RC/flaky.sh" <<'FL'
#!/usr/bin/env bash
n=$(cat "$COUNT" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$COUNT"
if [ "$n" -le "$FAIL_UNTIL" ]; then echo "$MSG" >&2; exit 1; fi
echo done
FL
chmod +x "$RC/flaky.sh"
rcase() { # rcase FAIL_UNTIL MSG → печатает число попыток и код
  rm -f "$RC/count"; local out rc
  out="$(env COUNT="$RC/count" FAIL_UNTIL="$1" MSG="$2" PULL_RETRY_PAUSE=0 bash -c 'source "$1"; retry_cmd pull 3 0 "$2"; echo "rc=$?"' _ "$LIB" "$RC/flaky.sh" 2>&1)"
  echo "$out" | grep "^rc=" ; echo "attempts=$(cat "$RC/count")"
}
NETMSG="failed to copy: read tcp 10.0.0.1:443: read: connection timed out"; export RC LIB NETMSG; export -f rcase
t "сеть упала 2 раза, 3-я попытка успешна" bash -c '[ "$(rcase 2 "$NETMSG" | tr "\n" " ")" = "rc=0 attempts=3 " ]' _
t "сеть недоступна все 3 попытки → отказ (код ≠ 0, 3 попытки)" bash -c 'r="$(rcase 9 "$NETMSG" | tr "\n" " ")"; [ "$r" = "rc=1 attempts=3 " ]'
t "ошибка проекта не повторяется (1 попытка)" bash -c 'r="$(rcase 9 "COPY failed: file not found" | tr "\n" " ")"; [ "$r" = "rc=1 attempts=1 " ]'
t "в логе повтора виден номер попытки" bash -c 'env COUNT="$1/count" FAIL_UNTIL=1 MSG="TLS handshake timeout" bash -c "source \"$2\"; rm -f \"$1/count\"; retry_cmd pull 3 0 \"$1/flaky.sh\"" 2>&1 | grep -q "попытка 2/3"' _ "$RC" "$LIB"

# ---- Alembic: фактическая ревизия, а не только код возврата
t "alembic_rev: «0002 (head)»" bash -c 'source "$1"; [ "$(printf "INFO log\n0002 (head)\n" | alembic_rev)" = 0002 ]' _ "$ROOT/scripts/lib/verifylib.sh"
t "alembic_rev: пустой вывод → пусто" bash -c 'source "$1"; [ -z "$(printf "" | alembic_rev)" ]' _ "$ROOT/scripts/lib/verifylib.sh"
t "ревизия БД = head → ok" dk FAKE_ALEMBIC_CUR=0002 'alembic_verify exec && [ "$ALEMBIC_CUR" = 0002 ]'
t "ревизия БД отстаёт от head → FAIL" dk FAKE_ALEMBIC_CUR=0001 '! alembic_verify exec && [ "$ALEMBIC_CUR" = 0001 ] && [ "$ALEMBIC_HEAD" = 0002 ]'

# ---- порядок этапов: pull→build, verify после nginx, report последним; ctl.sh
posd() { grep -n "== stage: $1 ==" "$TMP/install-dry.out" | head -1 | cut -d: -f1; }
t "pull раньше build" bash -c '[ "$1" -lt "$2" ]' _ "$(posd pull)" "$(posd build)"
t "database (postgres+redis) раньше migrations раньше services" bash -c '[ "$1" -lt "$2" ] && [ "$2" -lt "$3" ]' _ "$(posd database)" "$(posd migrations)" "$(posd services)"
t "verify после nginx, report — последним" bash -c '[ "$1" -lt "$2" ] && [ "$2" -lt "$3" ]' _ "$(posd nginx)" "$(posd verify)" "$(posd report)"
t "миграции: в плане есть проверка current==head" grep -q "alembic current == alembic heads" "$TMP/install-dry.out"
t "ctl.sh отвергает неизвестный сервис" bash -c '! bash "$1/scripts/ctl.sh" restart nonexistent --env "$2" >/dev/null 2>&1' _ "$IW" "$IW/.env"
