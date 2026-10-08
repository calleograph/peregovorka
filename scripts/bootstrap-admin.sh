#!/usr/bin/env bash
# bootstrap-admin.sh — создаёт ЛОКАЛЬНОГО администратора (если его ещё нет) и печатает заметный блок «ПЕРВИЧНЫЙ ВХОД».
#
#   scripts/bootstrap-admin.sh [--env FILE] [--username admin]
#
# Пароль генерируется случайно и показывается один раз, только в терминал (не пишется в файлы, .env и журналы установки).
# Заранее заданного пароля нет. Если локальный администратор уже существует, пароль НЕ показывается — для нового пароля есть scripts/admin-reset.sh.
# Вызывается мастером установки (scripts/setup.sh) после успешного запуска сервисов; безопасно запускать повторно.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/adminlib.sh"

USERNAME_ARG="admin"
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --username) USERNAME_ARG="$2"; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

sanitize_project_env; load_env "$ENV_FILE"
validate_project_name
command -v docker >/dev/null 2>&1 || die "Docker не найден"

# backend должен быть готов принимать команды (после запуска сервисов он уже healthy; даём до ~60 с на случай свежего старта)
ready=0
for _ in $(seq 1 30); do
  if dc exec -T backend python -c "import app" >/dev/null 2>&1; then ready=1; break; fi
  sleep 2
done
[ "$ready" -eq 1 ] || die "Контейнер backend не отвечает. Проверьте: scripts/status.sh, scripts/logs.sh backend"

OUT="$(admin_cli bootstrap-admin --username "$USERNAME_ARG")" || die "Не удалось создать локального администратора (scripts/logs.sh backend)"
USER_NAME="$(cli_value USERNAME "$OUT")"
if [ "$(cli_value EXISTS "$OUT")" = "1" ]; then
  ok "Локальный администратор «$USER_NAME» уже создан ранее — первичный пароль больше не показывается."
  info "Забыли пароль? ./scripts/admin-reset.sh  (на этом сервере, от root)"
  exit 0
fi
PASS="$(cli_value PASSWORD "$OUT")"
[ -n "$USER_NAME" ] && [ -n "$PASS" ] || die "Служебная команда вернула неполный ответ"
print_credentials_block "${APP_PUBLIC_URL:-https://<адрес сервера>}" "$USER_NAME" "$PASS" first
unset PASS OUT
print_next_steps
