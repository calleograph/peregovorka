#!/usr/bin/env bash
# Регрессионные тесты shell-части (setup/preflight/models): bash tests/scripts/run.sh
# Не требуют Docker/root; все действия — во временном каталоге.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=../../scripts/lib/envlib.sh
source "$ROOT/scripts/lib/envlib.sh"
PASS=0; FAIL=0
t() { # t "описание" команда...
  local d="$1"; shift
  if "$@" >/dev/null 2>&1; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); echo "FAIL: $d"; [ -z "${GITHUB_ACTIONS:-}" ] || echo "::error title=shell-тест не пройден::$d"; fi
}
eq() { [ "$1" = "$2" ]; }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

# ---- 2. квотирование .env: значения читаются bash и load_env ровно как введены, ничего не исполняется
roundtrip() {
  local v="$1" q f; q="$(dotenv_quote "$v")" || return 2
  f="$TMP/rt.env"; printf 'KEY=%s\n' "$q" > "$f"
  local got; got="$(env -i PATH="$PATH" bash -c 'set -a; . "$1"; printf "%s" "$KEY"' _ "$f" 2>"$TMP/err")" || return 1
  [ ! -s "$TMP/err" ] && [ "$got" = "$v" ] || return 1
  got="$(env -i PATH="$PATH" bash -c 'source "$1"; load_env "$2"; printf "%s" "$KEY"' _ "$ROOT/scripts/lib/common.sh" "$f")" || return 1
  [ "$got" = "$v" ]
}
t "DN с пробелами и кириллицей" roundtrip 'CN=Отдел ИТ,OU=Отдел ИТ,OU=Staff,OU=CORP,DC=corp,DC=local'
t "решётка" roundtrip 'pass#word with # hash'
t "доллар" roundtrip 'abc$HOME$(rm -rf x)'
t "обратный слэш" roundtrip 'C:\path\to'
t "двойные кавычки" roundtrip 'say "hi" now'
t "одинарная кавычка" roundtrip "it's fine"
t "подстановка команд" roundtrip '`id`; touch /tmp/x'
t "простое значение без кавычек" eq "$(dotenv_quote /srv/data)" "/srv/data"
t "смесь ' \" \$ отвергается" bash -c 'source "$1"; ! dotenv_quote "a'"'"'b\"c\$d"' _ "$ROOT/scripts/lib/envlib.sh"
t "перевод строки отвергается" bash -c 'source "$1"; ! dotenv_quote $'"'"'a\nb'"'"'' _ "$ROOT/scripts/lib/envlib.sh"

# ---- 4/3. LDAP URI
t "uri без слэша" bash -c 'source "$1"; parse_ldap_uri ldaps://AD1.corp.local:636 && [ "$LDAP_HOST" = AD1.corp.local ] && [ "$LDAP_PORT" = 636 ]' _ "$ROOT/scripts/lib/envlib.sh"
t "uri со слэшем" bash -c 'source "$1"; parse_ldap_uri ldaps://AD1.corp.local:636/ && [ "$LDAP_HOST" = AD1.corp.local ] && [ "$LDAP_PORT" = 636 ]' _ "$ROOT/scripts/lib/envlib.sh"
t "uri без порта" bash -c 'source "$1"; parse_ldap_uri ldaps://dc.corp.local && [ "$LDAP_PORT" = 636 ]' _ "$ROOT/scripts/lib/envlib.sh"
t "ldap:// отвергается" bash -c 'source "$1"; ! normalize_ldap_uris ldap://dc:389' _ "$ROOT/scripts/lib/envlib.sh"
t "нормализация списка" eq "$(normalize_ldap_uris ' ldaps://a.x:636/ , ldaps://b.x ')" "ldaps://a.x:636,ldaps://b.x:636"

