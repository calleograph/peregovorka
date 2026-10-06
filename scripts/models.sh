#!/usr/bin/env bash
# models.sh — подготовка весов ASR-модели в ${DATA_ROOT}/models/gigaam. Модели в Git НЕ хранятся.
#
#   scripts/models.sh [--env FILE] [--model v3_e2e_rnnt]
#                     [--from-dir КАТАЛОГ]   # закрытая сеть: готовые файлы модели
#
# Нужные файлы: <model>.ckpt и <model>_tokenizer.model.
#  * без --from-dir: скачивание с GIGAAM_MODEL_BASE_URL (нужен доступ в интернет);
#  * с --from-dir: копирование из заранее скачанного каталога.
# Контрольные суммы НЕ проверяются (upstream может обновлять файлы): проверяется только, что файл скачан/скопирован
# целиком и не пуст. Повторный запуск не качает заново, если оба файла уже на месте.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODEL=""; FROM_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --from-dir) FROM_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"
require_vars DATA_ROOT
msg="$(validate_local_dir "$DATA_ROOT" DATA_ROOT)" || die "$msg"
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

if have "$CKPT" "$MIN_CKPT_BYTES" && have "$TOK" 1; then
  ok "Модель $MODEL уже на месте ($(fsize "$CKPT") байт) — повторная загрузка не требуется."
  exit 0
fi

fetch() { # fetch FILE_NAME DEST_PATH MIN_BYTES
  local name="$1" path="$2" min="$3" size
  if [ -n "$FROM_DIR" ]; then
    [ -f "$FROM_DIR/$name" ] || die "В $FROM_DIR нет файла $name"
    info "Копирование $FROM_DIR/$name → $path"
    cp -f "$FROM_DIR/$name" "$path.part"
  else
    command -v curl >/dev/null || die "curl не найден"
    info "Загрузка $BASE_URL/$name → $path"
    curl -fL --retry 3 -C - -o "$path.part" "$BASE_URL/$name" \
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

have "$CKPT" "$MIN_CKPT_BYTES" || { rm -f "$CKPT"; fetch "$MODEL.ckpt" "$CKPT" "$MIN_CKPT_BYTES"; }
have "$TOK" 1 || { rm -f "$TOK"; fetch "${MODEL}_tokenizer.model" "$TOK" 1; }
printf '%s prepared=%s size=%s\n' "$MODEL" "$(date -Is)" "$(fsize "$CKPT")" > "$DEST/$MODEL.version"
ok "Модель $MODEL готова: $DEST"
