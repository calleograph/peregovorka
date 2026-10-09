#!/usr/bin/env bash
# llmlib.sh — локальная языковая модель (LLM): манифест модели, проверка файла, загрузка, признак «включена и можно запускать».
#
# Модель — Qwen3 1.7B (квантование Q4_K_M, формат GGUF, ~1,28 ГБ), runtime — llama.cpp (llama-server, CPU) в контейнере `llm-local`.
# Файл хранится ВНЕ образов: ${DATA_ROOT}/models/llm/ — при обновлении проекта заново не скачивается. Данные из этой модели наружу не отправляются:
# контейнер подключён только к внутренней сети compose (internal), доступ к нему есть у backend.
#
# Прежняя Qwen3 0.6B больше не устанавливается и не предлагается: для протоколов она оказалась слишком слабой. Её файл на старых серверах можно удалить
# (${DATA_ROOT}/models/llm/Qwen3-0.6B-Q4_K_M.gguf).
#
# Источник: файл Qwen_Qwen3-1.7B-Q4_K_M.gguf из репозитория bartowski/Qwen_Qwen3-1.7B-GGUF (1 282 439 584 байт). Адрес, имя и хеш можно переопределить
# в .env (LLM_MODEL_URL, LLM_MODEL_FILE, LLM_MODEL_SHA256); LLM_MODEL_SHA256=skip отключает проверку хеша (остаётся проверка размера).
#
# Как добавить другую локальную модель (например, более крупную): положить её .gguf в ${DATA_ROOT}/models/llm, задать LLM_MODEL_FILE, LLM_MODEL_URL,
# LLM_MODEL_SHA256, LLM_MODEL_BYTES (и LLM_MODEL_ALIAS) в .env и добавить запись в LOCAL_MODELS (backend/app/services/local_llm.py). Остальная система
# обращается к модели по OpenAI-совместимому API (/v1/chat/completions) и от конкретного runtime не зависит.
#
# Только функции, ничего не выполняет. Зависит от common.sh (load_env выполнен).

if [ -n "${_VM_LLMLIB_LOADED:-}" ]; then return 0; fi
_VM_LLMLIB_LOADED=1

LLM_DEFAULT_FILE="Qwen3-1.7B-Q4_K_M.gguf"
LLM_DEFAULT_URL="https://huggingface.co/bartowski/Qwen_Qwen3-1.7B-GGUF/resolve/main/Qwen_Qwen3-1.7B-Q4_K_M.gguf"
LLM_DEFAULT_BYTES=1282439584
LLM_DEFAULT_SHA256="72c5c3cb38fa32d5256e2fe30d03e7a64c6c79e668ad84057e3bd66e250b24fb"
LLM_DEFAULT_IMAGE="ghcr.io/ggml-org/llama.cpp:server-b11371"
LLM_DEFAULT_ALIAS="qwen3-1.7b-q4_k_m"

llm_file_name()  { printf '%s' "${LLM_MODEL_FILE:-$LLM_DEFAULT_FILE}"; }
llm_dir()        { printf '%s' "${DATA_ROOT:-}/models/llm"; }
llm_path()       { printf '%s/%s' "$(llm_dir)" "$(llm_file_name)"; }
llm_url()        { printf '%s' "${LLM_MODEL_URL:-$LLM_DEFAULT_URL}"; }
llm_image()      { printf '%s' "${LLM_LOCAL_IMAGE:-$LLM_DEFAULT_IMAGE}"; }
# Ожидаемый размер/хеш относятся к модели по умолчанию; для другого файла без явных значений проверяется только «не пуст и не обрезан» (≥ 50 МБ).
llm_expected_bytes() { if [ -n "${LLM_MODEL_BYTES:-}" ]; then printf '%s' "$LLM_MODEL_BYTES"; elif [ "$(llm_file_name)" = "$LLM_DEFAULT_FILE" ]; then printf '%s' "$LLM_DEFAULT_BYTES"; fi; }
llm_expected_sha()   { if [ -n "${LLM_MODEL_SHA256:-}" ]; then printf '%s' "$LLM_MODEL_SHA256"; elif [ "$(llm_file_name)" = "$LLM_DEFAULT_FILE" ]; then printf '%s' "$LLM_DEFAULT_SHA256"; fi; }
LLM_MIN_BYTES=$((50 * 1024 * 1024))

llm_local_enabled() { [ "${LLM_LOCAL_ENABLED:-yes}" = "yes" ]; }

_llm_size() { stat -c '%s' "$1" 2>/dev/null || wc -c < "$1" 2>/dev/null || echo 0; }

