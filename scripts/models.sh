#!/usr/bin/env bash
# models.sh — подготовка весов ASR-модели в ${DATA_ROOT}/models/gigaam. Модели в Git НЕ хранятся.
#
#   scripts/models.sh [--env FILE] [--model v3_e2e_rnnt]
#                     [--from-dir КАТАЛОГ]   # закрытая сеть: готовые файлы модели
#                     [--gguf] [--skip-full] # дополнительно подготовить квантованную модель Q5_K_M (GGUF)
#                     [--llm]                # дополнительно подготовить локальную языковую модель (Qwen3 0.6B Q4_K_M) — см. ниже
#                     [--llm-only]           # только локальная LLM (ASR-модели не трогать)
#                     [--soft]               # (с --llm/--llm-only) нет интернета/сбой — предупредить и завершиться с кодом 0
#                     [--force]              # (с --llm/--llm-only) удалить файл LLM и скачать заново (повреждение)
#                     [--check]              # (с --llm-only) только проверить файл LLM: код 0 — на месте и валиден
#
# Локальная LLM (scripts/lib/llmlib.sh): ${DATA_ROOT}/models/llm/<файл>.gguf, проверка размера и SHA-256, докачка, существующий валидный файл не качается.
#
# Нужные файлы: <model>.ckpt и <model>_tokenizer.model (Full, PyTorch) и, по желанию, gigaam-v3-e2e-rnnt-Q5_K_M.gguf (GGUF).
# Обе модели лежат в одном каталоге одновременно; какая из них активна, выбирается в админке (Интеграции → Распознавание речи).
# GGUF: с --from-dir копируется файл из каталога; без него нужен прямой URL в GGUF_MODEL_URL (в .env или окружении).
#  * без --from-dir: скачивание с GIGAAM_MODEL_BASE_URL (нужен доступ в интернет);
#  * с --from-dir: копирование из заранее скачанного каталога.
# Контрольные суммы НЕ проверяются (upstream может обновлять файлы): проверяется только, что файл скачан/скопирован
# целиком и не пуст. Повторный запуск не качает заново, если оба файла уже на месте.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODEL=""; FROM_DIR=""; GGUF=0; SKIP_FULL=0; LLM=0; LLM_ONLY=0; LLM17=0; SOFT=0; FORCE=0; CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --from-dir) FROM_DIR="$2"; shift 2 ;;
    --gguf) GGUF=1; shift ;;
    --skip-full) SKIP_FULL=1; shift ;;
    --llm) LLM=1; shift ;;
    --llm-only) LLM=1; LLM_ONLY=1; shift ;;
    --llm17-only) LLM17=1; shift ;;       # Qwen3 1.7B (необязательная модель): скачать и проверить, остальное не трогать
    --soft) SOFT=1; shift ;;
    --force) FORCE=1; shift ;;
    --check) CHECK=1; shift ;;
    -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"
require_vars DATA_ROOT
msg="$(validate_local_dir "$DATA_ROOT" DATA_ROOT)" || die "$msg"
# ---- Qwen3 1.7B (необязательная локальная модель)
if [ "$LLM17" = 1 ]; then
  if [ "$CHECK" = 1 ]; then st="$(llm17_model_state)"; log "Qwen3 1.7B ($LLM17_FILE): $(llm_state_text "$st")"; [ "$st" = ok ]; exit $?; fi
  if [ "$FORCE" = 1 ]; then llm17_model_fetch --force ${FROM_DIR:+--from-dir "$FROM_DIR"}; else llm17_model_fetch ${FROM_DIR:+--from-dir "$FROM_DIR"}; fi
  exit $?
fi
# ---- локальная LLM (отдельно от моделей распознавания речи)
if [ "$LLM" = 1 ]; then
  if [ "$CHECK" = 1 ]; then
    st="$(llm_model_state)"; log "Локальная LLM ($(llm_file_name)): $(llm_state_text "$st")"; [ "$st" = ok ]; exit $?
  fi
  rc=0
  if [ "$SOFT" = 1 ]; then llm_prepare soft || rc=$?
  elif [ "$FORCE" = 1 ]; then llm_model_fetch --force ${FROM_DIR:+--from-dir "$FROM_DIR"} || rc=$?
  else llm_model_fetch ${FROM_DIR:+--from-dir "$FROM_DIR"} || rc=$?; fi
  [ "$LLM_ONLY" = 1 ] && exit "$rc"
  [ "$rc" -eq 0 ] || [ "$SOFT" = 1 ] || exit "$rc"
fi

