#!/usr/bin/env bash
# check-updates.sh — какие более новые версии ключевых компонентов вышли (ничего не меняет и не скачивает образы).
#   scripts/check-updates.sh [--env FILE]
# Сравнивает то, что используется (.env, requirements, package-lock), с последними релизами: LiveKit Server (GitHub),
# livekit / livekit-api (PyPI), livekit-client (npm). Нужен доступ в интернет только у того, кто запускает проверку
# (рабочая станция администратора или сервер с выходом наружу). Обновление — осознанно: поменять LIVEKIT_IMAGE_TAG в .env
# (можно на более новую версию или `latest`), затем scripts/deploy.sh и scripts/smoke-test.sh (проверит /rtc/v1).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
while [ $# -gt 0 ]; do case "$1" in --env) ENV_FILE="$2"; shift 2 ;; *) die "Неизвестный аргумент: $1" ;; esac; done
[ -f "$ENV_FILE" ] && load_env "$ENV_FILE"
command -v curl >/dev/null 2>&1 || die "Нужен curl"
PY="$(command -v python3 || command -v python || command -v py || true)"
jget() { # jget 'выражение на python над переменной d' < json
  [ -n "$PY" ] || { echo "?"; return; }
  "$PY" -c "import sys,json
try:
    d=json.load(sys.stdin); print($1)
except Exception:
    print('?')" 2>/dev/null
}
row() { printf '%-28s %-14s %-14s %s\n' "$1" "$2" "$3" "$4"; }
verdict() { if [ "$3" = "?" ]; then echo "не удалось получить (нет сети?)"; elif [ "$2" = "$3" ]; then echo "актуально"; elif [ "$2" = latest ]; then echo "плавающий тег latest"; else echo "ЕСТЬ НОВЕЕ"; fi; }

lk_now="${LIVEKIT_IMAGE_TAG:-$(grep -E '^TESTED_LIVEKIT_SERVER=' "$REPO_ROOT/deployment/compat.env" 2>/dev/null | cut -d= -f2)}"
lk_new="$(curl -fsS -m 15 https://api.github.com/repos/livekit/livekit/releases/latest 2>/dev/null | jget "d['tag_name']")"
py_now="$(grep -E '^livekit==' "$REPO_ROOT/asr-service/requirements.txt" 2>/dev/null | cut -d= -f3)"
py_new="$(curl -fsS -m 15 https://pypi.org/pypi/livekit/json 2>/dev/null | jget "d['info']['version']")"
api_now="$(grep -E '^livekit-api==' "$REPO_ROOT/backend/requirements.txt" 2>/dev/null | cut -d= -f3)"
api_new="$(curl -fsS -m 15 https://pypi.org/pypi/livekit-api/json 2>/dev/null | jget "d['info']['version']")"
js_now="$(grep -A2 '"node_modules/livekit-client"' "$REPO_ROOT/frontend/package-lock.json" 2>/dev/null | grep -m1 '"version"' | cut -d'"' -f4)"
js_new="$(curl -fsS -m 15 https://registry.npmjs.org/livekit-client/latest 2>/dev/null | jget "d['version']")"

log "== Версии компонентов реального времени =="
row "Компонент" "используется" "последняя" "статус"
row "LiveKit Server" "${lk_now:-?}" "$lk_new" "$(verdict x "${lk_now:-?}" "$lk_new")"
row "livekit (Python, ASR)" "${py_now:-?}" "$py_new" "$(verdict x "${py_now:-?}" "$py_new")"
row "livekit-api (Python)" "${api_now:-?}" "$api_new" "$(verdict x "${api_now:-?}" "$api_new")"
row "livekit-client (браузер)" "${js_now:-?}" "$js_new" "$(verdict x "${js_now:-?}" "$js_new")"
log
info "Правило: версии сервера и клиентов обновляйте вместе и проверяйте scripts/smoke-test.sh (строка «LiveKit /rtc/v1»)."
info "Сборка берёт Python-зависимости по диапазонам из requirements, а npm — по package-lock.json (npm update livekit-client обновит его)."
info "Свежую версию сервера можно задать в .env: LIVEKIT_IMAGE_TAG=<тег> (или latest), затем scripts/deploy.sh."
