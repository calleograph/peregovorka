#!/bin/bash
# Восстановление полного набора backup.sh --full на ЧИСТОМ изолированном экземпляре (свои база, Redis, каталог данных, порты). Настоящий compose, настоящий restore.sh.
#   restore_clean.sh <каталог со скриптами и deployment> <файл .env из набора> <дамп> <архив файлов> <тег образов>
set -u
SRC="$1"; ENVB="$2"; DUMP="$3"; FILES="$4"; TAG="$5"
P=pgrest; D="$HOME/pg-work/$P-data"; E="$HOME/pg-work/$P.env"
mkdir -p "$D"
sed -e "s/^COMPOSE_PROJECT_NAME=.*/COMPOSE_PROJECT_NAME=$P/" -e "s#^DATA_ROOT=.*#DATA_ROOT=$D#" -e "s/^IMAGE_TAG=.*/IMAGE_TAG=$TAG/" -e "s/^WEB_PORT=.*/WEB_PORT=28580/" \
    -e "s/^LIVEKIT_HTTP_PORT=.*/LIVEKIT_HTTP_PORT=27980/" -e "s/^LIVEKIT_TCP_PORT=.*/LIVEKIT_TCP_PORT=27981/" -e "s/^LIVEKIT_UDP_PORT=.*/LIVEKIT_UDP_PORT=27982/" "$ENVB" > "$E"; chmod 600 "$E"
for s in backend web asr; do docker tag "pgdoc-$s:$TAG" "$P-$s:$TAG"; done
LK="$(sed -n 's/^LIVEKIT_IMAGE_TAG=//p' "$E")"; docker tag "pgdoc-livekit:$LK" "$P-livekit:$LK"
dc() { docker compose --env-file "$E" -f "$SRC/deployment/compose.yml" -p "$P" "$@"; }
dc up -d --no-build postgres redis 2>&1 | tail -1
until dc exec -T postgres pg_isready >/dev/null 2>&1; do sleep 1; done
sleep 2
own() { docker run --rm -v "$D:/d" alpine:3 sh -c 'cd /d; mkdir -p recordings exports ca chat-files avatars branding live/b live/acc live/nginx models/gigaam models/llm updater ext; chown -R 10001:10001 recordings exports ca chat-files avatars branding live; chmod 2750 live/nginx; chmod 1777 updater'; }
own
SHIM="$(mktemp -d "$HOME/pg-work/shim.XXXXXX")"; printf '#!/bin/sh\nexit 1\n' > "$SHIM/sudo"; chmod +x "$SHIM/sudo"     # без повышения прав
PATH="$SHIM:$PATH" WAIT=150 bash "$SRC/scripts/restore.sh" "$DUMP" --files "$FILES" --env "$E" --yes 2>&1 | tail -12 | cut -c1-170
echo "restore rc=${PIPESTATUS[0]}"
own
dc up -d --no-build backend web 2>&1 | tail -1
