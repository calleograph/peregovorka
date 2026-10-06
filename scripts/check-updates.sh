#!/usr/bin/env bash
# check-updates.sh — какие более новые версии ключевых компонентов вышли (ничего не меняет и не скачивает образы).
#   scripts/check-updates.sh [--env FILE]
# 1) Приложение: git fetch origin и список новых commit'ов, новые параметры .env.example (только имена).
# 2) Компоненты реального времени: что используется (.env, requirements, package-lock) против последних релизов: LiveKit Server (GitHub),
#    livekit / livekit-api (PyPI), livekit-client (npm). Это справочная информация: в production используется ПРОВЕРЕННЫЙ набор
#    (deployment/compat.env), его меняют разработчики после проверки.
# Обновить установку: ./scripts/update.sh
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

if git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  log "== Обновления приложения (git) =="
  HEAD12="$(git -C "$REPO_ROOT" rev-parse HEAD | cut -c1-12)"
  if upd_git_fetch >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1; then
    UP="$(git -C "$REPO_ROOT" rev-parse '@{u}')"; N="$(git -C "$REPO_ROOT" rev-list --count "HEAD..$UP")"
    if [ "$N" -eq 0 ]; then ok "Установка на актуальной версии (${HEAD12})"
    else
      warn "Доступно обновлений: $N (${HEAD12} → ${UP:0:12})"; git -C "$REPO_ROOT" log --oneline --no-decorate -n 15 "HEAD..$UP" | sed 's/^/  /'
      if [ -f "$ENV_FILE" ]; then
        TMPX="$(mktemp)"; git -C "$REPO_ROOT" show "$UP:.env.example" > "$TMPX" 2>/dev/null && { upd_env_classify "$ENV_FILE" "$TMPX"
          [ "${#UPD_NEW_SAFE[@]}" -gt 0 ] && log "  Новые параметры .env (добавятся автоматически): ${UPD_NEW_SAFE[*]}"
          [ "${#UPD_NEW_DECIDE[@]}" -gt 0 ] && log "  Новые параметры, требующие решения: ${UPD_NEW_DECIDE[*]}"; }
        rm -f "$TMPX"
      fi
      git -C "$REPO_ROOT" diff --quiet "HEAD" "$UP" -- backend/migrations/versions 2>/dev/null || log "  Есть миграции БД (перед ними update.sh сделает backup)"
      info "Обновить:  ./scripts/update.sh   (сначала можно: ./scripts/update.sh --dry-run)"
    fi
  else warn "Не удалось проверить origin (нет сети/доступа к GitHub или у ветки нет upstream)"; fi
  log
fi

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
info "Версии LiveKit/SDK в production меняют разработчики вместе с deployment/compat.env после проверки; на сервере они приходят с ./scripts/update.sh."
