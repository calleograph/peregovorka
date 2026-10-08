#!/usr/bin/env bash
# asr-bench.sh — бенчмарк распознавания в контейнере asr: подбор ASR_CPU_THREADS по ЗАДЕРЖКЕ, а не по загрузке CPU.
#   scripts/asr-bench.sh --wav /путь/на/хосте/речь.wav [--threads 2,4] [--interop 1] [--repeat 5] [--env FILE]
#   scripts/asr-bench.sh --synthetic      (только проверка работоспособности; шум нерепрезентативен)
# Берите реальную русскую речь 5–15 секунд. Не запускайте на загруженном сервере. Бенчмарк грузит ВТОРУЮ копию модели
# во временном процессе внутри контейнера asr (нужно ~1–2 ГБ RAM сверх рабочей); рабочий сервис не останавливается.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
WAV=""; ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --wav) WAV="$2"; shift 2 ;;
    --synthetic|--threads|--interop|--repeat|--seconds) ARGS+=("$1"); case "$1" in --synthetic) shift ;; *) ARGS+=("$2"); shift 2 ;; esac ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
if [ -n "$WAV" ]; then
  [ -r "$WAV" ] || die "Файл не найден: $WAV"
  cid="$(dc ps -q asr | head -1)"; [ -n "$cid" ] || die "Контейнер asr не запущен"
  docker cp "$WAV" "$cid:/tmp/bench.wav" >/dev/null || die "Не удалось скопировать файл в контейнер"
  ARGS+=(--wav /tmp/bench.wav)
fi
[ "${#ARGS[@]}" -gt 0 ] || die "Укажите --wav ФАЙЛ (или --synthetic)"
log "== Бенчмарк ASR: сравнение числа потоков (сейчас в .env: ASR_CPU_THREADS=${ASR_CPU_THREADS:-0}, ASR_INTEROP_THREADS=${ASR_INTEROP_THREADS:-0}) =="
dc exec -T asr python -m app.bench "${ARGS[@]}"
rc=$?
[ -n "$WAV" ] && dc exec -T asr rm -f /tmp/bench.wav >/dev/null 2>&1
exit $rc
