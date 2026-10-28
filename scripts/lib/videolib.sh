#!/usr/bin/env bash
# videolib.sh — видеозапись встреч (LiveKit Egress): признак «включена и можно запускать», подготовка образа, строка конфигурации LiveKit для общей шины Redis.
#
# Видеозапись по умолчанию ВЫКЛЮЧЕНА: контейнер livekit-egress не создаётся. Включение — scripts/video.sh enable: LiveKit подключается к Redis (общая шина с Egress и SIP),
# запускается контейнер livekit-egress (профиль compose `video`). Портов наружу у службы нет. Только функции, ничего не выполняет. Зависит от common.sh.

if [ -n "${_VM_VIDEOLIB_LOADED:-}" ]; then return 0; fi
_VM_VIDEOLIB_LOADED=1

VIDEO_DEFAULT_IMAGE="livekit/egress:v1.15.0@sha256:ac244a40268ce1dd1510fbff6eb529609ec68aceff1602343f79be8ca08c355b"

video_enabled() { [ "${VIDEO_ENABLED:-no}" = "yes" ]; }
video_image()   { printf '%s' "${VIDEO_IMAGE:-$VIDEO_DEFAULT_IMAGE}"; }

_VIDEO_ACTIVE=""
video_refresh() { _VIDEO_ACTIVE=""; }
video_active() {
  if [ -n "$_VIDEO_ACTIVE" ]; then [ "$_VIDEO_ACTIVE" = 1 ]; return; fi
  _VIDEO_ACTIVE=0
  video_enabled || return 1
  command -v docker >/dev/null 2>&1 || return 1
  docker image inspect "$(video_image)" >/dev/null 2>&1 || return 1
  _VIDEO_ACTIVE=1
  return 0
}

# Образ Egress (≈ 1,3 ГБ): мягко — нет интернета или места, остальная система работает, видеозапись заработает после загрузки образа
video_prepare() { # video_prepare [soft]
  local soft="${1:-}"
  video_enabled || return 0
  if [ "${DRY_RUN:-0}" = "1" ]; then info "[dry-run] Видеозапись включена: образ $(video_image)"; return 0; fi
  if command -v docker >/dev/null 2>&1 && ! docker image inspect "$(video_image)" >/dev/null 2>&1; then
    info "Загрузка образа LiveKit Egress (≈ 1,3 ГБ): $(video_image)"
    if ! docker pull "$(video_image)" >/dev/null 2>&1; then
      warn "Образ LiveKit Egress ($(video_image)) не скачан (нет доступа к Docker Hub или места на диске?). Видеозапись заработает после его загрузки: повторите обновление."
      video_refresh; [ "$soft" = soft ] && return 0 || return 1
    fi
  fi
  video_refresh
  return 0
}

# Строка конфигурации LiveKit: подключение к общему Redis (db 1 — та же, что у SIP; данные приложения лежат в другой)
video_livekit_redis_line() { printf 'redis: {address: redis:6379, password: %s, db: 1}' "${REDIS_PASSWORD:-}"; }
