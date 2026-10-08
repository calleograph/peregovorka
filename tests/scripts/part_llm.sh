# shellcheck shell=bash
# Подключается из run.sh: локальная языковая модель (Qwen3 0.6B Q4_K_M) — scripts/lib/llmlib.sh, models.sh --llm*, профиль compose, проверка и установка без интернета.
LL="$TMP/llm"; mkdir -p "$LL/data/models/llm" "$LL/src" "$LL/bin"
printf 'GGUF-test-model-%s' "$(head -c 3000 /dev/zero | tr '\0' 'x')" > "$LL/src/Qwen3-0.6B-Q4_K_M.gguf"
LL_SIZE="$(stat -c %s "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" 2>/dev/null || wc -c < "$LL/src/Qwen3-0.6B-Q4_K_M.gguf")"
LL_SHA="$(sha256sum "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" | cut -d' ' -f1)"
cat > "$LL/env" <<ENVF
COMPOSE_PROJECT_NAME=pg-llm-test
DATA_ROOT=$LL/data
LLM_MODEL_BYTES=$LL_SIZE
LLM_MODEL_SHA256=$LL_SHA
ENVF
# llm CMD… — выполнить фрагмент с загруженной библиотекой и тестовым .env (стерильное окружение)
llm() { env -i PATH="$LL/bin:$PATH" HOME="$TMP" ENV_FILE="$LL/env" "$@" bash -c 'source "$1/scripts/lib/common.sh"; load_env "$ENV_FILE"; shift; eval "$1"' _ "$ROOT" "$LLM_CMD"; }
q() { LLM_CMD="$1"; shift; llm "$@"; }
FILE="$LL/data/models/llm/Qwen3-0.6B-Q4_K_M.gguf"

t "llm: манифест — Qwen3 0.6B Q4_K_M, размер 484 220 320, SHA-256 задан" bash -c 'source "$1/scripts/lib/llmlib.sh"; [ "$LLM_DEFAULT_FILE" = Qwen3-0.6B-Q4_K_M.gguf ] && [ "$LLM_DEFAULT_BYTES" = 484220320 ] && [ "${#LLM_DEFAULT_SHA256}" = 64 ]' _ "$ROOT"
t "llm: источник — репозиторий с файлом Q4_K_M (bartowski), а не tensorblock без него" bash -c 'source "$1/scripts/lib/llmlib.sh"; [[ "$LLM_DEFAULT_URL" == https://huggingface.co/bartowski/Qwen_Qwen3-0.6B-GGUF/resolve/main/Qwen_Qwen3-0.6B-Q4_K_M.gguf ]]' _ "$ROOT"
t "llm: файла нет → missing" eq "$(q 'llm_model_state')" missing
printf 'x' > "$FILE.part"
t "llm: есть только .part → partial" eq "$(q 'llm_model_state')" partial
rm -f "$FILE.part"; head -c 100 "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" > "$FILE"
t "llm: обрезанный файл → bad_size" eq "$(q 'llm_model_state')" bad_size
head -c "$LL_SIZE" /dev/zero > "$FILE"
t "llm: размер тот же, содержимое другое → bad_hash (повреждение)" eq "$(q 'llm_model_state')" bad_hash
cp "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" "$FILE"
t "llm: размер и SHA-256 верны → ok" eq "$(q 'llm_model_state')" ok
t "llm: контрольная сумма закэширована рядом с файлом (повторные проверки быстрые)" test -s "$FILE.sha256"
cp "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" "$FILE"; rm -f "$FILE.sha256"
printf 'COMPOSE_PROJECT_NAME=pg-llm-test
DATA_ROOT=%s
LLM_MODEL_BYTES=%s
LLM_MODEL_SHA256=skip
' "$LL/data" "$LL_SIZE" > "$LL/env.skip"
t "llm: LLM_MODEL_SHA256=skip отключает проверку хеша (размер проверяется)" bash -c 'head -c "$3" /dev/zero > "$2"; out="$(env -i PATH="$PATH" ENV_FILE="$4" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_model_state")"; [ "$out" = ok ]' _ "$ROOT" "$FILE" "$LL_SIZE" "$LL/env.skip"
cp "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" "$FILE"; rm -f "$FILE.sha256"