# ---- 4. CHANGE_ME
printf '# Строки CHANGE_ME обязательны к замене\nA=1\n' > "$TMP/c1.env"
t "комментарий с CHANGE_ME — не ошибка" eq "$(env_placeholders "$TMP/c1.env")" ""
printf '# комментарий\nPW=CHANGE_ME_x\n' > "$TMP/c2.env"
t "значение CHANGE_ME обнаруживается" test -n "$(env_placeholders "$TMP/c2.env")"
t ".env.example: заглушки есть, а комментарий их не создаёт" test "$(env_placeholders "$ROOT/.env.example" | wc -l)" -ge 5

# ---- 1. DATA_ROOT
for bad in '\fileserver\share' 'smb://srv/share' '//srv/x/../y' 'relative/dir' 'C:\data' 'https://x/y' '/srv/with space' '/'; do
  t "DATA_ROOT отвергает «$bad»" bash -c 'source "$1"; ! validate_local_dir "$2" DATA_ROOT >/dev/null' _ "$ROOT/scripts/lib/envlib.sh" "$bad"
done
t "DATA_ROOT принимает /srv/peregovorka-data" validate_local_dir /srv/peregovorka-data
t "сообщение про SMB понятно" bash -c 'source "$1"; validate_local_dir "\\fileserver\share" DATA_ROOT | grep -q "SMB"' _ "$ROOT/scripts/lib/envlib.sh"

# ---- 5. RAM
t "ВМ 7.8 ГиБ при требовании 8 — ок" eq "$(ram_verdict 8178893 8)" ok
t "7.25 ГиБ — ок (в пределах допуска)" eq "$(ram_verdict 7602176 8)" ok
t "6.5 ГиБ — предупреждение" eq "$(ram_verdict 6815744 8)" warn
t "4 ГиБ — отказ" eq "$(ram_verdict 4194304 8)" fail

# ---- сквозной «чистый» запуск мастера (.env), включая неверные ответы
W="$TMP/repo"; mkdir -p "$W/scripts"; cp -r "$ROOT/scripts/." "$W/scripts/"; cp "$ROOT/.env.example" "$W/"
ANS=$(printf '%s\n' \
  "peregovorka-test" "meet.corp.local" "192.0.2.241" \
  '\fileserver\share' "smb://x/y" "/srv/peregovorka-test-data" "2" "y" \
  "ldaps://AD1.corp.local:636/" "DC=corp,DC=local" "CN=svc ldap,OU=Служебные,DC=corp,DC=local" 'p@ss $w#rd"x' \
  "/etc/peregovorka/ad-ca.pem" "CN=Отдел ИТ,OU=Отдел ИТ,OU=Staff,OU=CORP,DC=corp,DC=local")
t "мастер --env-only завершается успешно" bash -c 'cd "$1" && printf "%s\n" "$2" | bash scripts/setup.sh --profile shared-host --env-only' _ "$W" "$ANS"
E="$W/.env"
t ".env создан" test -f "$E"
t "DATA_ROOT локальный" eq "$(env -i PATH="$PATH" bash -c 'set -a; . "$1"; printf "%s" "$DATA_ROOT"' _ "$E")" "/srv/peregovorka-test-data"
t "LDAP_URIS без слэша" eq "$(env -i PATH="$PATH" bash -c 'set -a; . "$1"; printf "%s" "$LDAP_URIS"' _ "$E")" "ldaps://AD1.corp.local:636"
t "DN администраторов читается целиком" eq "$(env -i PATH="$PATH" bash -c 'set -a; . "$1"; printf "%s" "$LDAP_ADMIN_GROUP_DN"' _ "$E")" "CN=Отдел ИТ,OU=Отдел ИТ,OU=Staff,OU=CORP,DC=corp,DC=local"
t "пароль со спецсимволами сохранён буквально" eq "$(env -i PATH="$PATH" bash -c 'set -a; . "$1"; printf "%s" "$LDAP_BIND_PASSWORD"' _ "$E")" 'p@ss $w#rd"x'
t "после мастера заглушек нет" eq "$(env_placeholders "$E")" ""
t "shared-host: потоки ASR заданы разумно (1..nproc), а не «все ядра»" bash -c 'set -a; . "$1"; n="$(nproc 2>/dev/null || echo 4)"; [ "$ASR_CPU_THREADS" -ge 1 ] && [ "$ASR_CPU_THREADS" -le "$n" ] && [ "$ASR_INTEROP_THREADS" -ge 1 ]' _ "$E"
[ "$(uname -s)" = Linux ] && t "права .env 600 или строже" bash -c '[ "$(stat -c %a "$1")" -le 600 ]' _ "$E"
t "source .env не исполняет ничего (stderr пуст)" bash -c '[ -z "$(env -i PATH="$PATH" bash -c "set -a; . \"$1\"" 2>&1)" ]' _ "$E"

