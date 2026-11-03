#!/usr/bin/env bash
# run.sh — живое испытание медиасоединений LiveKit в модели сети (topo.sh): настоящие scripts/medianet.py (на «сервере» стенда),
# deployment/compose.yml (окружение LiveKit берётся из `docker compose config`) и образ deployment/livekit (entrypoint.sh).
# Клиенты — LiveKit Python SDK: публикуют звук (тон) и видео (кадры), принимают чужие; печатаются фактические ICE-кандидаты сервера
# и выбранная пара. Сценарии: tests/live/medianet/run.sh [имя…] (без имён — все). Итог — out/medianet/results.json.
# Нужен только Docker (без root на хосте). Образы: mn-net, mn-client (*.Dockerfile), mn-livekit (deployment/livekit).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ./topo.sh
OUT="${OUT:-$MN_REPO/tests/live/out/medianet}"; mkdir -p "$OUT"
[ "${MN_BUILD:-1}" = 1 ] && { docker build -q -t mn-net -f net.Dockerfile . >/dev/null; docker build -q -t mn-client -f client.Dockerfile . >/dev/null
  docker build -q -t mn-livekit "$MN_REPO/deployment/livekit" >/dev/null; }
FAILS=0; RESULTS="$OUT/results.jsonl"; : > "$RESULTS"
trap mn_down EXIT

# .env стенда из .env.example; $1 — файл, остальное — KEY=VALUE
mkenv() {
  local f="$1"; shift
  cp "$MN_REPO/.env.example" "$f"
  for kv in COMPOSE_PROJECT_NAME=mn LIVEKIT_TCP_PORT=$TCP LIVEKIT_UDP_PORT=$UDP LIVEKIT_STUN_SERVERS=192.0.2.3:3478 LIVEKIT_NODE_IP= \
            LIVEKIT_API_KEY=devkey LIVEKIT_API_SECRET=ssssssssssssssssssssssssssssssssssssssss "$@"; do
    python3 - "$f" "$kv" <<'PY'
import sys
p, kv = sys.argv[1], sys.argv[2]; k = kv.split("=", 1)[0]
lines = open(p).read().splitlines(); done = False
for i, l in enumerate(lines):
    if l.startswith(k + "=") and not done:
        lines[i] = kv; done = True
open(p, "w").write("\n".join(lines + ([] if done else [kv])) + "\n")
PY
  done
}
configure() {   # на «сервере» стенда: scripts/medianet.py configure --write; $1 — .env
  docker cp "$1" mn-srv:/tmp/.env
  x mn-srv "python3 -I /repo/scripts/medianet.py configure --env /tmp/.env --write" | tee "$1.configure.log"
  docker cp mn-srv:/tmp/.env "$1"
}
diag() { x mn-srv "python3 -I /repo/scripts/medianet.py check --env /tmp/.env --network-only --json" > "$1" || true; }
lkenv() {   # окружение контейнера livekit из настоящего compose.yml
  docker compose --env-file "$1" -f "$MN_REPO/deployment/compose.yml" config --format json 2>/dev/null \
    | python3 -c 'import json,sys; e=json.load(sys.stdin)["services"]["livekit"]["environment"]; json.dump({k:v for k,v in e.items() if k.startswith("LIVEKIT_")}, open(sys.argv[1],"w"))' "$2"
}
# expect ИМЯ_СЦЕНАРИЯ клиент ожидание(ok|fail) [протокол]
check_client() {
  python3 - "$@" <<'PY'
import json, sys
name, f, want, proto = sys.argv[1], sys.argv[2], sys.argv[3], (sys.argv[4] if len(sys.argv) > 4 else "")
j = json.load(open(f)); sel = (j.get("publisher") or {}).get("selected") or []
media = j["received"]["audio"] > 200 and j["received"]["video"] > 30
ok = not j["error"] and bool(sel) and media
good = ok if want == "ok" else not ok
if good and want == "ok" and proto:
    good = all(s.split(" ")[0] == proto for s in sel)
print(f"  {'✓' if good else '✗'} {j['identity']}: {'соединение' if ok else 'нет соединения'} (ожидалось {'соединение' if want == 'ok' else 'отказ'}"
      f"{', ' + proto if proto and want == 'ok' else ''}); принято звук {j['received']['audio']} кадр., видео {j['received']['video']} кадр.")
print(f"      кандидаты сервера: {', '.join((j.get('publisher') or {}).get('server_candidates') or []) or '—'}")
print(f"      выбранная пара: {'; '.join(sel) or '—'}" + (f"; ошибка: {j['error']}" if j["error"] else ""))
sys.exit(0 if good else 1)
PY
}
record() { python3 - "$@" >> "$RESULTS" <<'PY'
import json, sys
name, env, diagf, *clients = sys.argv[1:]
e = {l.split("=", 1)[0]: l.split("=", 1)[1] for l in open(env).read().splitlines() if "=" in l and not l.startswith("#")}
try: d = json.load(open(diagf))
except Exception: d = {"checks": []}
print(json.dumps({"scenario": name, "mode": e.get("LIVEKIT_MEDIA_MODE"), "node_ip": e.get("LIVEKIT_NODE_IP"), "public": e.get("LIVEKIT_PUBLIC_IP"),
                  "rtc_node_ip": e.get("LIVEKIT_RTC_NODE_IP"), "checks": {c["id"]: c["status"] for c in d.get("checks", [])},
                  "clients": [json.load(open(c)) for c in clients]}, ensure_ascii=False))
PY
}

