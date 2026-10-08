#!/usr/bin/env bash
# sip.sh — SIP-телефония (LiveKit SIP): включить / выключить на сервере, состояние, правила файрвола. Нужен root (кроме status).
#
#   sudo scripts/sip.sh enable  [--env FILE] [--yes]    включить: LiveKit подключается к Redis, запускается контейнер livekit-sip, публикуются порты SIP и RTP
#   sudo scripts/sip.sh disable [--env FILE] [--yes]    выключить: контейнер и порты убираются (профили SIP в базе сохраняются)
#   scripts/sip.sh status [--env FILE]                  включена ли телефония, работает ли служба, какие порты нужны
#   scripts/sip.sh firewall [--apply] [--env FILE]      показать (или с --apply создать) правила ufw ТОЛЬКО для адресов АТС из SIP_ALLOWED_CIDRS
#
# Порты телефонии (SIP signalling и RTP) НЕ имеют отношения к 443 и портам LiveKit RTC для браузеров: они нужны только АТС/SIP-провайдеру.
# Внутреннюю АТС в Интернет не публикуем: задайте SIP_ALLOWED_CIDRS (подсети/адреса АТС) и, при необходимости, SIP_BIND_ADDR (внутренний адрес сервера).
# Скрипт меняет только .env этого экземпляра и контейнеры его compose-проекта; чужие правила файрвола, сервисы и nginx не затрагиваются.
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
source "$(dirname "${BASH_SOURCE[0]}")/lib/siplib.sh"

CMD="${1:-status}"; [ $# -gt 0 ] && shift
YES=0; APPLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --apply) APPLY=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name; require_vars DATA_ROOT

ports_text() {
  log "  SIP signalling : $(sip_port)/udp и $(sip_port)/tcp  (АТС → сервер; адрес привязки: ${SIP_BIND_ADDR:-0.0.0.0})"
  log "  RTP (голос)    : $(sip_rtp)/udp  (АТС ↔ сервер; в SDP объявляется адрес ${SIP_MEDIA_IP:-${LIVEKIT_NODE_IP:-?}})"
  log "  Разрешённые адреса АТС (для файрвола): ${SIP_ALLOWED_CIDRS:-не заданы}"
  log "  Браузерные участники используют другие порты (443 и LiveKit RTC ${LIVEKIT_TCP_PORT:-?}/tcp, ${LIVEKIT_UDP_PORT:-?}/udp) — они не меняются."
}

need_root() { [ "$(id -u)" -eq 0 ] || die "Нужны права root: sudo scripts/sip.sh $CMD"; }

