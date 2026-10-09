#!/usr/bin/env bash
# install.sh — установка экземпляра ЭТАПАМИ; повторный запуск после сбоя безопасен и продолжает работу.
#
#   scripts/install.sh --profile shared-host|standalone [--env FILE] [--dry-run]
#       [--from ЭТАП] [--force-build] [--adopt-images] [--skip-preflight] [--skip-models]
#       [--skip-start] [--configure-firewall]
#
# Этапы (в этом порядке):
#   prerequisites → preflight → dirs → models → pull → build → database(postgres+redis, ждём healthy) → migrations
#   (+проверка alembic current == head) → services → healthcheck → nginx → firewall → verify (итоговая проверка) → report
# host nginx активируется ПОСЛЕДНИМ, только когда приложение уже запущено и здорово: при сбое раньше внешний мир
# (nginx, порты) остаётся как до установки. Каждый этап печатает [ok]/[warn]/[FAIL]; сбой — одной строкой
# «FAIL stage=… subsystem=…», где subsystem отличает проблему Docker/хоста от проблемы проекта.
#
# Возобновление: просто запустите ту же команду снова — готовые этапы пропускаются (образы не пересобираются, если
# исходники не менялись; свой nginx-site распознаётся и не конфликтует с собой). --from ЭТАП начинает с указанного этапа.
# --adopt-images — принять уже собранные вручную образы проекта как актуальные (явное решение администратора).
# --force-build — пересобрать образы. BUILD_MODE=auto|buildkit|legacy (см. scripts/lib/dockerlib.sh): при
# неработающем BuildKit (характерные ошибки containerd/экспорта) — явный fallback на legacy builder.
#
# Что делает ТОЛЬКО это приложение: каталоги внутри DATA_ROOT; ОДИН новый nginx-site + symlink (nginx -t до reload);
# образы, тома, сеть и контейнеры своего COMPOSE_PROJECT_NAME.
# Чего НЕ делает никогда: apt upgrade/dist-upgrade/autoremove, reboot, docker system/image/volume prune, удаление
# /var/lib/docker|containerd, размонтирование чужих mount'ов, остановку чужих контейнеров, правку чужих nginx/apache/php.
# standalone: ставит ВСЁ необходимое сам (манифест scripts/lib/prereqlib.sh: Docker Engine + Compose + Buildx, nginx, git, curl, openssl и системные
# утилиты) и применяет параметры ядра (/etc/sysctl.d/99-peregovorka.conf). shared-host: Docker, nginx и параметры ядра — решение администратора,
# автоматически не ставятся и не меняются (только список недостающего и предупреждения).
# Файрвол меняется ТОЛЬКО с явным --configure-firewall. --dry-run печатает план и ничего не меняет.

set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

STAGES=(prerequisites preflight dirs kernel models pull build database migrations services healthcheck nginx firewall verify report)
PROFILE_ARG=""; SKIP_PREFLIGHT=0; SKIP_START=0; FIREWALL=0; SKIP_MODELS=0; FROM_STAGE=""
FORCE_BUILD=0; ADOPT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE_ARG="$2"; shift 2 ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --from) FROM_STAGE="$2"; shift 2 ;;
    --force-build) FORCE_BUILD=1; shift ;;
    --adopt-images) ADOPT=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --skip-models) SKIP_MODELS=1; shift ;;
    --skip-start) SKIP_START=1; shift ;;
    --configure-firewall) FIREWALL=1; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
export DRY_RUN FORCE_BUILD
if [ -n "$FROM_STAGE" ]; then printf '%s\n' "${STAGES[@]}" | grep -qx "$FROM_STAGE" || die "Неизвестный этап: $FROM_STAGE (доступны: ${STAGES[*]})"; fi

sanitize_project_env; load_env "$ENV_FILE"
PROFILE="${PROFILE_ARG:-${INSTALL_PROFILE:-}}"
case "$PROFILE" in shared-host|standalone) ;; *) die "Укажите --profile shared-host|standalone" ;; esac
validate_project_name
require_vars DATA_ROOT WEB_PORT LIVEKIT_HTTP_PORT LIVEKIT_TCP_PORT LIVEKIT_UDP_PORT
msg="$(validate_local_dir "$DATA_ROOT" DATA_ROOT)" || die "$msg"
host_version_info
export IMAGE_TAG="${APP_GIT_COMMIT}"
[ "$IMAGE_TAG" = "unknown" ] && IMAGE_TAG="local"
export IMAGE_TAG

