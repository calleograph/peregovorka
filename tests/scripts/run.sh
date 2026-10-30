#!/usr/bin/env bash
# Регрессионные тесты shell-части (setup/preflight/models): bash tests/scripts/run.sh
# Не требуют Docker/root; все действия — во временном каталоге.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"; export ROOT
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

# ---- 6a. models.sh: докачка при обрыве, права доступа, целостность, диагностика
R="$TMP/rng"; mkdir -p "$R/srv"; head -c 3000000 /dev/urandom > "$R/srv/v3_e2e_rnnt.ckpt"; echo tok > "$R/srv/v3_e2e_rnnt_tokenizer.model"
# адрес источника — только через .env (скрипты сбрасывают GIGAAM_* из окружения), порт 18931 — тестовый сервер
printf 'DATA_ROOT=%s/data\nASR_MODEL_NAME=v3_e2e_rnnt\nGIGAAM_MODEL_BASE_URL=http://127.0.0.1:18931\n' "$R" > "$R/env"
printf 'DATA_ROOT=%s/data\nASR_MODEL_NAME=v3_e2e_rnnt\nGIGAAM_MODEL_BASE_URL=http://127.0.0.1:1\n' "$R" > "$R/env.dead"
python3 "$ROOT/tests/scripts/rangeserver.py" "$R/srv" 18931 >/dev/null 2>&1 & RNG_PID=$!; sleep 1
t "models.sh: обрыв загрузки посреди файла — докачка и полный размер" bash -c 'bash "$1/scripts/models.sh" --env "$2/env" && cmp "$2/srv/v3_e2e_rnnt.ckpt" "$2/data/models/gigaam/v3_e2e_rnnt.ckpt"' _ "$ROOT" "$R"
t "models.sh: без .part после успеха" bash -c '! ls "$1"/data/models/gigaam/*.part'  _ "$R"
t "models.sh: готовая модель не качается (сервер недоступен)" bash -c 'bash "$1/scripts/models.sh" --env "$2/env.dead" | grep -q "уже на месте"' _ "$ROOT" "$R"
rm -f "$R/data/models/gigaam/v3_e2e_rnnt_tokenizer.model"
t "models.sh: наличие одного чекпойнта — не готовность, дозагружает токенайзер" bash -c 'bash "$1/scripts/models.sh" --env "$2/env" && [ -s "$2/data/models/gigaam/v3_e2e_rnnt_tokenizer.model" ]' _ "$ROOT" "$R"
# импорт из каталога с ограниченными правами: модель должна стать читаемой для «чужого» пользователя контейнера
rm -rf "$R/data"; mkdir -p "$R/imp"; cp "$R/srv/"* "$R/imp/"; chmod 600 "$R/imp/"*
t "models.sh --from-dir: файлы 600 → читаемы остальным (uid контейнера)" bash -c 'bash "$1/scripts/models.sh" --env "$2/env" --from-dir "$2/imp" && [ "$(stat -c %a "$2/data/models/gigaam/v3_e2e_rnnt.ckpt")" = 644 ]' _ "$ROOT" "$R"
chmod 700 "$R/data/models" "$R/data/models/gigaam"; chmod 600 "$R/data/models/gigaam/"*
t "models_fix_perms: каталоги 700/файлы 600 → 755/644, не 777" bash -c 'source "$1/scripts/lib/common.sh"; load_env "$2/env"; models_fix_perms && [ "$(stat -c %a "$DATA_ROOT/models/gigaam")" = 755 ] && [ "$(stat -c %a "$DATA_ROOT/models/gigaam/v3_e2e_rnnt.ckpt")" = 644 ]' _ "$ROOT" "$R"
t "models_fix_perms: трогает только каталог моделей" bash -c 'chmod 700 "$1/data"; source "$2/scripts/lib/common.sh"; load_env "$1/env"; models_fix_perms; [ "$(stat -c %a "$1/data")" = 700 ]' _ "$R" "$ROOT"
chmod 755 "$R/data"
rm -rf "$R/data"; head -c 3000000 /dev/urandom > "$R/srv/other.ckpt"
t "models.sh: неверный SHA-256 из .env — файл в .failed и отказ" bash -c 'cp "$2/env" "$2/env.sha"; echo GIGAAM_CKPT_SHA256=0000 >> "$2/env.sha"; bash "$1/scripts/models.sh" --env "$2/env.sha"; [ $? -ne 0 ] && [ -f "$2/data/models/gigaam/v3_e2e_rnnt.ckpt.failed" ]' _ "$ROOT" "$R"
t "models.sh: верный SHA-256 из .env принимается" bash -c 'rm -rf "$2/data"; cp "$2/env" "$2/env.sha"; echo "GIGAAM_CKPT_SHA256=$(sha256sum "$2/srv/v3_e2e_rnnt.ckpt" | cut -d" " -f1)" >> "$2/env.sha"; bash "$1/scripts/models.sh" --env "$2/env.sha"' _ "$ROOT" "$R"
rm -rf "$R/data"; printf '<html>login</html>' > "$R/srv/html.txt"; head -c 2000000 /dev/zero | tr '\0' ' ' > "$R/srv/v3_e2e_rnnt.ckpt"; { printf '<!DOCTYPE html>'; cat "$R/srv/v3_e2e_rnnt.ckpt"; } > "$R/x" && mv "$R/x" "$R/srv/v3_e2e_rnnt.ckpt"
t "models.sh: HTML вместо модели (страница входа прокси) отвергается" bash -c 'bash "$1/scripts/models.sh" --env "$2/env"; [ $? -ne 0 ] && [ -f "$2/data/models/gigaam/v3_e2e_rnnt.ckpt.failed" ]' _ "$ROOT" "$R"
t "models.sh: недоступный адрес — понятная причина и способ продолжить" bash -c 'out="$(bash "$1/scripts/models.sh" --env "$2/env.dead" 2>&1)"; [ $? -ne 0 ] && echo "$out" | grep -q "соединение невозможно" && echo "$out" | grep -q -- "--from-dir"' _ "$ROOT" "$R"
kill "$RNG_PID" 2>/dev/null
# разбор причины падения сервиса по журналу (подмена dc): PermissionError на модели → автоисправление прав возможно, ключи маскируются
t "diagnose_service: PermissionError на /models → DIAG_FIX=models и команда исправления" bash -c 'source "$1/scripts/lib/common.sh"; dc() { printf "Traceback (most recent call last):\nPermissionError: [Errno 13] Permission denied: %s\nINTERNAL_API_TOKEN=supersecret\n" "/models/gigaam/catalog.json"; }; out="$(diagnose_service asr 2>&1)"; echo "$out" | grep -q "scripts/models.sh" && ! echo "$out" | grep -q supersecret' _ "$ROOT"
t "diagnose_service: DIAG_FIX выставляется для прав на модель" bash -c 'source "$1/scripts/lib/common.sh"; dc() { echo "PermissionError: [Errno 13] Permission denied: /models/gigaam/catalog.json"; }; diagnose_service asr >/dev/null 2>&1; [ "$DIAG_FIX" = models ]' _ "$ROOT"
t "diagnose_service: нет файла модели → подсказка про models.sh --from-dir" bash -c 'source "$1/scripts/lib/common.sh"; dc() { echo "FileNotFoundError: /models/gigaam/v3_e2e_rnnt.ckpt"; }; diagnose_service asr 2>&1 | grep -q -- "--from-dir"' _ "$ROOT"