# ---- 6. models.sh: без проверки хеша, с диагностикой
M="$TMP/models"; mkdir -p "$M/src"; head -c 2000000 /dev/zero > "$M/src/v3_e2e_rnnt.ckpt"; echo tok > "$M/src/v3_e2e_rnnt_tokenizer.model"
printf 'DATA_ROOT=%s/data\nASR_MODEL_NAME=v3_e2e_rnnt\n' "$M" > "$M/env"
t "models.sh принимает файл с любым содержимым (хеш не проверяется)" bash -c 'bash "$1/scripts/models.sh" --env "$2/env" --from-dir "$2/src"' _ "$ROOT" "$M"
t "модель на месте" test -s "$M/data/models/gigaam/v3_e2e_rnnt.ckpt"
t "повторный запуск ничего не качает" bash -c 'bash "$1/scripts/models.sh" --env "$2/env" --from-dir /nonexistent | grep -q "уже на месте"' _ "$ROOT" "$M"
printf 'DATA_ROOT=%s\n' '\fileserver\share' > "$M/bad.env"
t "models.sh отказывает при UNC в DATA_ROOT" bash -c '! bash "$1/scripts/models.sh" --env "$2/bad.env" --from-dir "$2/src"' _ "$ROOT" "$M"
rm -rf "$M/data"; echo x > "$M/src/v3_e2e_rnnt.ckpt"
t "слишком маленький файл сохраняется как .failed" bash -c 'bash "$1/scripts/models.sh" --env "$2/env" --from-dir "$2/src"; [ $? -ne 0 ] && [ -f "$2/data/models/gigaam/v3_e2e_rnnt.ckpt.failed" ]' _ "$ROOT" "$M"

# shellcheck source=part_docker.sh
source "$ROOT/tests/scripts/part_docker.sh"
# shellcheck source=part_diag.sh
source "$ROOT/tests/scripts/part_diag.sh"
# shellcheck source=part_update.sh
source "$ROOT/tests/scripts/part_update.sh"
# shellcheck source=part_updater.sh
source "$ROOT/tests/scripts/part_updater.sh"
# shellcheck source=part_updater_race.sh
source "$ROOT/tests/scripts/part_updater_race.sh"
# shellcheck source=part_updater_install.sh
source "$ROOT/tests/scripts/part_updater_install.sh"
# shellcheck source=part_fixes.sh
source "$ROOT/tests/scripts/part_fixes.sh"
# shellcheck source=part_version.sh
source "$ROOT/tests/scripts/part_version.sh"
# shellcheck source=part_nginx.sh
source "$ROOT/tests/scripts/part_nginx.sh"
# shellcheck source=part_bootstrap.sh
source "$ROOT/tests/scripts/part_bootstrap.sh"
# shellcheck source=part_prereq.sh
source "$ROOT/tests/scripts/part_prereq.sh"
# shellcheck source=part_llm.sh
source "$ROOT/tests/scripts/part_llm.sh"
# shellcheck source=part_sip.sh
source "$ROOT/tests/scripts/part_sip.sh"

echo "shell-тесты: пройдено $PASS, провалено $FAIL"
[ "$FAIL" -eq 0 ]