# ---- загрузка: валидный файл не качается; повреждённый заменяется; хеш/размер проверяются
t "llm: валидный файл на месте — загрузка пропускается" bash -c 'out="$(env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; LLM_MODEL_URL=http://127.0.0.1:9/never llm_model_fetch")"; grep -q "уже на месте" <<<"$out"' _ "$ROOT" "$LL/env"
head -c 50 /dev/zero > "$FILE"
t "llm: повреждённый файл → скачивается заново (из каталога) и становится ok" bash -c 'env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_model_fetch --from-dir \"$3\"" >/dev/null 2>&1 && [ "$(env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_model_state")" = ok ]' _ "$ROOT" "$LL/env" "$LL/src"
t "llm: --force удаляет и копирует заново" bash -c 'env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_model_fetch --force --from-dir \"$3\"" 2>&1 | grep -q "загружена и проверена"' _ "$ROOT" "$LL/env" "$LL/src"
mkdir -p "$LL/bad"; head -c "$LL_SIZE" /dev/zero > "$LL/bad/Qwen3-0.6B-Q4_K_M.gguf"; rm -f "$FILE" "$FILE.sha256"
t "llm: подмена файла (тот же размер, другой хеш) отвергается и не используется" bash -c '! env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_model_fetch --from-dir \"$3\"" >/dev/null 2>&1 && [ ! -f "$4" ] && [ -f "$4.failed" ]' _ "$ROOT" "$LL/env" "$LL/bad" "$FILE"
rm -f "$FILE.failed"; head -c 10 /dev/zero > "$LL/bad/Qwen3-0.6B-Q4_K_M.gguf"
t "llm: обрезанный файл отвергается по размеру" bash -c '! env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_model_fetch --from-dir \"$3\"" >/dev/null 2>&1 && [ ! -f "$4" ]' _ "$ROOT" "$LL/env" "$LL/bad" "$FILE"
rm -f "$FILE.failed"

# ---- нет интернета: явное сообщение, установка продолжается (soft) / команда сообщает об ошибке (не soft)
printf 'LLM_MODEL_URL=http://127.0.0.1:9/Qwen3.gguf\n' >> "$LL/env"
OFF="$(env -i PATH="$PATH" ENV_FILE="$LL/env" bash -c 'source "$1/scripts/lib/common.sh"; load_env "$ENV_FILE"; llm_prepare soft; echo "rc=$?"' _ "$ROOT" 2>&1)"
t "llm: без интернета (soft): явно «НЕ ЗАГРУЖЕНА», остальная система продолжает, код 0" bash -c 'grep -q "ЛОКАЛЬНАЯ LLM НЕ ЗАГРУЖЕНА" <<<"$1" && grep -q "Остальная Peregovorka" <<<"$1" && grep -q "rc=0" <<<"$1"' _ "$OFF"
t "llm: без интернета и без soft — ошибка" bash -c '! env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_prepare" >/dev/null 2>&1' _ "$ROOT" "$LL/env"
sed -i '/^LLM_MODEL_URL=/d' "$LL/env"
t "llm: LLM_LOCAL_ENABLED=no — ничего не делает" bash -c 'out="$(env -i PATH="$PATH" ENV_FILE="$2" LLM_LOCAL_ENABLED=no bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_prepare soft")"; grep -q "отключена" <<<"$out"' _ "$ROOT" "$LL/env"
t "llm: DRY_RUN — только план" bash -c 'out="$(env -i PATH="$PATH" ENV_FILE="$2" DRY_RUN=1 bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm_prepare soft")"; grep -q "dry-run" <<<"$out" && [ ! -e "$3" ]' _ "$ROOT" "$LL/env" "$FILE"

