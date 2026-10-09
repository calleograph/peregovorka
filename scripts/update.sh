#!/usr/bin/env bash
# update.sh — ШТАТНОЕ обновление существующей установки Peregovorka. Единственный рекомендуемый способ обычного обновления:
#
#   ./scripts/check-updates.sh      # есть ли обновления (ничего не меняет)
#   ./scripts/update.sh             # обновить
#
#   scripts/update.sh [--env FILE] [--ref SHA|TAG] [--pull] [--force-build] [--yes] [--dry-run]
#                     [--stash] [--no-backup] [--skip-models] [--skip-preflight] [--no-nginx] [--skip-smoke] [--wait СЕК]
#
# Что делает (по порядку): определяет проект и текущий commit → git fetch и список новых commit'ов → проверяет локальные изменения
# (при их наличии НЕ продолжает) → резервная копия .env и сравнение с новым .env.example (только имена, без значений) →
# git merge --ff-only → [новая версия скрипта продолжает работу] → безопасные новые параметры .env, закрепление LiveKit на проверенной
# версии → preflight → проверка моделей (ничего не скачивает повторно) → backup БД → сборка образов (retry сети, fallback BuildKit→legacy,
# commit/время сборки в образах) → Alembic upgrade head (current == head) → запуск и ожидание healthcheck → миграция собственного
# nginx-site → verify → smoke-test → итог.
#
# Никогда: git reset --hard, удаление DATA_ROOT/моделей/томов PostgreSQL, перезапись .env (только резервная копия и дописывание
# безопасных параметров), повторный install. Локальные изменения (--stash) прячутся ТОЛЬКО по явной просьбе и потом НЕ применяются.
# --dry-run: показывает план (fetch допустим), ничего не меняет. Прерванное обновление безопасно повторить той же командой.
#
# Весь рабочий код обёрнут в функции и читается целиком до выполнения: git merge может заменить этот файл на диске, не ломая запуск.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

REF=""; PULL=0; YES=0; FORCE=0; NO_BACKUP=0; SKIP_SMOKE=0; SKIP_MODELS=0; SKIP_PREFLIGHT=0; NO_NGINX=0; STASH=0; WAIT=180; PHASE=1
ARGV=("$@")
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --pull) PULL=1; shift ;;
    --force-build) FORCE=1; shift ;;
    --yes|-y) YES=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --stash) STASH=1; shift ;;
    --no-backup) NO_BACKUP=1; shift ;;
    --skip-models) SKIP_MODELS=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --no-nginx) NO_NGINX=1; shift ;;
    --skip-smoke) SKIP_SMOKE=1; shift ;;
    --wait) WAIT="$2"; shift 2 ;;
    --phase2) PHASE=2; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1 (справка: scripts/update.sh --help)" ;;
  esac
done
export DRY_RUN

STEP=0; STEPS=16; CUR_STAGE="init"; UPD_STARTED="$(date +%s)"
step() { STEP=$((STEP + 1)); CUR_STAGE="$1"; [ "$DRY_RUN" = "1" ] || upd_state_set stage "$1" 2>/dev/null; log; log "[$STEP/$STEPS] $1"; }
stop_update() { # stop_update сообщение — остановка с понятным указанием, что делать дальше
  fail "$*"
  [ "$DRY_RUN" = "1" ] || { upd_state_set result "failed_at_${CUR_STAGE}" 2>/dev/null; upd_state_set update_status failed 2>/dev/null; upd_history_append failed "$CUR_STAGE"; }
  warn "Обновление остановлено на этапе «${CUR_STAGE}». Данные, .env и модели не затронуты. Устраните причину и повторите ./scripts/update.sh — он продолжит (сборка и миграции идемпотентны)."
  exit 1
}

