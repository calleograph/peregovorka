#!/usr/bin/env bash
# rollback.sh — возврат КОДА и образов приложения к предыдущей версии (после scripts/update.sh).
#
#   scripts/rollback.sh [--to COMMIT_SHA] [--env FILE] [--yes] [--dry-run]
#
# ВАЖНО: git rollback ≠ database rollback. Скрипт возвращает код и образы, но НЕ откатывает схему БД.
# Перед откатом проверяется, что ТЕКУЩАЯ ревизия БД известна коду целевой версии. Если нет (после обновления была применена новая
# миграция) — скрипт ОСТАНАВЛИВАЕТСЯ: старый образ поверх новой схемы может не запуститься или портить данные. Тогда:
#   1) восстановить БД из backup, сделанного перед обновлением (scripts/restore.sh; потеряются данные, записанные ПОСЛЕ backup), и
#   2) только затем откатывать код; либо обновиться вперёд (scripts/update.sh) и чинить проблему в новой версии.
# По умолчанию — к commit из ${DATA_ROOT}/state/last-update.state (previous_git_commit). Образы берутся по тегу короткого SHA либо prev-<SHA>
# (их сохраняет update.sh); пересборка при откате НЕ выполняется. .env автоматически не восстанавливается (путь к копии выводится).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

TO=""; YES=0; WAIT=180
while [ $# -gt 0 ]; do
  case "$1" in
    --to) TO="$2"; shift 2 ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --wait) WAIT="$2"; shift 2 ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
require_vars DATA_ROOT
git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 || die "Не git-репозиторий"
[ -z "$(git -C "$REPO_ROOT" status --porcelain)" ] || die "Есть локальные изменения в рабочей копии — отказ (ничего не изменено)."

CUR="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [ -z "$TO" ]; then
  TO="$(upd_state_get previous_git_commit)"
  if [ -z "$TO" ] || [ "$(upd_state_get target_git_commit)" != "$CUR" ]; then
    HIST="$DATA_ROOT/state/deploy-history.log"
    [ -f "$HIST" ] || die "Нет сведений о предыдущей версии ($(upd_state_file), $HIST). Укажите --to <SHA>."
    TO="$(tac "$HIST" | sed -n 's/.* old=\([0-9a-f]\{40\}\) new=\([0-9a-f]\{40\}\) .*/\1 \2/p' | awk -v cur="$CUR" '$2==cur {print $1; exit}')"
    [ -n "$TO" ] || die "В журнале нет обновления на текущий commit ${CUR:0:12}. Укажите --to <SHA>."
  fi
fi
TARGET="$(git -C "$REPO_ROOT" rev-parse --verify "${TO}^{commit}" 2>/dev/null)" || die "Неизвестный commit: $TO"
[ "$TARGET" != "$CUR" ] || die "Целевая версия совпадает с текущей."
TAG="${TARGET:0:12}"
log "Откат кода: ${CUR:0:12} → ${TAG}"

# 1. Совместимость БД с кодом целевой версии (главная проверка безопасности).
if [ "$(upd_svc_state postgres)" != missing ]; then
  if alembic_verify exec >/dev/null 2>&1 || [ -n "${ALEMBIC_CUR:-}" ]; then :; else alembic_verify run >/dev/null 2>&1 || true; fi
  DBREV="${ALEMBIC_CUR:-}"
else DBREV=""; fi
if [ -n "$DBREV" ]; then
  if upd_rev_in_commit "$DBREV" "$TARGET"; then ok "Ревизия БД $DBREV известна коду ${TAG} — откат кода совместим со схемой"
  else
    fail "============================================================"
    fail "ОТКАТ ОСТАНОВЛЕН: БД на ревизии $DBREV, а коду ${TAG} такая ревизия неизвестна."
    fail "Git rollback ≠ database rollback: старый код поверх новой схемы может не запуститься или портить данные."
    fail "Что делать:"
    fail "  • вернуть БД из backup, сделанного перед обновлением: $(upd_state_get db_backup || true) (scripts/restore.sh; данные, записанные ПОСЛЕ backup, будут потеряны), затем повторить откат;"
    fail "  • либо остаться на новой версии и исправить проблему обновлением (scripts/update.sh)."
    fail "============================================================"
    exit 3
  fi
else warn "Ревизию БД определить не удалось (PostgreSQL/backend не запущены) — совместимость схемы НЕ проверена"; fi

# 2. Образы целевой версии: тег <sha12> либо prev-<sha12> (сохранён update.sh).
ITAG=""
for cand in "$TAG" "prev-$TAG"; do
  miss=0; for svc in backend asr web; do docker image inspect "${COMPOSE_PROJECT_NAME}-${svc}:${cand}" >/dev/null 2>&1 || miss=1; done
  [ "$miss" -eq 0 ] && { ITAG="$cand"; break; }
done
[ -n "$ITAG" ] || die "Образы ${COMPOSE_PROJECT_NAME}-{backend,asr,web} для ${TAG} не найдены локально (ни ${TAG}, ни prev-${TAG}). Откат без пересборки невозможен: ./scripts/update.sh --ref ${TAG}"
ok "Образы найдены (тег $ITAG)"

if [ "$DRY_RUN" = "1" ]; then info "[dry-run] git checkout --detach ${TAG}; IMAGE_TAG=$ITAG; docker compose up -d --no-build; ожидание healthcheck"; exit 0; fi
if [ "$YES" -ne 1 ]; then
  read -r -p "Откатить код и образы ${CUR:0:12} → ${TAG} (схема БД не меняется)? [y/N] " ans; [ "$ans" = y ] || die "Отменено."
fi

git -C "$REPO_ROOT" checkout --detach "$TARGET" >/dev/null 2>&1 || die "git checkout не удался"
host_version_info
export IMAGE_TAG="$ITAG"
dc up -d --no-build || die "docker compose up не выполнен"
printf '%s ROLLBACK from=%s to=%s image_tag=%s\n' "$(date -Is)" "$CUR" "$TARGET" "$ITAG" >> "$DATA_ROOT/state/deploy-history.log"
upd_state_set result "rolled_back_to_${TAG}"
upd_wait_healthy "$WAIT" || die "После отката сервисы не healthy: scripts/logs.sh"
ok "Код и образы возвращены к ${TAG}."
EB="$(upd_state_get env_backup)"; [ -n "$EB" ] && info ".env автоматически не менялся; резервная копия перед обновлением: $EB"
warn "Рабочая копия теперь в состоянии detached HEAD. Чтобы снова получать обновления: git switch main && ./scripts/update.sh"
