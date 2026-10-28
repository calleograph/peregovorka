# shellcheck shell=bash
# Подключается из run.sh: видеозапись (LiveKit Egress) — scripts/lib/videolib.sh, scripts/video.sh, профиль compose, общая шина Redis.
VP="$TMP/video"; mkdir -p "$VP/data" "$VP/bin"
printf 'COMPOSE_PROJECT_NAME=pg-video-test\nDATA_ROOT=%s\n' "$VP/data" > "$VP/env"
printf 'VIDEO_ENABLED=yes\n' > "$VP/env.on"; cat "$VP/env" >> "$VP/env.on"

t "video: по умолчанию видеозапись выключена" bash -c '! { source "$1/scripts/lib/videolib.sh"; video_enabled; }' _ "$ROOT"
t "video: образ по умолчанию совпадает в compose, videolib и .env.example" bash -c 'a="$(grep -oE "livekit/egress:v[0-9.]+@sha256:[0-9a-f]{64}" "$1/deployment/compose.yml" | head -1)"; b="$(source "$1/scripts/lib/videolib.sh"; video_image)"; c="$(grep -oE "livekit/egress:v[0-9.]+@sha256:[0-9a-f]{64}" "$1/.env.example" | head -1)"; [ -n "$a" ] && [ "$a" = "$b" ] && [ "$a" = "$c" ]' _ "$ROOT"
t "video: VIDEO_IMAGE переопределяет образ" bash -c '[ "$(VIDEO_IMAGE=example/egress:1 bash -c "source \"$1/scripts/lib/videolib.sh\"; video_image")" = example/egress:1 ]' _ "$ROOT"
t "video: строка Redis для LiveKit — общая шина (db 1), как у SIP" bash -c 'source "$1/scripts/lib/videolib.sh"; REDIS_PASSWORD=pw; [[ "$(video_livekit_redis_line)" == "redis: {address: redis:6379, password: pw, db: 1}" ]]' _ "$ROOT"
t "video: включена, но Docker/образа нет → video_active ложь (контейнер не запускаем)" bash -c '! env -i PATH="/nonexistent" ENV_FILE="$2" /bin/bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; video_active"' _ "$ROOT" "$VP/env.on"
t "video: выключена — compose_args не содержит профиль video" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; compose_args 2>/dev/null; echo \"\${COMPOSE_ARGS[*]}\"")"; grep -q -- "compose" <<<"$out" && ! grep -q -- "--profile video" <<<"$out"' _ "$ROOT" "$VP/env"

COMPOSE="$ROOT/deployment/compose.yml"
vblock() { awk '/^  livekit-egress:/{f=1;next} f&&/^  [a-z]/{f=0} f' "$COMPOSE"; }
t "video: compose — livekit-egress запускается только по профилю video" bash -c 'grep -q "profiles: \[\"video\"\]" <<<"$1"' _ "$(vblock)"
t "video: compose — без публикуемых портов, host-сети и privileged" bash -c '! grep -Eq "^    ports:|network_mode: *host|privileged" <<<"$1"' _ "$(vblock)"
t "video: compose — секреты из .env, том записей смонтирован как /out" bash -c 'grep -q "api_secret: \${LIVEKIT_API_SECRET" <<<"$1" && grep -q "password: \${REDIS_PASSWORD" <<<"$1" && grep -q "recordings:/out" <<<"$1"' _ "$(vblock)"
t "video: compose — права ограничены SYS_ADMIN (песочница Chrome), остановка дописывает запись" bash -c 'grep -q "SYS_ADMIN" <<<"$1" && grep -q "stop_grace_period" <<<"$1"' _ "$(vblock)"

t "video.sh status при выключенной видеозаписи: объясняет, как включить" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/video.sh" status --env "$2" 2>&1)"; grep -q "выключена" <<<"$out" && grep -q "video.sh enable" <<<"$out"' _ "$ROOT" "$VP/env"
t "video.sh enable без root и без --yes отказывается" bash -c '[ "$(id -u)" -eq 0 ] && exit 0; ! env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/video.sh" enable --env "$2" >/dev/null 2>&1' _ "$ROOT" "$VP/env"
t "video.sh: неизвестная команда — ошибка" bash -c '! env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/video.sh" bogus --env "$2" >/dev/null 2>&1' _ "$ROOT" "$VP/env"
t "sip.sh disable не убирает общую шину Redis, если включена видеозапись" bash -c 'grep -q "VIDEO_ENABLED:-no.*!= \"yes\"" "$1/scripts/sip.sh"' _ "$ROOT"
