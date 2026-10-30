# shellcheck shell=bash
# Подключается из run.sh: загрузка образов (scripts/lib/imglib.sh) — повторы, причины отказа, зеркала по дайджесту, согласование имени образа с compose, возврат на основной источник.
# Docker подменён скриптом: поведение каждого источника задают файлы в $IM/scn (ok или текст ошибки).
IM="$TMP/img"; mkdir -p "$IM/bin" "$IM/scn" "$IM/have" "$IM/data/state"
cat > "$IM/bin/docker" <<'FAKE'
#!/usr/bin/env bash
IM="${IM:?}"
key() { printf '%s' "$1" | tr '/:@' '___'; }
src() { case "$1" in mirror.gcr.io/*) echo mirror ;; reg.example.org/*) echo internal ;; *) echo primary ;; esac; }
case "$1" in
  version) echo "${FAKE_ARCH:-amd64}" ;;
  image) # image inspect [-f F] REF
    shift 2; [ "$1" = -f ] && shift 2; ref="$1"
    [ -e "$IM/have/$(key "$ref")" ] || exit 1
    echo "${FAKE_IMG_ARCH:-amd64}|[\"${ref%%@*}@${ref##*@}\"]|" ;;
  pull) ref="$2"; echo "pull $ref" >> "$IM/calls"; s="$(src "$ref")"; r="$(cat "$IM/scn/pull-$s" 2>/dev/null || echo ok)"
    if [ "$r" = ok ]; then : > "$IM/have/$(key "$ref")"; echo "Status: Downloaded newer image for $ref"; else printf '%s\n' "$r" >&2; exit 1; fi ;;
  manifest) ref="$3"; echo "manifest $ref" >> "$IM/calls"; s="$(src "$ref")"; r="$(cat "$IM/scn/manifest-$s" 2>/dev/null || echo ok)"
    if [ "$r" = ok ]; then echo '{}'; else printf '%s\n' "$r" >&2; exit 1; fi ;;
  *) exit 0 ;;
esac
FAKE
chmod +x "$IM/bin/docker"
DG=sha256:ac244a40268ce1dd1510fbff6eb529609ec68aceff1602343f79be8ca08c355b
PIN="livekit/egress:v1.15.0@$DG"
MIR="mirror.gcr.io/livekit/egress@$DG"
TIMEOUT_TXT='failed to copy: httpReadSeeker: failed open: Get "https://production.cloudfront.docker.com/registry-v2/docker/registry/v2/blobs/sha256/ab/abcdef/data?Expires=1&Signature=SECRETSIG&Key-Pair-Id=KID": net/http: timeout awaiting response headers'

printf '%s\n' "$TIMEOUT_TXT" > "$IM/tt.txt"
# imgrun СЦЕНАРИЙ-СКРИПТ: чистая песочница (.env, состояние, «образы на сервере»), затем код сценария в подпроцессе
imgrun() {
  rm -rf "$IM/have" "$IM/scn" "$IM/data/state" "$IM/calls"; mkdir -p "$IM/have" "$IM/scn" "$IM/data/state"
  printf 'COMPOSE_PROJECT_NAME=pg-img-test\nDATA_ROOT=%s\nVIDEO_ENABLED=yes\n' "$IM/data" > "$IM/env"
  ( export IM; export PATH="$IM/bin:$PATH"; export ENV_FILE="$IM/env" IMAGE_PULL_PAUSE=0 IMAGE_PULL_ATTEMPTS=3
    # shellcheck disable=SC1091
    source "$ROOT/scripts/lib/common.sh"; load_env "$ENV_FILE"; DATA_ROOT="$IM/data"; DRY_RUN=0
    eval "$1" )
}
calls() { grep -c "^$1" "$IM/calls" 2>/dev/null || true; }

t "img: образ скачивается основным источником; зеркало не трогается, .env не меняется" imgrun '
  img_ensure egress VIDEO_IMAGE "'"$PIN"'" || exit 1
  [ "$(grep -c "^pull mirror" "$IM/calls")" = 0 ] && ! grep -q "^VIDEO_IMAGE=" "$ENV_FILE" && [ ! -s "$IM/data/state/image-error-egress.json" ]'
t "img: таймаут слоя CDN → три попытки основного источника, затем зеркало по тому же дайджесту; .env получает адрес зеркала" imgrun '
  cp "'"$IM"'/tt.txt" "$IM/scn/pull-primary"
  img_ensure egress VIDEO_IMAGE "'"$PIN"'" || exit 1
  [ "$(grep -c "^pull livekit/egress" "$IM/calls")" = 3 ] && grep -q "^manifest '"$MIR"'" "$IM/calls" && grep -q "^pull '"$MIR"'" "$IM/calls" \
    && grep -q "^VIDEO_IMAGE=.*mirror.gcr.io/livekit/egress@'"$DG"'" "$ENV_FILE" && [ "$VIDEO_IMAGE" = "'"$MIR"'" ]'
t "img: после зеркала video_image() и локальная проверка видят образ под именем зеркала (рассогласования имён нет)" imgrun '
  cp "'"$IM"'/tt.txt" "$IM/scn/pull-primary"
  img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null || exit 1
  [ "$(video_image)" = "'"$MIR"'" ] && docker image inspect "$(video_image)" && [ "$(_comp_img "$(video_image)" egress VIDEO_IMAGE)" = true ] && [ "$(_comp_src egress VIDEO_IMAGE)" = "\"mirror\"" ]'
t "img: основной недоступен, у зеркала нет манифеста → отказ; причина и пробованные источники записаны, второй раз зеркало не «мерцает»" imgrun '
  cp "'"$IM"'/tt.txt" "$IM/scn/pull-primary"; echo "manifest unknown" > "$IM/scn/manifest-mirror"
  ! img_ensure egress VIDEO_IMAGE "'"$PIN"'" || exit 1
  j="$(img_error_json egress)"; grep -q "\"code\":\"timeout\"" <<<"$j" && grep -q "mirror.gcr.io" <<<"$j" && [ "$(grep -c "^pull mirror" "$IM/calls")" = 0 ] && ! grep -q "^VIDEO_IMAGE=." "$ENV_FILE"'
t "img: в сохранённой ошибке нет подписи URL и токенов" imgrun '
  { cat "'"$IM"'/tt.txt"; printf "\nAuthorization: Bearer abcdefghijklmnop1234\nhttps://user:pa55word@reg.example.org/v2/\n"; } > "$IM/scn/pull-primary"; echo "manifest unknown" > "$IM/scn/manifest-mirror"
  ! img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null 2>&1; j="$(img_error_json egress)"
  ! grep -q "SECRETSIG\|abcdefghijklmnop1234\|pa55word" <<<"$j" && grep -q "\*\*\*" <<<"$j"'
t "img: JSON ошибки корректен (разбирается Python) и содержит этап" imgrun '
  { cat "'"$IM"'/tt.txt"; printf "%s\n" "line \"quoted\" and back\\slash"; } > "$IM/scn/pull-primary"; echo "manifest unknown" > "$IM/scn/manifest-mirror"
  ! img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null 2>&1; img_error_json egress | python3 -c "import json,sys; d=json.load(sys.stdin); assert d[\"stage\"] and d[\"code\"]==\"timeout\" and \"quoted\" in d[\"details\"]"'
t "img: нехватка диска — зеркало не пробуется (причина не в источнике), сообщение про диск" imgrun '
  echo "write /var/lib/docker/tmp/x: no space left on device" > "$IM/scn/pull-primary"
  ! img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null 2>&1; [ "$(grep -c "^pull mirror\|^manifest" "$IM/calls")" = 0 ] && [ "$(grep -c "^pull livekit" "$IM/calls")" = 1 ] && grep -q "\"code\":\"disk\"" "$IM/data/state/image-error-egress.json"'
t "img: нет образа в основном реестре (manifest unknown) → зеркало, повторов основного нет" imgrun '
  echo "Error response from daemon: manifest unknown" > "$IM/scn/pull-primary"
  img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null || exit 1; [ "$(grep -c "^pull livekit" "$IM/calls")" = 1 ] && [ "$VIDEO_IMAGE" = "'"$MIR"'" ]'
t "img: образ уже загружен из зеркала раньше — ни одного обращения к сети, значение сохраняется в .env" imgrun '
  : > "$IM/have/$(printf "%s" "'"$MIR"'" | tr "/:@" "___")"
  img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null || exit 1; [ ! -e "$IM/calls" ] && grep -q "^VIDEO_IMAGE=.*mirror.gcr.io" "$ENV_FILE"'
t "img: образ уже есть под основным именем — сеть не нужна" imgrun '
  : > "$IM/have/$(printf "%s" "'"$PIN"'" | tr "/:@" "___")"
  img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null || exit 1; [ ! -e "$IM/calls" ]'
t "img: вышла новая закреплённая версия — выбранное автоматически зеркало снимается, загрузка идёт с основного источника" imgrun '
  cp "'"$IM"'/tt.txt" "$IM/scn/pull-primary"; img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null || exit 1
  rm "$IM/scn/pull-primary"; : > "$IM/calls"
  NEW="livekit/egress:v1.16.0@sha256:'"$(printf 'b%.0s' $(seq 64))"'"
  img_ensure egress VIDEO_IMAGE "$NEW" >/dev/null || exit 1
  grep -q "^pull livekit/egress:v1.16.0" "$IM/calls" && [ "$(grep -c "^pull mirror" "$IM/calls")" = 0 ] && ! grep -q "^VIDEO_IMAGE=.*mirror" "$ENV_FILE" && [ -z "$(img_state_get egress)" ]'
t "img: значение VIDEO_IMAGE, заданное администратором, не перезаписывается и не снимается" imgrun '
  printf "VIDEO_IMAGE=reg.example.org/hub/egress:custom\n" >> "$ENV_FILE"; load_env "$ENV_FILE"; : > "$IM/have/reg.example.org_hub_egress_custom"
  img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null || exit 1; [ ! -e "$IM/calls" ] && grep -q "^VIDEO_IMAGE=reg.example.org/hub/egress:custom" "$ENV_FILE"'
t "img: ссылка без дайджеста (только тег) — зеркала не используются, версия не подменяется" imgrun '
  [ -z "$(img_mirror_refs livekit/egress:v1.15.0)" ] || exit 1
  cp "'"$IM"'/tt.txt" "$IM/scn/pull-primary"; ! img_ensure egress VIDEO_IMAGE livekit/egress:v1.15.0 >/dev/null 2>&1; [ "$(grep -c "^pull mirror\|^manifest" "$IM/calls")" = 0 ]'
t "img: IMAGE_MIRRORS=none отключает зеркала, внутренний реестр организации указывается списком" imgrun '
  IMAGE_MIRRORS=none; [ -z "$(img_mirror_refs "'"$PIN"'")" ] || exit 1
  IMAGE_MIRRORS="reg.example.org/dockerhub mirror.gcr.io"; r="$(img_mirror_refs "'"$PIN"'")"
  [ "$(sed -n 1p <<<"$r")" = "reg.example.org/dockerhub/livekit/egress@'"$DG"'" ] && [ "$(sed -n 2p <<<"$r")" = "'"$MIR"'" ]'
t "img: зеркало образов ghcr.io задаётся явно (ИСТОЧНИК=ПРЕФИКС) и не подходит образам Docker Hub" imgrun '
  IMAGE_MIRRORS="ghcr.io=reg.example.org/ghcr"; L="ghcr.io/ggml-org/llama.cpp:server-b1@sha256:'"$(printf 'c%.0s' $(seq 64))"'"
  [ "$(img_mirror_refs "$L")" = "reg.example.org/ghcr/ggml-org/llama.cpp@sha256:'"$(printf 'c%.0s' $(seq 64))"'" ] && [ -z "$(img_mirror_refs "'"$PIN"'")" ]'
t "img: официальный образ Docker Hub получает library/ в пути зеркала" imgrun 'IMAGE_MIRRORS=mirror.gcr.io; [ "$(img_mirror_refs redis:7@sha256:'"$(printf 'd%.0s' $(seq 64))"')" = "mirror.gcr.io/library/redis@sha256:'"$(printf 'd%.0s' $(seq 64))"'" ]'
t "img: образ другой архитектуры отклоняется и не принимается как загруженный" imgrun '
  cp "'"$IM"'/tt.txt" "$IM/scn/pull-primary"; FAKE_IMG_ARCH=arm64; export FAKE_IMG_ARCH
  ! img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null 2>&1; ! grep -q "^VIDEO_IMAGE=." "$ENV_FILE" && grep -q "\"code\":\"timeout\"" "$IM/data/state/image-error-egress.json"'
t "img: успешная загрузка после неудачи стирает прошлую ошибку" imgrun '
  echo "x509: certificate signed by unknown authority" > "$IM/scn/pull-primary"; echo "manifest unknown" > "$IM/scn/manifest-mirror"; ! img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null 2>&1
  [ -s "$IM/data/state/image-error-egress.json" ] || exit 1; rm "$IM/scn/pull-primary"; img_ensure egress VIDEO_IMAGE "'"$PIN"'" >/dev/null || exit 1; [ ! -s "$IM/data/state/image-error-egress.json" ]'
t "img: soft-режим — при неудаче код 0 (остальная система работает), ошибка записана" imgrun '
  echo "no such host" > "$IM/scn/pull-primary"; echo "no such host" > "$IM/scn/manifest-mirror"; img_ensure egress VIDEO_IMAGE "'"$PIN"'" soft >/dev/null 2>&1 && grep -q "\"code\":\"dns\"" "$IM/data/state/image-error-egress.json"'
t "img: video_prepare использует общий механизм (зеркало подхватывается, VIDEO_IMAGE меняется)" imgrun '
  cp "'"$IM"'/tt.txt" "$IM/scn/pull-primary"; video_prepare >/dev/null 2>&1 || exit 1; [ "$(video_image)" = "'"$MIR"'" ]'

# классификация причин по настоящим текстам docker
cls() { [ "$(source "$ROOT/scripts/lib/common.sh" 2>/dev/null; img_classify "$1")" = "$2" ]; }
t "img: причина — таймаут заголовков (из расследования)" cls "$TIMEOUT_TXT" timeout
t "img: причина — обрыв чтения у CDN" cls 'read tcp 192.0.2.10:38688->203.0.113.8:443: read: connection timed out' timeout
t "img: причина — DNS" cls 'dial tcp: lookup registry-1.docker.io: no such host' dns
t "img: причина — отказ соединения" cls 'dial tcp 203.0.113.8:443: connect: connection refused' connect
t "img: причина — сертификат" cls 'tls: failed to verify certificate: x509: certificate signed by unknown authority' tls
t "img: причина — лимит" cls 'toomanyrequests: You have reached your pull rate limit' ratelimit
t "img: причина — авторизация" cls 'Error response from daemon: unauthorized: authentication required' auth
t "img: причина — нет образа" cls 'Error response from daemon: manifest for livekit/egress:v9 not found: manifest unknown' notfound
t "img: причина — «no such manifest» у зеркала (docker manifest inspect)" cls 'no such manifest: mirror.gcr.io/livekit/egress@sha256:aaa' notfound
t "img: причина — диск" cls 'failed to register layer: write /x: no space left on device' disk
t "img: причина — контрольная сумма" cls 'failed to verify layer: digest mismatch: expected sha256:aaa got sha256:bbb' digest
t "img: причина — служба Docker" cls 'Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?' daemon
t "img: причина — другая архитектура" cls 'no matching manifest for linux/arm64/v8 in the manifest list entries' arch
t "img: неопознанное — unknown, а не выдуманная причина" cls 'something odd happened' unknown
t "img: redact скрывает Bearer, подписи URL и пароль в адресе" bash -c 'source "$1/scripts/lib/common.sh"; o="$(printf "%s\n" "Authorization: Bearer abcdefghijklmnop" "https://u:p4ss@h.example/x?X-Amz-Signature=ZZZ&a=1" | img_redact)"; ! grep -q "abcdefghijklmnop\|p4ss\|ZZZ" <<<"$o" && grep -q "a=1" <<<"$o"' _ "$ROOT"
t "img: compose использует VIDEO_IMAGE, SIP_IMAGE и LLM_LOCAL_IMAGE (на это опирается выбор зеркала)" bash -c 'grep -q "VIDEO_IMAGE:-" "$1/deployment/compose.yml" && grep -q "SIP_IMAGE:-" "$1/deployment/compose.yml" && grep -q "LLM_LOCAL_IMAGE:-" "$1/deployment/compose.yml"' _ "$ROOT"
