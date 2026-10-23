#!/usr/bin/env bash
# Нагрузка на медиасервер LiveKit: сценарии презентации и совещаний через `lk load-test` (livekit-cli), замер CPU/памяти контейнера LiveKit раз в 2 с.
# Нужны Docker и lk (https://github.com/livekit/livekit-cli). Использование:
#   LK=/путь/к/lk tests/perf/livekit_load.sh <каталог результатов> "<сценарий> …"
#   сценарий presentation:<зрителей>          — 1 выступающий (видео 720p simulcast + звук), остальные только принимают (layout speaker)
#   сценарий meetings:<комнат>:<в комнате>    — в каждой комнате <в комнате> говорящих + столько же слушателей (в lk публикующий не принимает), двое с камерой, 3x3
# Генератор нагрузки и LiveKit на одной машине: результат — нижняя оценка (генератор отнимает процессор у сервера).
set -uo pipefail
OUT="${1:?каталог результатов}"; PLAN="${2:-presentation:100}"
LK="${LK:-lk}"; IMG="${LIVEKIT_IMAGE:-livekit/livekit-server:v1.13.10}"; DUR="${DURATION:-60s}"
KEY=loadkey; SECRET=loadsecret-loadsecret-loadsecret-0123456789; URL=ws://127.0.0.1:7980
mkdir -p "$OUT"
docker rm -f lk-load >/dev/null 2>&1
docker run -d --name lk-load --network host "$IMG" --config-body "$(printf 'port: 7980\nbind_addresses: [127.0.0.1]\nrtc:\n  tcp_port: 7981\n  port_range_start: 51000\n  port_range_end: 59000\n  use_external_ip: false\n  node_ip: 127.0.0.1\nkeys:\n  %s: %s\nlogging:\n  level: warn\n' "$KEY" "$SECRET")" >/dev/null
for _ in $(seq 1 30); do curl -fs localhost:7980/ >/dev/null 2>&1 && break; sleep 1; done
sample() {   # CPU (% одного ядра) и память контейнера LiveKit каждые 2 с
  while :; do docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' lk-load 2>/dev/null | awk -v t="$(date +%s)" '{gsub("%","",$1); print t, $1, $2}'; sleep 2; done
}
for item in $PLAN; do
  IFS=: read -r KIND A B <<<"$item"
  sample > "$OUT/stats-$item.txt" & SP=$!
  case "$KIND" in
    presentation)
      "$LK" load-test --url "$URL" --api-key "$KEY" --api-secret "$SECRET" --room "pres-$A" --video-publishers 1 --audio-publishers 1 --subscribers "$A" \
        --video-resolution high --layout speaker --num-per-second 20 --duration "$DUR" > "$OUT/lk-$item.txt" 2>&1 ;;
    meetings)
      for r in $(seq 1 "$A"); do
        "$LK" load-test --url "$URL" --api-key "$KEY" --api-secret "$SECRET" --room "meet-$r" --video-publishers 2 --audio-publishers "$B" --subscribers "$B" \
          --video-resolution medium --layout 3x3 --num-per-second 10 --duration "$DUR" > "$OUT/lk-$item-$r.txt" 2>&1 &
      done
      wait $(jobs -p | grep -v "^$SP$") 2>/dev/null ;;
  esac
  kill $SP 2>/dev/null; wait $SP 2>/dev/null
  awk 'NR>3 {c+=$2; n++; if ($2>m) m=$2} END {printf "%s: LiveKit CPU среднее %.0f%%, максимум %.0f%% (100%% = одно ядро)\n", "'"$item"'", c/n, m}' "$OUT/stats-$item.txt"
  tail -1 "$OUT/stats-$item.txt" | awk '{print "  память в конце:", $3}'
done
docker rm -f lk-load >/dev/null 2>&1