as_root() { if [ "$(id -u)" -eq 0 ]; then run "$@"; else run sudo "$@"; fi; }
dc_run() { if [ "$DRY_RUN" = "1" ]; then compose_args; printf '%s[dry-run]%s docker %s\n' "$C_YEL" "$C_OFF" "${COMPOSE_ARGS[*]} $*"; else dc "$@"; fi; }

# ------------------------------------------------------------------ каркас этапов
CURRENT_STAGE="start"; FAIL_REPORTED=0; STARTED=0; COMPLETED=()
fail_stage() { # fail_stage subsystem сообщение
  fail "FAIL stage=${CURRENT_STAGE} subsystem=$1: $2"; FAIL_REPORTED=1; exit 1
}
on_exit() {
  local rc=$?
  [ "$rc" -eq 0 ] && return 0
  [ "$FAIL_REPORTED" -eq 1 ] || fail "FAIL stage=${CURRENT_STAGE} subsystem=installer (код выхода $rc)"
  if [ "$DRY_RUN" != "1" ]; then
    warn "Установка прервана на этапе «${CURRENT_STAGE}». Выполнены: ${COMPLETED[*]:-—}."
    warn "Состояние безопасно: чужие объекты не затрагивались; host nginx активируется только на последнем этапе."
    warn "Исправьте причину и запустите ту же команду снова (или: scripts/install.sh --profile $PROFILE --from ${CURRENT_STAGE})."
  fi
}
trap on_exit EXIT

run_stage() {
  local name="$1"; shift
  if [ -n "$FROM_STAGE" ] && [ "$STARTED" -eq 0 ]; then
    if [ "$name" = "$FROM_STAGE" ]; then STARTED=1; else info "stage=$name пропущен (--from $FROM_STAGE)"; return 0; fi
  fi
  CURRENT_STAGE="$name"
  log; log "== stage: $name =="
  "$@"
  COMPLETED+=("$name"); ok "stage=$name завершён"
}

log "== Установка экземпляра '${COMPOSE_PROJECT_NAME}' (профиль: ${PROFILE}, версия ${APP_GIT_COMMIT}) =="
[ "$DRY_RUN" = "1" ] && warn "РЕЖИМ DRY-RUN: ничего не изменяется, печатается план."

# ----------------------------------------------------------------------- этапы
stage_prerequisites() {
  local out
  prereq_os_detect || { [ "$PREREQ_SUPPORTED" = yes ] || {
    if [ "$PROFILE" = standalone ]; then fail_stage os-unsupported "${PREREQ_WHY}"; else warn "${PREREQ_WHY}"; fi; }; }
  [ -z "$PREREQ_WHY" ] || [ "$PREREQ_SUPPORTED" != yes ] || warn "$PREREQ_WHY"
  [ -z "${PREREQ_ID:-}" ] || info "Система: ${PREREQ_ID} ${PREREQ_VERSION} (${PREREQ_CODENAME:-?})"
  if [ "$PROFILE" = "standalone" ]; then
    if out="$(prereq_verify standalone)"; then ok "Все системные зависимости (Docker, Compose, Buildx, nginx, git, curl, openssl и утилиты) на месте."
    else
      info "standalone: недостающее будет установлено автоматически (apt install, без upgrade):"; printf '%s\n' "$out" | sed 's/^/    - /'
      as_root "$REPO_ROOT/scripts/prereq.sh" --install --profile standalone || fail_stage prerequisites "Не удалось установить системные зависимости (подробности выше)."
      [ "$DRY_RUN" = "1" ] || prereq_verify standalone >/dev/null || fail_stage prerequisites "После установки не хватает: $(prereq_verify standalone | tr '\n' ';')"
    fi
  else
    # shared-host: ничего не ставим на чужой сервер сами — только точный список недостающего
    if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
      fail_stage docker-missing "Docker/Compose отсутствуют. В профиле shared-host автоматическая установка Docker ЗАПРЕЩЕНА — установите вручную и повторите."
    fi
    if out="$(prereq_verify shared-host)"; then ok "Системные утилиты на месте."
    elif [ "$DRY_RUN" = "1" ]; then warn "На сервере не хватает утилит: $(printf %s "$out" | tr "\n" ";") — реальная установка остановится на этом этапе."
    else fail_stage prerequisites "На сервере не хватает утилит: $(printf '%s' "$out" | tr '\n' ';'). Установите их средствами системы (shared-host ничего не ставит сам; список: scripts/prereq.sh --list)."; fi
    docker buildx version >/dev/null 2>&1 || warn "docker buildx не установлен — сборка пойдёт старым встроенным builder'ом (допустимо, но медленнее); рекомендуется поставить docker-buildx-plugin."
  fi
  if [ "${NGINX_MANAGE:-no}" = "yes" ] && ! command -v nginx >/dev/null 2>&1; then
    [ "$DRY_RUN" = "1" ] && { info "[dry-run] nginx будет установлен"; return 0; }
    fail_stage nginx-missing "nginx не найден, а NGINX_MANAGE=yes. В shared-host nginx не ставится автоматически."
  fi
  if command -v docker >/dev/null 2>&1 && [ "$DRY_RUN" != "1" ]; then docker_diag ok warn fail || fail_stage docker-runtime "Docker не готов к работе (см. выше)."; fi
}

