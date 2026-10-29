#!/usr/bin/env bash
# mask.sh — маскирование секретов в любом текстовом выводе (журналы, `docker compose config`, диагностика).
# Подключается через `source`; ничего не выполняет. Использование:   какая-то_команда | mask_stream
#
# Скрывается значение (имя ключа остаётся — по нему видно, что параметр задан):
#   *_PASSWORD, *_PASSWD, *_SECRET, *_TOKEN, *KEY* (API-ключи, LiveKit key/secret, master/application keys), *_AUTH (REDISCLI_AUTH),
#   значение после --requirepass/--masterauth/--password (в строке и следующим элементом списка YAML),
#   LDAP bind password, *_CREDENTIAL*, *PRIVATE*, Authorization/Bearer;
#   JWT-токены в любом месте строки; access_token/token/api_key/password в query-строках URL;
#   пароль в URL вида scheme://user:пароль@host (DATABASE_URL, REDIS_URL, SMB);
#   блок `keys:` конфигурации LiveKit (ключ: секрет — скрываются и имя ключа, и секрет);
#   JSON-пары "password": "…" / "token": "…" и т.п.
# Строки со «временными» параметрами (…_SECONDS, …_TTL…, …_FAILURES, …_LENGTH, …_PORT) не трогаются: это не секреты.
# Нужен GNU sed (Linux; Git Bash под Windows тоже подходит). Вывод построчный, без буферизации: годится для журнала, который читают по ходу работы.

mask_stream() {
  awk '
    # блок LiveKit "keys:" — значения всех следующих более вложенных строк "ключ: секрет"
    { line = $0 }
    # значение отдельным элементом списка после флага пароля (docker compose config: «- --requirepass» / «- <пароль>»)
    nextmask { nextmask = 0; if (line ~ /^[ \t]*-[ \t]*[^ \t]/) { sub(/-[ \t]*.*$/, "- ***", line); print line; fflush(); next } }
    line ~ /^[ \t]*-[ \t]*"?--?(requirepass|masterauth|password|passwd|pass|a)"?[ \t]*$/ { nextmask = 1 }
    inkeys {
      match(line, /^[ \t]*/); ind = RLENGTH
      if (line ~ /^[ \t]*$/ || ind <= keys_indent) { inkeys = 0 }
      # «ключ: секрет» LiveKit: скрываются оба — имя ключа API тоже значение из .env
      else if (line ~ /^[ \t]*[^ \t:#][^:]*:[ \t]*[^ \t]/) { sub(/[^ \t].*$/, "***: ***", line); print line; next }
    }
    line ~ /^[ \t]*keys:[ \t]*$/ { match(line, /^[ \t]*/); keys_indent = RLENGTH; inkeys = 1 }
    { print line; fflush() }
  ' | sed -u -E \
    -e '/^[[:space:]]*[A-Za-z0-9_.-]*(_SECONDS|_TTL|_TTL_[A-Za-z]+|_FAILURES|_LENGTH|_PORT)[[:space:]]*[=:]/Ib' \
    -e 's/^([[:space:]]*(export[[:space:]]+)?-?[[:space:]]*[A-Za-z0-9_.-]*(PASSWORD|PASSWD|SECRET|TOKEN|KEY|CREDENTIAL|PRIVATE|AUTHORIZATION|_AUTH)[A-Za-z0-9_.-]*[[:space:]]*[=:][[:space:]]*).+$/\1***/I' \
    -e 's/(--(requirepass|masterauth|password|passwd|pass)[[:space:]=]+)[^[:space:]"'"'"']+/\1***/Ig' \
    -e 's/(redis-cli([^|;&]*[[:space:]])?-a[[:space:]]+)[^[:space:]"'"'"']+/\1***/g' \
    -e 's/("[A-Za-z0-9_.-]*(password|passwd|secret|token|key|credential|authorization)[A-Za-z0-9_.-]*"[[:space:]]*:[[:space:]]*)"[^"]*"/\1"***"/Ig' \
    -e 's#(://[^:/@[:space:]]+:)[^@/[:space:]]+@#\1***@#g' \
    -e 's/eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*/***jwt***/g' \
    -e 's/((access_token|token|api_key|apikey|secret|password|passwd|key)=)[^&[:space:]"'"'"']+/\1***/Ig' \
    -e 's/(authorization[[:space:]]*[=:][[:space:]]*)((bearer|basic)[[:space:]]+)?[^[:space:]",]+/\1***/Ig' \
    -e 's/(bearer[[:space:]]+)[A-Za-z0-9._~+\/=-]{8,}/\1***/Ig'
}

# mask_text "строка" → маскированная строка
mask_text() { printf '%s\n' "$1" | mask_stream; }

# env_secret_values FILE → значения секретных переменных (≥ 6 символов) по одному в строке; нужно для финальной проверки bundle
env_secret_values() {
  local f="${1:-}" line key val
  [ -r "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"; val="${line#*=}"
    if [[ "$val" == \"*\" && ${#val} -ge 2 ]]; then val="${val:1:${#val}-2}"; elif [[ "$val" == \'*\' && ${#val} -ge 2 ]]; then val="${val:1:${#val}-2}"; fi
    case "$key" in
      *_SECONDS|*_TTL*|*_FAILURES|*_LENGTH|*_PORT) continue ;;
      *PASSWORD*|*PASSWD*|*SECRET*|*TOKEN*|*KEY*|*CREDENTIAL*) [ "${#val}" -ge 6 ] && printf '%s\n' "$val" ;;
    esac
  done < "$f"
}