# ---- вики: проверка исходников (scripts/check_wiki.py) ловит битые ссылки, служебные пометки, ссылки [[…]] и посторонние адреса
W="$TMP/wikitest"; mkdir -p "$W/scripts" "$W/wiki/_meta"; cp "$ROOT/scripts/check_wiki.py" "$W/scripts/"; cp "$ROOT/VERSION" "$W/"
printf '# Home\n\n%s\n\n[Другая](Другая)\n' "$(cat "$ROOT/VERSION")" > "$W/wiki/Home.md"
printf '# Другая\n\n%s\n\n## Что дальше\n\n- [Home](Home)\n' "$(printf 'а%.0s' $(seq 1 700))" > "$W/wiki/Другая.md"
printf '[Home](Home)\n- [Другая](Другая)\n' > "$W/wiki/_Sidebar.md"; printf 'v%s\n' "$(cat "$ROOT/VERSION")" > "$W/wiki/_Footer.md"
printf '| Home | 1 |\n| Другая | 1 |\n' > "$W/wiki/_meta/Проверка-страниц.md"
t "check_wiki: корректные страницы проходят" python3 "$W/scripts/check_wiki.py"
cp "$W/wiki/Другая.md" "$W/good.md"
printf '\n[Нет такой](Нет-такой) и 10.1.2.3\n' >> "$W/wiki/Другая.md"
t "check_wiki: битая ссылка и чужой адрес отвергаются" bash -c '! python3 "$1/scripts/check_wiki.py"' _ "$W"
for bad in '> Относится к версии 0.1.0 · проверено 2026-01-01 · уровень проверки: 1' '| a | [[Home|Home]] |' 'см. [[Home' 'проверено на стенде'; do
  cp "$W/good.md" "$W/wiki/Другая.md"; printf '\n%s\n' "$bad" >> "$W/wiki/Другая.md"
  t "check_wiki: отвергается «$bad»" bash -c '! python3 "$1/scripts/check_wiki.py" >/dev/null' _ "$W"
