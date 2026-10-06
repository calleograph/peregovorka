#!/usr/bin/env bash
# tune-kernel.sh — рекомендуемые параметры ядра для WebRTC (LiveKit) и Redis.
#   scripts/tune-kernel.sh                 показать текущие значения и рекомендации (ничего не меняет)
#   scripts/tune-kernel.sh --apply         применить рекомендации (спросит подтверждение; нужен root/sudo)
#   scripts/tune-kernel.sh --apply --yes   применить без вопроса (только для standalone-профиля)
#
# ВАЖНО: sysctl глобален для всего хоста. На shared-host (профиль INSTALL_PROFILE=shared-host) скрипт не применяет
# ничего без явного --apply И подтверждения, а --yes там игнорируется: изменение должен осознанно подтвердить человек.
# Применяется через собственный файл /etc/sysctl.d/99-peregovorka.conf (чужие файлы не правятся).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/envlib.sh"
APPLY=0; YES=0; CONF="${SYSCTL_CONF:-/etc/sysctl.d/99-peregovorka.conf}"
while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --yes|-y) YES=1; shift ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
PROFILE="${INSTALL_PROFILE:-}"
[ -f "$ENV_FILE" ] && { load_env "$ENV_FILE"; PROFILE="${INSTALL_PROFILE:-$PROFILE}"; }

show() {
  log "== Параметры ядра (рекомендации LiveKit и Redis) =="
  kernel_tuning_check ok warn
}
show
WANT=$'vm.overcommit_memory = 1\nnet.core.rmem_max = '"$KERNEL_RECOMMENDED_RMEM"$'\nnet.core.wmem_max = '"$KERNEL_RECOMMENDED_WMEM"$'\nnet.core.netdev_max_backlog = '"$KERNEL_RECOMMENDED_BACKLOG"
log; log "Будет записано в $CONF:"; printf '%s\n' "$WANT" | sed 's/^/    /'
if [ "$APPLY" -ne 1 ]; then
  log; info "Ничего не изменено. Применить: sudo scripts/tune-kernel.sh --apply"
  exit 0
fi
[ "$(uname -s)" = Linux ] || die "Применение поддерживается только на Linux"
[ "$(id -u)" -eq 0 ] || die "Нужны права root: sudo scripts/tune-kernel.sh --apply"
if [ "$PROFILE" = "shared-host" ] || [ -z "$PROFILE" ]; then
  [ "$YES" -eq 1 ] && warn "--yes игнорируется: профиль ${PROFILE:-не задан} (общий сервер) — требуется ручное подтверждение"
  warn "Профиль ${PROFILE:-не задан}: параметры ядра затронут ВСЕ приложения этого сервера."
  read -r -p "Применить перечисленные параметры? Введите «да»: " ans
  [ "$ans" = "да" ] || { info "Отменено"; exit 0; }
elif [ "$YES" -ne 1 ]; then
  read -r -p "Применить параметры (standalone)? [y/N]: " ans
  case "$ans" in y|Y|да) ;; *) info "Отменено"; exit 0 ;; esac
fi
printf '# Создано scripts/tune-kernel.sh (Peregovorka). Удалить файл и выполнить sysctl --system, чтобы откатить.\n%s\n' "$WANT" > "$CONF" || die "Не удалось записать $CONF"
sysctl -p "$CONF" >/dev/null || die "sysctl не принял параметры (см. $CONF)"
ok "Применено ($CONF). Перезапуск контейнеров не требуется; проверьте: scripts/tune-kernel.sh"
