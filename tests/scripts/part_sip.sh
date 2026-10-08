# shellcheck shell=bash
# Подключается из run.sh: SIP-телефония (LiveKit SIP) — scripts/lib/siplib.sh, scripts/sip.sh, профиль compose, проверка при выключенной телефонии.
SP="$TMP/sip"; mkdir -p "$SP/data" "$SP/bin"
cat > "$SP/env" <<ENVF
COMPOSE_PROJECT_NAME=pg-sip-test
DATA_ROOT=$SP/data
ENVF
# sipx CMD… — выполнить фрагмент с загруженной библиотекой и тестовым .env (стерильное окружение; docker недоступен)
sipx() { local f="$1"; shift; env -i PATH="$SP/bin:$PATH" HOME="$TMP" ENV_FILE="$f" "$@" bash -c 'source "$1/scripts/lib/common.sh"; load_env "$ENV_FILE"; shift; eval "$1"' _ "$ROOT" "$SIPCMD"; }
sq() { SIPCMD="$2"; sipx "$1"; }

t "sip: по умолчанию телефония выключена" bash -c '! { source "$1/scripts/lib/siplib.sh"; sip_enabled; }' _ "$ROOT"
t "sip: порты по умолчанию — signalling 5060, RTP 20000-20100" eq "$(sq "$SP/env" 'echo "$(sip_port) $(sip_rtp)"')" "5060 20000-20100"
printf 'SIP_SIGNALING_PORT=5070\nSIP_RTP_START=30000\nSIP_RTP_END=30010\n' > "$SP/env.ports"; cat "$SP/env" >> "$SP/env.ports"
t "sip: порты настраиваются через .env" eq "$(sq "$SP/env.ports" 'echo "$(sip_port) $(sip_rtp)"')" "5070 30000-30010"
t "sip: образ по умолчанию livekit/sip, переопределяется SIP_IMAGE" bash -c '[ "$(source "$1/scripts/lib/siplib.sh"; sip_image)" = livekit/sip:v1.13.0 ] && [ "$(SIP_IMAGE=example/sip:1 bash -c "source \"$1/scripts/lib/siplib.sh\"; sip_image")" = example/sip:1 ]' _ "$ROOT"
t "sip: строка Redis для LiveKit использует общую шину (db 1)" bash -c 'source "$1/scripts/lib/siplib.sh"; REDIS_PASSWORD=pw; [[ "$(sip_livekit_redis_line)" == "redis: {address: redis:6379, password: pw, db: 1}" ]]' _ "$ROOT"
printf 'SIP_ENABLED=yes\n' > "$SP/env.on"; cat "$SP/env" >> "$SP/env.on"
t "sip: включена, но Docker/образа нет → sip_active ложь (контейнер не запускаем)" bash -c '! env -i PATH="/nonexistent" ENV_FILE="$2" /bin/bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; sip_active"' _ "$ROOT" "$SP/env.on"
t "sip: выключена — compose_args не содержит профиль sip" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; load_env \"\$ENV_FILE\"; compose_args 2>/dev/null; echo \"\${COMPOSE_ARGS[*]}\"")"; grep -q -- "compose" <<<"$out" && ! grep -q -- "--profile sip" <<<"$out"' _ "$ROOT" "$SP/env"

# ---- compose: сервис livekit-sip только по профилю, порты задаются переменными, без host-сети
COMPOSE="$ROOT/deployment/compose.yml"
t "sip: compose — livekit-sip запускается только по профилю sip" bash -c 'awk "/^  livekit-sip:/{f=1;next} f&&/^  [a-z]/{f=0} f" "$1" | grep -q "profiles: \[\"sip\"\]"' _ "$COMPOSE"
t "sip: compose — порты SIP/RTP из переменных, UDP и TCP для signalling" bash -c 'b="$(awk "/^  livekit-sip:/{f=1;next} f&&/^  [a-z]/{f=0} f" "$1")"; grep -q "SIP_SIGNALING_PORT" <<<"$b" && grep -q "/tcp" <<<"$b" && grep -q "SIP_RTP_START" <<<"$b"' _ "$COMPOSE"
t "sip: compose — без network_mode: host и без privileged" bash -c '! awk "/^  livekit-sip:/{f=1;next} f&&/^  [a-z]/{f=0} f" "$1" | grep -Eq "network_mode: *host|privileged"' _ "$COMPOSE"
t "sip: compose — секреты берутся из .env, а не зашиты" bash -c 'b="$(awk "/^  livekit-sip:/{f=1;next} f&&/^  [a-z]/{f=0} f" "$1")"; grep -q "api_secret: \${LIVEKIT_API_SECRET" <<<"$b" && grep -q "password: \${REDIS_PASSWORD" <<<"$b"' _ "$COMPOSE"