done
cp "$W/good.md" "$W/wiki/Другая.md"; printf '| Home | 1 |\n' > "$W/wiki/_meta/Проверка-страниц.md"
t "check_wiki: страница без строки в _meta отвергается" bash -c '! python3 "$1/scripts/check_wiki.py" >/dev/null' _ "$W"

# ---- scripts/ports.sh: перечень портов из .env без Docker; другой порт в .env — другой перечень; неверные аргументы и отсутствие файла — код 2
printf 'APP_PUBLIC_URL=https://meet.example.org\nLIVEKIT_NODE_IP=203.0.113.9\nLIVEKIT_TCP_PORT=7881\nLIVEKIT_UDP_PORT=50000\nINSTALL_PROFILE=standalone\n' > "$TMP/ports.env"
t "ports.sh: порты берутся из .env" bash -c 'out="$("$1/scripts/ports.sh" --env "$2" --format csv)"; echo "$out" | grep -q "UDP,50000" && echo "$out" | grep -q "TCP,7881" && echo "$out" | grep -q "203.0.113.9"' _ "$ROOT" "$TMP/ports.env"
t "ports.sh: заявка сетевому администратору" bash -c '"$1/scripts/ports.sh" --env "$2" --format request | grep -q "Прошу открыть"' _ "$ROOT" "$TMP/ports.env"
t "ports.sh: вывод в файл" bash -c '"$1/scripts/ports.sh" --env "$2" --format md --out "$3/p.md" >/dev/null && grep -q "50000" "$3/p.md"' _ "$ROOT" "$TMP/ports.env" "$TMP"
t "ports.sh: нет .env — код 2" bash -c '"$1/scripts/ports.sh" --env /nonexistent >/dev/null 2>&1; [ $? -eq 2 ]' _ "$ROOT"
t "ports.sh: неизвестный формат — код 2" bash -c '"$1/scripts/ports.sh" --env "$2" --format exe >/dev/null 2>&1; [ $? -eq 2 ]' _ "$ROOT" "$TMP/ports.env"

# shellcheck source=part_docker.sh
source "$ROOT/tests/scripts/part_docker.sh"
# shellcheck source=part_diag.sh
source "$ROOT/tests/scripts/part_diag.sh"
# shellcheck source=part_build.sh
source "$ROOT/tests/scripts/part_build.sh"
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
# shellcheck source=part_video.sh
source "$ROOT/tests/scripts/part_video.sh"
# shellcheck source=part_img.sh
source "$ROOT/tests/scripts/part_img.sh"
# shellcheck source=part_security_reset.sh
source "$ROOT/tests/scripts/part_security_reset.sh"
# shellcheck source=part_release_channel.sh
source "$ROOT/tests/scripts/part_release_channel.sh"
# shellcheck source=part_livekit_url.sh
source "$ROOT/tests/scripts/part_livekit_url.sh"

echo "shell-тесты: пройдено $PASS, провалено $FAIL"
[ "$FAIL" -eq 0 ]