case "$CMD" in
  status)
    if sip_enabled; then ok "Телефония включена на сервере (SIP_ENABLED=yes)"; else info "Телефония выключена (SIP_ENABLED=no): служба SIP не запущена, порты не открыты. Включить: sudo scripts/sip.sh enable"; fi
    ports_text
    if sip_enabled; then
      cid="$(dc ps -q livekit-sip 2>/dev/null | head -1)"
      if [ -n "$cid" ]; then log "  Контейнер livekit-sip: $(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"; else warn "Контейнер livekit-sip не запущен (образ $(sip_image) $(docker image inspect "$(sip_image)" >/dev/null 2>&1 && echo есть || echo 'не скачан'))"; fi
    fi ;;
  enable)
    need_root
    [ "$YES" -eq 1 ] || { [ -t 0 ] || die "Нужно подтверждение: добавьте --yes"; ports_text; read -r -p "Включить SIP-телефонию (откроются перечисленные порты, LiveKit будет перезапущен — идущие звонки прервутся)? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || die "Отменено."; }
    command -v docker >/dev/null 2>&1 || die "Docker не найден"
    upd_env_set_key "$ENV_FILE" SIP_ENABLED yes || die "Не удалось записать SIP_ENABLED в .env"
    upd_env_set_key "$ENV_FILE" LIVEKIT_REDIS_LINE "$(sip_livekit_redis_line)" || die "Не удалось записать LIVEKIT_REDIS_LINE"
    for kv in "SIP_SIGNALING_PORT=5060" "SIP_RTP_START=20000" "SIP_RTP_END=20100"; do
      grep -qE "^${kv%%=*}=" "$ENV_FILE" || upd_env_set_key "$ENV_FILE" "${kv%%=*}" "${kv#*=}"
    done
    unset SIP_ENABLED LIVEKIT_REDIS_LINE SIP_SIGNALING_PORT SIP_RTP_START SIP_RTP_END; load_env "$ENV_FILE"
    sip_refresh
    sip_prepare || die "Не удалось скачать образ SIP"
    info "Перезапуск LiveKit с подключением к Redis и запуск livekit-sip…"
    dc up -d --no-build livekit livekit-sip || die "docker compose up не выполнен (scripts/logs.sh livekit)"
    ok "Телефония включена. Откройте Администрирование → SIP-телефония: добавьте профиль (транк) и нажмите «Проверить настройки»."
    ports_text
    warn "Файрвол сервера не изменён. Разрешите порты только для адресов АТС: sudo scripts/sip.sh firewall --apply (нужен SIP_ALLOWED_CIDRS в .env)." ;;
  disable)
    need_root
    [ "$YES" -eq 1 ] || { [ -t 0 ] || die "Нужно подтверждение: добавьте --yes"; read -r -p "Выключить SIP-телефонию? [y/N] " a; [[ "$a" =~ ^[Yy] ]] || die "Отменено."; }
    dc stop livekit-sip >/dev/null 2>&1 || true; dc rm -f livekit-sip >/dev/null 2>&1 || true
    upd_env_set_key "$ENV_FILE" SIP_ENABLED no; upd_env_set_key "$ENV_FILE" LIVEKIT_REDIS_LINE ""
    unset SIP_ENABLED LIVEKIT_REDIS_LINE; load_env "$ENV_FILE"; sip_refresh
    dc up -d --no-build livekit || warn "LiveKit не перезапущен (scripts/logs.sh livekit)"
    ok "Телефония выключена: контейнер livekit-sip остановлен, порты SIP/RTP не публикуются. Правила файрвола, если вы их создавали, удалите вручную (ufw status numbered)." ;;
  firewall)
    cidrs="$(printf '%s' "${SIP_ALLOWED_CIDRS:-}" | tr ',;' '  ')"
    if [ -z "${cidrs// /}" ]; then die "SIP_ALLOWED_CIDRS не задан в .env (адреса/подсети вашей АТС через запятую). Открывать SIP всем подряд скрипт не предлагает."; fi
    for c in $cidrs; do [[ "$c" =~ ^[0-9a-fA-F:.]+(/[0-9]{1,3})?$ ]] || die "Некорректный адрес в SIP_ALLOWED_CIDRS: $c"; done
    log "Правила ufw (только для адресов АТС: ${cidrs}):"
    for c in $cidrs; do
      log "  ufw allow from $c to any port $(sip_port) proto udp comment 'peregovorka sip'"
      log "  ufw allow from $c to any port $(sip_port) proto tcp comment 'peregovorka sip'"
      log "  ufw allow from $c to any port $(sip_rtp | tr - :) proto udp comment 'peregovorka sip rtp'"
    done
    if [ "$APPLY" -eq 1 ]; then
      need_root; command -v ufw >/dev/null 2>&1 || die "ufw не найден — примените правила вашим файрволом по списку выше"
      for c in $cidrs; do
        ufw allow from "$c" to any port "$(sip_port)" proto udp comment 'peregovorka sip' >/dev/null && ufw allow from "$c" to any port "$(sip_port)" proto tcp comment 'peregovorka sip' >/dev/null \
          && ufw allow from "$c" to any port "$(sip_rtp | tr - :)" proto udp comment 'peregovorka sip rtp' >/dev/null || die "ufw отказал для $c"
      done
      ok "Правила ufw добавлены"
    else info "Показано без применения. Применить: sudo scripts/sip.sh firewall --apply"; fi ;;
  *) die "Неизвестная команда: $CMD (enable | disable | status | firewall)" ;;
esac
