#!/usr/bin/env bash
# deploy.sh — УСТАРЕЛ. Обычное обновление выполняет scripts/update.sh (полный сценарий: backup .env и БД, проверка моделей,
# сборка с retry и commit в образах, Alembic, ожидание healthcheck, verify и smoke-test). Этот файл оставлен как совместимая обёртка:
# флаги те же (--ref, --env, --pull, --no-backup, --yes, --dry-run).
echo "[i] scripts/deploy.sh устарел — запускаю scripts/update.sh (штатный способ обновления)" >&2
exec "$(dirname "${BASH_SOURCE[0]}")/update.sh" "$@"
