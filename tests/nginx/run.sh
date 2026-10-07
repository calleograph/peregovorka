#!/usr/bin/env bash
# Проверка собранного образа web на настоящем nginx: синтаксис (nginx -t) И работа при запросах, как их присылает внешний прокси
# (X-Forwarded-Proto: https и цепочка X-Forwarded-For) — ошибки вида «cycle while evaluating variable "xfp"» проявляются только при непустом заголовке.
#   tests/nginx/run.sh [ОБРАЗ]      (по умолчанию собирает frontend/ в образ peregovorka-web-ci)
# Нужен Docker. Всё создаётся с префиксом pgci-* и удаляется в конце.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
IMG="${1:-}"
NET="pgci-net-$$"; WEB="pgci-web-$$"; PASS=0; FAIL=0
t() { # t "описание" команда… — при провале печатает вывод команды (в CI — ещё и как аннотация, видимую без открытия журнала)
  local d="$1" out; shift
  if out="$("$@" 2>&1)"; then PASS=$((PASS+1)); echo "PASS: $d"
  else FAIL=$((FAIL+1)); echo "FAIL: $d"; printf '%s
' "$out" | head -20 | sed 's/^/    /'
    [ -z "${GITHUB_ACTIONS:-}" ] || echo "::error title=web-image: $d::$(printf '%s' "$out" | head -c 600 | tr '
' ' ')"
  fi
}
cleanup() { docker rm -f "$WEB" "pgci-backend-$$" "pgci-livekit-$$" >/dev/null 2>&1; docker network rm "$NET" >/dev/null 2>&1; }
trap cleanup EXIT

if [ -z "$IMG" ]; then IMG=peregovorka-web-ci; docker build -q -t "$IMG" "$ROOT/frontend" >/dev/null || { echo "FAIL: сборка образа web"; exit 1; }; fi
t "nginx -t в образе" docker run --rm --entrypoint nginx "$IMG" -t

docker network create "$NET" >/dev/null || exit 1
# имена backend и livekit должны разрешаться внутри сети (nginx берёт их через resolver Docker)
for s in backend:8000 livekit:7880; do
  n="${s%%:*}"; p="${s##*:}"
  docker run -d --rm --name "pgci-$n-$$" --network "$NET" --network-alias "$n" -v "$ROOT/tests/nginx/stub.py:/stub.py:ro" python:3.12-alpine python /stub.py "$p" >/dev/null || exit 1
done
docker run -d --rm --name "$WEB" --network "$NET" -p 127.0.0.1:18199:8080 "$IMG" >/dev/null || exit 1
for _ in $(seq 1 45); do curl -fs http://127.0.0.1:18199/healthz >/dev/null 2>&1 && break; sleep 1; done
# заглушки backend/livekit тоже должны успеть подняться: ждём ответа ЧЕРЕЗ прокси, а не только самого nginx
for _ in $(seq 1 45); do curl -fs http://127.0.0.1:18199/api/v1/health/live >/dev/null 2>&1 && curl -fs http://127.0.0.1:18199/livekit/ >/dev/null 2>&1 && break; sleep 1; done

U=http://127.0.0.1:18199
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
t "/healthz → 200" test "$(code $U/healthz)" = 200
t "/ (SPA) → 200" test "$(code $U/)" = 200
t "/version.json → 200 с версией" bash -c "curl -s $U/version.json | grep -q '\"version\"'"
t "/api без X-Forwarded-Proto: схема берётся из соединения (http)" bash -c "curl -s $U/api/v1/health/live | grep -q '\"xfp\": *\"http\"'"
t "/api с X-Forwarded-Proto: https — схема сохраняется (нет цикла переменной)" bash -c "curl -s -H 'X-Forwarded-Proto: https' $U/api/v1/health/live | grep -q '\"xfp\": *\"https\"'"
t "/api: цепочка X-Forwarded-For передаётся без изменений" bash -c "curl -s -H 'X-Forwarded-For: 172.16.48.7, 10.0.0.1' $U/api/v1/health/live | grep -q '172.16.48.7, 10.0.0.1'"
t "/livekit/ со схемой https → 200" test "$(code -H 'X-Forwarded-Proto: https' $U/livekit/)" = 200
t "/internal/ снаружи закрыт (404)" test "$(code $U/internal/v1/smoke)" = 404
t "/drawio/index.html → 200" test "$(code $U/drawio/index.html)" = 200
t "/drawio/: CSP и CORS для песочницы" bash -c "curl -sI $U/drawio/index.html | tr -d '\r' | grep -qi '^content-security-policy:.*frame-ancestors' && curl -sI $U/drawio/index.html | grep -qi '^access-control-allow-origin: \*'"
t "приложение разрешает только свои фреймы (frame-src 'self')" bash -c "curl -sI $U/ | grep -i '^content-security-policy:' | grep -q \"frame-src 'self'\""
t "ошибок «cycle»/«emerg» в журнале nginx нет" bash -c "! docker logs $WEB 2>&1 | grep -Eqi 'cycle while|\[emerg\]'"
echo "nginx/web: пройдено $PASS, провалено $FAIL"
if [ "$FAIL" -gt 0 ]; then
  LOGS="$(docker logs "$WEB" 2>&1 | tail -25)"
  echo "--- журнал nginx (web) ---"; echo "$LOGS"
  [ -z "${GITHUB_ACTIONS:-}" ] || echo "::error title=web-image: журнал nginx::$(printf '%s' "$LOGS" | tail -c 700 | tr '
' ' ')"
  echo "--- ответы ---"
  for p in /healthz /api/v1/health/live /livekit/ /drawio/index.html; do printf '%s -> ' "$p"; curl -s -o /dev/null -w '%{http_code}
' -H 'X-Forwarded-Proto: https' "$U$p"; done
fi
[ "$FAIL" -eq 0 ]
