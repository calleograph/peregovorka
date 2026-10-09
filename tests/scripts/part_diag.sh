#!/usr/bin/env bash
# Тесты: маскирование секретов, WebSocket/TLS/ядро/совместимость версий, шаблоны nginx. Подключается из run.sh (используются t, eq, ROOT, TMP).
source "$ROOT/scripts/lib/mask.sh"
source "$ROOT/scripts/lib/verifylib.sh"

# ---- маскирование (диагностика, журналы, compose config)
M="$(cat <<'EOF' | mask_stream
LDAP_BIND_PASSWORD=p@ss $w#rd"x
APP_MASTER_KEY: "base64abc=="
      LIVEKIT_API_KEY: devkey123
LIVEKIT_TOKEN_TTL_SECONDS=300
LIVEKIT_NODE_IP=10.0.0.5
DATABASE_URL=postgresql://app:Sup3rPw@postgres:5432/db
{"password": "abc", "user": "bob", "access_token":"zzz"}
GET /rtc/v1?access_token=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl&sdk=js 101
Authorization: Bearer abcdefghijkl123
      LIVEKIT_CONFIG: |
        keys:
          devkey: s3cretvalue
        webhook:
          api_key: devkey
          urls:
            - http://backend:8000/hook
ROOM_PASSWORD_MAX_FAILURES=5
EOF
)"
for leaked in 'p@ss' 'base64abc' 'devkey123' 'Sup3rPw' '"abc"' 'zzz' 'eyJhbGci' 'abcdefghijkl123' 's3cretvalue'; do
  t "маска скрывает «$leaked»" bash -c '! printf "%s" "$1" | grep -qF -- "$2"' _ "$M" "$leaked"
done
for kept in 'LIVEKIT_TOKEN_TTL_SECONDS=300' 'LIVEKIT_NODE_IP=10.0.0.5' 'sdk=js' 'ROOM_PASSWORD_MAX_FAILURES=5' 'http://backend:8000/hook' '"user": "bob"'; do
  t "маска не трогает «$kept»" bash -c 'printf "%s" "$1" | grep -qF -- "$2"' _ "$M" "$kept"
done
printf 'A_PASSWORD=hunter22\nLIVEKIT_API_KEY=abcdef12\nX_PORT=8000\nSHORT_TOKEN=abc\nLIVEKIT_TOKEN_TTL_SECONDS=300\n' > "$TMP/sec.env"
t "env_secret_values: только секреты длиннее 5 символов" eq "$(env_secret_values "$TMP/sec.env" | sort | tr '\n' ' ')" "abcdef12 hunter22 "

# ---- реальный WebSocket Upgrade: трактовка кодов
t "101 → OK" bash -c 'set -u; source "$1"; ws_verdict 101; [ "$WS_STATUS" = OK ]' _ "$ROOT/scripts/lib/verifylib.sh"
t "404 → FAIL, в пояснении /rtc/v1" bash -c 'set -u; source "$1"; ws_verdict 404; [ "$WS_STATUS" = FAIL ] && [[ "$WS_NOTE" == *"rtc/v1"* ]]' _ "$ROOT/scripts/lib/verifylib.sh"
t "400/426 → FAIL (прокси не передаёт Upgrade)" bash -c 'set -u; source "$1"; ws_verdict 426; a=$WS_STATUS; ws_verdict 400; [ "$a$WS_STATUS" = FAILFAIL ]' _ "$ROOT/scripts/lib/verifylib.sh"
t "нет ответа → WARNING" bash -c 'set -u; source "$1"; ws_verdict 000; [ "$WS_STATUS" = WARNING ]' _ "$ROOT/scripts/lib/verifylib.sh"
t "401 → WARNING" bash -c 'set -u; source "$1"; ws_verdict 401; [ "$WS_STATUS" = WARNING ]' _ "$ROOT/scripts/lib/verifylib.sh"

# ---- TLS: без сети — только классификация URL
tls_check "http://meet.example.org"; t "http:// → WARNING (нужен защищённый контекст)" eq "$TLS_STATUS" WARNING
tls_check ""; t "пустой URL → SKIP" eq "$TLS_STATUS" SKIP

