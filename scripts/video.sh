#!/usr/bin/env bash
# video.sh — видеозапись встреч (LiveKit Egress): включить / выключить на сервере, состояние. Нужен root (кроме status).
#
#   sudo scripts/video.sh enable  [--env FILE] [--yes]    включить: LiveKit подключается к Redis (общая шина), запускается контейнер livekit-egress (≈ 1,3 ГБ образ)
#   sudo scripts/video.sh disable [--env FILE] [--yes]    выключить: контейнер livekit-egress останавливается (готовые записи и настройки комнат сохраняются)
#   scripts/video.sh status [--env FILE]                  включена ли видеозапись на сервере, работает ли служба, хватает ли места и процессора
#
# Служба Egress запускает внутри браузер и кодирует видео: на каждую одновременную запись нужно ≈ 2–4 ядра процессора и ≈ 1–2 ГБ памяти (замеры: docs/VIDEO_RECORDING.md).
# После включения на сервере администратор разрешает функцию в приложении: Администрирование → Видеозапись → «Включить видеозапись».
# Скрипт меняет только .env этого экземпляра и контейнеры его compose-проекта; чужие сервисы, файрвол и nginx не затрагиваются. Внешних портов у службы нет.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CMD="${1:-status}"; [ $# -gt 0 ] && shift
YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name; require_vars DATA_ROOT

need_root() { [ "$(id -u)" -eq 0 ] || die "Нужны права root: sudo scripts/video.sh $CMD"; }
free_gb() { df -Pk "$DATA_ROOT" 2>/dev/null | awk 'NR==2{printf "%d", $4/1048576}'; }

case "$CMD" in
  status)
    if video_enabled; then ok "Видеозапись включена на сервере (VIDEO_ENABLED=yes)"; else info "Видеозапись выключена (VIDEO_ENABLED=no): служба Egress не запущена. Включить: sudo scripts/video.sh enable"; fi
    log "  Образ: $(video_image)"
    log "  Свободно на томе записей: $(free_gb) ГБ; процессоров: $(nproc 2>/dev/null || echo ?)"
    if video_enabled; then
      cid="$(dc ps -q livekit-egress 2>/dev/null | head -1)"
      if [ -n "$cid" ]; then log "  Контейнер livekit-egress: $(docker inspect -f '{{.State.Status}} / {{if .State.Health}}{{.State.Health.Status}}{{else}}без проверки{{end}}' "$cid" 2>/dev/null)"; else warn "Контейнер livekit-egress не запущен (образ $(video_image) $(docker image inspect "$(video_image)" >/dev/null 2>&1 && echo скачан || echo не скачан)). Повторите: sudo scripts/video.sh enable"; fi
    fi ;;
  enable)
    need_root
    [ "$YES" -eq 1 ] || { [ -t 0 ] || die "Нужно подтверждение: добавьте --yes"; log "Будет скачан образ ≈ 1,3 ГБ (свободно $(free_gb) ГБ), LiveKit будет перезапущен — идущие звонки на секунды прервутся."; read -r -p "Включить видеозапись? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || die "Отменено."; }
    command -v docker >/dev/null 2>&1 || die "Docker не найден"
    fg="$(free_gb)"; [ -n "$fg" ] && [ "$fg" -lt 8 ] && die "Мало места на томе данных ($fg ГБ): для образа Egress и записей нужно не менее 8 ГБ."
    upd_env_set_key "$ENV_FILE" VIDEO_ENABLED yes || die "Не удалось записать VIDEO_ENABLED в .env"
    upd_env_set_key "$ENV_FILE" LIVEKIT_REDIS_LINE "$(video_livekit_redis_line)" || die "Не удалось записать LIVEKIT_REDIS_LINE"
    unset VIDEO_ENABLED LIVEKIT_REDIS_LINE; load_env "$ENV_FILE"; video_refresh
    video_prepare || die "Не удалось скачать образ Egress"
    info "Перезапуск LiveKit с подключением к Redis и запуск livekit-egress…"
    dc up -d --no-build livekit livekit-egress || die "docker compose up не выполнен (scripts/logs.sh livekit-egress)"
    ok "Видеозапись включена на сервере. Теперь в приложении: Администрирование → Видеозапись → «Включить видеозапись», затем режим в настройках комнаты (вкладка «Запись»)." ;;
  disable)
    need_root
    [ "$YES" -eq 1 ] || { [ -t 0 ] || die "Нужно подтверждение: добавьте --yes"; read -r -p "Выключить видеозапись (идущие записи будут закрыты)? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || die "Отменено."; }
    dc stop -t 60 livekit-egress >/dev/null 2>&1 || true; dc rm -f livekit-egress >/dev/null 2>&1 || true
    upd_env_set_key "$ENV_FILE" VIDEO_ENABLED no
    # общая шина Redis остаётся, если нужна телефонии
    if [ "${SIP_ENABLED:-no}" != "yes" ]; then upd_env_set_key "$ENV_FILE" LIVEKIT_REDIS_LINE ""; fi
    unset VIDEO_ENABLED LIVEKIT_REDIS_LINE; load_env "$ENV_FILE"; video_refresh
    dc up -d --no-build livekit || warn "LiveKit не перезапущен (scripts/logs.sh livekit)"
    ok "Видеозапись выключена: контейнер livekit-egress остановлен. Готовые записи и настройки комнат сохранены." ;;
  *) die "Неизвестная команда: $CMD (enable | disable | status)" ;;
esac