stage_preflight() {
  if [ "$SKIP_PREFLIGHT" -eq 1 ]; then warn "Preflight пропущен по --skip-preflight"; return 0; fi
  info "Preflight (read-only)…"
  if ! "$REPO_ROOT/scripts/preflight.sh" --env "$ENV_FILE" --profile "$PROFILE"; then
    if [ "$DRY_RUN" = "1" ]; then warn "Preflight не пройден — реальная установка будет отклонена."; else fail_stage preflight "Preflight не пройден (пункты FAIL выше)."; fi
  fi
}

stage_dirs() {
  for d in postgres redis models/gigaam models/llm recordings exports backups state updater ca chat-files; do
    if [ -d "$DATA_ROOT/$d" ]; then info "есть: $DATA_ROOT/$d"; else run mkdir -p "$DATA_ROOT/$d"; fi
  done
  [ "$DRY_RUN" = "1" ] || chmod 750 "$DATA_ROOT" 2>/dev/null || true
  # каталог обмена с исполнителем обновлений: backend (uid 10001) пишет запросы, исполнитель на хосте — статус и журнал
  [ "$DRY_RUN" = "1" ] || chmod 1777 "$DATA_ROOT/updater" 2>/dev/null || true
  # Контейнеры backend и asr работают от uid 10001: каталоги записей и выгрузок (и только они) должны быть им доступны на запись.
  for d in recordings exports ca chat-files; do
    if [ "$(stat -c '%u' "$DATA_ROOT/$d" 2>/dev/null || echo x)" != "10001" ]; then as_root chown 10001:10001 "$DATA_ROOT/$d"; fi
  done
}

# Параметры ядра для звука/видео (UDP-буферы, vm.overcommit_memory) — собственным файлом /etc/sysctl.d/99-peregovorka.conf.
# standalone: применяются автоматически; shared-host: sysctl глобален для всего сервера — только предупреждение (решает администратор).
stage_kernel() {
  if [ "$PROFILE" != standalone ]; then
    local kv=""; kern_note() { kv="$kv $1"; }
    kernel_tuning_check : kern_note 2>/dev/null || true
    if [ -z "$kv" ]; then ok "Параметры ядра соответствуют рекомендациям."; else warn "Параметры ядра ниже рекомендаций (shared-host: автоматически не меняются). Посмотреть и применить осознанно: sudo scripts/tune-kernel.sh --apply"; fi
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then info "[dry-run] будет создан /etc/sysctl.d/99-peregovorka.conf и применён (sysctl -p)"; return 0; fi
  as_root "$REPO_ROOT/scripts/tune-kernel.sh" --env "$ENV_FILE" --apply --yes >/dev/null \
    && ok "Параметры ядра применены (/etc/sysctl.d/99-peregovorka.conf)" \
    || warn "Параметры ядра не применены (контейнер/виртуальная среда без права менять sysctl?). Установка продолжается; звук при высокой нагрузке может страдать."
}

# Локальная LLM (Qwen3 1.7B): мягко — без интернета установка продолжается, но об этом сказано явно
stage_llm() {
  if [ "$SKIP_MODELS" -eq 1 ]; then warn "Локальная LLM не загружается (--skip-models): позже scripts/models.sh --llm-only или кнопка в админке (Языковая модель)."; return 0; fi
  llm_prepare soft
  llm_local_refresh
  if llm_local_enabled && [ "$DRY_RUN" != "1" ]; then
    if llm_local_active; then ok "Локальная LLM готова к запуску (контейнер llm-local)."
    else warn "Локальная LLM НЕ ЗАГРУЖЕНА — остальная Peregovorka устанавливается и будет работать; протоколы и резюме пока возможны только через внешнюю LLM."; fi
  fi
}

