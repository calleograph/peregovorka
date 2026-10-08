# shellcheck shell=bash
# Подключается из run.sh: установка одной командой (install.sh, setup.sh --auto), первичный вход (bootstrap-admin.sh) и восстановление доступа
# (admin-reset.sh). Docker подставной; пароли и адреса — вымышленные. Реальный Docker/nginx/сеть не используются.
BB="$TMP/bootstrap"; mkdir -p "$BB/bin"
cat > "$BB/bin/docker" <<'DOCK'
#!/usr/bin/env bash
args="$*"
case "$args" in
  *"app.cli bootstrap-admin"*)
    if [ "${FAKE_EXISTS:-0}" = 1 ]; then printf 'EXISTS=1\nUSERNAME=admin\n'; else printf 'USERNAME=admin\nPASSWORD=Fk3-test-PASS-xyz\n'; fi ;;
  *"app.cli admin-reset"*)
    printf '%s\n' "$args" > "${FAKE_LOG:-/dev/null}"
    printf 'USERNAME=admin\nPASSWORD=Nw9-reset-PASS-abc\nSESSIONS_CLOSED=2\n' ;;
  *"import app"*) exit 0 ;;
  info) exit 0 ;;
  *) exit 0 ;;
esac
DOCK
chmod +x "$BB/bin/docker"
cat > "$BB/test.env" <<'ENV'
COMPOSE_PROJECT_NAME=peregovorka-test
APP_PUBLIC_URL=https://192.0.2.10
DATA_ROOT=/srv/peregovorka-test-data
ENV
bb() { # bb СКРИПТ [арг…] — запуск скрипта проекта с подставным docker и тестовым .env
  local sc="$1"; shift
  env -i PATH="$BB/bin:$PATH" HOME="$TMP" ENV_FILE="$BB/test.env" FAKE_LOG="$BB/log" FAKE_EXISTS="${FAKE_EXISTS:-0}" bash "$ROOT/scripts/$sc" "$@" 2>&1
}

OUT1="$(bb bootstrap-admin.sh)"
t "bootstrap-admin: блок «ПЕРВИЧНЫЙ ВХОД В PEREGOVORKA»" bash -c 'grep -q "ПЕРВИЧНЫЙ ВХОД В PEREGOVORKA" <<<"$1"' _ "$OUT1"
t "bootstrap-admin: показаны URL, логин и первичный пароль" bash -c 'grep -q "https://192.0.2.10" <<<"$1" && grep -q "admin" <<<"$1" && grep -q "Fk3-test-PASS-xyz" <<<"$1"' _ "$OUT1"
t "bootstrap-admin: «ПАРОЛЬ ПОКАЗЫВАЕТСЯ ТОЛЬКО СЕЙЧАС» и инструкция восстановления" bash -c 'grep -q "ПАРОЛЬ ПОКАЗЫВАЕТСЯ ТОЛЬКО СЕЙЧАС" <<<"$1" && grep -q "admin-reset.sh" <<<"$1"' _ "$OUT1"
t "bootstrap-admin: подсказка про мастер настройки и обновление" bash -c 'grep -q "LDAP" <<<"$1" && grep -q "update.sh" <<<"$1"' _ "$OUT1"
t "bootstrap-admin: пароль нигде не записан в файлы" bash -c '! grep -Il "Fk3-test-PASS-xyz" "$1" "$2" "$3" 2>/dev/null | grep -q .' _ "$BB/test.env" "$ROOT/.env.example" "$BB/log"
OUT2="$(FAKE_EXISTS=1 bb bootstrap-admin.sh)"
t "bootstrap-admin: если администратор уже есть — пароль не показывается" bash -c '! grep -q "PASSWORD\|Fk3-" <<<"$1" && grep -q "уже создан" <<<"$1"' _ "$OUT2"

OUT3="$(bb admin-reset.sh --yes --create --username admin)"
t "admin-reset: новый пароль показан в заметном блоке" bash -c 'grep -q "Nw9-reset-PASS-abc" <<<"$1" && grep -q "ВОССТАНОВЛЕН" <<<"$1" && grep -q "ПАРОЛЬ ПОКАЗЫВАЕТСЯ ТОЛЬКО СЕЙЧАС" <<<"$1"' _ "$OUT3"
t "admin-reset: в команду передан исполнитель (для аудита) и --create" bash -c 'grep -q -- "--actor" "$1" && grep -q -- "--create" "$1" && grep -q -- "--username admin" "$1"' _ "$BB/log"
t "admin-reset: без --yes и без согласия ничего не делает" bash -c 'out="$(env -i PATH="$1/bin:$PATH" HOME="$2" ENV_FILE="$1/test.env" bash "$3/scripts/admin-reset.sh" <<<"n" 2>&1)"; ! grep -q "Nw9-reset" <<<"$out" && grep -q "Отменено" <<<"$out"' _ "$BB" "$TMP" "$ROOT"
t "admin-reset: --help печатает справку" bash -c 'bash "$1/scripts/admin-reset.sh" --help | grep -q "ОФИЦИАЛЬНОЕ восстановление"' _ "$ROOT"
t "admin-reset и bootstrap-admin: синтаксис" bash -c 'bash -n "$1/scripts/admin-reset.sh" && bash -n "$1/scripts/bootstrap-admin.sh" && bash -n "$1/scripts/lib/adminlib.sh"' _ "$ROOT"

