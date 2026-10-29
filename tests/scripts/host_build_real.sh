#!/usr/bin/env bash
# Настоящий Docker (не подмена): сборка в сети хоста тем же путём, что у обновления — `docker compose build` с `network: host`.
# Проверяет, что select_host_build_mode выбирает работающий способ (BuildKit с разрешением network.host либо классический builder), и что маленький образ
# с шагом RUN действительно собирается с BUILD_NETWORK=host и с обычной сетью. Нужен Docker; запускается в CI и вручную: bash tests/scripts/host_build_real.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../scripts/lib/common.sh
source "$ROOT/scripts/lib/common.sh"
command -v docker >/dev/null 2>&1 || { echo "SKIP: Docker не установлен"; exit 0; }
docker version >/dev/null 2>&1 || { echo "SKIP: Docker недоступен"; exit 0; }
COMPOSE_PROJECT_NAME=pgrealhost; SELECTED_BUILD_MODE=buildkit; HOST_BUILD_MODE=""
echo "docker: $(docker version --format '{{.Server.Version}}' 2>/dev/null); buildx: $(docker buildx version 2>/dev/null | head -1 || echo нет); compose: $(docker compose version --short 2>/dev/null)"
rc=0
select_host_build_mode; sel=$?
echo "select_host_build_mode: rc=$sel режим=${HOST_BUILD_MODE:-?} ${HOST_BUILD_WHY:+причина: $HOST_BUILD_WHY}"
[ "$sel" -eq 0 ] || { echo "FAIL: ни BuildKit, ни классический builder не умеют собирать в сети хоста"; exit 1; }

dir="$(mktemp -d)"; trap 'docker image rm -f pgrealhost-t:1 >/dev/null 2>&1; rm -rf "$dir"' EXIT
mkdir -p "$dir/ctx"; printf 'FROM alpine:3.20\nRUN echo build-step-ran > /marker\n' > "$dir/ctx/Dockerfile"
printf 'services:\n  t:\n    image: pgrealhost-t:1\n    build:\n      context: ./ctx\n      network: ${BUILD_NETWORK:-default}\n' > "$dir/compose.yml"
docker pull -q alpine:3.20 >/dev/null 2>&1 || true
for net in default host; do
  # способ — тот, что выбрал установщик для сети хоста; для обычной сети — BuildKit
  mode=buildkit; [ "$net" = host ] && mode="$HOST_BUILD_MODE"
  if [ "$mode" = legacy ]; then bk=0; else bk=1; fi
  if BUILD_NETWORK="$net" COMPOSE_BAKE=false DOCKER_BUILDKIT=$bk COMPOSE_DOCKER_CLI_BUILD=$bk docker compose -p pgrealhost -f "$dir/compose.yml" build >"$dir/$net.log" 2>&1 \
     && [ "$(docker run --rm --entrypoint cat pgrealhost-t:1 /marker 2>/dev/null)" = build-step-ran ]; then
    echo "OK:   сборка с RUN, BUILD_NETWORK=$net, builder=$mode"
  else
    echo "FAIL: сборка с RUN, BUILD_NETWORK=$net, builder=$mode"; tail -5 "$dir/$net.log" | sed 's/^/    | /'; rc=1
  fi
  docker image rm -f pgrealhost-t:1 >/dev/null 2>&1
done
exit $rc