# ---- models.sh
t "models.sh --llm-only --from-dir копирует и проверяет модель" bash -c 'bash "$1/scripts/models.sh" --env "$2" --llm-only --from-dir "$3" >/dev/null 2>&1 && [ -s "$4" ]' _ "$ROOT" "$LL/env" "$LL/src" "$FILE"
t "models.sh --llm-only --check: код 0 при валидном файле" bash -c 'bash "$1/scripts/models.sh" --env "$2" --llm-only --check >/dev/null 2>&1' _ "$ROOT" "$LL/env"
t "models.sh --llm-only --check: код 1 при повреждении" bash -c 'head -c 7 /dev/zero > "$3"; ! bash "$1/scripts/models.sh" --env "$2" --llm-only --check >/dev/null 2>&1' _ "$ROOT" "$LL/env" "$FILE"
cp "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" "$FILE"; rm -f "$FILE.sha256"
t "models.sh: справка описывает --llm-only/--soft/--force/--check" bash -c 'bash "$1/scripts/models.sh" --help | grep -q -- "--llm-only" && bash "$1/scripts/models.sh" --help | grep -q -- "--soft"' _ "$ROOT"

# ---- профиль compose включается только при готовой модели и образе
cat > "$LL/bin/docker" <<'DOCK'
#!/usr/bin/env bash
case "$1 $2" in "image inspect") [ "${FAKE_LLM_IMAGE:-yes}" = yes ] && exit 0 || exit 1 ;; esac
exit 0
DOCK
chmod +x "$LL/bin/docker"
args() { env -i PATH="$LL/bin:$PATH" ENV_FILE="$LL/env" "$@" bash -c 'source "$1/scripts/lib/common.sh"; load_env "$ENV_FILE"; compose_args; echo "${COMPOSE_ARGS[*]}"' _ "$ROOT" 2>/dev/null; }
t "compose: модель валидна и образ есть → --profile llm" bash -c 'grep -q -- "--profile llm" <<<"$1"' _ "$(args)"
t "compose: образа runtime нет (нет интернета) → профиль не включается, остальное работает" bash -c '! grep -q -- "--profile llm" <<<"$1"' _ "$(args FAKE_LLM_IMAGE=no)"
t "compose: LLM_LOCAL_ENABLED=no → профиль не включается" bash -c '! grep -q -- "--profile llm" <<<"$1"' _ "$(args LLM_LOCAL_ENABLED=no)"
head -c 9 /dev/zero > "$FILE"; rm -f "$FILE.sha256"
t "compose: файл повреждён → профиль не включается (битая модель не запускается)" bash -c '! grep -q -- "--profile llm" <<<"$1"' _ "$(args)"
cp "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" "$FILE"; rm -f "$FILE.sha256"

# ---- compose.yml: контейнер без выхода наружу и без публикуемых портов
CY="$ROOT/deployment/compose.yml"
llm_block() { awk '/^  llm-local:/{f=1;print;next} f&&/^  [a-z]/{exit} f{print}' "$CY"; }
t "compose.yml: llm-local — профиль llm, образ llama.cpp с фиксированным тегом" bash -c 'b="$(awk "/^  llm-local:/{f=1;print;next} f&&/^  [a-z]/{exit} f{print}" "$1")"; grep -q "profiles: \[\"llm\"\]" <<<"$b" && grep -q "llama.cpp:server-b[0-9]" <<<"$b"' _ "$CY"
t "compose.yml: llm-local не публикует портов и сидит только во внутренней сети" bash -c 'b="$(awk "/^  llm-local:/{f=1;print;next} f&&/^  [a-z]/{exit} f{print}" "$1")"; ! grep -q "ports:" <<<"$b" && grep -q "llm_internal" <<<"$b" && ! grep -q -- "- default" <<<"$b"' _ "$CY"
t "compose.yml: сеть llm_internal объявлена как internal: true" bash -c 'awk "/^networks:/{f=1} f" "$1" | grep -A3 "llm_internal:" | grep -q "internal: true"' _ "$CY"
t "compose.yml: модель монтируется только для чтения и лежит вне образа (DATA_ROOT/models/llm)" bash -c 'grep -q "\${DATA_ROOT}/models/llm:/models:ro" "$1" && grep -q "\${DATA_ROOT}/models/llm:/models/llm:ro" "$1"' _ "$CY"
t "compose.yml: backend подключён к обеим сетям и знает адрес llm-local" bash -c 'grep -q "LOCAL_LLM_URL: http://llm-local:8080" "$1" && grep -q -- "- llm_internal" "$1"' _ "$CY"
t ".env.example: параметры локальной LLM описаны" bash -c 'grep -q "^LLM_LOCAL_ENABLED=yes" "$1/.env.example" && grep -q "^LLM_LOCAL_THREADS=" "$1/.env.example" && grep -q "^LLM_LOCAL_CTX=" "$1/.env.example"' _ "$ROOT"

