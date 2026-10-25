#!/usr/bin/env bash
# models.sh — подготовка весов ASR-модели в ${DATA_ROOT}/models/gigaam. Модели в Git НЕ хранятся.
#
#   scripts/models.sh [--env FILE] [--model v3_e2e_rnnt]
#                     [--from-dir КАТАЛОГ]   # закрытая сеть: готовые файлы модели
#                     [--skip-full]          # не трогать полную модель (например, только локальная LLM: --llm-only)
#                     [--llm]                # дополнительно подготовить локальную языковую модель (Qwen3 1.7B Q4_K_M) — см. ниже
#                     [--llm-only]           # только локальная LLM (ASR-модели не трогать)
#                     [--soft]               # (с --llm/--llm-only) нет интернета/сбой — предупредить и завершиться с кодом 0
#                     [--force]              # (с --llm/--llm-only) удалить файл LLM и скачать заново (повреждение)
#                     [--check]              # (с --llm-only) только проверить файл LLM: код 0 — на месте и валиден
#
# Локальная LLM (scripts/lib/llmlib.sh): ${DATA_ROOT}/models/llm/<файл>.gguf, проверка размера и SHA-256, докачка, существующий валидный файл не качается.
#
# Нужные файлы: <model>.ckpt и <model>_tokenizer.model (полная GigaAM v3 e2e RNNT, PyTorch). Квантованная модель снята с вооружения и не скачивается.
# Обе модели лежат в одном каталоге одновременно; какая из них активна, выбирается в админке (Интеграции → Распознавание речи).
#  * без --from-dir: скачивание с GIGAAM_MODEL_BASE_URL (нужен доступ в интернет);
#  * с --from-dir: копирование из заранее скачанного каталога.
# Контрольные суммы по умолчанию НЕ сверяются (upstream может обновлять файлы): проверяются размер (по Content-Length сервера и нижняя граница), что это не HTML-страница,
# и наличие обоих файлов. Хеши можно задать в .env: GIGAAM_CKPT_SHA256, GIGAAM_TOKENIZER_SHA256. Докачка, тайм-ауты, 4 попытки; причина сбоя печатается словами.
# После загрузки/копирования права внутри каталога моделей приводятся к читаемым для сервиса ASR (только этот каталог). Повторный запуск не качает заново, если оба файла на месте.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

MODEL=""; FROM_DIR=""; SKIP_FULL=0; LLM=0; LLM_ONLY=0; SOFT=0; FORCE=0; CHECK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --from-dir) FROM_DIR="$2"; shift 2 ;;
    --skip-full) SKIP_FULL=1; shift ;;
    --llm) LLM=1; shift ;;
    --llm-only) LLM=1; LLM_ONLY=1; shift ;;
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
MIN_CKPT_BYTES="$ASR_MIN_CKPT_BYTES"

mkdir -p "$DEST"
info "Каталог моделей: $DEST"

fsize() { stat -c '%s' "$1" 2>/dev/null || wc -c < "$1"; }
have() { [ -f "$1" ] && [ "$(fsize "$1")" -ge "$2" ]; }

# Причина сбоя curl по коду выхода — человеческим языком (нужна администратору в корпоративной сети).
curl_reason() {
  case "$1" in
    5|6) echo "не удаётся разрешить имя сервера или прокси (DNS)" ;;
    7) echo "соединение невозможно (нет маршрута, файрвол или прокси блокируют доступ)" ;;
    22) echo "сервер ответил ошибкой HTTP (адрес недоступен или файл удалён)" ;;
    28) echo "тайм-аут или слишком низкая скорость" ;;
    35|51|58|60|77|83) echo "ошибка TLS (корпоративная проверка HTTPS? нужен корневой сертификат организации в системе)" ;;
    56) echo "соединение оборвано" ;;
    *) echo "код curl $1" ;;
  esac
}

# Размер файла на сервере (Content-Length), если сервер его сообщает; иначе пусто. Код выхода при недоступности сервера — код curl.
remote_size() { # URL
  local h rc=0
  h="$(curl -sIL --connect-timeout 20 --max-time 60 "$1")" || { rc=$?; return "$rc"; }
  printf '%s\n' "$h" | tr -d '\r' | awk 'tolower($1)=="content-length:" {n=$2} END {if (n != "") print n}'
}

fail_fetch() { # имя url причина
  die "Не удалось скачать $1: $3.
    Адрес: $2
    Что делать: проверьте доступ к адресу с этого сервера (прокси — переменные HTTPS_PROXY/NO_PROXY), либо скачайте файлы на другом компьютере и подготовьте модель из каталога:
      scripts/models.sh --from-dir КАТАЛОГ        (нужны $MODEL.ckpt и ${MODEL}_tokenizer.model)
    Другой источник: GIGAAM_MODEL_BASE_URL в .env (например, внутреннее зеркало). Частично скачанное сохранено (*.part) — повторный запуск продолжит загрузку."
}

