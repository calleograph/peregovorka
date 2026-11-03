#!/bin/sh
# entrypoint.sh — запуск LiveKit с верными адресами в кандидатах ICE (настройки готовит scripts/medianet.py на сервере).
# 1) LIVEKIT_LAN_IP — адрес сервера в локальной сети, когда клиенты должны получать и его, и публичный (сервер за NAT). В контейнере Docker
#    «внутренний» адрес для LiveKit — адрес контейнера (172.x), недоступный клиентам; поэтому адрес сервера добавляется на интерфейс
#    контейнера (/32, без маршрутов) — LiveKit объявляет его вместе с публичным (advertise_internal_ip), а пакеты на него приходят через
#    опубликованные порты Docker, как и сейчас. Нужна возможность NET_ADMIN (compose.yml); без неё — предупреждение, остаётся публичный адрес.
# 2) Режим nat-stun (use_external_ip: true): публичный адрес определяется здесь, при каждом запуске, по первому ответившему серверу STUN из
#    LIVEKIT_STUN_SERVERS и встроенного списка (Cloudflare, Twilio, Google — какой-то из них может быть недоступен у провайдера или через
#    VPN), и передаётся LiveKit явным node_ip. Сам LiveKit при use_external_ip перебирает только свой список, без ответа не запускается, а
#    найденный адрес проверяет «петлёй» через маршрутизатор и без неё молча объявляет прежний node_ip. Никто не ответил — последний
#    известный адрес (node_ip из .env).
# 3) Адрес меняется на ходу (VPN, другой провайдер, маршрут): каждые LIVEKIT_IP_RECHECK секунд адрес определяется снова; новый адрес
#    подтвердился три раза подряд — LiveKit перезапускается (контейнер поднимается заново, restart: unless-stopped) с новым адресом.
set -eu
say() { echo "peregovorka-entrypoint: $*" >&2; }
BUILTIN_STUN="stun.cloudflare.com:3478,global.stun.twilio.com:3478,stun.l.google.com:19302"

if [ -n "${LIVEKIT_LAN_IP:-}" ]; then
  if ip -o -4 addr show | grep -q " ${LIVEKIT_LAN_IP}/"; then :
  else
    dev="$(ip -o -4 route show default | awk '{print $5; exit}')"
    if ip addr add "${LIVEKIT_LAN_IP}/32" dev "${dev:-eth0}" 2>/dev/null; then say "адрес локальной сети ${LIVEKIT_LAN_IP} объявляется клиентам"
    else say "ПРЕДУПРЕЖДЕНИЕ: не удалось добавить ${LIVEKIT_LAN_IP} (нет NET_ADMIN?) — клиенты локальной сети получат только публичный адрес"; fi
  fi
fi

stun_ip() {   # $1 = host:port → внешний IPv4 из ответа STUN Binding (XOR-MAPPED-ADDRESS или MAPPED-ADDRESS); пусто — нет ответа
  { printf '\000\001\000\000\041\022\244\102peregovorka1' | timeout 3 nc -u -w 2 "${1%:*}" "${1##*:}"; } 2>/dev/null | od -An -tu1 -v | awk '
    { for (i = 1; i <= NF; i++) b[n++] = $i + 0 }
    END {
      if (n < 20 || b[0] != 1 || b[1] != 1) exit
      for (i = 20; i + 4 <= n; i = v + l + (4 - l % 4) % 4) {
        t = b[i] * 256 + b[i+1]; l = b[i+2] * 256 + b[i+3]; v = i + 4
        if (l < 8 || b[v+1] != 1) continue
        if (t == 32) { printf "%d.%d.%d.%d\n", xor(b[v+4], 33), xor(b[v+5], 18), xor(b[v+6], 164), xor(b[v+7], 66); exit }
        if (t == 1 && m == "") m = b[v+4] "." b[v+5] "." b[v+6] "." b[v+7]
      }
      if (m != "") print m
    }'
}
public_ip() {   # «адрес сервер» по первому ответившему серверу STUN
  for s in $(printf '%s,%s' "${LIVEKIT_STUN_SERVERS:-}" "$BUILTIN_STUN" | tr ',' '\n' | awk 'NF && !seen[$1]++'); do
    a="$(stun_ip "$s" || true)"
    if [ -n "$a" ]; then echo "$a $s"; return 0; fi
  done
  return 1
}

watch_ip=""
case "${LIVEKIT_CONFIG:-}" in
  *"use_external_ip: true"*)
    prev="$(printf '%s\n' "$LIVEKIT_CONFIG" | sed -n 's/^[[:space:]]*node_ip:[[:space:]]*//p' | head -1)"
    if r="$(public_ip)"; then
      ip="${r%% *}"
      say "публичный адрес $ip определён через STUN (${r#* })$([ "$ip" = "$prev" ] || echo "; прежний в настройках: ${prev:-—}")"
    else
      ip="$prev"
      say "ПРЕДУПРЕЖДЕНИЕ: ни один сервер STUN не ответил (${LIVEKIT_STUN_SERVERS:-}, $BUILTIN_STUN) — объявляется последний известный адрес ${ip:-—}"
    fi
    LIVEKIT_CONFIG="$(printf '%s\n' "$LIVEKIT_CONFIG" | sed -e 's/use_external_ip: true/use_external_ip: false/' \
      -e "s/^\([[:space:]]*node_ip:\).*/\1 $ip/")"; export LIVEKIT_CONFIG
    watch_ip="$ip" ;;
esac

if [ -z "$watch_ip" ] || [ "${LIVEKIT_IP_RECHECK:-300}" -le 0 ]; then exec /usr/local/bin/livekit-server "$@"; fi

/usr/local/bin/livekit-server "$@" &
lk=$!
trap 'kill -TERM "$lk" 2>/dev/null' TERM INT
(
  seen=""; n=0
  while sleep "${LIVEKIT_IP_RECHECK:-300}"; do
    kill -0 "$lk" 2>/dev/null || exit 0
    now="$(public_ip)" || continue           # STUN не ответил — ничего не меняем
    now="${now%% *}"
    if [ "$now" = "$watch_ip" ]; then n=0; continue; fi
    if [ "$now" = "$seen" ]; then n=$((n + 1)); else seen="$now"; n=1; fi
    if [ "$n" -ge 3 ]; then
      say "публичный адрес сменился: $watch_ip → $now (VPN, провайдер или маршрут) — LiveKit перезапускается, клиенты переподключатся"
      kill -TERM "$lk"; exit 0
    fi
  done
) &
w=$!
rc=0
wait "$lk" || rc=$?
while kill -0 "$lk" 2>/dev/null; do wait "$lk" || rc=$?; done   # wait прерван сигналом — дождаться завершения LiveKit
kill "$w" 2>/dev/null || true
exit "$rc"