# ---- встроено в установку, обновление, проверку, диагностику
t "install.sh: этап models готовит локальную LLM мягко (без интернета не падает), каталог models/llm создаётся" bash -c 'grep -q "stage_llm" "$1/scripts/install.sh" && grep -q "llm_prepare soft" "$1/scripts/install.sh" && grep -q "models/llm" "$1/scripts/install.sh"' _ "$ROOT"
t "update.sh: модель не качается повторно — llm_prepare soft; каталог models/llm создаётся до запуска" bash -c 'grep -q "llm_prepare soft" "$1/scripts/update.sh" && grep -q "models/llm" "$1/scripts/lib/updatelib.sh"' _ "$ROOT"
t "verify: стадия «локальная LLM» только предупреждает" bash -c 'grep -q "verify_local_llm" "$1/scripts/lib/verifylib.sh" && sed -n "/^verify_local_llm/,/^}/p" "$1/scripts/lib/verifylib.sh" | grep -q v_warn && ! sed -n "/^verify_local_llm/,/^}/p" "$1/scripts/lib/verifylib.sh" | grep -q v_fail' _ "$ROOT"
t "diag.sh и smoke-test.sh: проверяют локальную LLM" bash -c 'grep -q "local-llm.txt" "$1/scripts/diag.sh" && grep -q "Локальная LLM" "$1/scripts/smoke-test.sh"' _ "$ROOT"
t "verify_local_llm: нет модели → предупреждение с инструкцией, не отказ" bash -c 'out="$(env -i PATH="$1/bin:$PATH" ENV_FILE="$2" bash -c "source \"$3/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; rm -f \"$4\"; v_ok(){ echo OK \$*; }; v_warn(){ echo WARN \$*; }; verify_local_llm; echo rc=\$?")"; grep -q "WARN Локальная LLM: не загружена" <<<"$out" && grep -q "rc=0" <<<"$out"' _ "$LL" "$LL/env" "$ROOT" "$FILE"
cp "$LL/src/Qwen3-0.6B-Q4_K_M.gguf" "$FILE"
t "repair llm_model: описание без команд Linux, ID в белом списке" bash -c 'source "$1/scripts/lib/repairlib.sh"; repair_id_valid llm_model && t="$(repair_text llm_model)" && [ "$(tr -cd "|" <<<"$t" | wc -c)" -ge 2 ] && ! grep -qE "sudo|chown|docker compose" <<<"$t"' _ "$ROOT"

# ---- память llama-server: без кэша промптов (иначе RSS растёт до 4–5 ГБ при лимите контейнера 3 ГБ и контейнер убивает OOM)
t "llm: compose — llama-server запускается с --cache-ram 0" bash -c 'b="$(awk "/^  llm-local:/{f=1;next} f&&/^  [a-z]/{f=0} f" "$1/deployment/compose.yml")"; tr -d "\n" <<<"$b" | grep -Eq "\"--cache-ram\" +- \"0\""' _ "$ROOT"

