#!/usr/bin/env bash
# topo.sh — испытательная сеть для медиасоединений LiveKit (только Docker, без root на хосте). Адреса — документационные и частные диапазоны.
#   «интернет»   mn-wan    192.0.2.0/24    : STUN 192.0.2.3 (192.0.2.4 — «молчащий» STUN), домашний маршрутизатор 192.0.2.60, публичный адрес офиса 192.0.2.2,
#                                            сервер с публичным адресом на интерфейсе 192.0.2.10 (сценарий direct)
#   дом          mn-home   192.168.77.0/24 : внешний клиент 192.168.77.20 за NAT домашнего маршрутизатора 192.168.77.2
#   офис (LAN)   mn-lan    10.77.0.0/24    : маршрутизатор 10.77.0.2 (NAT + проброс портов на сервер), сервер 10.77.0.10, клиент 10.77.0.20
#   оператор     mn-cgn    100.64.0.0/24   : (сценарий cgnat) NAT оператора 100.64.0.3 ↔ 192.0.2.2, внешний адрес офиса 100.64.0.2
#   Docker сервера mn-inner 172.31.77.0/24 : шлюз-сервер 172.31.77.2, контейнер LiveKit 172.31.77.10 (как публикация портов Docker)
# У клиентов — межсетевой экран узла (как Windows): входящие только в ответ на свои исходящие. «Петли» NAT на маршрутизаторе нет.
#   mn_up nat|direct|cgnat [nofwd] — поднять; mn_down — убрать. Функции подключает run.sh.
set -euo pipefail
MN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MN_REPO="$(cd "$MN_DIR/../../.." && pwd)"
MN_LK_IMAGE="${MN_LK_IMAGE:-mn-livekit}"
TCP=17881; UDP=17882; SIG=7880

# имена встроенных серверов STUN в модели: Google «молчит» (192.0.2.4 — там никого), Cloudflare и Twilio отвечают (STUN стенда 192.0.2.3)
MN_HOSTS=(--add-host stun.l.google.com:192.0.2.4 --add-host stun1.l.google.com:192.0.2.4 --add-host stun.cloudflare.com:192.0.2.3
          --add-host global.stun.twilio.com:192.0.2.3)
# _c имя сеть адрес образ [команда…]
_c() { docker run -d --name "$1" --hostname "$1" --cap-add NET_ADMIN --sysctl net.ipv4.ip_forward=1 --sysctl net.ipv4.conf.all.rp_filter=0 \
  "${MN_HOSTS[@]}" -v "$MN_DIR:/t:ro" --network "$2" --ip "$3" "${@:4}" >/dev/null; }
_n() { docker network connect --ip "$3" "$2" "$1"; }
x() { docker exec "$1" sh -c "$2"; }
ifc() { echo "ifc() { ip -o -4 addr show | awk -v p=\"\$1\" 'index(\$4,p)==1{print \$2; exit}'; }"; }

mn_down() {
  docker rm -f mn-stun mn-rt mn-cr mn-srv mn-lk mn-ext mn-lan mn-home-rt >/dev/null 2>&1 || true
  for n in mn-wan mn-lan mn-inner mn-home mn-cgn; do docker network rm "$n" >/dev/null 2>&1 || true; done
}