# Скачивание с докачкой: до 4 попыток; каждая — с тайм-аутами и повтором curl. Размер сверяется с Content-Length сервера, если он известен.
download() { # имя url путь.part
  local name="$1" url="$2" part="$3" want="" n rc=0 size
  want="$(remote_size "$url")" || fail_fetch "$name" "$url" "$(curl_reason "$?")"
  if [ -n "$want" ] && [ -f "$part" ] && [ "$(fsize "$part")" -gt "$want" ]; then warn "Временный файл больше файла на сервере — удаляю и начинаю заново"; rm -f "$part"; fi
  for n in 1 2 3 4; do
    [ "$n" -eq 1 ] || { warn "Попытка $n из 4…"; sleep 5; }
    rc=0
    curl -fL --connect-timeout 20 --speed-limit 10240 --speed-time 60 --retry 3 --retry-delay 5 --retry-connrefused -C - -o "$part" "$url" || rc=$?
    size="$(fsize "$part" 2>/dev/null || echo 0)"
    if [ "$rc" -eq 33 ]; then warn "Сервер не поддерживает докачку — начинаю заново"; rm -f "$part"; continue; fi
    # 416/22 при уже полностью скачанном файле — не ошибка
    if [ "$rc" -ne 0 ] && { [ -z "$want" ] || [ "$size" != "$want" ]; }; then continue; fi
    if [ -n "$want" ] && [ "$size" -lt "$want" ]; then warn "Скачано $size из $want байт — докачиваю"; continue; fi
    if [ -n "$want" ] && [ "$size" -gt "$want" ]; then warn "Файл больше ожидаемого — удаляю и скачиваю заново"; rm -f "$part"; continue; fi
    return 0
  done
  fail_fetch "$name" "$url" "$(curl_reason "$rc"); скачано $size${want:+ из $want} байт за 4 попытки"
}

check_sha() { # файл ожидаемый_sha256 — только если хеш задан в .env (upstream может обновлять файлы, поэтому по умолчанию не требуется)
  [ -z "$2" ] || [ "$2" = skip ] || [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$2" ]
}

fetch() { # fetch FILE_NAME DEST_PATH MIN_BYTES [SHA256]
  local name="$1" path="$2" min="$3" sha="${4:-}" url size
  url="$BASE_URL/$name"
  rm -f "$path.failed"
  if [ -n "$FROM_DIR" ]; then
    [ -f "$FROM_DIR/$name" ] || die "В $FROM_DIR нет файла $name (нужны $MODEL.ckpt и ${MODEL}_tokenizer.model)"
    [ -r "$FROM_DIR/$name" ] || die "Файл $FROM_DIR/$name не читается этим пользователем (владелец $(stat -c '%U' "$FROM_DIR/$name")). Выполните от root: sudo scripts/models.sh --from-dir $FROM_DIR"
    info "Копирование $FROM_DIR/$name → $path"
    cp -f "$FROM_DIR/$name" "$path.part"
  else
    command -v curl >/dev/null || die "curl не найден"
    info "Загрузка $url → $path"
    download "$name" "$url" "$path.part"
  fi
  size="$(fsize "$path.part")"
  if [ "$size" -lt "$min" ] || head -c 64 "$path.part" | grep -qiE '<!doctype|<html'; then
    mv -f "$path.part" "$path.failed"
    die "Файл $name получился непригодным ($size байт, ожидалось не менее $min, либо это HTML-страница вместо файла — прокси или страница входа?). Сохранён для разбора: $path.failed"
  fi
  if ! check_sha "$path.part" "$sha"; then
    mv -f "$path.part" "$path.failed"; die "Контрольная сумма SHA-256 файла $name не совпала с заданной в .env. Файл не используется, сохранён: $path.failed"
  fi
  mv -f "$path.part" "$path"
  ok "$name: $size байт"
}

if [ "$SKIP_FULL" = 0 ]; then
  if asr_model_ready "$MODEL"; then
    ok "Модель $MODEL уже на месте ($(fsize "$CKPT") байт, чекпойнт и токенайзер) — повторная загрузка не требуется."
  else
    have "$CKPT" "$MIN_CKPT_BYTES" || { rm -f "$CKPT"; fetch "$MODEL.ckpt" "$CKPT" "$MIN_CKPT_BYTES" "${GIGAAM_CKPT_SHA256:-}"; }
    have "$TOK" 1 || { rm -f "$TOK"; fetch "${MODEL}_tokenizer.model" "$TOK" 1 "${GIGAAM_TOKENIZER_SHA256:-}"; }
    printf '%s prepared=%s size=%s\n' "$MODEL" "$(date -Is)" "$(fsize "$CKPT")" > "$DEST/$MODEL.version"
    ok "Модель $MODEL готова: $DEST"
  fi
  # Файлы могли попасть сюда от другого пользователя (копирование, распаковка) с ограниченными правами: контейнер ASR (uid 10001) их не прочитает.
  models_fix_perms || exit 1
fi
