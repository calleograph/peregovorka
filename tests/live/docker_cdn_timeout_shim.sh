#!/usr/bin/env bash
# Подмена команды docker для проверки на настоящих контейнерах: загрузка (`docker pull`) любых образов, кроме зеркала mirror.gcr.io, заканчивается так же, как в реальном
# сбое CDN основного реестра (таймаут ответа слоёв). Всё остальное — настоящий docker. Использование: PATH="<каталог этого файла под именем docker>:$PATH".
REAL="${REAL_DOCKER:-/usr/bin/docker}"
if [ "${1:-}" = pull ]; then
  case "${2:-}" in
    mirror.gcr.io/*) exec "$REAL" "$@" ;;
    *) echo 'Error response from daemon: failed to copy: httpReadSeeker: failed open: Get "https://production.cloudfront.docker.com/registry-v2/docker/registry/v2/blobs/sha256/ab/abcd/data?Expires=1&Signature=SECRETSIG&Key-Pair-Id=KID": net/http: timeout awaiting response headers' >&2; exit 1 ;;
  esac
fi
exec "$REAL" "$@"