# $1 = nat | direct | cgnat; $2 = nofwd — на маршрутизаторе офиса не проброшены порты медиа
mn_up() {
  mn_down
  docker network create --subnet 192.0.2.0/24 mn-wan >/dev/null
  docker network create --subnet 192.168.77.0/24 mn-home >/dev/null
  docker network create --subnet 172.31.77.0/24 mn-inner >/dev/null
  _c mn-stun mn-wan 192.0.2.3 mn-net python3 /t/stunserver.py 3478
  _c mn-home-rt mn-wan 192.0.2.60 mn-net sleep infinity; _n mn-home-rt mn-home 192.168.77.2
  x mn-home-rt "$(ifc); ip route del default || true; iptables -t nat -A POSTROUTING -s 192.168.77.0/24 -o \$(ifc 192.0.2.) -j MASQUERADE"
  _c mn-ext mn-home 192.168.77.20 mn-client sleep infinity
  x mn-ext "ip route replace default via 192.168.77.2"
  MN_CLIENTS="mn-ext"
  if [ "$1" = direct ]; then
    _c mn-srv mn-wan 192.0.2.10 -v "$MN_REPO:/repo:ro" mn-net sleep infinity; _n mn-srv mn-inner 172.31.77.2
    x mn-srv "ip route del default || true"
    SRV_IP=192.0.2.10
  else
    docker network create --subnet 10.77.0.0/24 mn-lan >/dev/null
    local rt_wan=192.0.2.2 rt_pfx=192.0.2.
    if [ "$1" = cgnat ]; then
      docker network create --subnet 100.64.0.0/24 mn-cgn >/dev/null
      _c mn-cr mn-wan 192.0.2.2 mn-net sleep infinity; _n mn-cr mn-cgn 100.64.0.3
      x mn-cr "$(ifc); ip route del default || true; iptables -t nat -A POSTROUTING -s 100.64.0.0/24 -o \$(ifc 192.0.2.) -j MASQUERADE"
      rt_wan=100.64.0.2; rt_pfx=100.64.0.
      _c mn-rt mn-cgn "$rt_wan" mn-net sleep infinity
      x mn-rt "ip route replace default via 100.64.0.3"
    else
      _c mn-rt mn-wan "$rt_wan" mn-net sleep infinity
      x mn-rt "ip route del default || true"
    fi
    _n mn-rt mn-lan 10.77.0.2
    x mn-rt "$(ifc); W=\$(ifc $rt_pfx); iptables -t nat -A POSTROUTING -s 10.77.0.0/24 -o \$W -j MASQUERADE"
    local fw="tcp:$SIG"; [ "${2:-}" = nofwd ] || fw="$fw tcp:$TCP udp:$UDP"     # сигнализация (в работе — HTTPS 443) проброшена всегда
    x mn-rt "$(ifc); W=\$(ifc $rt_pfx); for p in $fw; do iptables -t nat -A PREROUTING -i \$W -p \${p%:*} --dport \${p#*:} -j DNAT --to-destination 10.77.0.10; done"
    _c mn-srv mn-lan 10.77.0.10 -v "$MN_REPO:/repo:ro" mn-net sleep infinity; _n mn-srv mn-inner 172.31.77.2
    x mn-srv "ip route replace default via 10.77.0.2"
    SRV_IP=10.77.0.10
    _c mn-lan mn-lan 10.77.0.20 mn-client sleep infinity
    x mn-lan "ip route replace default via 10.77.0.2"
    MN_CLIENTS="mn-ext mn-lan"
  fi
  for c in $MN_CLIENTS; do x "$c" "iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT; iptables -A INPUT -i lo -j ACCEPT; iptables -P INPUT DROP"; done
  # без выхода в настоящий интернет: встроенные серверы STUN (Cloudflare, Twilio, Google) не должны отвечать ни сервису, ни LiveKit стенда
  x mn-srv "for ch in OUTPUT FORWARD; do for n in 127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 192.0.2.0/24 100.64.0.0/10; do
    iptables -A \$ch -d \$n -j ACCEPT; done; iptables -A \$ch -j DROP; done"
  # сервер: опубликованные порты → контейнер LiveKit (как Docker), исходящие из сети Docker — с маскарадингом
  x mn-srv "for p in tcp:$TCP udp:$UDP tcp:$SIG; do for ch in PREROUTING OUTPUT; do iptables -t nat -A \$ch -d $SRV_IP -p \${p%:*} --dport \${p#*:} -j DNAT --to-destination 172.31.77.10; done; done
    iptables -t nat -A POSTROUTING -s 172.31.77.0/24 ! -d 172.31.77.0/24 -j MASQUERADE"
}

# LiveKit с окружением из compose (JSON-объект переменных в файле $1): как в продукте, плюс маршрут через «сервер»
mn_lk() {
  docker rm -f mn-lk >/dev/null 2>&1 || true
  local args=()
  while IFS= read -r -d '' kv; do args+=(-e "$kv"); done < <(python3 -c 'import json,sys; [sys.stdout.write(f"{k}={v}\0") for k,v in json.load(open(sys.argv[1])).items()]' "$1")
  docker run -d --name mn-lk --hostname mn-lk --cap-add NET_ADMIN --init --restart unless-stopped "${MN_HOSTS[@]}" --network mn-inner --ip 172.31.77.10 "${args[@]}" \
    --entrypoint sh "$MN_LK_IMAGE" -c "ip route replace default via 172.31.77.2; exec /usr/local/bin/peregovorka-livekit" >/dev/null
  for _ in $(seq 60); do x mn-lk "wget -q -O /dev/null http://127.0.0.1:$SIG/" 2>/dev/null && return 0; sleep 1; done
  docker logs mn-lk 2>&1 | tail -20; return 1
}

# клиент: $1 контейнер, $2 адрес сигнализации, $3 identity, $4 секунды (публикует звук и видео и принимает чужие)
mn_client() { docker exec "$1" python /t/mnclient.py --url "ws://$2:$SIG" --identity "$3" --role pub --seconds "${4:-15}" 2>/dev/null; }