# --------------------------------------------------------------------------------------------- фаза 1
phase1() {
  sanitize_project_env; load_env "$ENV_FILE"; validate_project_name; require_vars DATA_ROOT
  if ! pmsg="$(validate_env_paths)"; then printf '%s\n' "$pmsg" >&2; die "Некорректный путь в .env — обновление не начато, ничего не изменено."; fi
  step "Определение проекта и текущей версии"
  command -v git >/dev/null || die "git не найден"
  git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1 || die "$REPO_ROOT не является git-репозиторием"
  [ "$DRY_RUN" = "1" ] || command -v docker >/dev/null || die "docker не найден"
  OLD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  log "Проект: ${COMPOSE_PROJECT_NAME} · каталог: $REPO_ROOT · текущий commit: ${OLD:0:12}"
  [ "$DRY_RUN" = "1" ] || mkdir -p "$DATA_ROOT/state" 2>/dev/null || true

  # Прерванное ранее обновление: код уже обновлён — продолжаем с фазы 2 (все этапы идемпотентны).
  if [ -f "$(upd_marker_file)" ] && [ "$(upd_state_get target_git_commit)" = "$OLD" ] && [ "$DRY_RUN" != "1" ]; then
    warn "Найдено прерванное обновление (этап «$(upd_state_get stage)»): код уже на ${OLD:0:12}. Продолжаю с сборки/проверок."
    exec bash "$REPO_ROOT/scripts/update.sh" "${ARGV[@]}" --phase2
  fi

  step "Проверка origin и новых commit'ов"
  upd_git_fetch >/dev/null 2>&1 || stop_update "git fetch не удался (нет доступа к GitHub?). Проверьте сеть/прокси и повторите."
  if [ -n "$REF" ]; then
    TARGET="$(git -C "$REPO_ROOT" rev-parse --verify "${REF}^{commit}" 2>/dev/null)" || stop_update "Не найден ref: $REF"
  else
    BRANCH="$(git -C "$REPO_ROOT" symbolic-ref --short -q HEAD || true)"
    [ -n "$BRANCH" ] || stop_update "Detached HEAD: укажите --ref <SHA|tag> или вернитесь на ветку (git switch main)"
    git -C "$REPO_ROOT" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 || stop_update "У ветки $BRANCH не задан upstream"
    TARGET="$(git -C "$REPO_ROOT" rev-parse '@{u}')"
    git -C "$REPO_ROOT" merge-base --is-ancestor "$OLD" "$TARGET" || stop_update "Обновление не является fast-forward (локальная история разошлась с origin) — автоматически не продолжаю."
  fi
  log "Текущий:  ${OLD:0:12}"; log "Целевой:  ${TARGET:0:12}"
  if [ "$OLD" = "$TARGET" ]; then
    ok "Новых commit'ов нет — установка на актуальной версии."
    if [ "$FORCE" -eq 0 ] && [ "$PULL" -eq 0 ]; then
      info "Выполняю только проверку состояния (verify). Пересобрать образы на том же commit: ./scripts/update.sh --force-build"
      exec "$REPO_ROOT/scripts/verify.sh" --env "$ENV_FILE"
    fi
  else
    log "Новые commit'ы ($(git -C "$REPO_ROOT" rev-list --count "$OLD..$TARGET")):"
    git -C "$REPO_ROOT" log --oneline --no-decorate -n 30 "$OLD..$TARGET" | sed 's/^/  /'
    [ "$(git -C "$REPO_ROOT" rev-list --count "$OLD..$TARGET")" -gt 30 ] && log "  … (показаны последние 30)"
    if ! git -C "$REPO_ROOT" diff --quiet "$OLD" "$TARGET" -- backend/migrations/versions 2>/dev/null; then
      MIG=1; warn "Между версиями есть МИГРАЦИИ БАЗЫ ДАННЫХ — перед ними будет сделан backup БД."
    fi
  fi

  step "Проверка локальных изменений"
  if [ -n "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=normal)" ]; then
    git -C "$REPO_ROOT" status --short >&2
    warn "Обнаружены локальные изменения. Обновление остановлено."
    if [ "$STASH" -eq 1 ] && [ "$DRY_RUN" != "1" ]; then
      SN="peregovorka-update-$(date +%Y%m%d-%H%M%S)"
      git -C "$REPO_ROOT" stash push --include-untracked -m "$SN" >/dev/null || stop_update "Не удалось выполнить stash"
      upd_state_set stashed "$SN"
      warn "Изменения сохранены в stash «$SN» и НЕ будут применены автоматически (вернуть вручную: git stash list / git stash pop)."
    elif [ "$STASH" -eq 1 ]; then info "[dry-run] локальные изменения были бы сохранены в stash (по --stash)"
    else
      info "Варианты: закоммитьте/уберите изменения сами либо разрешите безопасный stash:  ./scripts/update.sh --stash"
      info "(stash сохраняет изменения и НЕ применяется автоматически; скрипт ничего не удаляет и не прячет молча)"
      exit 2
    fi
  else ok "Рабочее дерево чистое"; fi

  step "Сравнение .env с новым .env.example (только имена переменных)"
  EXNEW="$(mktemp)"; trap 'rm -f "$EXNEW"' EXIT
  git -C "$REPO_ROOT" show "$TARGET:.env.example" > "$EXNEW" 2>/dev/null || cp "$REPO_ROOT/.env.example" "$EXNEW"
  upd_env_classify "$ENV_FILE" "$EXNEW"
  if [ $((${#UPD_NEW_SAFE[@]} + ${#UPD_NEW_DECIDE[@]} + ${#UPD_NEW_EMPTY[@]})) -eq 0 ]; then ok "Новых параметров нет"; fi
  if [ "${#UPD_NEW_SAFE[@]}" -gt 0 ]; then
    log "Новые параметры (будут ДОБАВЛЕНЫ со значениями по умолчанию, существующие значения не меняются):"
    for n in "${UPD_NEW_SAFE[@]}"; do log "  + $n=$(upd_env_value "$EXNEW" "$n")"; done
  fi
  if [ "${#UPD_NEW_DECIDE[@]}" -gt 0 ]; then
    warn "Новые параметры, требующие РЕШЕНИЯ администратора (не добавляются автоматически; см. .env.example):"
    for n in "${UPD_NEW_DECIDE[@]}"; do log "  ? $n"; done
  fi
  if [ "${#UPD_NEW_EMPTY[@]}" -gt 0 ]; then
    log "Необязательные новые параметры (пустые по умолчанию):"; for n in "${UPD_NEW_EMPTY[@]}"; do log "  · $n"; done
  fi
  log "LiveKit: проверенная версия $(grep -E '^TESTED_LIVEKIT_SERVER=' "$REPO_ROOT/deployment/compat.env" 2>/dev/null | cut -d= -f2) (новая — из deployment/compat.env целевого commit)"

  if [ "$DRY_RUN" = "1" ]; then
    log; info "[dry-run] дальше было бы: резервная копия .env → git merge --ff-only ${TARGET:0:12} → перезапуск скрипта новой версией →"
    info "[dry-run] preflight → проверка моделей → backup БД → сборка образов → Alembic upgrade head → up -d + ожидание healthcheck → nginx-site → verify → smoke-test"
    exit 0
  fi

  if [ "$YES" -ne 1 ]; then
    if [ -t 0 ]; then read -r -p "Продолжить обновление ${OLD:0:12} → ${TARGET:0:12}? [y/N] " ans; [ "$ans" = y ] || [ "$ans" = Y ] || die "Отменено."
    else die "Нет терминала для подтверждения: добавьте --yes для автоматического запуска."; fi
  fi

  step "Резервная копия .env и фиксация состояния для отката"
  ENVBK="${ENV_FILE}.backup-$(date +%Y%m%d-%H%M%S)"
  cp -p "$ENV_FILE" "$ENVBK" && chmod 600 "$ENVBK" || stop_update "Не удалось сделать резервную копию .env"
  ok "Резервная копия: $ENVBK (содержит секреты — права 600)"
  : > "$(upd_marker_file)"
  upd_state_set started_at "$(date -Is)"; upd_state_set previous_git_commit "$OLD"; upd_state_set target_git_commit "$TARGET"
  upd_state_set env_backup "$ENVBK"; upd_state_set migrations_changed "${MIG:-0}"; upd_state_set result "in_progress"
  upd_state_set update_status in_progress; upd_state_set deploy_status ""; upd_state_set health_status ""; upd_state_set integration_status ""; upd_state_set integration_issues ""
  host_version_info; OLD12="${OLD:0:12}"
  if [ "$(upd_svc_state backend)" != missing ]; then
    upd_save_prev_images "$OLD12"; ok "Образы работающей версии сохранены (теги prev-${OLD12}) — для отката без пересборки"
    if alembic_verify exec; then upd_state_set alembic_before "$ALEMBIC_CUR"; else upd_state_set alembic_before "${ALEMBIC_CUR:-unknown}"; fi
  fi

  step "Обновление кода (git merge --ff-only)"
  if [ -n "$REF" ]; then git -C "$REPO_ROOT" checkout --detach "$TARGET" >/dev/null 2>&1 || stop_update "git checkout $REF не удался"
  elif [ "$OLD" != "$TARGET" ]; then git -C "$REPO_ROOT" merge --ff-only "$TARGET" >/dev/null 2>&1 || stop_update "git merge --ff-only не удался (локальные изменения конфликтуют?)"; fi
  ok "Код: ${OLD:0:12} → ${TARGET:0:12}"
  info "Продолжает уже НОВАЯ версия скрипта (чтобы использовать актуальные проверки)…"
  rm -f "$EXNEW"
  exec bash "$REPO_ROOT/scripts/update.sh" "${ARGV[@]}" --phase2
}

# --------------------------------------------------------------------------------------------- фаза 2
phase2() {
  sanitize_project_env; load_env "$ENV_FILE"; validate_project_name; require_vars DATA_ROOT
  OLD="$(upd_state_get previous_git_commit)"; TARGET="$(git -C "$REPO_ROOT" rev-parse HEAD)"; OLD12="${OLD:0:12}"
  STEP=6
  upd_state_set target_git_commit "$TARGET"
  host_version_info
  export IMAGE_TAG="${APP_GIT_COMMIT}"
  [ "$IMAGE_TAG" != unknown ] || stop_update "Не удалось определить commit для тегов и версии образов"
  [ "$FORCE" -eq 1 ] && export FORCE_BUILD=1
  [ "$PULL" -eq 1 ] && { export PULL_BASES=1 FORCE_BUILD=1; }

  step "Параметры .env: безопасные новые значения и закрепление LiveKit"
  upd_env_classify "$ENV_FILE" "$REPO_ROOT/.env.example"
  if [ "${#UPD_NEW_SAFE[@]}" -gt 0 ]; then upd_env_append_safe "$ENV_FILE" "$REPO_ROOT/.env.example"; ok "Добавлено новых параметров: ${#UPD_NEW_SAFE[@]} (${UPD_NEW_SAFE[*]})"; load_env "$ENV_FILE"
  else ok "Новых безопасных параметров нет"; fi
  [ "${#UPD_NEW_DECIDE[@]}" -gt 0 ] && warn "Требуют решения администратора: ${UPD_NEW_DECIDE[*]}"
  upd_livekit_pin "$ENV_FILE" || stop_update "Не удалось закрепить версию LiveKit"
  host_version_info

  step "Preflight новой версии"
  if [ "$SKIP_PREFLIGHT" -eq 1 ]; then warn "Preflight пропущен (--skip-preflight)"
  else "$REPO_ROOT/scripts/preflight.sh" --env "$ENV_FILE" --skip-network || stop_update "Preflight не пройден (код уже обновлён до ${TARGET:0:12}; контейнеры не тронуты)"; fi

  step "Модели (ничего не скачивается повторно)"
  upd_models_check
  if [ "$UPD_FULL_OK" -eq 0 ] && [ "$SKIP_MODELS" -eq 0 ]; then
    warn "Модель Full отсутствует — запускаю scripts/models.sh (уже существующие файлы он не качает)"
    "$REPO_ROOT/scripts/models.sh" --env "$ENV_FILE" || stop_update "Модель не подготовлена (scripts/models.sh); при закрытой сети: --from-dir"
    upd_models_check
  fi
  # Локальная LLM (Qwen3 1.7B): модель лежит вне образов (DATA_ROOT/models/llm) — существующий валидный файл не скачивается; повреждённый скачивается заново.
  # Без интернета — явное предупреждение, обновление продолжается.
  if [ "$SKIP_MODELS" -eq 0 ]; then llm_prepare soft; llm_local_refresh; else info "Локальная LLM: пропущено (--skip-models)"; fi
  # SIP-телефония (если включена на сервере): образ livekit-sip скачивается заранее, без интернета — предупреждение
  sip_prepare soft

  step "Резервная копия БД"
  if [ "$NO_BACKUP" -eq 1 ]; then warn "Backup БД отключён (--no-backup) — на вашу ответственность"
  elif [ "$(upd_svc_state postgres)" = missing ]; then warn "PostgreSQL не запущен — backup невозможен (первая установка? тогда используйте install.sh)"
  else
    "$REPO_ROOT/scripts/backup.sh" --env "$ENV_FILE" --label "pre-${OLD12:-update}" || stop_update "Резервная копия БД не создана — без неё миграции не выполняются"
    BK="$(ls -1t "${BACKUP_DIR:-$DATA_ROOT/backups}/${COMPOSE_PROJECT_NAME}"-db-*.dump 2>/dev/null | head -1)"
    [ -n "$BK" ] && upd_state_set db_backup "$BK"
  fi

  upd_ensure_updater_dir
  upd_ensure_data_dirs

  step "Сборка образов (retry сети, fallback BuildKit → legacy, commit ${APP_GIT_COMMIT} в образах)"
  info "Сборка ASR (torch, pip install gigaam с GitHub) может занимать 15+ минут — это не зависание; каждую минуту печатается прогресс."
  [ "$PULL" -eq 1 ] && { pull_base_images || stop_update "docker pull postgres/redis не удался (сеть/registry)"; }
  build_images || stop_update "Сборка образов не удалась (код и контейнеры не тронуты; лог: $DATA_ROOT/state/build-*.log). Уже собранные сервисы повторно не собираются."
  require_images || stop_update "Не хватает образов проекта"
  check_web_image_config || stop_update "Конфигурация nginx в новом образе web некорректна — прежняя версия продолжает работать (исправьте frontend/nginx.conf и повторите)"

  step "Миграции базы данных (Alembic)"
  dc up -d --no-build postgres redis >/dev/null || stop_update "Не удалось запустить PostgreSQL/Redis"
  local sv=("${UPD_SERVICES[@]}"); UPD_SERVICES=(postgres redis)
  upd_wait_healthy 120 || { UPD_SERVICES=("${sv[@]}"); stop_update "PostgreSQL/Redis не стали healthy"; }
  UPD_SERVICES=("${sv[@]}")
  alembic_verify run >/dev/null 2>&1; log "Ревизия БД: текущая ${ALEMBIC_CUR:-—} · целевая (head) ${ALEMBIC_HEAD:-—}"
  dc run --rm --no-deps backend alembic upgrade head || stop_update "alembic upgrade head не выполнен (откат БД — только из backup: docs/MIGRATIONS.md)"
  alembic_verify run >/dev/null 2>&1 || stop_update "После миграции current (${ALEMBIC_CUR:-?}) != head (${ALEMBIC_HEAD:-?})"
  ok "Alembic: ${ALEMBIC_CUR} = head"; upd_state_set alembic_after "$ALEMBIC_CUR"

  step "Запуск сервисов и ожидание healthcheck (до ${WAIT} с)"
  dc up -d --no-build || stop_update "docker compose up не выполнен"
  upd_wait_healthy "$WAIT" || stop_update "Сервисы не стали healthy: ${UPD_UNHEALTHY[*]:-?} (scripts/logs.sh <сервис>; откат кода: scripts/rollback.sh)"
  printf '%s old=%s new=%s migrations=%s image_tag=%s\n' "$(date -Is)" "$OLD" "$TARGET" "$(upd_state_get migrations_changed)" "$IMAGE_TAG" >> "$DATA_ROOT/state/deploy-history.log"

  step "Host nginx (собственный site)"
  if [ "$NO_NGINX" -eq 1 ]; then info "Пропущено (--no-nginx)"; else upd_nginx_migrate; fi

  upd_state_set update_status ok; upd_state_set deploy_status ok
  step "Проверка (verify)"
  VF=0; WARN_N=0
  log "== Проверка экземпляра ${COMPOSE_PROJECT_NAME} =="
  verify_deployment; VF=$?
  WARN_N=${#VERIFY_WARNINGS[@]}; VSTAGES="$(print_verify_stages)"
  [ "$VF" -eq 0 ] && ok "Verify: PASS" || fail "Verify: FAIL (ошибок: $VF)"

  SM="skipped"; SMRC=0; SMISS=""
  step "Smoke-test"
  if [ "$SKIP_SMOKE" -eq 1 ]; then info "Пропущено (--skip-smoke)"
  else
    SMLOG="$(mktemp)"; "$REPO_ROOT/scripts/smoke-test.sh" --env "$ENV_FILE" 2>&1 | tee "$SMLOG"; SMRC=${PIPESTATUS[0]}
    case "$SMRC" in 0) SM="PASS" ;; 3) SM="INTEGRATION" ;; *) SM="FAIL" ;; esac
    SMISS="$(sed -n 's/^Integration issues: //p' "$SMLOG" | tail -1 | tr -d '\r' | cut -c1-200)"
    SW="$(grep -o 'Предупреждений: [0-9]*' "$SMLOG" | grep -o '[0-9]*$' | tail -1)"; WARN_N=$((WARN_N + ${SW:-0})); rm -f "$SMLOG"
  fi

  # Четыре независимых итога. Сбой проверки интеграций (например, каталога LDAP) НЕ делает обновление «неудачным»:
  # код, образы, миграции и запуск выполнены, сервисы здоровы — это отдельное предупреждение с кнопкой в диагностику.
  upd_classify_outcome "$VF" "$SMRC" "$SM"
  upd_state_set health_status "$H_ST"; upd_state_set integration_status "$I_ST"; upd_state_set integration_issues "$SMISS"
  repair_verify_record "$([ "$H_ST" = ok ] && { [ "$I_ST" = fail ] && echo integration || echo pass; } || echo fail)"
  summary "$VF" "$SM" "$WARN_N" "$H_ST" "$I_ST" "$SMISS"
  rm -f "$(upd_marker_file)"
  upd_self_heal_updater
  if [ "$H_ST" = ok ]; then
    upd_state_set result "ok"; upd_state_set completed_at "$(date -Is)"; upd_history_append ok
    [ "$I_ST" = fail ] && exit 3
    exit 0
  fi
  upd_state_set result "health_failed"; upd_history_append failed "проверка работоспособности после обновления"
  exit 1
}

# Итоговый вывод: коротко и по делу.
summary() { # vf smoke warnings health integration integration_issues
  local s st lk asrline add="" up_s="SUCCESS" dep_s="SUCCESS" h_s i_s
  [ "$4" = ok ] && h_s=SUCCESS || h_s=FAIL
  case "$5" in ok) i_s=SUCCESS ;; skipped) i_s=SKIPPED ;; *) i_s="WARNING${6:+ ($6)}" ;; esac
  log; log "===================================================="
  if [ "$4" = ok ]; then ok "Peregovorka update completed"; else fail "Peregovorka update: версия установлена, но проверка работоспособности нашла ошибки"; fi
  log
  printf 'Update:       %s\nDeployment:   %s\nHealth:       %s\nIntegrations: %s\n' "$up_s" "$dep_s" "$h_s" "$i_s"
  if [ "$5" = fail ]; then
    warn "Интеграции требуют внимания (${6:-см. журнал выше}). Обновление и сервисы в порядке. Откройте веб-интерфейс: Администрирование → LDAP и доступ → Диагностика."
  fi
  log
  local oldv newv
  oldv="$(git -C "$REPO_ROOT" show "${OLD:-HEAD}:VERSION" 2>/dev/null | tr -d '[:space:]')"; newv="$(version_file_read "$REPO_ROOT/VERSION")"
  if [ -n "$oldv" ] && [ "$oldv" != "$newv" ]; then printf 'Version:         %s → %s\n' "$oldv" "$newv"; else printf 'Version:         %s\n' "${newv:-?}"; fi
  printf 'Previous commit: %s\nCurrent commit:  %s\n\n' "${OLD12:-?}" "${TARGET:0:12}"
  alembic_verify exec >/dev/null 2>&1
  [ "${ALEMBIC_CUR:-}" = "${ALEMBIC_HEAD:-x}" ] && printf 'Database:   %s (head)\n' "$ALEMBIC_CUR" || printf 'Database:   %s (head: %s) — НЕСООТВЕТСТВИЕ\n' "${ALEMBIC_CUR:-?}" "${ALEMBIC_HEAD:-?}"
  lk="$(dc exec -T livekit livekit-server --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
  for s in postgres redis backend asr livekit web; do
    st="$(upd_svc_state "$s")"
    case "$s" in
      postgres) printf 'PostgreSQL: %s\n' "$st" ;; redis) printf 'Redis:      %s\n' "$st" ;; backend) printf 'Backend:    %s\n' "$st" ;; asr) printf 'ASR:        %s\n' "$st" ;;
      livekit) printf 'LiveKit:    %s %s\n' "${lk:+v$lk}" "$st" ;; web) printf 'Web:        %s\n' "$st" ;;
    esac
  done
  asrline="$(dc exec -T asr python -c "