# scenario ИМЯ ТОПОЛОГИЯ [nofwd|-] "KEY=VAL …" "ожидания: контейнер:identity:ok|fail[:протокол] …" [udp-blocked] [stun-down] [stale]
scenario() {
  local name="$1" topo="$2" fwd="$3" kvs="$4" exp="$5" opts="${6:-}" d="$OUT/$1"
  echo; echo "=== $name"
  mkdir -p "$d"; mn_up "$topo" "$([ "$fwd" = nofwd ] && echo nofwd || true)"
  [[ " $opts " == *" stun-down "* ]] && docker stop mn-stun >/dev/null
  # shellcheck disable=SC2086
  mkenv "$d/.env" $kvs
  configure "$d/.env"
  # stale: .env, записанный прежней версией до смены маршрута — адрес выхода через VPN и список STUN «только Google»; затем перезапуск LiveKit
  [[ " $opts " == *" stale "* ]] && sed -i -e 's/^LIVEKIT_RTC_NODE_IP=.*/LIVEKIT_RTC_NODE_IP=192.0.2.99/' \
    -e 's/^LIVEKIT_STUN_SERVERS=.*/LIVEKIT_STUN_SERVERS=stun.l.google.com:19302,stun1.l.google.com:19302/' "$d/.env"
  [[ " $opts " == *" rerun "* ]] && { cp "$d/.env" "$d/.env.first"; configure "$d/.env" >/dev/null
    cmp -s "$d/.env" "$d/.env.first" && echo "  ✓ повторный запуск configure .env не меняет" || { echo "  ✗ повторный запуск изменил .env"; diff "$d/.env.first" "$d/.env" || true; FAILS=$((FAILS+1)); }; }
  lkenv "$d/.env" "$d/livekit-env.json"
  mn_lk "$d/livekit-env.json"
  docker logs mn-lk 2>&1 | grep -E "peregovorka-entrypoint|NAT1To1|external IPs" | sed 's/^/  livekit: /' | cut -c1-220
  if [[ " $opts " == *" udp-blocked "* ]]; then
    x mn-ext "iptables -I OUTPUT -p udp --dport $UDP -j DROP"; docker ps --format '{{.Names}}' | grep -qx mn-lan && x mn-lan "iptables -I OUTPUT -p udp --dport $UDP -j DROP"
  fi
  local pids=() files=() e c id
  for e in $exp; do
    IFS=: read -r c id _ _ <<<"$e"
    local sig=192.0.2.2; [ "$topo" = direct ] && sig=192.0.2.10; [ "$c" = mn-lan ] && sig=10.77.0.10
    mn_client "$c" "$sig" "$id" 15 > "$d/$id.json" & pids+=($!); files+=("$d/$id.json")
  done
  wait "${pids[@]}" || true
  diag "$d/diag.json"
  for e in $exp; do
    IFS=: read -r c id want proto <<<"$e"
    check_client "$name" "$d/$id.json" "$want" "${proto:-}" || FAILS=$((FAILS+1))
  done
  python3 -c 'import json,sys; [print(f"  проверка {c["status"]:7} {c["title"]}: {c["detail"][:160]}") for c in json.load(open(sys.argv[1])).get("checks",[])]' "$d/diag.json" 2>/dev/null || true
  record "$name" "$d/.env" "$d/diag.json" "${files[@]}"
}
want_diag() {   # want_diag СЦЕНАРИЙ id статус — проверка из диагностики должна иметь этот статус
  python3 - "$@" <<'PY' || FAILS=$((FAILS+1))
import json, sys
name, cid, st = sys.argv[1:]
for l in open(__import__("os").environ["RESULTS"]):
    r = json.loads(l)
    if r["scenario"] == name:
        got = r["checks"].get(cid)
        print(f"  {'✓' if got == st else '✗'} диагностика {name}: {cid} = {got} (ожидалось {st})"); sys.exit(0 if got == st else 1)
sys.exit(1)
PY
}
export RESULTS
want_mode() { grep -q "\"scenario\": \"$1\", \"mode\": \"$2\"" "$RESULTS" && echo "  ✓ режим $1: $2" || { echo "  ✗ режим $1: ожидался $2"; FAILS=$((FAILS+1)); }; }