# ---- параметры ядра: чтение /proc/sys из подставного каталога
PS="$TMP/procsys"; mkdir -p "$PS/net/core" "$PS/vm"
echo 1 > "$PS/vm/overcommit_memory"; echo 5000000 > "$PS/net/core/rmem_max"; echo 5000000 > "$PS/net/core/wmem_max"; echo 5000 > "$PS/net/core/netdev_max_backlog"
cnt() { PROC_SYS_ROOT="$PS" bash -c 'source "$1"; n=0; w() { n=$((n+1)); }; kernel_tuning_check : w; echo $n' _ "$ROOT/scripts/lib/envlib.sh"; }
t "ядро в порядке — предупреждений нет" eq "$(cnt)" 0
echo 212992 > "$PS/net/core/rmem_max"; echo 212992 > "$PS/net/core/wmem_max"; echo 1000 > "$PS/net/core/netdev_max_backlog"; echo 0 > "$PS/vm/overcommit_memory"
t "малые буферы/очередь/overcommit — 4 предупреждения" eq "$(cnt)" 4
t "tune-kernel.sh без --apply ничего не меняет и выходит 0" bash -c 'PROC_SYS_ROOT="$2" bash "$1/scripts/tune-kernel.sh" --env /nonexistent | grep -q "Ничего не изменено"' _ "$ROOT" "$PS"
t "tune-kernel.sh перечисляет рекомендованные значения" bash -c 'PROC_SYS_ROOT="$2" bash "$1/scripts/tune-kernel.sh" --env /nonexistent | grep -q "net.core.rmem_max = 5000000"' _ "$ROOT" "$PS"

# ---- версии: проверенный набор согласован с тем, что реально собирается
cv() { grep -E "^$1=" "$ROOT/deployment/compat.env" | cut -d= -f2; }
t "LiveKit закреплён: один и тот же проверенный тег в Dockerfile, .env.example, dockerlib и compat.env (не latest)" eq "$(grep -E '^ARG LIVEKIT_IMAGE_TAG=' "$ROOT/deployment/livekit/Dockerfile" | cut -d= -f2)|$(grep -E '^LIVEKIT_IMAGE_TAG=' "$ROOT/.env.example" | cut -d= -f2)|$(grep -oE 'LIVEKIT_IMAGE_TAG:-[a-z0-9.]+' "$ROOT/scripts/lib/dockerlib.sh" | head -1 | cut -d- -f2)|$(cv TESTED_LIVEKIT_SERVER)" "v1.13.7|v1.13.7|v1.13.7|v1.13.7"
t "compat.env фиксирует проверенную версию сервера" bash -c 'grep -qE "^TESTED_LIVEKIT_SERVER=v[0-9]+\.[0-9]+\.[0-9]+$" "$1/deployment/compat.env"' _ "$ROOT"
t "compat.env: Python SDK/API и JS SDK зафиксированы точно (==) на проверенных версиях" bash -c '. "$1/deployment/compat.env"; grep -qx "livekit==${TESTED_LIVEKIT_PYTHON_SDK}\(  #.*\)\?" <(sed "s/  #.*//" "$1/asr-service/requirements.txt") && grep -qx "livekit-api==${TESTED_LIVEKIT_API_PYTHON}" <(sed "s/  #.*//" "$1/asr-service/requirements.txt") && grep -qx "livekit-api==${TESTED_LIVEKIT_API_PYTHON}" <(sed "s/  #.*//" "$1/backend/requirements.txt") && grep -q "\"livekit-client\": \"${TESTED_LIVEKIT_CLIENT_JS}\"" "$1/frontend/package.json"' _ "$ROOT"
t "compat.env: livekit-client = package-lock" eq "$(cv TESTED_LIVEKIT_CLIENT_JS)" "$(grep -A2 '"node_modules/livekit-client"' "$ROOT/frontend/package-lock.json" | grep -m1 '"version"' | cut -d'"' -f4)"
t "compat_check: совпадение → OK (без подоболочки, под set -u)" bash -c 'set -u; source "$1"; REPO_ROOT="$2"; LIVEKIT_IMAGE_TAG="$(grep -E "^TESTED_LIVEKIT_SERVER=" "$2/deployment/compat.env" | cut -d= -f2)"; compat_check; [ "$COMPAT_STATUS" = OK ] && [ -n "$COMPAT_NOTE" ]' _ "$ROOT/scripts/lib/verifylib.sh" "$ROOT"
t "compat_check: latest → WARNING (в production нужен проверенный тег)" bash -c 'set -u; source "$1"; REPO_ROOT="$2"; LIVEKIT_IMAGE_TAG=latest; compat_check; [ "$COMPAT_STATUS" = WARNING ] && [[ "$COMPAT_NOTE" == *update.sh* ]]' _ "$ROOT/scripts/lib/verifylib.sh" "$ROOT"
t "зависимости Python — диапазоны (≥ проверенной, < следующего мажора), кроме закреплённых LiveKit-компонентов" bash -c '! grep -E "^[A-Za-z]" "$1/backend/requirements.txt" "$1/asr-service/requirements.txt" | sed "s/^[^:]*://" | grep -v "^livekit" | grep -E "=="' _ "$ROOT"
t "в образах версии баз задаются ARG (можно переопределить)" bash -c 'grep -q "^ARG PYTHON_VERSION" "$1/backend/Dockerfile" && grep -q "^ARG NODE_VERSION" "$1/frontend/Dockerfile" && grep -q "^ARG NGINX_VERSION" "$1/frontend/Dockerfile"' _ "$ROOT"
t "update.sh --pull обновляет базовые образы (deploy.sh — обёртка над update.sh)" bash -c 'grep -q -- "--pull)" "$1/scripts/update.sh" && grep -q "PULL_BASES:+--pull" "$1/scripts/lib/dockerlib.sh" && grep -q "exec .*update.sh" "$1/scripts/deploy.sh"' _ "$ROOT"
t "compat_check: другая (старая) версия → WARNING (не блокировка)" bash -c 'set -u; source "$2/scripts/lib/envlib.sh"; source "$1"; REPO_ROOT="$2"; LIVEKIT_IMAGE_TAG=v1.9.0; compat_check; [ "$COMPAT_STATUS" = WARNING ] && [[ "$COMPAT_NOTE" == *СТАРШЕ* ]]' _ "$ROOT/scripts/lib/verifylib.sh" "$ROOT"