# ---- Qwen3 1.7B: необязательная модель в отдельном контейнере
Q17="$TMP/llm17"; mkdir -p "$Q17/data/models/llm"
printf 'GGUF-17b-%s' "$(head -c 2000 /dev/zero | tr '\0' 'y')" > "$Q17/src.gguf"
Q17_SIZE="$(wc -c < "$Q17/src.gguf")"; Q17_SHA="$(sha256sum "$Q17/src.gguf" | cut -d' ' -f1)"
printf 'COMPOSE_PROJECT_NAME=pg-q17-test\nDATA_ROOT=%s\n' "$Q17/data" > "$Q17/env"
t "llm17: манифест — Qwen3 1.7B Q4_K_M, размер 1 282 439 584, SHA-256 задан" bash -c 'source "$1/scripts/lib/llmlib.sh"; [ "$LLM17_FILE" = Qwen3-1.7B-Q4_K_M.gguf ] && [ "$LLM17_BYTES" = 1282439584 ] && [ "${#LLM17_SHA256}" = 64 ] && [[ "$LLM17_URL" == https://huggingface.co/bartowski/Qwen_Qwen3-1.7B-GGUF/resolve/main/Qwen_Qwen3-1.7B-Q4_K_M.gguf ]]' _ "$ROOT"
t "llm17: по умолчанию выключена (в обычную установку не входит)" bash -c '! { source "$1/scripts/lib/llmlib.sh"; llm17_enabled; }' _ "$ROOT"
t "llm17: файла нет → missing" eq "$(env -i PATH="$PATH" ENV_FILE="$Q17/env" bash -c 'source "$1/scripts/lib/common.sh"; load_env "$ENV_FILE"; llm17_model_state' _ "$ROOT")" missing
t "llm17: состояние 1.7B не влияет на основную модель (подмена LLM_MODEL_* только в подоболочке)" bash -c 'out="$(env -i PATH="$PATH" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; llm17_model_state >/dev/null; llm_file_name")"; [ "$out" = Qwen3-0.6B-Q4_K_M.gguf ]' _ "$ROOT" "$Q17/env"
printf 'data' > "$Q17/data/models/llm/Qwen3-1.7B-Q4_K_M.gguf"
t "llm17: неверный размер → bad_size" eq "$(env -i PATH="$PATH" ENV_FILE="$Q17/env" bash -c 'source "$1/scripts/lib/common.sh"; load_env "$ENV_FILE"; llm17_model_state' _ "$ROOT")" bad_size
rm -f "$Q17/data/models/llm/Qwen3-1.7B-Q4_K_M.gguf"
t "llm17: compose — llm-local-17b только по профилю llm17, без опубликованных портов и host-сети" bash -c 'b="$(awk "/^  llm-local-17b:/{f=1;next} f&&/^  [a-z]/{f=0} f" "$1/deployment/compose.yml")"; grep -q "profiles: \[\"llm17\"\]" <<<"$b" && ! grep -Eq "^    ports:|network_mode: *host|privileged" <<<"$b" && grep -q "llm_internal" <<<"$b"' _ "$ROOT"
t "llm17: compose — без кэша промптов (--cache-ram 0) и с лимитом памяти" bash -c 'b="$(awk "/^  llm-local-17b:/{f=1;next} f&&/^  [a-z]/{f=0} f" "$1/deployment/compose.yml")"; tr -d "\n" <<<"$b" | grep -Eq "\"--cache-ram\" +- \"0\"" && grep -q "mem_limit" <<<"$b"' _ "$ROOT"
t "llm17: выключенная модель не добавляет профиль llm17 в compose_args" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; compose_args 2>/dev/null; echo \"\${COMPOSE_ARGS[*]}\"")"; grep -q compose <<<"$out" && ! grep -q -- "--profile llm17" <<<"$out"' _ "$ROOT" "$Q17/env"
t "llm.sh status при выключенной модели объясняет, как включить" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/llm.sh" status --env "$2" 2>&1)"; grep -q "выключена" <<<"$out" && grep -q "enable-17b" <<<"$out"' _ "$ROOT" "$Q17/env"
t "llm.sh enable-17b без root не меняет .env" bash -c '[ "$(id -u)" -eq 0 ] && exit 0; cp "$2" "$3"; ! env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/llm.sh" enable-17b --env "$3" --yes >/dev/null 2>&1 && ! grep -q "LLM_17B_ENABLED=yes" "$3"' _ "$ROOT" "$Q17/env" "$Q17/env.copy"
t "llm17: исправления llm17_enable / llm17_disable разрешены помощнику" bash -c 'source "$1/scripts/lib/repairlib.sh"; printf "%s\n" "${REPAIR_IDS[@]}" | grep -qx llm17_enable && printf "%s\n" "${REPAIR_IDS[@]}" | grep -qx llm17_disable' _ "$ROOT"
t ".env.example: Qwen3 1.7B выключена по умолчанию" bash -c 'grep -q "^LLM_17B_ENABLED=no" "$1/.env.example"' _ "$ROOT"