stage_models() {
  stage_llm
  local ckpt="$DATA_ROOT/models/gigaam/${ASR_MODEL_NAME:-v3_e2e_rnnt}.ckpt"
  if [ -s "$ckpt" ]; then ok "Модель ASR на месте: $ckpt"; return 0; fi
  if [ "$SKIP_MODELS" -eq 1 ] || [ "$DRY_RUN" = "1" ]; then
    warn "Модель ASR не подготовлена ($ckpt): подготовьте scripts/models.sh — без неё ASR-сервис не станет ready."; return 0
  fi
  fail_stage models "Модель ASR не подготовлена ($ckpt). Выполните scripts/models.sh (или --from-dir для закрытой сети) и повторите; --skip-models пропускает проверку."
}

stage_pull() {
  if [ "$DRY_RUN" = "1" ]; then info "[dry-run] docker compose pull postgres redis (до 3 попыток при сетевых сбоях registry/CDN)"; return 0; fi
  pull_base_images || fail_stage registry-network "Не удалось скачать образы PostgreSQL/Redis (сеть/registry). Повторите установку — докачка продолжится."
}

stage_build() {
  if [ "$DRY_RUN" = "1" ]; then
    info "[dry-run] проверка builder'а: пробная сборка BuildKit; при инфраструктурном сбое — fallback на legacy (BUILD_MODE=${BUILD_MODE:-auto})"
    local s need=()
    if command -v docker >/dev/null 2>&1; then
      for s in "${BUILD_SERVICES[@]}"; do if build_needed "$s" 2>/dev/null; then need+=("$s"); fi; done
      info "[dry-run] будут собраны образы: ${need[*]:-— (все актуальны)}"
    else info "[dry-run] будут собраны образы: ${BUILD_SERVICES[*]}"; fi
    return 0
  fi
  [ "$ADOPT" -eq 1 ] && adopt_images
  select_build_mode || fail_stage docker-build "Сборка образов на этом сервере невозможна (проблема Docker, не проекта)."
  build_images || fail_stage docker-build "Сборка образов не удалась (подробности выше; логи: $DATA_ROOT/state/build-*.log)."
}

wait_healthy() { # wait_healthy сервис секунд
  local s="$1" t="$2" end cid st
  end=$(( $(date +%s) + t ))
  while [ "$(date +%s)" -lt "$end" ]; do
    cid="$(dc ps -q "$s" 2>/dev/null | head -1)"
    if [ -n "$cid" ]; then
      st="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null)"
      if [ "$st" = "healthy" ] || [ "$st" = "running" ]; then return 0; fi
    fi
    sleep 3
  done
  return 1
}

stage_database() {
  if [ "$DRY_RUN" != "1" ]; then require_images || fail_stage build "Нет собранных образов проекта."; fi
  dc_run up -d --no-build postgres redis
  [ "$DRY_RUN" = "1" ] && return 0
  wait_healthy postgres 120 || fail_stage postgres "PostgreSQL не стал healthy за 120 с (scripts/logs.sh postgres)."
  wait_healthy redis 60 || fail_stage redis "Redis не стал healthy (scripts/logs.sh redis)."
}

stage_migrations() {
  dc_run run --rm --no-deps -T backend alembic upgrade head
  [ "$DRY_RUN" = "1" ] && { info "[dry-run] затем: alembic current == alembic heads (иначе FAIL)"; return 0; }
  # Код возврата «0» и пустой вывод — не доказательство: сверяем фактическую ревизию БД с head.
  if alembic_verify run; then ok "Alembic: ${ALEMBIC_CUR} (head)"
  else fail_stage alembic "после миграции ревизия БД «${ALEMBIC_CUR:-не определена}» не совпадает с head «${ALEMBIC_HEAD:-не определён}»."; fi
}

stage_services() {
  dc_run up -d --no-build
}

stage_healthcheck() {
  if [ "$DRY_RUN" = "1" ]; then info "[dry-run] ожидание healthy всех сервисов (до 240 с), затем проверка"; return 0; fi
  if ! "$REPO_ROOT/scripts/status.sh" --env "$ENV_FILE" --wait 240; then
    warn "Сервисы не стали healthy. Останавливаю ТОЛЬКО прикладные контейнеры проекта (данные БД/Redis не удаляются): web backend asr livekit."
    dc stop web backend asr livekit >/dev/null 2>&1 || true
    fail_stage services "не все сервисы healthy — см. scripts/logs.sh <сервис>; после исправления повторите установку."
  fi
}

