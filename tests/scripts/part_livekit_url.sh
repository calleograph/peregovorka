# shellcheck shell=bash
# Подключается из run.sh: проверка публичного адреса LiveKit называет этап сбоя (DNS, TCP, TLS), установщик предупреждает о доверии к сертификату.
# Используются: ROOT, t, eq.

VL="$ROOT/scripts/lib/verifylib.sh"
export VL

t "ws_verdict 000: код curl 6 → DNS" bash -c 'set -u; source "$VL"; WS_RC=6; ws_verdict 000; [ "$WS_STATUS" = WARNING ] && [[ "$WS_NOTE" == DNS:* ]]'
t "ws_verdict 000: код curl 7 → TCP, порт закрыт" bash -c 'set -u; source "$VL"; WS_RC=7; ws_verdict 000; [[ "$WS_NOTE" == TCP:*закрыт* ]]'
t "ws_verdict 000: код curl 28 → TCP, нет ответа" bash -c 'set -u; source "$VL"; WS_RC=28; ws_verdict 000; [[ "$WS_NOTE" == TCP:*"нет ответа"* ]]'
t "ws_verdict 000: код curl 60 → TLS, последствие для звонков названо" bash -c 'set -u; source "$VL"; WS_RC=60; ws_verdict 000; [ "$WS_STATUS" = WARNING ] && [[ "$WS_NOTE" == TLS:*самоподписанный* ]] && [[ "$WS_NOTE" == *"звонки"*"не подключатся"* ]]'
t "ws_verdict 000: код curl 51 → сертификат на другое имя" bash -c 'set -u; source "$VL"; WS_RC=51; ws_verdict 000; [[ "$WS_NOTE" == TLS:*"другое имя"* ]]'
t "ws_verdict 000: код curl 35 → возможно, на порту нет TLS" bash -c 'set -u; source "$VL"; WS_RC=35; ws_verdict 000; [[ "$WS_NOTE" == *"не быть TLS"* ]]'
t "ws_verdict 000 без кода curl — прежнее общее сообщение, WARNING" bash -c 'set -u; source "$VL"; unset WS_RC; ws_verdict 000; [ "$WS_STATUS" = WARNING ] && [[ "$WS_NOTE" == "нет ответа"* ]]'
t "ws_upgrade_probe запоминает код возврата curl: закрытый порт → 000 и код 7" bash -c 'set -u; source "$VL"; ws_upgrade_probe "http://127.0.0.1:9/rtc" 3; [ "$WS_CODE" = 000 ] && [ "$WS_RC" = 7 ]'
t "ws_upgrade_probe: ответ получен → код возврата 0" bash -c '
  set -u; source "$VL"
  d="$(mktemp -d)"; printf "x" > "$d/rtc"
  ( cd "$d" && exec python3 -m http.server 0 --bind 127.0.0.1 >"$d/log" 2>&1 ) & pid=$!
  for _ in 1 2 3 4 5 6 7 8 9 10; do p="$(grep -o "port [0-9]*" "$d/log" 2>/dev/null | head -1 | cut -d" " -f2)"; [ -n "$p" ] && break; sleep 0.5; done
  [ -n "${p:-}" ] || { kill "$pid" 2>/dev/null; exit 0; }       # нет python3 или порта — проверка неприменима
  ws_upgrade_probe "http://127.0.0.1:$p/rtc" 3; kill "$pid" 2>/dev/null
  [ "$WS_RC" = 0 ] && [ "$WS_CODE" != 000 ]'
t "setup.sh: при самоподписанном сертификате названо, что звонкам нужно доверие к нему и что будет иначе" bash -c 'grep -q "доверенн" "$1/scripts/setup.sh" && grep -q "звонки не подключатся" "$1/scripts/setup.sh"' _ "$ROOT"