import json,urllib.request
d=json.load(urllib.request.urlopen('http://127.0.0.1:8090/models',timeout=6))
for m in d['models']:
    print('%s|%s|%s|%s|%s' % (m['id'],m['title'],m['runtime'],m['status'],'1' if m['active'] else '0'))" 2>/dev/null || true)"
  if [ -n "$asrline" ]; then
    printf '\nASR:\n'
    while IFS='|' read -r id title rt stt act; do [ "$act" = 1 ] && printf '%s\nruntime: %s\n' "$title" "$rt"; done <<<"$asrline"
    printf '\nAdditional models:\n'
    while IFS='|' read -r id title rt stt act; do
      [ "$act" = 1 ] && continue
      case "$stt" in available) stt=available ;; missing) stt="not installed" ;; esac
      printf '%s: %s\n' "$(printf '%s' "$title" | sed 's/.*— //')" "$stt"
    done <<<"$asrline"
  fi
  [ -n "${VSTAGES:-}" ] && printf '%s\n' "$VSTAGES"
  printf '\nVerify: %s\nSmoke:  %s\n\nWarnings: %s\n' "$([ "$1" -eq 0 ] && echo PASS || echo FAIL)" "$2" "$3"
  [ "$3" -gt 0 ] && echo "Run ./scripts/diag.sh for details."
  [ "$4" != ok ] && echo "Откат кода: ./scripts/rollback.sh  (откат кода ≠ откат БД: docs/INSTALL_AND_UPDATE.md, раздел «Rollback»)"
  log "===================================================="
}

main() {
  if [ "$PHASE" -eq 2 ]; then phase2; else phase1; fi
}
main
exit $?
