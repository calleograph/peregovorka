#!/usr/bin/env bash
# models.sh — подготовка весов ASR-модели в ${DATA_ROOT}/models/gigaam. Модели в Git НЕ хранятся.
#
#   scripts/models.sh [--env FILE] [--model v3_e2e_rnnt]
#                     [--from-dir КАТАЛОГ]   # закрытая сеть: готовые файлы модели
#
# Нужные файлы: <model>.ckpt и <model>_tokenizer.model.
#  * без --from-dir: скачивание с GIGAAM_MODEL_BASE_URL (нужен доступ в интернет);
#  * с --from-dir: копирование из заранее скачанного каталога.
# Повторный запуск НЕ скачивает заново, если файл на месте и контрольная сумма
# совпадает (md5 известен для v3_e2e_rnnt; для остальных моделей проверяется
# только наличие и ненулевой размер, о чём выводится предупреждение).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODEL=""; FROM_DIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --from-dir) FROM_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"
require_vars DATA_ROOT
MODEL="${MODEL:-${ASR_MODEL_NAME:-v3_e2e_rnnt}}"
[[ "$MODEL" =~ ^[A-Za-z0-9_]+$ ]] || die "Недопустимое имя модели: $MODEL"
BASE_URL="${GIGAAM_MODEL_BASE_URL:-https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM}"
DEST="$DATA_ROOT/models/gigaam"
mkdir -p "$DEST"

# md5 весов из исходников GigaAM (gigaam/__init__.py, _MODEL_HASHES).
declare -A MD5=(
  [v3_e2e_rnnt]="2730de7545ac43ad256485a462b0a27a"
  [v3_e2e_ctc]="367074d6498f426d960b25f49531cf68"
  [v3_rnnt]="0fd2c9a1ff66abd8d32a3a07f7592815"
  [v3_ctc]="73413e7be9c6a5935827bfab5c0dd678"
)
CKPT="$DEST/$MODEL.ckpt"
TOK="$DEST/${MODEL}_tokenizer.model"
EXPECT="${MD5[$MODEL]:-}"

verify_ckpt() {
  [ -s "$1" ] || return 1
  if [ -n "$EXPECT" ]; then [ "$(md5sum "$1" | cut -d' ' -f1)" = "$EXPECT" ]; else return 0; fi
}

if verify_ckpt "$CKPT" && [ -s "$TOK" ]; then
  ok "Модель $MODEL уже подготовлена и проверена: $DEST — повторная загрузка не требуется."
  exit 0
fi
[ -n "$EXPECT" ] || warn "Для модели $MODEL нет эталонной md5 — проверяется только наличие файла."

fetch() { # fetch FILE_NAME DEST_PATH
  local name="$1" path="$2"
  if [ -n "$FROM_DIR" ]; then
    [ -f "$FROM_DIR/$name" ] || die "В $FROM_DIR нет файла $name"
    info "Копирование $FROM_DIR/$name"
    cp -f "$FROM_DIR/$name" "$path.part"
  else
    command -v curl >/dev/null || die "curl не найден"
    info "Загрузка $BASE_URL/$name"
    curl -fL --retry 3 -C - -o "$path.part" "$BASE_URL/$name" || { rm -f "$path.part"; die "Не удалось скачать $name"; }
  fi
  mv -f "$path.part" "$path"
}

if ! verify_ckpt "$CKPT"; then rm -f "$CKPT"; fetch "$MODEL.ckpt" "$CKPT"; fi
verify_ckpt "$CKPT" || { rm -f "$CKPT"; die "Контрольная сумма $MODEL.ckpt не совпала с ожидаемой ($EXPECT)"; }
[ -s "$TOK" ] || fetch "${MODEL}_tokenizer.model" "$TOK"
[ -s "$TOK" ] || die "Токенайзер пуст: $TOK"
printf '%s md5=%s prepared=%s\n' "$MODEL" "${EXPECT:-unknown}" "$(date -Is)" > "$DEST/$MODEL.version"
ok "Модель $MODEL готова: $DEST"