# ---- nginx: реальная схема публичного URL не подменяется
t "web nginx: X-Forwarded-Proto берётся из map с запасным \$scheme" bash -c 'grep -q "map \$http_x_forwarded_proto \$xfp" "$1/frontend/nginx.conf" && ! grep -q "X-Forwarded-Proto \$http_x_forwarded_proto" "$1/frontend/nginx.conf"' _ "$ROOT"
t "host nginx (http): схема сохраняется, запасной \$scheme, без map{}" bash -c 'f="$1/deployment/nginx/site.http.conf.tpl"; grep -q "set \$xfp \$http_x_forwarded_proto" "$f" && grep -q "X-Forwarded-Proto \$xfp" "$f" && ! grep -Eq "^[[:space:]]*map " "$f"' _ "$ROOT"
t "host nginx (tls): сам завершает TLS — https фиксирован" bash -c 'grep -q "X-Forwarded-Proto https" "$1/deployment/nginx/site.tls.conf.tpl"' _ "$ROOT"
t "web nginx: /livekit/ передаёт X-Forwarded-*" bash -c 'awk "/location \/livekit\//,/^    }/" "$1/frontend/nginx.conf" | grep -q "X-Forwarded-Proto"' _ "$ROOT"

# ---- конфигурация: потоки ASR, таймауты комнаты
t ".env.example описывает ASR_INTEROP_THREADS и рекомендации по vCPU" bash -c 'grep -q "^ASR_INTEROP_THREADS=" "$1/.env.example" && grep -q "4 vCPU" "$1/.env.example"' _ "$ROOT"
t "compose передаёт ASR_INTEROP_THREADS и таймауты комнаты LiveKit" bash -c 'grep -q "ASR_INTEROP_THREADS" "$1/deployment/compose.yml" && grep -q "departure_timeout" "$1/deployment/compose.yml" && grep -q "empty_timeout" "$1/deployment/compose.yml"' _ "$ROOT"

# ---- models.sh: только полная модель (квантованная GigaAM снята с вооружения)
G="$TMP/gg"; mkdir -p "$G/src"; head -c 2000000 /dev/zero > "$G/src/v3_e2e_rnnt.ckpt"; echo tok > "$G/src/v3_e2e_rnnt_tokenizer.model"
printf 'DATA_ROOT=%s/data\nASR_MODEL_NAME=v3_e2e_rnnt\n' "$G" > "$G/env"
t "models.sh: Full подготовлена" bash -c 'bash "$1/scripts/models.sh" --env "$2/env" --from-dir "$2/src"' _ "$ROOT" "$G"
t "models.sh: параметр --gguf больше не поддерживается" bash -c '! bash "$1/scripts/models.sh" --env "$2/env" --from-dir "$2/src" --gguf --skip-full >/dev/null 2>&1' _ "$ROOT" "$G"
t "models.sh: адреса квантованной модели в скрипте нет, на диск она не попадает" bash -c '! grep -q "handy-computer" "$1/scripts/models.sh" && ! ls "$2/data/models/gigaam" | grep -qi "gguf"' _ "$ROOT" "$G"
t "compose: выбор модели не зашит (ASR_MODEL_ID — запасной), токен для управления моделями передаётся" bash -c 'grep -q "ASR_MODEL_ID:" "$1/deployment/compose.yml" && grep -q "INTERNAL_API_TOKEN" "$1/deployment/compose.yml"' _ "$ROOT"