# sha256 файла с кэшем: повторные проверки (verify, обновление) не читают 1,3 ГБ заново, пока размер и время изменения те же.
llm_sha_cached() { # путь → печатает sha256
  local f="$1" cache="$1.sha256" key sum
  key="$(_llm_size "$f") $(stat -c '%y %i' "$f" 2>/dev/null || echo 0)"
  if [ -r "$cache" ] && [ "$(sed -n 1p "$cache" 2>/dev/null)" = "$key" ]; then sed -n 2p "$cache"; return 0; fi
  sum="$(sha256sum "$f" 2>/dev/null | cut -d' ' -f1)"
  [ -n "$sum" ] || return 1
  { printf '%s\n%s\n' "$key" "$sum" > "$cache"; } 2>/dev/null || true
  printf '%s' "$sum"
}

# Состояние файла модели: missing | partial (идёт/оборвана загрузка) | bad_size | bad_hash | ok
llm_model_state() {
  local f size want sha got
  f="$(llm_path)"
  if [ ! -f "$f" ]; then [ -f "$f.part" ] && echo partial || echo missing; return 0; fi
  size="$(_llm_size "$f")"; want="$(llm_expected_bytes)"
  if [ -n "$want" ]; then [ "$size" = "$want" ] || { echo bad_size; return 0; }
  else [ "$size" -ge "$LLM_MIN_BYTES" ] || { echo bad_size; return 0; }; fi
  sha="$(llm_expected_sha)"
  if [ -n "$sha" ] && [ "$sha" != skip ]; then
    got="$(llm_sha_cached "$f")" || { echo bad_hash; return 0; }
    [ "$got" = "$sha" ] || { echo bad_hash; return 0; }
  fi
  echo ok
}

llm_state_text() { # состояние → человеческий текст
  case "$1" in
    ok) echo "на месте и проверена (размер и SHA-256)" ;;
    missing) echo "не загружена" ;;
    partial) echo "загрузка не завершена (файл неполный)" ;;
    bad_size) echo "файл повреждён: размер не совпадает с ожидаемым" ;;
    bad_hash) echo "файл повреждён или изменён: контрольная сумма SHA-256 не совпадает" ;;
    *) echo "$1" ;;
  esac
}

