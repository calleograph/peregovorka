#!/usr/bin/env bash
# install.sh — первичная установка экземпляра. Повторный запуск безопасен.
#
#   scripts/install.sh --profile shared-host|standalone [--env FILE] [--dry-run]
#                      [--skip-preflight] [--skip-start] [--configure-firewall]
#
# Что делает ТОЛЬКО это приложение:
#   * создаёт каталоги внутри DATA_ROOT;
#   * (nginx) создаёт ОДИН отдельный site-файл + симлинк в sites-enabled,
#     проверяет `nginx -t`, и только после успеха делает reload;
#   * собирает образы ЭТОГО проекта, применяет миграции Alembic, запускает
#     сервисы ЭТОГО compose-проекта.
# Чего НЕ делает никогда: apt upgrade/dist-upgrade/autoremove, reboot,
# docker system/volume prune, остановку чужих контейнеров, правку чужих
# nginx/apache/php-конфигов (в т.ч. sites-available/projects).
# shared-host: Docker и nginx считаются внешними зависимостями, не ставятся.
# standalone: может поставить недостающие пакеты (nginx, Docker) через apt install.
# Файрвол меняется ТОЛЬКО с явным --configure-firewall.
#
# --dry-run печатает план (команды и файлы) и ничего не меняет.

set -euo pipefail
# shellcheck source=lib/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROFILE_ARG=""; SKIP_PREFLIGHT=0; SKIP_START=0; FIREWALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE_ARG="$2"; shift 2 ;;
    --env) ENV_FILE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --skip-start) SKIP_START=1; shift ;;
    --configure-firewall) FIREWALL=1; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
export DRY_RUN

load_env "$ENV_FILE"
PROFILE="${PROFILE_ARG:-${INSTALL_PROFILE:-}}"
case "$PROFILE" in shared-host|standalone) ;; *) die "Укажите --profile shared-host|standalone" ;; esac
validate_project_name
require_vars DATA_ROOT WEB_PORT LIVEKIT_HTTP_PORT LIVEKIT_TCP_PORT LIVEKIT_UDP_PORT
host_version_info
export IMAGE_TAG="${APP_GIT_COMMIT}"
[ "$IMAGE_TAG" = "unknown" ] && IMAGE_TAG="local"
export IMAGE_TAG

as_root() { if [ "$(id -u)" -eq 0 ]; then run "$@"; else run sudo "$@"; fi; }

log "== Установка экземпляра '${COMPOSE_PROJECT_NAME}' (профиль: ${PROFILE}) =="
[ "$DRY_RUN" = "1" ] && warn "РЕЖИМ DRY-RUN: ничего не изменяется, печатается план."

# ---------------------------------------------------------------- 1. preflight
if [ "$SKIP_PREFLIGHT" -eq 1 ]; then
  warn "Preflight пропущен по --skip-preflight"
else
  info "Preflight (read-only)…"
  if ! "$REPO_ROOT/scripts/preflight.sh" --env "$ENV_FILE" --profile "$PROFILE"; then
    # В dry-run показываем план даже при FAIL, но ясно предупреждаем.
    [ "$DRY_RUN" = "1" ] && warn "Preflight не пройден — реальная установка будет отклонена." || die "Preflight не пройден."
  fi
fi

# ----------------------------------------------------- 2. зависимости профиля
log; log "== Зависимости =="
if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
  if [ "$PROFILE" = "shared-host" ]; then
    die "Docker/Compose отсутствуют. В профиле shared-host автоматическая установка Docker ЗАПРЕЩЕНА. Установите вручную и повторите."
  fi
  info "standalone: будет установлен Docker Engine из официального репозитория Docker (apt install, без upgrade)."
  as_root apt-get update
  as_root apt-get install -y ca-certificates curl gnupg
  as_root install -m 0755 -d /etc/apt/keyrings
  as_root curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  as_root chmod a+r /etc/apt/keyrings/docker.asc
  if [ "$DRY_RUN" != "1" ]; then
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  else
    info "[dry-run] будет создан /etc/apt/sources.list.d/docker.list"
  fi
  as_root apt-get update
  as_root apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
  ok "Docker и Compose уже установлены — не трогаем."
