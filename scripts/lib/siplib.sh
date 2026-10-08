#!/usr/bin/env bash
# siplib.sh — SIP-телефония (LiveKit SIP Service): признак «включена и можно запускать», подготовка образа, строка конфигурации LiveKit для общей шины Redis.
#
# Телефония по умолчанию ВЫКЛЮЧЕНА: ни контейнер livekit-sip, ни порты SIP/RTP не создаются. Включение — scripts/sip.sh enable (или кнопка в админке
# «SIP-телефония»): LiveKit получает доступ к Redis (общая шина с SIP), запускается контейнер livekit-sip (профиль compose `sip`), публикуются порты
#   * SIP signalling   SIP_SIGNALING_PORT (по умолчанию 5060) — UDP и TCP;
#   * RTP (голос)       SIP_RTP_START–SIP_RTP_END (по умолчанию 20000–20100) — UDP.
# Это ОТДЕЛЬНЫЕ от браузерных (443 + порты LiveKit RTC) порты: они нужны только АТС/провайдеру. Если АТС внутренняя, в Интернет их публиковать не нужно:
# SIP_BIND_ADDR ограничивает адрес, SIP_ALLOWED_CIDRS (адреса АТС) используется в правилах файрвола (scripts/sip.sh firewall).
#
# Только функции, ничего не выполняет. Зависит от common.sh (load_env выполнен).

if [ -n "${_VM_SIPLIB_LOADED:-}" ]; then return 0; fi
_VM_SIPLIB_LOADED=1

SIP_DEFAULT_IMAGE="livekit/sip:v1.13.0"

sip_enabled()  { [ "${SIP_ENABLED:-no}" = "yes" ]; }
sip_image()    { printf '%s' "${SIP_IMAGE:-$SIP_DEFAULT_IMAGE}"; }
sip_port()     { printf '%s' "${SIP_SIGNALING_PORT:-5060}"; }
sip_rtp()      { printf '%s-%s' "${SIP_RTP_START:-20000}" "${SIP_RTP_END:-20100}"; }

# Контейнер livekit-sip нужно запускать: телефония включена и образ скачан (кэш на время процесса; после pull вызвать sip_refresh)
_SIP_ACTIVE=""
sip_refresh() { _SIP_ACTIVE=""; }
sip_active() {
  if [ -n "$_SIP_ACTIVE" ]; then [ "$_SIP_ACTIVE" = 1 ]; return; fi
  _SIP_ACTIVE=0
  sip_enabled || return 1
  command -v docker >/dev/null 2>&1 || return 1
  docker image inspect "$(sip_image)" >/dev/null 2>&1 || return 1
  _SIP_ACTIVE=1
  return 0
}

# Образ SIP: мягко (нет интернета — предупреждение, остальная система работает)
sip_prepare() { # sip_prepare [soft]
  local soft="${1:-}"
  sip_enabled || return 0
  if [ "${DRY_RUN:-0}" = "1" ]; then info "[dry-run] SIP-телефония включена: образ $(sip_image)"; return 0; fi
  if command -v docker >/dev/null 2>&1 && ! docker image inspect "$(sip_image)" >/dev/null 2>&1; then
    info "Загрузка образа LiveKit SIP: $(sip_image)"
    if ! docker pull "$(sip_image)" >/dev/null 2>&1; then
      warn "Образ LiveKit SIP ($(sip_image)) не скачан (нет доступа к Docker Hub?). Телефония заработает после его загрузки: повторите обновление при наличии интернета."
      sip_refresh; [ "$soft" = soft ] && return 0 || return 1
    fi
  fi
  sip_refresh
  return 0
}

# Строка конфигурации LiveKit: подключение к общему Redis (db 1, чтобы не пересекаться с данными приложения)
sip_livekit_redis_line() { printf 'redis: {address: redis:6379, password: %s, db: 1}' "${REDIS_PASSWORD:-}"; }