ALL="before direct nat nat-tcp manual-stundown stun-down nofwd cgnat legacy reroute"
lklog() { docker logs mn-lk 2>&1 | grep -c -- "$1" || true; }
reroute() {   # сервер за маршрутизатором, трафик переведён с VPN на провайдера: первый STUN (как Google) молчит, в .env адрес прежнего
              # маршрута; затем публичный адрес меняется ещё раз на ходу — LiveKit должен сам перейти на новый
  scenario reroute nat - "LIVEKIT_STUN_SERVERS=stun.l.google.com:19302,stun1.l.google.com:19302 LIVEKIT_IP_RECHECK=4" "mn-ext:ext:ok:udp mn-lan:lan:ok:udp" stale
  local d="$OUT/reroute"
  grep -q "LIVEKIT_STUN_SERVERS=stun.cloudflare.com" "$d/.env.configure.log" && echo "  ✓ обновление заменяет прежний список «только Google» встроенным" \
    || { echo "  ✗ прежний список STUN не заменён"; FAILS=$((FAILS+1)); }
  [ "$(lklog "публичный адрес 192.0.2.2 определён через STUN (stun.cloudflare.com:3478)")" -ge 1 ] && echo "  ✓ при запуске: Google молчит — адрес по Cloudflare, прежний 192.0.2.99 не объявлен" \
    || { echo "  ✗ при запуске адрес не определён по запасному STUN"; FAILS=$((FAILS+1)); }
  echo "  … маршрутизатор офиса получает новый внешний адрес 192.0.2.5 (192.0.2.2 больше не существует)"
  x mn-rt "$(ifc); W=\$(ifc 192.0.2.); ip addr add 192.0.2.5/32 dev \$W; ip addr del 192.0.2.2/24 dev \$W; ip route add 192.0.2.0/24 dev \$W src 192.0.2.5"
  local ok=""
  for _ in $(seq 90); do [ "$(lklog "публичный адрес 192.0.2.5 определён")" -ge 1 ] && x mn-lk "wget -q -O /dev/null http://127.0.0.1:$SIG/" 2>/dev/null && { ok=1; break; }; sleep 1; done
  docker logs mn-lk 2>&1 | grep -E "peregovorka-entrypoint" | sed 's/^/  livekit: /' | cut -c1-220
  [ -n "$ok" ] && echo "  ✓ LiveKit перезапустился сам с новым адресом" || { echo "  ✗ LiveKit не перешёл на новый адрес"; FAILS=$((FAILS+1)); return; }
  mn_client mn-ext 192.0.2.5 ext-after 15 > "$d/ext-after.json" & local p1=$!
  mn_client mn-lan 10.77.0.10 lan-after 15 > "$d/lan-after.json"; wait $p1 || true
  check_client reroute "$d/ext-after.json" ok udp || FAILS=$((FAILS+1))
  check_client reroute "$d/lan-after.json" ok udp || FAILS=$((FAILS+1))
  grep -q "192.0.2.5:$UDP" "$d/ext-after.json" && echo "  ✓ внешний клиент соединился через новый адрес 192.0.2.5" || { echo "  ✗ выбранная пара не через 192.0.2.5"; FAILS=$((FAILS+1)); }
}
for s in ${*:-$ALL}; do
  case "$s" in
    before)  # как прежняя конфигурация продукта: клиентам объявляется только адрес сервера в офисе (LIVEKIT_NODE_IP)
      scenario before nat - "LIVEKIT_PUBLIC_IP=off" "mn-ext:ext:fail mn-lan:lan:ok:udp mn-lan:lan2:ok:udp" ;;
    direct)
      scenario direct direct - "LIVEKIT_NODE_IP=192.0.2.10" "mn-ext:ext1:ok:udp mn-ext:ext2:ok:udp" rerun; want_mode direct direct ;;
    nat)
      scenario nat nat - "" "mn-ext:ext:ok:udp mn-lan:lan:ok:udp" rerun; want_mode nat nat-stun; want_diag nat nat ok ;;
    nat-tcp)
      scenario nat-tcp nat - "" "mn-ext:ext:ok:tcp mn-lan:lan:ok:tcp" udp-blocked ;;
    manual-stundown)
      scenario manual-stundown nat - "LIVEKIT_PUBLIC_IP=192.0.2.2" "mn-ext:ext:ok:udp mn-lan:lan:ok:udp" stun-down; want_mode manual-stundown nat-manual ;;
    stun-down)
      scenario stun-down nat - "" "mn-ext:ext:fail mn-lan:lan:ok:udp mn-lan:lan2:ok:udp" stun-down; want_mode stun-down lan; want_diag stun-down public warn ;;
    nofwd)
      scenario nofwd nat nofwd "" "mn-ext:ext:fail mn-lan:lan:ok:udp mn-lan:lan2:ok:udp"; want_mode nofwd nat-stun; want_diag nofwd external unknown ;;
    cgnat)
      scenario cgnat cgnat - "" "mn-lan:lan:ok:udp mn-lan:lan2:ok:udp"; want_diag cgnat nat fail ;;
    legacy)  # прежняя рекомендация документации: внешний адрес в LIVEKIT_NODE_IP — обновление переносит его в LIVEKIT_PUBLIC_IP
      scenario legacy nat - "LIVEKIT_NODE_IP=192.0.2.2 LIVEKIT_PUBLIC_IP=" "mn-ext:ext:ok:udp mn-lan:lan:ok:udp" rerun; want_mode legacy nat-manual ;;
    reroute) reroute ;;
    *) echo "неизвестный сценарий $s"; FAILS=$((FAILS+1)) ;;
  esac
done
python3 -c 'import json,sys; json.dump([json.loads(l) for l in open(sys.argv[1])], open(sys.argv[2],"w"), ensure_ascii=False, indent=1)' "$RESULTS" "$OUT/results.json"
echo; [ "$FAILS" = 0 ] && echo "medianet: все сценарии прошли ($OUT/results.json)" || { echo "medianet: ошибок $FAILS"; exit 1; }
