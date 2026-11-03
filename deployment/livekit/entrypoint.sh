#!/bin/sh
# entrypoint.sh — запуск LiveKit с верными адресами в кандидатах ICE (настройки готовит scripts/lib/medianet.py на сервере).
# 1) LIVEKIT_LAN_IP — адрес сервера в локальной сети, когда клиенты должны получать и его, и публичный (сервер за NAT). В контейнере Docker
#    «внутренний» адрес для LiveKit — адрес контейнера (172.x), недоступный клиентам; поэтому адрес сервера добавляется на интерфейс
#    контейнера (/32, без маршрутов) — LiveKit объявляет его вместе с публичным (advertise_internal_ip), а пакеты на него приходят через
#    опубликованные порты Docker, как и сейчас. Нужна возможность NET_ADMIN (compose.yml); без неё — предупреждение, остаётся публичный адрес.
# 2) use_external_ip: true и STUN недоступен — LiveKit не запускается вовсе («could not resolve external IP»). Поэтому STUN проверяется
#    до запуска; не отвечает — запуск с явным адресом node_ip из той же конфигурации (последний публичный адрес, найденный при установке).
set -eu
say() { echo "peregovorka-entrypoint: $*" >&2; }

if [ -n "${LIVEKIT_LAN_IP:-}" ]; then
  if ip -o -4 addr show | grep -q " ${LIVEKIT_LAN_IP}/"; then :
  else
    dev="$(ip -o -4 route show default | awk '{print $5; exit}')"
    if ip addr add "${LIVEKIT_LAN_IP}/32" dev "${dev:-eth0}" 2>/dev/null; then say "адрес локальной сети ${LIVEKIT_LAN_IP} объявляется клиентам"
    else say "ПРЕДУПРЕЖДЕНИЕ: не удалось добавить ${LIVEKIT_LAN_IP} (нет NET_ADMIN?) — клиенты локальной сети получат только публичный адрес"; fi
  fi
fi

stun_ok() {   # $1 = host:port; ответ на STUN Binding за 3 с
  printf '\000\001\000\000\041\022\244\102peregovorka1' | timeout 4 nc -u -w 3 "${1%:*}" "${1##*:}" 2>/dev/null | head -c 2 | od -An -tx1 | grep -q "01 01"
}
case "${LIVEKIT_CONFIG:-}" in
  *"use_external_ip: true"*)
    ok=""
    for s in $(echo "${LIVEKIT_STUN_SERVERS:-stun.l.google.com:19302,stun1.l.google.com:19302}" | tr ',' ' '); do
      if stun_ok "$s"; then ok="$s"; break; fi
    done
    if [ -n "$ok" ]; then say "публичный адрес определяется через STUN ($ok)"
    else
      say "ПРЕДУПРЕЖДЕНИЕ: STUN недоступен (${LIVEKIT_STUN_SERVERS:-}) — запуск с последним известным адресом node_ip"
      LIVEKIT_CONFIG="$(printf '%s\n' "$LIVEKIT_CONFIG" | sed 's/use_external_ip: true/use_external_ip: false/')"; export LIVEKIT_CONFIG
    fi ;;
esac
exec /usr/local/bin/livekit-server "$@"
