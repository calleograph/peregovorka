#!/usr/bin/env bash
# security-reset.sh — АВАРИЙНОЕ восстановление доступа к веб-интерфейсу, если ограничение по доверенным подсетям (Администрирование → Безопасность)
# закрыло вход администратору. Работает без веб-интерфейса: напрямую с базой через контейнер backend.
#
#   scripts/security-reset.sh [--env FILE] [--unblock-all] [--yes]
#
# Что делает: выключает ограничение доступа по IP-адресам и подсетям (список сетей сохраняется — потом можно поправить и включить снова), снимает пробный срок.
#   --unblock-all   дополнительно снять все временные блокировки адресов
#   --yes           не спрашивать подтверждение
# Запись попадает в журнал аудита («выполнено с консоли сервера»). Другие меры защиты (CAPTCHA, счётчики входа, WAF) не отключаются.
# Есть и второй способ, не зависящий от приложения: IP_ACCESS_DISABLE=1 в .env и перезапуск backend.
# Требуется доступ к Docker на этом сервере (root или группа docker).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/adminlib.sh"

UNBLOCK=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --unblock-all) UNBLOCK=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done

sanitize_project_env; load_env "$ENV_FILE"
validate_project_name
command -v docker >/dev/null 2>&1 || die "Docker не найден: сброс выполняется на сервере приложения"
docker info >/dev/null 2>&1 || die "Нет доступа к Docker. Запустите от root: sudo $0"
dc exec -T backend python -c "import app" >/dev/null 2>&1 || die "Контейнер backend не запущен. Запустите сервисы (scripts/ctl.sh start) и повторите. Если сервис не стартует, добавьте IP_ACCESS_DISABLE=1 в .env."

if [ "$YES" -ne 1 ]; then
  log "Будет выключено ограничение доступа по доверенным подсетям${UNBLOCK:+ и сняты блокировки адресов}."
  log "Список разрешённых сетей сохранится. Событие попадёт в журнал аудита."
  read -r -p "Продолжить? [y/N] " a || die "Ввод завершён"
  [[ "${a:-N}" =~ ^[Yy]$ ]] || { info "Отменено."; exit 0; }
fi

args=(security-reset --actor "$(id -un)@$(hostname -s 2>/dev/null || echo server)")
[ "$UNBLOCK" -eq 1 ] && args+=(--unblock-all)
dc exec -T backend python -m app.cli "${args[@]}"
