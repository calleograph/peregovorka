#!/usr/bin/env bash
# rollback.sh — возврат КОДА и образов приложения к предыдущей версии.
#
#   scripts/rollback.sh [--to COMMIT_SHA] [--env FILE] [--yes]
#
# По умолчанию — к commit, который был текущим перед последним деплоем
# (из ${DATA_ROOT}/state/deploy-history.log). Образы должны существовать
# локально (тег = короткий SHA); пересборка при откате НЕ выполняется.
#
# ВАЖНО: схема БД НЕ откатывается. Если между версиями были миграции,
# скрипт предупредит и потребует подтверждения: старый код может не работать
# с новой схемой. Тогда используйте restore.sh из backup, сделанного перед
# деплоем (docs/MIGRATIONS.md).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

TO=""; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --to) TO="$2"; shift 2 ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    --yes) YES=1; shift ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
require_vars DATA_ROOT
git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 || die "Не git-репозиторий"
[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "Есть локальные изменения в рабочей копии — отказ."

CUR="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [ -z "$TO" ]; then
  HIST="$DATA_ROOT/state/deploy-history.log"
  [ -f "$HIST" ] || die "Нет журнала деплоев ($HIST). Укажите --to <SHA>."
  TO="$(tac "$HIST" | sed -n 's/.* old=\([0-9a-f]\{40\}\) new=\([0-9a-f]\{40\}\) .*/\1 \2/p' | awk -v cur="$CUR" '$2==cur {print $1; exit}')"
  [ -n "$TO" ] || die "В журнале нет деплоя на текущий commit ${CUR:0:12}. Укажите --to <SHA>."
fi
TARGET="$(git -C "$REPO_ROOT" rev-parse --verify "${TO}^{commit}" 2>/dev/null)" || die "Неизвестный commit: $TO"
[ "$TARGET" != "$CUR" ] || die "Целевая версия совпадает с текущей."
TAG="${TARGET:0:12}"

for svc in backend asr web; do
  docker image inspect "${COMPOSE_PROJECT_NAME}-${svc}:${TAG}" >/dev/null 2>&1 \
    || die "Образ ${COMPOSE_PROJECT_NAME}-${svc}:${TAG} не найден локально. Откат без пересборки невозможен: scripts/deploy.sh --ref ${TAG}"
done

if ! git -C "$REPO_ROOT" diff --quiet "$TARGET" "$CUR" -- backend/migrations/versions; then
  warn "============================================================"
  warn "МЕЖДУ ВЕРСИЯМИ ЕСТЬ МИГРАЦИИ БАЗЫ ДАННЫХ:"
  git -C "$REPO_ROOT" diff --name-status "$TARGET" "$CUR" -- backend/migrations/versions | sed 's/^/    /' >&2
  warn "БД останется на НОВОЙ схеме. Старый код может не запуститься или"
  warn "работать неверно. Надёжный путь: scripts/restore.sh из backup,"
  warn "сделанного перед деплоем (pre-<sha>). Продолжать откат только кода?"
  warn "============================================================"
  if [ "$YES" -ne 1 ]; then
    read -r -p "Введите 'rollback-code-only' для продолжения: " ans
    [ "$ans" = "rollback-code-only" ] || die "Отменено."
  fi
elif [ "$YES" -ne 1 ]; then
  read -r -p "Откатить ${CUR:0:12} → ${TAG}? [y/N] " ans; [ "$ans" = "y" ] || die "Отменено."
fi

git -C "$REPO_ROOT" checkout --detach "$TARGET"
host_version_info
export IMAGE_TAG="$TAG"
dc up -d --no-build
printf '%s ROLLBACK from=%s to=%s image_tag=%s\n' "$(date -Is)" "$CUR" "$TARGET" "$TAG" >> "$DATA_ROOT/state/deploy-history.log"
"$REPO_ROOT/scripts/status.sh" --env "$ENV_FILE" --wait 240 || die "После отката сервисы не healthy: scripts/logs.sh"
ok "Код и образы возвращены к ${TAG}."
