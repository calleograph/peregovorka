#!/usr/bin/env bash
# Общие функции для scripts/*.sh. Подключается через `source`, сама ничего не выполняет.
#
# Принципы:
#  - работаем ТОЛЬКО с объектами текущего compose-проекта (-p COMPOSE_PROJECT_NAME);
#  - .env разбирается как данные (НЕ выполняется как shell), поэтому спецсимволы в
#    паролях безопасны;
#  - любая неопределённость = отказ (die), а не «попытка исправить».

if [ -n "${_VM_COMMON_LOADED:-}" ]; then return 0; fi
_VM_COMMON_LOADED=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
COMPOSE_FILE_MAIN="$REPO_ROOT/deployment/compose.yml"
COMPOSE_FILE_GPU="$REPO_ROOT/deployment/compose.gpu.yml"
DRY_RUN="${DRY_RUN:-0}"

if [ -t 1 ]; then
  C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_DIM=$'\033[2m'; C_OFF=$'\033[0m'
else
  C_RED=''; C_GRN=''; C_YEL=''; C_DIM=''; C_OFF=''
fi

log()  { printf '%s\n' "$*"; }
info() { printf '%s[i]%s %s\n' "$C_DIM" "$C_OFF" "$*"; }
ok()   { printf '%s[ok]%s %s\n' "$C_GRN" "$C_OFF" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$C_YEL" "$C_OFF" "$*" >&2; }
fail() { printf '%s[FAIL]%s %s\n' "$C_RED" "$C_OFF" "$*" >&2; }
die()  { fail "$*"; exit 1; }

# Загружает KEY=VALUE из файла в переменные окружения процесса (без eval/source).
# Пустые строки и комментарии пропускаются; кавычки вокруг значения снимаются.
# Уже заданные в окружении переменные НЕ перезаписываются (окружение приоритетнее).
load_env() {
  local file="${1:-$ENV_FILE}" line key val
  [ -f "$file" ] || die "Файл конфигурации не найден: $file (создайте из .env.example)"
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*|' '*'#'*) continue ;; esac
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"; val="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [[ "$val" == \"*\" && "$val" == *\" && ${#val} -ge 2 ]]; then val="${val:1:${#val}-2}"
    elif [[ "$val" == \'*\' && "$val" == *\' && ${#val} -ge 2 ]]; then val="${val:1:${#val}-2}"; fi
    if [ -z "${!key+x}" ]; then export "$key=$val"; fi
  done < "$file"
}

# «Стерильное» окружение для жизненного цикла (install/update/updater/rebuild/ctl): параметры проекта берутся ТОЛЬКО из .env.
# Docker Compose отдаёт переменным окружения приоритет над --env-file, а load_env не перезаписывает уже заданные переменные —
# поэтому случайно унаследованные ASR_*, NGINX_*, LIVEKIT_* (из профиля пользователя, sudo -E, systemd Environment=) тихо подменяли .env
# (реальный случай: в .env ASR_MAX_CONCURRENT_INFERENCE=1, а в контейнер попадало 2). Вызывать ДО load_env.
# Сохраняются только управляющие переменные (PEREGOVORKA_KEEP_VARS) и всё, что явно перечислено в PEREGOVORKA_KEEP_ENV (через пробел);
# PEREGOVORKA_KEEP_ENV=all отключает очистку (для отладки и тестов).
PEREGOVORKA_KEEP_VARS="ENV_FILE IMAGE_TAG DRY_RUN BUILD_MODE FORCE_BUILD PULL_BASES APP_BUILT_AT_OVERRIDE APP_BUILD_TIME UPDATE_SOURCE UPDATE_BY GIT_RETRIES UPD_POLL UPD_REPORT_EVERY"
sanitize_project_env() {
  [ "${PEREGOVORKA_KEEP_ENV:-}" = all ] && return 0
  local f n k keep
  for n in $(compgen -e); do
    case "$n" in
      ASR_*|NGINX_*|LIVEKIT_*|LDAP_*|POSTGRES_*|REDIS_*|GIGAAM_*|TRUSTED_PROXY_*|SESSION_*|LOGIN_*|DEFAULT_*|COOKIE_*|DOCS_*|LOG_LEVEL|LOG_FORMAT|WEB_*|MEETING_*|MIN_FREE_DISK_GB|MIN_RAM_GB|BACKUP_DIR|INTERNAL_API_TOKEN|APP_MASTER_KEY|APP_PUBLIC_URL|APP_ROOT|DATA_ROOT|INSTALL_PROFILE|COMPOSE_*) ;;
      *) continue ;;
    esac
    keep=0
    for k in $PEREGOVORKA_KEEP_VARS ${PEREGOVORKA_KEEP_ENV:-}; do [ "$k" = "$n" ] && keep=1 && break; done
    [ "$keep" -eq 1 ] || unset "$n"
  done
  # всё, что описано в самом .env / .env.example (на случай новых параметров вне перечисленных префиксов)
  for f in "$ENV_FILE" "$REPO_ROOT/.env.example"; do
    [ -r "$f" ] || continue
    while IFS= read -r n; do
      keep=0
      for k in $PEREGOVORKA_KEEP_VARS ${PEREGOVORKA_KEEP_ENV:-}; do [ "$k" = "$n" ] && keep=1 && break; done
      [ "$keep" -eq 1 ] || unset "$n"
    done < <(grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$f" | tr -d '=')
  done
  return 0
}

require_vars() {
  local missing=0 v
  for v in "$@"; do
    if [ -z "${!v:-}" ]; then fail "Не задана переменная $v в $ENV_FILE"; missing=1; fi
  done
  [ "$missing" -eq 0 ] || exit 1
}

validate_project_name() {
  [[ "${COMPOSE_PROJECT_NAME:-}" =~ ^[a-z][a-z0-9_-]{2,40}$ ]] \
    || die "COMPOSE_PROJECT_NAME должен быть уникальным: a-z0-9_- , начинаться с буквы, 3-41 символ"
  case "$COMPOSE_PROJECT_NAME" in
    postgres|redis|backend|frontend|web|app|livekit|asr|nginx|docker|default)
      die "COMPOSE_PROJECT_NAME='$COMPOSE_PROJECT_NAME' — слишком общее имя, возможен конфликт с другими проектами" ;;
  esac
}

# Массив аргументов compose для текущего экземпляра.
compose_args() {
  COMPOSE_ARGS=(compose -p "$COMPOSE_PROJECT_NAME" --project-directory "$REPO_ROOT/deployment"
                --env-file "$ENV_FILE" -f "$COMPOSE_FILE_MAIN")
  if [ "${ASR_DEVICE:-cpu}" = "cuda" ]; then COMPOSE_ARGS+=(-f "$COMPOSE_FILE_GPU"); fi
  # локальная LLM (контейнер llm-local) — отдельный профиль compose: включается, только если модель на месте, проверена и образ runtime скачан;
  # без интернета при установке остальные сервисы работают как обычно
  if declare -F llm_local_active >/dev/null 2>&1 && llm_local_active; then COMPOSE_ARGS=("${COMPOSE_ARGS[0]}" --profile llm "${COMPOSE_ARGS[@]:1}"); fi
}

dc() { compose_args; docker "${COMPOSE_ARGS[@]}" "$@"; }

# Выполняет команду либо только печатает её в режиме DRY_RUN=1.
run() {
  if [ "$DRY_RUN" = "1" ]; then
    printf '%s[dry-run]%s %s\n' "$C_YEL" "$C_OFF" "$*"
  else
    "$@"
  fi
}

# Занят ли локальный порт ("tcp"|"udp"). Возвращает 0, если занят.
port_busy() {
  local proto="$1" port="$2"
  if ! command -v ss >/dev/null 2>&1; then return 2; fi
  if [ "$proto" = "tcp" ]; then
    ss -H -ltn "sport = :$port" 2>/dev/null | grep -q .
  else
    ss -H -lun "sport = :$port" 2>/dev/null | grep -q .
  fi
}

# Опубликован ли порт контейнерами ИМЕННО этого compose-проекта.
port_owned_by_project() {
  local port="$1"
  command -v docker >/dev/null 2>&1 || return 1
  docker ps --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" \
    --format '{{.Ports}}' 2>/dev/null | grep -Eq "(:|^)${port}->|:${port}->"
}

# Версия сборки: VERSION (единственный источник) + commit из git + время сборки. Передаётся образам как APP_VERSION/APP_GIT_COMMIT/APP_BUILT_AT.
# commit «unknown» возникает только вне git-репозитория; update.sh/rebuild.sh в этом случае останавливаются, а не собирают образ без commit.
host_version_info() {
  APP_VERSION_FILE="$REPO_ROOT/VERSION"
  APP_VERSION="$(version_file_read "$APP_VERSION_FILE")"
  ver_valid "$APP_VERSION" || APP_VERSION="0.0.0"
  APP_GIT_COMMIT="$(git_head_commit "$REPO_ROOT")"
  [ -n "$APP_GIT_COMMIT" ] || APP_GIT_COMMIT="unknown"
  APP_BUILT_AT="${APP_BUILT_AT_OVERRIDE:-${APP_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}}"
  export APP_VERSION APP_GIT_COMMIT APP_BUILT_AT
}

# Чистые функции (.env, пути, LDAP, RAM, сводка портов)
# shellcheck source=envlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/envlib.sh"
# shellcheck source=versionlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/versionlib.sh"

# shellcheck source=dockerlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/dockerlib.sh"
# shellcheck source=updatelib.sh
source "$(dirname "${BASH_SOURCE[0]}")/updatelib.sh"

# shellcheck source=verifylib.sh
source "$(dirname "${BASH_SOURCE[0]}")/verifylib.sh"

# shellcheck source=prereqlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/prereqlib.sh"
# shellcheck source=repairlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/repairlib.sh"
# shellcheck source=llmlib.sh
source "$(dirname "${BASH_SOURCE[0]}")/llmlib.sh"