fi

if [ "${NGINX_MANAGE:-no}" = "yes" ] && ! command -v nginx >/dev/null 2>&1; then
  if [ "$PROFILE" = "shared-host" ]; then die "nginx не найден, а NGINX_MANAGE=yes. В shared-host nginx не ставится автоматически."; fi
  as_root apt-get install -y nginx
fi

# -------------------------------------------------------------- 3. каталоги
log; log "== Каталоги данных =="
for d in postgres redis models/gigaam recordings exports backups state; do
  if [ -d "$DATA_ROOT/$d" ]; then info "есть: $DATA_ROOT/$d"; else run mkdir -p "$DATA_ROOT/$d"; fi
done
[ "$DRY_RUN" = "1" ] || chmod 750 "$DATA_ROOT" 2>/dev/null || true
# Контейнеры backend и asr работают от uid 10001: каталоги записей и выгрузок (и только они) должны быть им доступны на запись.
for d in recordings exports; do
  if [ "$(stat -c '%u' "$DATA_ROOT/$d" 2>/dev/null || echo x)" != "10001" ]; then as_root chown 10001:10001 "$DATA_ROOT/$d"; fi
done

# ----------------------------------------------------------------- 4. nginx
if [ "${NGINX_MANAGE:-no}" = "yes" ]; then
  log; log "== Host nginx: отдельный site-файл =="
  require_vars NGINX_SITE_NAME NGINX_SERVER_NAME NGINX_LISTEN_PORT NGINX_SITES_AVAILABLE NGINX_SITES_ENABLED
  [[ "$NGINX_SITE_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || die "NGINX_SITE_NAME содержит недопустимые символы"
  SITE="$NGINX_SITES_AVAILABLE/$NGINX_SITE_NAME"
  LINK="$NGINX_SITES_ENABLED/$NGINX_SITE_NAME"
  if [ -n "${NGINX_TLS_CERT:-}" ]; then TPL="$REPO_ROOT/deployment/nginx/site.tls.conf.tpl"; else TPL="$REPO_ROOT/deployment/nginx/site.http.conf.tpl"; fi
  if [ -e "$SITE" ] && ! grep -q "managed-by: voicemeet:${COMPOSE_PROJECT_NAME}" "$SITE"; then
    die "$SITE существует и принадлежит не этому проекту — отказ (выберите другой NGINX_SITE_NAME)."
  fi
  PROJECT_ID="$(printf '%s' "$COMPOSE_PROJECT_NAME" | tr -c 'a-zA-Z0-9' '_')"
  RENDERED="$(sed -e "s|@@PROJECT@@|${COMPOSE_PROJECT_NAME}|g" -e "s|@@PROJECT_ID@@|${PROJECT_ID}|g" \
      -e "s|@@LISTEN_PORT@@|${NGINX_LISTEN_PORT}|g" -e "s|@@SERVER_NAME@@|${NGINX_SERVER_NAME}|g" \
      -e "s|@@WEB_PORT@@|${WEB_PORT}|g" -e "s|@@TLS_CERT@@|${NGINX_TLS_CERT:-}|g" -e "s|@@TLS_KEY@@|${NGINX_TLS_KEY:-}|g" "$TPL")"
  if [ "$DRY_RUN" = "1" ]; then
    info "[dry-run] будет записан файл: $SITE"
    info "[dry-run] будет создан симлинк: $LINK -> $SITE"
    info "[dry-run] затем: nginx -t  и, только при успехе, reload nginx"
    printf '%s\n' "$RENDERED" | sed 's/^/    | /'
  else
    NEW_FILE=0; NEW_LINK=0
    [ -e "$SITE" ] || NEW_FILE=1
    [ -e "$LINK" ] || [ -L "$LINK" ] || NEW_LINK=1
    PREV=""; [ -f "$SITE" ] && PREV="$(cat "$SITE")"
    printf '%s\n' "$RENDERED" | as_root tee "$SITE" >/dev/null
    [ "$NEW_LINK" -eq 1 ] && as_root ln -s "$SITE" "$LINK"
    NGINX_BIN="nginx"; [ "$(id -u)" -eq 0 ] || NGINX_BIN="sudo nginx"
    if $NGINX_BIN -t 2>/tmp/vm_nginx_t.$$; then
      ok "nginx -t успешно"
      if command -v systemctl >/dev/null 2>&1; then as_root systemctl reload nginx; else as_root nginx -s reload; fi
      ok "nginx перезагружен (reload)"
    else
      fail "nginx -t НЕ прошёл: $(tail -5 /tmp/vm_nginx_t.$$ | tr '\n' ' ')"
      # Откат ТОЛЬКО собственных изменений; существующий nginx продолжает работать.
      [ "$NEW_LINK" -eq 1 ] && as_root rm -f "$LINK"
      if [ "$NEW_FILE" -eq 1 ]; then as_root rm -f "$SITE"; elif [ -n "$PREV" ]; then printf '%s\n' "$PREV" | as_root tee "$SITE" >/dev/null; fi
      rm -f /tmp/vm_nginx_t.$$
      die "Конфигурация nginx не применена, перезагрузка не выполнялась. Остальные сервисы nginx не затронуты."
    fi
    rm -f /tmp/vm_nginx_t.$$
  fi
else
  info "NGINX_MANAGE!=yes — конфигурация host nginx не создаётся (настройте reverse proxy вручную, см. DEPLOYMENT.md)."
fi

# --------------------------------------------------------------- 5. файрвол
if [ "$FIREWALL" -eq 1 ]; then
  log; log "== Файрвол (явно запрошен) =="
  if command -v ufw >/dev/null 2>&1; then
    as_root ufw allow "${LIVEKIT_TCP_PORT}/tcp" comment "${COMPOSE_PROJECT_NAME} livekit ice-tcp"
    as_root ufw allow "${LIVEKIT_UDP_PORT}/udp" comment "${COMPOSE_PROJECT_NAME} livekit ice-udp"
  else
    warn "ufw не найден — правила файрвола не добавлены; откройте tcp/${LIVEKIT_TCP_PORT} и udp/${LIVEKIT_UDP_PORT} вручную."
  fi
else
  info "Файрвол не изменяется. Для работы с клиентов LAN должны быть доступны tcp/${LIVEKIT_TCP_PORT} и udp/${LIVEKIT_UDP_PORT} хоста."
fi

# --------------------------------------------------------------- 6. запуск
if [ "$SKIP_START" -eq 1 ]; then ok "Запуск сервисов пропущен (--skip-start)."; exit 0; fi
log; log "== Сборка и запуск (только проект ${COMPOSE_PROJECT_NAME}) =="
if [ ! -f "$DATA_ROOT/models/gigaam/${ASR_MODEL_NAME:-v3_e2e_rnnt}.ckpt" ]; then
  warn "Модель ASR не подготовлена. Выполните: scripts/models.sh  (ASR-сервис без модели не станет ready)."
fi
dc_run() { if [ "$DRY_RUN" = "1" ]; then compose_args; printf '%s[dry-run]%s docker %s\n' "$C_YEL" "$C_OFF" "${COMPOSE_ARGS[*]} $*"; else dc "$@"; fi; }
dc_run build
dc_run up -d postgres redis
dc_run run --rm --no-deps backend alembic upgrade head
dc_run up -d
if [ "$DRY_RUN" != "1" ]; then
  info "Ожидание готовности сервисов…"
  "$REPO_ROOT/scripts/status.sh" --env "$ENV_FILE" --wait 180 || warn "Не все сервисы стали healthy — см. scripts/logs.sh"
  "$REPO_ROOT/scripts/preflight.sh" --env "$ENV_FILE" --profile "$PROFILE" --phase post --skip-network || true
fi
ok "Установка завершена."
log "Дальше: scripts/smoke-test.sh"