MODEL="${MODEL:-${ASR_MODEL_NAME:-v3_e2e_rnnt}}"
[[ "$MODEL" =~ ^[A-Za-z0-9_]+$ ]] || die "Недопустимое имя модели: $MODEL"
BASE_URL="${GIGAAM_MODEL_BASE_URL:-https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM}"
DEST="$DATA_ROOT/models/gigaam"
CKPT="$DEST/$MODEL.ckpt"
TOK="$DEST/${MODEL}_tokenizer.model"
MIN_CKPT_BYTES=$((1024 * 1024))   # заведомо меньше любого реального чекпойнта: отсекает HTML-заглушки и обрывы

mkdir -p "$DEST"
info "Каталог моделей: $DEST"

fsize() { stat -c '%s' "$1" 2>/dev/null || wc -c < "$1"; }
have() { [ -f "$1" ] && [ "$(fsize "$1")" -ge "$2" ]; }

FULL_READY=0
if [ "$SKIP_FULL" = 0 ] && have "$CKPT" "$MIN_CKPT_BYTES" && have "$TOK" 1; then
  ok "Модель $MODEL уже на месте ($(fsize "$CKPT") байт) — повторная загрузка не требуется."
  FULL_READY=1
fi

fetch() { # fetch FILE_NAME DEST_PATH MIN_BYTES [URL]
  local name="$1" path="$2" min="$3" url size
  url="${4:-$BASE_URL/$name}"
  if [ -n "$FROM_DIR" ]; then
    [ -f "$FROM_DIR/$name" ] || die "В $FROM_DIR нет файла $name"
    info "Копирование $FROM_DIR/$name → $path"
    cp -f "$FROM_DIR/$name" "$path.part"
  else
    command -v curl >/dev/null || die "curl не найден"
    info "Загрузка $url → $path"
    curl -fL --retry 3 -C - -o "$path.part" "$url" \
      || die "Не удалось скачать $name (частично скачанное сохранено: $path.part — повторный запуск продолжит загрузку)"
  fi
  size="$(fsize "$path.part")"
  if [ "$size" -lt "$min" ]; then
    mv -f "$path.part" "$path.failed"
    die "Файл $name получился слишком маленьким ($size байт, ожидалось не менее $min). Сохранён для разбора: $path.failed"
  fi
  mv -f "$path.part" "$path"
  ok "$name: $size байт"
}

if [ "$SKIP_FULL" = 0 ] && [ "$FULL_READY" = 0 ]; then
  have "$CKPT" "$MIN_CKPT_BYTES" || { rm -f "$CKPT"; fetch "$MODEL.ckpt" "$CKPT" "$MIN_CKPT_BYTES"; }
  have "$TOK" 1 || { rm -f "$TOK"; fetch "${MODEL}_tokenizer.model" "$TOK" 1; }
  printf '%s prepared=%s size=%s\n' "$MODEL" "$(date -Is)" "$(fsize "$CKPT")" > "$DEST/$MODEL.version"
  ok "Модель $MODEL готова: $DEST"
fi

if [ "$GGUF" = 1 ]; then
  GGUF_NAME="${GGUF_MODEL_FILE:-gigaam-v3-e2e-rnnt-Q5_K_M.gguf}"
  [[ "$GGUF_NAME" =~ ^[A-Za-z0-9_.-]+\.gguf$ ]] || die "Недопустимое имя GGUF-файла: $GGUF_NAME"
  GG="$DEST/$GGUF_NAME"
  if have "$GG" "$MIN_CKPT_BYTES"; then
    ok "GGUF-модель $GGUF_NAME уже на месте ($(fsize "$GG") байт)."
  elif [ -n "$FROM_DIR" ]; then
    fetch "$GGUF_NAME" "$GG" "$MIN_CKPT_BYTES"
  elif [ -n "${GGUF_MODEL_URL:-}" ]; then
    fetch "$GGUF_NAME" "$GG" "$MIN_CKPT_BYTES" "$GGUF_MODEL_URL"
  elif [ "$GGUF_NAME" = "gigaam-v3-e2e-rnnt-Q5_K_M.gguf" ]; then
    # Публичный репозиторий квантованных моделей transcribe.cpp; хеш не проверяется (файлы могут обновляться)
    fetch "$GGUF_NAME" "$GG" "$MIN_CKPT_BYTES" "https://huggingface.co/handy-computer/gigaam-v3-e2e-rnnt-gguf/resolve/main/$GGUF_NAME"
  else
    die "Для GGUF укажите --from-dir КАТАЛОГ с файлом $GGUF_NAME либо прямой URL в GGUF_MODEL_URL. Файл можно просто положить в $DEST вручную."
  fi
  ok "GGUF-модель готова: $GG. Движок transcribe.cpp встроен в образ ASR (пакет transcribe-cpp); выбор модели — в админке."
fi