# Скачивание: докачка (curl -C -) в *.part, проверка размера и SHA-256, затем атомарное переименование. Повреждённый файл не используется.
# llm_model_fetch [--force] [--from-dir КАТАЛОГ]; код 0 — модель готова, иначе сообщение в stderr.
llm_model_fetch() {
  local force=0 from="" f state tmp size want sha got
  while [ $# -gt 0 ]; do case "$1" in --force) force=1; shift ;; --from-dir) from="$2"; shift 2 ;; *) shift ;; esac; done
  f="$(llm_path)"
  [[ "$(llm_file_name)" =~ ^[A-Za-z0-9_.-]+\.gguf$ ]] || { fail "Недопустимое имя файла модели: $(llm_file_name)"; return 2; }
  mkdir -p "$(llm_dir)" 2>/dev/null || { fail "Не удалось создать $(llm_dir)"; return 2; }
  state="$(llm_model_state)"
  if [ "$state" = ok ] && [ "$force" -eq 0 ]; then ok "Локальная LLM уже на месте ($(llm_file_name), $(_llm_size "$f") байт) — загрузка не требуется."; return 0; fi
  if [ "$state" != ok ] && [ "$state" != missing ] && [ "$state" != partial ]; then
    warn "Файл локальной LLM непригоден ($(llm_state_text "$state")) — скачиваю заново."
    rm -f "$f" "$f.sha256"
  elif [ "$force" -eq 1 ]; then rm -f "$f" "$f.sha256" "$f.part"; fi
  tmp="$f.part"
  if [ -n "$from" ]; then
    [ -f "$from/$(llm_file_name)" ] || { fail "В $from нет файла $(llm_file_name)"; return 1; }
    info "Копирование $from/$(llm_file_name) → $f"
    cp -f "$from/$(llm_file_name)" "$tmp" || return 1
  else
    command -v curl >/dev/null 2>&1 || { fail "curl не найден"; return 1; }
    info "Загрузка локальной LLM (~$(( $(llm_expected_bytes) / 1048576 )) МБ): $(llm_url)"
    curl -fL --retry 3 --retry-delay 5 --connect-timeout 20 -C - -o "$tmp" "$(llm_url)" \
      || { fail "Не удалось скачать модель (нет доступа к интернету или к адресу). Частично скачанное сохранено: $tmp — повторный запуск продолжит."; return 1; }
  fi
  size="$(_llm_size "$tmp")"; want="$(llm_expected_bytes)"
  if { [ -n "$want" ] && [ "$size" != "$want" ]; } || { [ -z "$want" ] && [ "$size" -lt "$LLM_MIN_BYTES" ]; }; then
    mv -f "$tmp" "$f.failed"; fail "Скачанный файл неполон или это не модель: $size байт (ожидалось ${want:-не менее $LLM_MIN_BYTES}). Сохранён для разбора: $f.failed"; return 1
  fi
  sha="$(llm_expected_sha)"
  if [ -n "$sha" ] && [ "$sha" != skip ]; then
    got="$(sha256sum "$tmp" | cut -d' ' -f1)"
    if [ "$got" != "$sha" ]; then mv -f "$tmp" "$f.failed"; fail "Контрольная сумма SHA-256 не совпала (получено ${got:0:16}…, ожидалось ${sha:0:16}…). Файл не используется, сохранён: $f.failed"; return 1; fi
  fi
  mv -f "$tmp" "$f"; rm -f "$f.sha256"
  ok "Локальная LLM загружена и проверена: $(llm_file_name), $(_llm_size "$f") байт."
}

# Модель включена в .env, файл валиден, а образ runtime есть локально → контейнер llm-local нужно запускать (compose profile `llm`).
# Результат кэшируется на время работы процесса; после загрузки/скачивания образа вызвать llm_local_refresh.
_LLM_ACTIVE=""
llm_local_refresh() { _LLM_ACTIVE=""; }
llm_local_active() {
  if [ -n "$_LLM_ACTIVE" ]; then [ "$_LLM_ACTIVE" = 1 ]; return; fi
  _LLM_ACTIVE=0
  llm_local_enabled || return 1
  [ -n "${DATA_ROOT:-}" ] || return 1
  [ "$(llm_model_state)" = ok ] || return 1
  command -v docker >/dev/null 2>&1 || return 1
  docker image inspect "$(llm_image)" >/dev/null 2>&1 || return 1
  _LLM_ACTIVE=1
  return 0
}

# В версии 0.6.0 Qwen3 1.7B запускалась отдельным необязательным контейнером llm-local-17b; теперь 1.7B — основная модель в llm-local. Старый контейнер, если он остался, убираем.
llm_remove_legacy_container() {
  command -v docker >/dev/null 2>&1 || return 0
  [ -n "${COMPOSE_PROJECT_NAME:-}" ] || return 0
  docker rm -f "${COMPOSE_PROJECT_NAME}-llm-local-17b-1" >/dev/null 2>&1 || true
}

# Подготовка при установке/обновлении: файл модели + образ runtime. Мягкий режим (soft): любая неудача — явное сообщение, но не отказ,
# чтобы отсутствие интернета не мешало остальной установке. Код 0 всегда в режиме soft.
llm_prepare() { # llm_prepare [soft]
  local soft="${1:-}" st
  if ! llm_local_enabled; then info "Локальная LLM отключена (LLM_LOCAL_ENABLED=no) — пропущено."; return 0; fi
  if [ "${DRY_RUN:-0}" = "1" ]; then info "[dry-run] локальная LLM: $(llm_file_name) → $(llm_dir) (если ещё нет), образ $(llm_image)"; return 0; fi
  llm_remove_legacy_container
  st="$(llm_model_state)"
  if [ "$st" != ok ]; then
    if ! llm_model_fetch; then
      warn "ЛОКАЛЬНАЯ LLM НЕ ЗАГРУЖЕНА. Краткие протоколы и резюме будут работать только через внешнюю LLM (если она настроена). Остальная Peregovorka устанавливается и работает."
      warn "Загрузить позже: Администрирование → Языковая модель (LLM) → «Скачать модель» либо sudo scripts/models.sh --llm-only (нужен интернет)."
      [ "$soft" = soft ] && return 0 || return 1
    fi
  else ok "Локальная LLM на месте: $(llm_file_name) ($(llm_state_text "$st"))."; fi
  if command -v docker >/dev/null 2>&1 && ! docker image inspect "$(llm_image)" >/dev/null 2>&1; then
    info "Загрузка образа llama.cpp: $(llm_image)"
    if ! docker pull "$(llm_image)" >/dev/null 2>&1; then
      warn "Образ llama.cpp ($(llm_image)) не скачан (нет доступа к ghcr.io?). Локальная LLM будет запущена после его загрузки: повторите установку/обновление при наличии интернета."
      llm_local_refresh; [ "$soft" = soft ] && return 0 || return 1
    fi
  fi
  llm_local_refresh
  return 0
}
