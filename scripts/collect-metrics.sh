#!/usr/bin/env bash
# collect-metrics.sh — запись нагрузки сервера на время приёмочного теста (CPU/RAM контейнеров и хоста) и итоговая сводка.
#   scripts/collect-metrics.sh [--minutes 30] [--interval 10] [--out FILE.csv] [--env FILE]
# Запускайте на сервере параллельно с приёмочным сценарием (docs/ACCEPTANCE_TEST.md). По завершении печатает max/avg CPU и
# max RAM по каждому сервису проекта. Задержки ASR, время входа и потери пакетов смотрите в админке «Состояние системы»
# и в диагностическом отчёте. Только чтение; чужие контейнеры не затрагиваются.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
MIN=30; INT=10; OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --minutes) MIN="$2"; shift 2 ;;
    --interval) INT="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
[[ "$MIN" =~ ^[0-9]+$ && "$INT" =~ ^[0-9]+$ && "$INT" -ge 2 ]] || die "--minutes и --interval — целые числа (interval ≥ 2)"
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
OUT="${OUT:-$REPO_ROOT/metrics-$(date +%Y%m%d-%H%M%S).csv}"
ids="$(dc ps -q 2>/dev/null)"; [ -n "$ids" ] || die "Контейнеры проекта не найдены"
echo "ts,service,cpu_pct,mem_bytes,load1" > "$OUT"
end=$(( $(date +%s) + MIN*60 ))
log "Сбор метрик ${MIN} мин, шаг ${INT} с → $OUT (Ctrl+C — остановить и показать сводку)"
summary() {
  log; log "== Сводка (${OUT}) =="
  awk -F, 'NR>1 && $2!="" { n[$2]++; c[$2]+=$3; if($3>cm[$2])cm[$2]=$3; if($4>mm[$2])mm[$2]=$4; if($5>lm)lm=$5 }
           END { printf "%-12s %10s %10s %12s\n","сервис","CPU avg%","CPU max%","RAM max, МБ";
                 for(s in n) printf "%-12s %10.1f %10.1f %12.0f\n", s, c[s]/n[s], cm[s], mm[s]/1048576;
                 printf "load1 max: %.2f\n", lm }' "$OUT"
}
trap 'summary; exit 0' INT TERM
to_bytes() { # "1.2GiB" → байты
  awk -v s="$1" 'BEGIN{ m=1; if(s~/GiB/)m=1073741824; else if(s~/MiB/)m=1048576; else if(s~/KiB/)m=1024; else if(s~/kB/)m=1000; sub(/[A-Za-z]+/,"",s); printf "%.0f", s*m }'
}
while [ "$(date +%s)" -lt "$end" ]; do
  load="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)"
  # shellcheck disable=SC2086
  docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' $ids 2>/dev/null | while read -r name cpu mem _; do
    svc="${name#"${COMPOSE_PROJECT_NAME}"-}"; svc="${svc%-[0-9]*}"
    echo "$(date +%s),${svc},${cpu%\%},$(to_bytes "$mem"),${load}" >> "$OUT"
  done
  sleep "$INT"
done
summary
