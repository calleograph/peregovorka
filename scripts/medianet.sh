#!/usr/bin/env bash
# medianet.sh — медиасоединения LiveKit: какие адреса получают клиенты (ICE), проброс портов, проверка сети.
#   scripts/medianet.sh check [--env FILE] [--json]          проверки (с sudo — и локальный межсетевой экран); ничего не меняет
#   sudo scripts/medianet.sh apply [--env FILE] [--public-ip auto|off|IPv4]
#                                                            пересчитать адреса (STUN, адрес интерфейса) и перезапустить LiveKit и backend
#   sudo scripts/medianet.sh ports --tcp N --udp N [--env FILE]
#                                                            сменить порты медиа согласованно: .env → публикация Docker → конфигурация LiveKit
#                                                            → адреса ICE → перечень портов (сначала проверка, что порты свободны)
# Публичный адрес: LIVEKIT_PUBLIC_IP=auto (по умолчанию: STUN; публичный адрес на интерфейсе — без STUN), off (только локальная сеть) или IPv4.
# Меняет только .env и контейнеры livekit и backend этого экземпляра; маршрутизатор и межсетевой экран не трогает — правила проброса печатает.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

case "${1:-}" in -h|--help) sed -n '2,10p' "$0"; exit 0 ;; esac
CMD="${1:-check}"; [ $# -gt 0 ] && shift
PUB=""; JSON=""; NTCP=""; NUDP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --public-ip) PUB="$2"; shift 2 ;;
    --json) JSON="--json"; shift ;;
    --tcp) NTCP="$2"; shift 2 ;;
    --udp) NUDP="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
[ -r "$ENV_FILE" ] || die "Не найден файл настроек: $ENV_FILE (укажите --env)"
PY=(python3 -I "$REPO_ROOT/scripts/medianet.py")

restart() {
  sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
  resolve_image_tag 2>/dev/null || true
  dc up -d --no-build livekit backend || die "Не удалось перезапустить livekit и backend (журнал: docker compose logs livekit)"
  ok "LiveKit и backend перезапущены с новыми настройками"
  sleep 5; "${PY[@]}" check --env "$ENV_FILE"
}

case "$CMD" in
  check) exec "${PY[@]}" check --env "$ENV_FILE" $JSON ;;
  apply)
    [ "$(id -u)" -eq 0 ] || [ -w "$ENV_FILE" ] || die "Нужны права на запись в $ENV_FILE: sudo scripts/medianet.sh apply"
    "${PY[@]}" configure --env "$ENV_FILE" --write ${PUB:+--public-ip "$PUB"} || exit $?
    restart ;;
  ports)
    [[ "$NTCP" =~ ^[0-9]+$ && "$NUDP" =~ ^[0-9]+$ && "$NTCP" -ge 1024 && "$NTCP" -le 65535 && "$NUDP" -ge 1024 && "$NUDP" -le 65535 ]] \
      || die "Укажите порты 1024–65535: --tcp N --udp N"
    load_env "$ENV_FILE"
    if [ "$NTCP" != "${LIVEKIT_TCP_PORT:-}" ] && port_busy tcp "$NTCP"; then die "TCP $NTCP уже занят на сервере"; fi
    if [ "$NUDP" != "${LIVEKIT_UDP_PORT:-}" ] && port_busy udp "$NUDP"; then die "UDP $NUDP уже занят на сервере"; fi
    source "$REPO_ROOT/scripts/lib/updatelib.sh"
    upd_env_set_key "$ENV_FILE" LIVEKIT_TCP_PORT "$NTCP" && upd_env_set_key "$ENV_FILE" LIVEKIT_UDP_PORT "$NUDP" || die "Не удалось записать порты в $ENV_FILE"
    ok "Порты медиа: TCP $NTCP, UDP $NUDP — не забудьте изменить проброс на маршрутизаторе (номер снаружи = номер на сервере)"
    "${PY[@]}" configure --env "$ENV_FILE" --write || exit $?
    unset LIVEKIT_TCP_PORT LIVEKIT_UDP_PORT; restart ;;
  *) die "Команда: check, apply или ports (scripts/medianet.sh --help)" ;;
esac