# ---- sip.sh
t "sip.sh status при выключенной телефонии: объясняет, что порты не открыты" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/sip.sh" status --env "$2" 2>&1)"; grep -q "выключена" <<<"$out" && grep -q "5060" <<<"$out" && grep -q "20000-20100" <<<"$out"' _ "$ROOT" "$SP/env"
t "sip.sh status напоминает, что браузерные порты (443/RTC) отдельные" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/sip.sh" status --env "$2" 2>&1)"; grep -q "не меняются" <<<"$out"' _ "$ROOT" "$SP/env"
t "sip.sh firewall без SIP_ALLOWED_CIDRS отказывается открывать порты" bash -c '! out="$(env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/sip.sh" firewall --env "$2" 2>&1)"; grep -q "SIP_ALLOWED_CIDRS" <<<"$out"' _ "$ROOT" "$SP/env"
printf 'SIP_ALLOWED_CIDRS=192.0.2.0/24, 198.51.100.7\n' > "$SP/env.fw"; cat "$SP/env" >> "$SP/env.fw"
t "sip.sh firewall показывает правила только для адресов АТС (без применения)" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/sip.sh" firewall --env "$2" 2>&1)"; grep -q "from 192.0.2.0/24 to any port 5060 proto udp" <<<"$out" && grep -q "from 198.51.100.7" <<<"$out" && grep -q "20000:20100" <<<"$out" && ! grep -q "allow 5060" <<<"$out" && grep -q "Показано без применения" <<<"$out"' _ "$ROOT" "$SP/env.fw"
printf 'SIP_ALLOWED_CIDRS=0.0.0.0/0;rm\n' > "$SP/env.bad"; cat "$SP/env" >> "$SP/env.bad"
t "sip.sh firewall отвергает некорректные адреса" bash -c '! env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/sip.sh" firewall --env "$2" >/dev/null 2>&1' _ "$ROOT" "$SP/env.bad"
t "sip.sh enable без root не меняет .env" bash -c '[ "$(id -u)" -eq 0 ] && exit 0; cp "$2" "$3"; ! env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/sip.sh" enable --env "$3" --yes >/dev/null 2>&1 && ! grep -q "SIP_ENABLED=yes" "$3"' _ "$ROOT" "$SP/env" "$SP/env.copy"
t "sip.sh: неизвестная команда — ошибка" bash -c '! env -i PATH="$PATH" HOME="$TMP" bash "$1/scripts/sip.sh" bogus --env "$2" >/dev/null 2>&1' _ "$ROOT" "$SP/env"

# ---- проверка: телефония не настроена = пропущено, а не сбой
t "sip: verify_sip при выключенной телефонии — «пропущено» и без сбоя" bash -c 'out="$(env -i PATH="$PATH" HOME="$TMP" ENV_FILE="$2" bash -c "source \"$1/scripts/lib/common.sh\"; source \"$1/scripts/lib/verifylib.sh\"; load_env \"\$ENV_FILE\"; verify_sip; echo FAILS=\${VERIFY_FAILS:-0}" 2>&1)"; grep -q "пропущено" <<<"$out" && grep -q "FAILS=0" <<<"$out"' _ "$ROOT" "$SP/env"
t "sip: точка обновления — sip_enable и sip_disable разрешены помощнику" bash -c 'source "$1/scripts/lib/repairlib.sh"; printf "%s\n" "${REPAIR_IDS[@]}" | grep -qx sip_enable && printf "%s\n" "${REPAIR_IDS[@]}" | grep -qx sip_disable' _ "$ROOT"
t ".env.example: SIP выключен по умолчанию, порты описаны" bash -c 'grep -q "^SIP_ENABLED=no" "$1/.env.example" && grep -q "^SIP_SIGNALING_PORT=5060" "$1/.env.example" && grep -q "^SIP_RTP_START=20000" "$1/.env.example"' _ "$ROOT"
