#!/usr/bin/env bash
# mask.sh — маскирование секретов в любом текстовом выводе (журналы, `docker compose config`, диагностика).
# Подключается через `source`; ничего не выполняет. Использование:   какая-то_команда | mask_stream
#
# Скрывается значение (имя ключа остаётся — по нему видно, что параметр задан):
#   *_PASSWORD, *_PASSWD, *_SECRET, *_TOKEN, *KEY* (API-ключи, LiveKit key/secret, master/application keys),
#   LDAP bind password, *_CREDENTIAL*, *PRIVATE*, Authorization/Bearer;
#   JWT-токены в любом месте строки; access_token/token/api_key/password в query-строках URL;
#   пароль в URL вида scheme://user:пароль@host (DATABASE_URL, REDIS_URL, SMB);
#   блок `keys:` конфигурации LiveKit (ключ: секрет);
#   JSON-пары "password": "…" / "token": "…" и т.п.
# Строки со «временными» параметрами (…_SECONDS, …_TTL…, …_FAILURES, …_LENGTH, …_PORT) не трогаются: это не секреты.
# Нужен GNU sed (Linux; Git Bash под Windows тоже подходит).

mask_stream() {
  awk '
    # блок LiveKit "keys:" — значения всех следующих более вложенных строк "ключ: секрет"
    { line = $0 }
    inkeys {
      match(line, /^[ \t]*/); ind = RLENGTH
      if (line ~ /^[ \t]*$/ || ind <= keys_indent) { inkeys = 0 }
      else if (line ~ /^[ \t]*[^ \t:#][^:]*:[ \t]*[^ \t]/) { sub(/:[ \t]*[^ \t].*$/, ": ***", line); print line; next }
    }
    line ~ /^[ \t]*keys:[ \t]*$/ { match(line, /^[ \t]*/); keys_indent = RLENGTH; inkeys = 1 }
    { print line }
  ' | sed -E \
    -e '/^[[:space:]]*[A-Za-z0-9_.-]*(_SECONDS|_TTL|_TTL_[A-Za-z]+|_FAILURES|_LENGTH|_PORT)[[:space:]]*[=:]/Ib' \
    -e 's/^([[:space:]]*(export[[:space:]]+)?-?[[:space:]]*[A-Za-z0-9_.-]*(PASSWORD|PASSWD|SECRET|TOKEN|KEY|CREDENTIAL|PRIVATE|AUTHORIZATION)[A-Za-z0-9_.-]*[[:space:]]*[=:][[:space:]]*).+$/\1***/I' \
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