# ---- корневой install.sh («wget → chmod → sudo ./install.sh»)
t "install.sh: синтаксис и справка" bash -c 'bash -n "$1/install.sh" && bash "$1/install.sh" --help | grep -q "sudo ./install.sh"' _ "$ROOT"
t "install.sh: неизвестный параметр отвергается" bash -c '! bash "$1/install.sh" --no-such-flag >/dev/null 2>&1' _ "$ROOT"
t "install.sh: не содержит паролей и заранее заданных учётных данных" bash -c '! grep -Eiq "password *= *['"'"'\"][^$]|passwd" "$1/install.sh"' _ "$ROOT"

# ---- setup.sh --auto: .env без вопросов, без LDAP, с самоподписанным сертификатом
AE="$TMP/auto.env"; rm -f "$AE"
t "setup --auto создаёт .env без вопросов" bash -c 'cd "$1" && PEREGOVORKA_TEST_NO_ROOT_CHECK=1 ENV_FILE="$2" bash scripts/setup.sh --auto --env-only --host 192.0.2.10 --https-port 8443 --data "$3/auto-data" --skip-models </dev/null' _ "$ROOT" "$AE" "$TMP"
t "auto .env: адрес, профиль и имя" bash -c 'grep -q "^APP_PUBLIC_URL=https://192.0.2.10:8443" "$1" && grep -q "^INSTALL_PROFILE=standalone" "$1" && grep -q "^COMPOSE_PROJECT_NAME=peregovorka" "$1" && grep -q "^NGINX_LISTEN_PORT=8443" "$1"' _ "$AE"
t "auto .env: LDAP не требуется (пусто), заглушек CHANGE_ME нет" bash -c 'source "$1/scripts/lib/envlib.sh"; ! grep -Eq "^LDAP_(URIS|BIND_PASSWORD|BASE_DN)=.+" "$2" && [ -z "$(env_placeholders "$2")" ]' _ "$ROOT" "$AE"
t "auto .env: секреты сгенерированы, права 600" bash -c 'grep -Eq "^POSTGRES_PASSWORD=[0-9a-f]{48}$" "$1" && [ "$(stat -c %a "$1")" = 600 ]' _ "$AE"
t "auto: самоподписанный сертификат HTTPS создан (ключ 600) и прописан в .env" bash -c 'crt="$(sed -n "s/^NGINX_TLS_CERT=//p" "$1")"; key="$(sed -n "s/^NGINX_TLS_KEY=//p" "$1")"; [ -s "$crt" ] && [ -s "$key" ] && [ "$(stat -c %a "$key")" = 600 ] && openssl x509 -in "$crt" -noout -text | grep -q "IP Address:192.0.2.10"' _ "$AE"
t "auto: первичного пароля в .env нет" bash -c '! grep -Eiq "bootstrap|admin_pass|first_pass" "$1"' _ "$AE"
t "compose: LDAP_* не обязательны (пустые значения допустимы)" bash -c 'grep -q "LDAP_URIS: \${LDAP_URIS:-}" "$1/deployment/compose.yml" && grep -q "LDAP_CA_FILE:-/dev/null" "$1/deployment/compose.yml" && ! grep -q "LDAP_URIS:?" "$1/deployment/compose.yml"' _ "$ROOT"
t "compose: каталоги CA и вложений чата вынесены в постоянные тома" bash -c 'grep -q "/data/ca" "$1/deployment/compose.yml" && grep -q "/data/chat-files" "$1/deployment/compose.yml"' _ "$ROOT"
t "update: новые каталоги данных (CA, вложения чата) создаются до запуска контейнеров" bash -c 'source "$1/scripts/lib/common.sh" 2>/dev/null; DATA_ROOT="$2/updata"; mkdir -p "$DATA_ROOT"; upd_ensure_data_dirs >/dev/null 2>&1; [ -d "$DATA_ROOT/ca" ] && [ -d "$DATA_ROOT/chat-files" ]' _ "$ROOT" "$TMP"