# ---- realtime: nginx и рекомендации
for f in deployment/nginx/site.http.conf.tpl deployment/nginx/site.tls.conf.tpl frontend/nginx.conf; do
  t "$f: у WebSocket/LiveKit нет буферизации, включён tcp_nodelay" bash -c 'f="$1/$2"; [ "$(grep -c "tcp_nodelay on" "$f")" -ge 2 ] && [ "$(grep -c "proxy_buffering off" "$f")" -ge 2 ] && [ "$(grep -c "proxy_request_buffering off" "$f")" -ge 2 ]' _ "$ROOT" "$f"
done
t "обычный location (не realtime) буферизацию не отключает" bash -c 'awk "/location \/ \{/,/^    \}/" "$1/deployment/nginx/site.http.conf.tpl" | grep -vq "proxy_buffering off"' _ "$ROOT"
rtc() { bash -c 'source "$1/scripts/lib/envlib.sh"; REPO_ROOT="$1"; shift; n=0; w=""; ko(){ :; }; kw(){ w+="$*"$'"'"'\n'"'"'; }; realtime_config_check ko kw; printf "%s" "$w"' _ "$ROOT" "$@"; }
t "ASR: 6 потоков × 2 параллельных на 8 ядер — предупреждение о переподписке" bash -c 'out="$(env NPROC_OVERRIDE=8 ASR_CPU_THREADS=6 ASR_MAX_CONCURRENT_INFERENCE=2 bash -c '"'"'source "$1/scripts/lib/envlib.sh"; REPO_ROOT="$1"; w=""; kw(){ w+="$*"; }; realtime_config_check : kw; printf "%s" "$w"'"'"' _ "$1")"; printf "%s" "$out" | grep -q "12 потоков на 8 ядер"' _ "$ROOT"
t "ASR: 6 потоков × 1 на 8 ядер — без предупреждений" bash -c 'out="$(env NPROC_OVERRIDE=8 ASR_CPU_THREADS=6 ASR_MAX_CONCURRENT_INFERENCE=1 bash -c '"'"'source "$1/scripts/lib/envlib.sh"; REPO_ROOT="$1"; w=""; kw(){ w+="$*"; }; realtime_config_check : kw; printf "%s" "$w"'"'"' _ "$1")"; [ -z "$out" ]' _ "$ROOT"
t "ASR_CPU_THREADS=0 — предупреждение «все ядра»" bash -c 'out="$(env NPROC_OVERRIDE=8 ASR_CPU_THREADS=0 bash -c '"'"'source "$1/scripts/lib/envlib.sh"; REPO_ROOT="$1"; w=""; kw(){ w+="$*"; }; realtime_config_check : kw; printf "%s" "$w"'"'"' _ "$1")"; printf "%s" "$out" | grep -q "ВСЕ ядра"' _ "$ROOT"
t "LiveKit v1.9.0 — предупреждение про 404 на /rtc/v1; v1.13.7 и новее — нет" bash -c 'chk(){ env NPROC_OVERRIDE=8 ASR_CPU_THREADS=4 LIVEKIT_IMAGE_TAG="$1" bash -c '"'"'source "$1/scripts/lib/envlib.sh"; REPO_ROOT="$1"; w=""; kw(){ w+="$*"; }; realtime_config_check : kw; printf "%s" "$w"'"'"' _ "$2"; }; chk v1.9.0 "$1" | grep -q "404 на /rtc/v1" && [ -z "$(chk v1.13.7 "$1")" ] && [ -z "$(chk v1.14.0 "$1")" ]' _ "$ROOT"
t "semver_lt сравнивает числа, а не строки (v1.9.0 < v1.13.7)" bash -c 'source "$1/scripts/lib/envlib.sh"; semver_lt v1.9.0 v1.13.7 && ! semver_lt v1.13.7 v1.9.0 && ! semver_lt v1.13.7 v1.13.7' _ "$ROOT"
t "собственный nginx-site без realtime-директив распознаётся" bash -c 'source "$1/scripts/lib/envlib.sh"; printf "location /livekit/ {}\n" > "$2/old.conf"; ! site_realtime_ok "$2/old.conf" && site_realtime_ok "$1/deployment/nginx/site.http.conf.tpl"' _ "$ROOT" "$TMP"
