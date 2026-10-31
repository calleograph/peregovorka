# shellcheck shell=bash
# Подключается из run.sh: встроенная локальная модель (Qwen3, llama.cpp) удалена в 0.56.0 — её нет в compose и .env.example,
# а обновление убирает файлы прежней модели и не трогает остальное (в том числе модели распознавания речи).
LL="$TMP/llm"; mkdir -p "$LL/data/models/llm" "$LL/data/models/gigaam" "$LL/bin"
printf '#!/bin/sh\nexit 0\n' > "$LL/bin/docker"; chmod +x "$LL/bin/docker"          # без Docker: контейнеров прежней версии нет
cat > "$LL/env" <<ENVF
COMPOSE_PROJECT_NAME=pg-llm-test
DATA_ROOT=$LL/data
ENVF
: > "$LL/data/models/llm/Qwen3-1.7B-Q4_K_M.gguf"; : > "$LL/data/models/llm/Qwen3-0.6B-Q4_K_M.gguf.part"
: > "$LL/data/models/gigaam/v3_e2e_rnnt.ckpt"
t "llm: в compose нет контейнера llm-local и сети llm_internal" bash -c '! grep -qE "llm-local|llm_internal|LOCAL_LLM_" "$1/deployment/compose.yml"' _ "$ROOT"
t "llm: в .env.example нет параметров встроенной модели" bash -c '! grep -qE "^#? ?LLM_(LOCAL_|MODEL_)" "$1/.env.example"' _ "$ROOT"
t "llm: models.sh --llm-only объясняет, что модель удалена" bash -c 'out="$(env -i PATH="$PATH" "$1/scripts/models.sh" --env "$2" --llm-only 2>&1)"; [ $? -ne 0 ] && grep -q "удалена" <<<"$out"' _ "$ROOT" "$LL/env"
t "llm: обновление удаляет файлы прежней модели, модели ASR на месте" bash -c 'env -i PATH="$2:$PATH" HOME="$3" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"$4\"; legacy_llm_cleanup" >/dev/null 2>&1
  [ ! -e "$5/models/llm" ] && [ -e "$5/models/gigaam/v3_e2e_rnnt.ckpt" ]' _ "$ROOT" "$LL/bin" "$TMP" "$LL/env" "$LL/data"
mkdir -p "$LL/data/models/llm"; : > "$LL/data/models/llm/my-own-file.bin"
t "llm: чужие файлы каталога не удаляются" bash -c 'env -i PATH="$2:$PATH" HOME="$3" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"$4\"; legacy_llm_cleanup" >/dev/null 2>&1
  [ -e "$5/models/llm/my-own-file.bin" ]' _ "$ROOT" "$LL/bin" "$TMP" "$LL/env" "$LL/data"
t "llm: прежняя модель убирается только после проверенного запуска новой версии" bash -c 'grep -A3 "if \[ \"\$H_ST\" = ok \]; then" "$1/scripts/update.sh" | grep -q legacy_llm_cleanup && [ "$(grep -c legacy_llm_cleanup "$1/scripts/update.sh")" = 1 ]' _ "$ROOT"
