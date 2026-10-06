# shellcheck shell=bash
# Подключается из run.sh: Docker build-подсистема, возобновление, свой nginx-site, порядок этапов установщика.
# Использует переменные/функции run.sh: ROOT, TMP, t.

# ---- подставной docker
BIN="$TMP/bin"; mkdir -p "$BIN"
cat > "$BIN/docker" <<'DOCK'
#!/usr/bin/env bash
case "$1" in
  version) echo 29.1.3 ;;
  compose) case "$2" in version) echo 2.40.3 ;; *) echo "compose $*" ;; esac ;;
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
t "dry-run установщика завершился" grep -q "Установка завершена" "$TMP/install-dry.out"
t "порядок: build < database < migrations < services < healthcheck < nginx" bash -c '[ "$1" -lt "$2" ] && [ "$2" -lt "$3" ] && [ "$3" -lt "$4" ] && [ "$4" -lt "$5" ] && [ "$5" -lt "$6" ]' _ "$(pos build)" "$(pos database)" "$(pos migrations)" "$(pos services)" "$(pos healthcheck)" "$(pos nginx)"
t "в плане нет prune/удаления /var/lib/docker|containerd/umount" bash -c '! grep -qiE "prune|rm -rf /var/lib|umount" "$1"' _ "$TMP/install-dry.out"
t "up идёт с --no-build (без скрытой сборки BuildKit)" grep -q "up -d --no-build" "$TMP/install-dry.out"
t "неизвестный этап отвергается" bash -c 'env PATH="$2:$PATH" bash "$1/scripts/install.sh" --profile shared-host --dry-run --skip-preflight --from nonexistent 2>&1 | grep -q "Неизвестный этап"' _ "$IW" "$BIN"
