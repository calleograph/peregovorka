#!/usr/bin/env bash
# admin-reset.sh — ОФИЦИАЛЬНОЕ восстановление доступа локального администратора (не нужен ни LDAP, ни ручная правка базы данных).
#
#   scripts/admin-reset.sh [--env FILE] [--username admin] [--create] [--yes]
#
# Что делает: выдаёт НОВЫЙ случайный пароль (при первом входе его нужно заменить), завершает все сессии этого администратора,
# снимает блокировку входа, записывает событие в журнал аудита («выполнено с консоли сервера»). Новый пароль печатается только в терминал.
#   --create   создать локального администратора, если его нет (например, после восстановления из копии без него)
#   --yes      не спрашивать подтверждение
# Требуется доступ к Docker на этом сервере (root или группа docker) — это и есть «административные права на сервере».
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/adminlib.sh"

USERNAME_ARG=""; CREATE=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --username) USERNAME_ARG="$2"; shift 2 ;;
    --create) CREATE=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

sanitize_project_env; load_env "$ENV_FILE"
validate_project_name
command -v docker >/dev/null 2>&1 || die "Docker не найден: сброс выполняется на сервере приложения"
docker info >/dev/null 2>&1 || die "Нет доступа к Docker. Запустите от root: sudo $0"
dc exec -T backend python -c "import app" >/dev/null 2>&1 || die "Контейнер backend не запущен. Запустите сервисы (scripts/ctl.sh start) и повторите."

if [ "$YES" -ne 1 ]; then
  log "Будет выдан НОВЫЙ пароль локального администратора${USERNAME_ARG:+ «$USERNAME_ARG»}."
  log "Прежний пароль перестанет работать, все сессии этого администратора будут завершены, событие попадёт в журнал аудита."
  read -r -p "Продолжить? [y/N] " a || die "Ввод завершён"
  [[ "${a:-N}" =~ ^[Yy]$ ]] || { info "Отменено."; exit 0; }
fi

args=(admin-reset --actor "$(id -un)@$(hostname -s 2>/dev/null || echo server)")
[ -n "$USERNAME_ARG" ] && args+=(--username "$USERNAME_ARG")
[ "$CREATE" -eq 1 ] && args+=(--create)
OUT="$(admin_cli "${args[@]}")" || die "Сброс не выполнен (если администратора нет — добавьте --create)"
USER_NAME="$(cli_value USERNAME "$OUT")"; PASS="$(cli_value PASSWORD "$OUT")"
[ -n "$USER_NAME" ] && [ -n "$PASS" ] || die "Служебная команда вернула неполный ответ"
print_credentials_block "${APP_PUBLIC_URL:-https://<адрес сервера>}" "$USER_NAME" "$PASS" reset
ok "Закрыто сессий: $(cli_value SESSIONS_CLOSED "$OUT"). Событие записано в журнал аудита."
unset PASS OUT