stage_nginx() {
  if [ "${NGINX_MANAGE:-no}" != "yes" ]; then
    info "NGINX_MANAGE!=yes — конфигурация host nginx не создаётся (настройте reverse proxy вручную, см. DEPLOYMENT.md)."; return 0
  fi
  require_vars NGINX_SITE_NAME NGINX_SERVER_NAME NGINX_LISTEN_PORT NGINX_SITES_AVAILABLE NGINX_SITES_ENABLED
  [[ "$NGINX_SITE_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || fail_stage nginx "NGINX_SITE_NAME содержит недопустимые символы"
  local SITE="$NGINX_SITES_AVAILABLE/$NGINX_SITE_NAME" LINK="$NGINX_SITES_ENABLED/$NGINX_SITE_NAME" RENDERED NB="nginx"
  [ "$(id -u)" -eq 0 ] || NB="sudo nginx"
  if [ -e "$SITE" ] && ! grep -qx "# managed-by: peregovorka:${COMPOSE_PROJECT_NAME}" "$SITE"; then
    fail_stage nginx "$SITE существует и принадлежит не этому проекту — отказ (выберите другой NGINX_SITE_NAME)."
  fi
  RENDERED="$(render_nginx_site)"
  if own_nginx_site_ok strict; then
    ok "Наш nginx-site уже установлен и актуален ($SITE) — без изменений."
    if [ "$DRY_RUN" != "1" ]; then
      $NB -t >/dev/null 2>&1 || fail_stage nginx "nginx -t не проходит при уже установленном site — проверьте конфигурацию nginx."
    fi
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    info "[dry-run] будет записан файл: $SITE"
    info "[dry-run] будет создан симлинк: $LINK -> $SITE"
    info "[dry-run] затем: nginx -t  и, только при успехе, reload nginx; при ошибке — откат собственных изменений"
    printf '%s\n' "$RENDERED" | sed 's/^/    | /'
    return 0
  fi
  local NEW_FILE=0 NEW_LINK=0 PREV="" tlog; tlog="$(mktemp)"
  [ -e "$SITE" ] || NEW_FILE=1
  { [ -e "$LINK" ] || [ -L "$LINK" ]; } || NEW_LINK=1
  [ -f "$SITE" ] && PREV="$(cat "$SITE")"
  printf '%s\n' "$RENDERED" | as_root tee "$SITE" >/dev/null
  [ "$NEW_LINK" -eq 1 ] && as_root ln -s "$SITE" "$LINK"
  if $NB -t 2>"$tlog"; then
    ok "nginx -t успешно"
    if command -v systemctl >/dev/null 2>&1; then as_root systemctl reload nginx; else as_root nginx -s reload; fi
    ok "nginx перезагружен (reload); приложение уже запущено и healthy"
  else
    fail "nginx -t НЕ прошёл: $(tail -5 "$tlog" | tr '\n' ' ')"
    # Откат ТОЛЬКО собственных изменений; существующий nginx продолжает работать, перезагрузки не было.
    [ "$NEW_LINK" -eq 1 ] && as_root rm -f "$LINK"
    if [ "$NEW_FILE" -eq 1 ]; then as_root rm -f "$SITE"; elif [ -n "$PREV" ]; then printf '%s\n' "$PREV" | as_root tee "$SITE" >/dev/null; fi
    rm -f "$tlog"
    fail_stage nginx "конфигурация не применена (свои изменения откатаны, reload не выполнялся). Приложение работает на 127.0.0.1:${WEB_PORT}."
  fi
  rm -f "$tlog"
}

stage_firewall() {
  if [ "$FIREWALL" -eq 1 ]; then
    if command -v ufw >/dev/null 2>&1; then
      as_root ufw allow "${LIVEKIT_TCP_PORT}/tcp" comment "${COMPOSE_PROJECT_NAME} livekit ice-tcp"
      as_root ufw allow "${LIVEKIT_UDP_PORT}/udp" comment "${COMPOSE_PROJECT_NAME} livekit ice-udp"
    else
      warn "ufw не найден — правила файрвола не добавлены; откройте tcp/${LIVEKIT_TCP_PORT} и udp/${LIVEKIT_UDP_PORT} вручную."
    fi
  else
    info "Файрвол не изменяется. Для работы с клиентов LAN должны быть доступны tcp/${LIVEKIT_TCP_PORT} и udp/${LIVEKIT_UDP_PORT} хоста."
  fi
}

stage_verify() {
  if [ "$DRY_RUN" = "1" ]; then info "[dry-run] итоговая проверка: контейнеры/healthcheck, PostgreSQL, Redis, backend, ASR+модель, LiveKit, web, alembic, HTTP-цепочка, порты, параметры ядра"; return 0; fi
  if ! verify_deployment; then fail_stage verification "итоговая проверка нашла ошибки (выше). Приложение НЕ считается работоспособным."; fi
  repair_verify_record pass      # метка «проверка выполнена для этой версии кода» — иначе помощник сочтёт, что нужна повторная проверка
}

stage_report() {
  [ "$DRY_RUN" = "1" ] && return 0
  local model="${DATA_ROOT}/models/gigaam/${ASR_MODEL_NAME:-v3_e2e_rnnt}.ckpt" w
  log; log "================ Памятка администратору ================"
  log " URL приложения:   ${APP_PUBLIC_URL:-?}"
  log " Внутренние URL:   web http://127.0.0.1:${WEB_PORT}  |  host nginx http://127.0.0.1:${NGINX_LISTEN_PORT:-—}"
  log " Compose-проект:   ${COMPOSE_PROJECT_NAME}   (каталог проекта: ${REPO_ROOT})"
  log " Данные:           ${DATA_ROOT}"
  log " Модель ASR:       ${ASR_MODEL_NAME:-v3_e2e_rnnt} → ${model}"
  log " Версия:           ${APP_VERSION} commit=${APP_GIT_COMMIT} built_at=${APP_BUILT_AT:-?}  (builder: ${SELECTED_BUILD_MODE:-не менялся})"
  log " Порты для клиентов (напрямую, не через HTTP-прокси): ${LIVEKIT_TCP_PORT}/tcp, ${LIVEKIT_UDP_PORT}/udp"
  [ "${NGINX_MANAGE:-no}" = "yes" ] && log " nginx-конфиг:     ${NGINX_SITES_AVAILABLE}/${NGINX_SITE_NAME}  (symlink: ${NGINX_SITES_ENABLED}/${NGINX_SITE_NAME})"
  log " Команды:          scripts/status.sh | scripts/logs.sh [сервис] -f | scripts/verify.sh | scripts/smoke-test.sh"
  log "                   scripts/ctl.sh restart [сервис] | scripts/ctl.sh stop | scripts/ctl.sh start"
  if [ "${#VERIFY_WARNINGS[@]}" -gt 0 ]; then
    log " Оставшиеся предупреждения (${#VERIFY_WARNINGS[@]}):"
    for w in "${VERIFY_WARNINGS[@]}"; do log "   - $(printf '%s' "$w" | cut -c1-220)"; done
  else log " Предупреждений нет."; fi
  log "========================================================="
  print_network_summary
  ok "Peregovorka deployment completed"
}

# ---------------------------------------------------------------------- запуск
run_stage prerequisites stage_prerequisites
run_stage preflight     stage_preflight
run_stage dirs          stage_dirs
run_stage kernel        stage_kernel
run_stage models        stage_models
run_stage pull          stage_pull
run_stage build         stage_build
if [ "$SKIP_START" -eq 1 ]; then ok "Запуск сервисов пропущен (--skip-start): образы готовы, nginx не тронут."; exit 0; fi
run_stage database      stage_database
run_stage migrations    stage_migrations
run_stage services      stage_services
run_stage healthcheck   stage_healthcheck
run_stage nginx         stage_nginx
run_stage firewall      stage_firewall
run_stage verify        stage_verify

if [ "$DRY_RUN" != "1" ]; then
  mkdir -p "$DATA_ROOT/state"
  printf '%s project=%s commit=%s builder=%s stages=%s\n' "$(date -Is)" "$COMPOSE_PROJECT_NAME" "$APP_GIT_COMMIT" "${SELECTED_BUILD_MODE:-n/a}" "${COMPLETED[*]}" >> "$DATA_ROOT/state/install-history.log"
fi
run_stage report        stage_report
[ "$DRY_RUN" = "1" ] && ok "Dry-run завершён: перечисленные этапы будут выполнены по порядку." || log "Дальше (по желанию): scripts/smoke-test.sh [--login ЛОГИН]"
