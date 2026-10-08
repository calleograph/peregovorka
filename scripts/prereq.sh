#!/usr/bin/env bash
# prereq.sh — системные зависимости Peregovorka: проверка и установка на чистой Ubuntu/Debian.
#
#   scripts/prereq.sh --check   [--profile standalone|shared-host]   что есть и чего не хватает (ничего не меняет)
#   scripts/prereq.sh --install [--profile standalone]               поставить всё недостающее (нужен root); для shared-host Docker/nginx не ставятся
#   scripts/prereq.sh --list                                         манифест: команда → пакет
#
# Тот же манифест использует install.sh: на standalone пользователю не нужно ставить Docker, Compose, Buildx, nginx, git, curl, openssl и прочее заранее.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/prereqlib.sh"

MODE="check"; PROFILE_ARG="standalone"
while [ $# -gt 0 ]; do
  case "$1" in
    --check) MODE=check; shift ;;
    --install) MODE=install; shift ;;
    --list) MODE=list; shift ;;
    --profile) PROFILE_ARG="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
case "$PROFILE_ARG" in standalone|shared-host) ;; *) die "--profile: standalone|shared-host" ;; esac

case "$MODE" in
  list) for c in $(printf '%s\n' "${!PREREQ_PKG[@]}" | sort); do printf '%-24s %s\n' "$c" "${PREREQ_PKG[$c]}"; done ;;
  check)
    prereq_os_detect && ok "ОС: ${PREREQ_ID} ${PREREQ_VERSION} (${PREREQ_FAMILY})" || warn "ОС: ${PREREQ_ID:-?} ${PREREQ_VERSION:-}: ${PREREQ_WHY}"
    if out="$(prereq_verify "$PROFILE_ARG")"; then ok "Все системные зависимости на месте (профиль $PROFILE_ARG)."
    else printf '%s\n' "$out" | sed 's/^/  - /'; fail "Не хватает зависимостей. Установить: sudo scripts/prereq.sh --install --profile $PROFILE_ARG"; exit 1; fi ;;
  install)
    prereq_install_all "$PROFILE_ARG" || die "Установка зависимостей не завершена"
    if out="$(prereq_verify "$PROFILE_ARG")"; then ok "Все системные зависимости установлены."
    else printf '%s\n' "$out" | sed 's/^/  - /'; die "После установки чего-то всё ещё не хватает"; fi ;;
esac
