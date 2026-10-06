#!/usr/bin/env bash
# deploy.sh — обычное обновление приложения ИЗ GIT. Ручное копирование каталогов не используется.
#
#   scripts/deploy.sh [--ref COMMIT_SHA|TAG] [--env FILE] [--no-backup] [--yes] [--dry-run]
#
# Порядок: проверка чистоты рабочей копии → git fetch → fast-forward (или
# checkout указанного --ref: SHA/тег для воспроизводимости) → preflight →
# [backup БД, если изменились миграции] → сборка ТОЛЬКО образов проекта →
# alembic upgrade head → up -d сервисов проекта → health checks.
#
# Запрещено и не выполняется: docker system/volume prune, удаление чужих
# образов/сетей/volumes, действия с контейнерами других проектов.
# Откат БД автоматически НЕ выполняется (см. docs/MIGRATIONS.md).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

REF=""; NO_BACKUP=0; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="$2"; shift 2 ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    --no-backup) NO_BACKUP=1; shift ;;
    --yes) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
export DRY_RUN

load_env "$ENV_FILE"; validate_project_name
require_vars DATA_ROOT
command -v git >/dev/null || die "git не найден"
git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 || die "$REPO_ROOT не является git-репозиторием"

# 1. Чистота рабочей копии (игнорируемые файлы — .env, данные — не считаются).
if [ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=normal)" ]; then
  git -C "$REPO_ROOT" status --short >&2
  die "В рабочей копии есть локальные изменения. Закоммитьте/уберите их: deploy обновляет только из Git."
fi

OLD_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
info "Текущий commit: ${OLD_SHA:0:12}"

# 2. Получение и переход на нужную версию.
run git -C "$REPO_ROOT" fetch --tags --prune origin
if [ -n "$REF" ]; then
  TARGET="$(git -C "$REPO_ROOT" rev-parse --verify "${REF}^{commit}" 2>/dev/null)" || die "Не найден ref: $REF"
else
  BRANCH="$(git -C "$REPO_ROOT" symbolic-ref --short -q HEAD || true)"
  [ -n "$BRANCH" ] || die "Detached HEAD: укажите --ref <SHA|tag> или вернитесь на ветку (git switch <ветка>)"
  git -C "$REPO_ROOT" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 || die "У ветки $BRANCH не задан upstream"
  TARGET="$(git -C "$REPO_ROOT" rev-parse '@{u}')"
  git -C "$REPO_ROOT" merge-base --is-ancestor "$OLD_SHA" "$TARGET" || die "Обновление не является fast-forward (история разошлась) — отказ."
fi
info "Целевой commit: ${TARGET:0:12}"

MIG_CHANGED=0
if ! git -C "$REPO_ROOT" diff --quiet "$OLD_SHA" "$TARGET" -- backend/migrations/versions 2>/dev/null; then MIG_CHANGED=1; fi

if [ "$OLD_SHA" = "$TARGET" ]; then
  info "Версия не изменилась — выполняется пересборка/проверка того же commit."
else
  log "Изменения:"; git -C "$REPO_ROOT" diff --stat "$OLD_SHA" "$TARGET" | tail -15 | sed 's/^/  /'
fi
[ "$MIG_CHANGED" -eq 1 ] && warn "Между версиями есть МИГРАЦИИ БАЗЫ ДАННЫХ — перед применением нужен backup."

if [ "$DRY_RUN" = "1" ]; then
  if [ -n "$REF" ]; then info "[dry-run] git checkout --detach ${TARGET:0:12}"; else info "[dry-run] git merge --ff-only ${TARGET:0:12}"; fi
  [ "$MIG_CHANGED" -eq 1 ] && info "[dry-run] scripts/backup.sh (обнаружены миграции)"
  info "[dry-run] preflight; compose build; alembic upgrade head; compose up -d; health checks"
  exit 0
fi

if [ -n "$REF" ]; then git -C "$REPO_ROOT" checkout --detach "$TARGET"; else git -C "$REPO_ROOT" merge --ff-only "$TARGET"; fi

host_version_info
export IMAGE_TAG="${APP_GIT_COMMIT}"
[ "$IMAGE_TAG" = "unknown" ] && die "Не удалось определить commit для тега образов"

# 3. Preflight на новой версии.
"$REPO_ROOT/scripts/preflight.sh" --env "$ENV_FILE" --skip-network || die "Preflight не пройден — деплой остановлен (код уже обновлён до ${TARGET:0:12}; контейнеры не тронуты)."

# 4. Backup перед миграциями схемы.
if [ "$MIG_CHANGED" -eq 1 ]; then
  if [ "$NO_BACKUP" -eq 1 ]; then warn "Backup отключён (--no-backup) при наличии миграций — на вашу ответственность.";
  elif dc ps -q postgres | grep -q .; then "$REPO_ROOT/scripts/backup.sh" --env "$ENV_FILE" --label "pre-${APP_GIT_COMMIT}";
  else warn "postgres не запущен — backup невозможен (первая установка?)"; fi
fi

# 5. Сборка ТОЛЬКО образов проекта, миграции, запуск.
dc build
dc up -d postgres redis
dc run --rm --no-deps backend alembic upgrade head
dc up -d

# 6. Журнал деплоев (для rollback.sh).
mkdir -p "$DATA_ROOT/state"
printf '%s old=%s new=%s migrations=%s image_tag=%s\n' "$(date -Is)" "$OLD_SHA" "$TARGET" "$MIG_CHANGED" "$IMAGE_TAG" >> "$DATA_ROOT/state/deploy-history.log"

# 7. Health checks.
"$REPO_ROOT/scripts/status.sh" --env "$ENV_FILE" --wait 240 || die "Сервисы не стали healthy. Диагностика: scripts/logs.sh ; откат кода: scripts/rollback.sh"
ok "Деплой завершён: ${TARGET:0:12} (версия $APP_VERSION). Проверка: scripts/smoke-test.sh"
