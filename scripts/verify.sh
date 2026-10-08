#!/usr/bin/env bash
# verify.sh — итоговая проверка работоспособности установленного экземпляра (read-only).
#   scripts/verify.sh [--env FILE]
# Контейнеры и healthcheck, PostgreSQL, Redis, backend, ASR и загрузка модели, LiveKit, web, фактическая ревизия Alembic,
# HTTP-цепочка (включая host nginx), слушающие порты, параметры ядра. Код возврата 1 — есть ошибки.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
while [ $# -gt 0 ]; do case "$1" in --env) ENV_FILE="$2"; shift 2 ;; *) die "Неизвестный аргумент: $1" ;; esac; done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
log "== Проверка экземпляра ${COMPOSE_PROJECT_NAME} =="
if verify_deployment; then
  print_verify_stages
  log
  [ "${#VERIFY_WARNINGS[@]}" -gt 0 ] && warn "Предупреждений: ${#VERIFY_WARNINGS[@]} (см. выше)"
  ok "Peregovorka deployment verified"
  exit 0
fi
print_verify_stages; log; fail "Проверка НЕ пройдена: ошибок ${VERIFY_FAILS}"; exit 1
