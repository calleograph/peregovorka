#!/usr/bin/env bash
# complib.sh — штатные дополнительные компоненты сервера: состояние и управление из веб-интерфейса (Администрирование → Видеозапись, Трансляции, SIP).
#
# Единый подход для всех компонентов, которыми раньше управляли по SSH:
#   • веб-интерфейс кладёт запрос `action=component op=ID` в каталог обмена; исполнитель (scripts/updater.sh, служба от root) проверяет ID по белому
#     списку COMPONENT_OPS и выполняет ТОЛЬКО фиксированную последовательность действий над контейнерами ЭТОГО compose-проекта (comp_apply_ID);
#   • произвольную команду, путь или имя контейнера передать нельзя: в запросе только ID из списка; одновременно выполняется одно действие
#     (блокировка исполнителя), повторный запрос во время работы backend отклоняет;
#   • фактическое состояние (что включено в .env, запущены ли контейнеры, их health, скачан ли образ) исполнитель пишет в components.json —
#     backend видит его без доступа к Docker. Переменные окружения контейнера backend задаются при его запуске и после включения функции
#     устаревают, поэтому истиной о сервере считается components.json.
# В отличие от «Исправить автоматически» (repairlib.sh), это действия, а не исправления: они выполняются по запросу администратора без
# предварительного поиска неисправности (включить выключенное — не неисправность). Только функции; зависит от common.sh (load_env выполнен).

COMPONENT_OPS=(video_enable video_disable video_restart sip_enable sip_disable sip_restart livekit_restart web_restart state)
comp_op_valid() { local i; for i in "${COMPONENT_OPS[@]}"; do [ "$i" = "$1" ] && return 0; done; return 1; }

# comp_text ID → ЗАГОЛОВОК (для журнала исполнителя). Последствия и подтверждение показывает веб-интерфейс (services/server_components.py).
comp_text() {
  case "$1" in
    video_enable) echo "Установка и включение видеоподсистемы (LiveKit Egress)" ;;
    video_disable) echo "Выключение видеоподсистемы" ;;
    video_restart) echo "Перезапуск службы записи и эфира (livekit-egress)" ;;
    sip_enable) echo "Установка и включение SIP-телефонии" ;;
    sip_disable) echo "Выключение SIP-телефонии" ;;
    sip_restart) echo "Перезапуск службы телефонии (livekit-sip)" ;;
    livekit_restart) echo "Перезапуск сервера звонков (LiveKit)" ;;
    web_restart) echo "Перезапуск веб-сервера приложения (web)" ;;
    state) echo "Обновление сведений о компонентах" ;;
  esac
}

# Пересоздать контейнер по текущему .env из уже собранного/скачанного образа (так применяются изменения .env; `restart` их не применяет)
_comp_recreate() { dc up -d --no-build --force-recreate "$@"; }

comp_apply_video_enable() { "$REPO_ROOT/scripts/video.sh" enable --env "$ENV_FILE" --yes; }
comp_apply_video_disable() { "$REPO_ROOT/scripts/video.sh" disable --env "$ENV_FILE" --yes; }
comp_apply_video_restart() {
  video_enabled || { fail "Видеоподсистема выключена (VIDEO_ENABLED=no): сначала «Установить и включить»."; return 1; }
  video_prepare || return 1
  info "Пересоздание контейнера livekit-egress (идущие записи и эфиры будут закрыты)…"
  _comp_recreate livekit-egress
}
comp_apply_sip_enable() { "$REPO_ROOT/scripts/sip.sh" enable --env "$ENV_FILE" --yes; }
comp_apply_sip_disable() { "$REPO_ROOT/scripts/sip.sh" disable --env "$ENV_FILE" --yes; }
comp_apply_sip_restart() {
  sip_enabled || { fail "Телефония выключена (SIP_ENABLED=no): сначала «Установить и включить»."; return 1; }
  sip_prepare || return 1
  info "Пересоздание контейнера livekit-sip (идущие телефонные звонки прервутся)…"
  _comp_recreate livekit-sip
}
comp_apply_livekit_restart() { info "Пересоздание контейнера livekit (идущие звонки на несколько секунд прервутся)…"; _comp_recreate livekit; }
comp_apply_web_restart() { info "Пересоздание контейнера web (страницы на несколько секунд недоступны)…"; _comp_recreate web; }
comp_apply_state() { return 0; }

# Ждать, пока контейнер станет healthy (или просто running, если проверки нет): COMP_WAIT_S секунд
comp_wait() { # comp_wait СЕРВИС
  local svc="$1" i cid st
  for ((i = 0; i < ${COMP_WAIT_S:-120}; i += 3)); do
    cid="$(dc ps -q "$svc" 2>/dev/null | head -1)"
    if [ -n "$cid" ]; then
      st="$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null)"
      case "$st" in running/healthy|running/none) return 0 ;; exited/*|dead/*) return 1 ;; esac
    fi
    sleep 3
  done
  return 1
}

# Какие службы должны заработать после действия (проверка результата)
comp_expect() {
  case "$1" in
    video_enable|video_restart) echo "livekit livekit-egress" ;;
    sip_enable|sip_restart) echo "livekit livekit-sip" ;;
    livekit_restart|video_disable|sip_disable) echo "livekit" ;;
    web_restart) echo "web" ;;
  esac
}

# comp_apply ID — действие → ожидание служб → итог. Код 0 — выполнено и службы работают.
comp_apply() {
  local id="$1" svc bad=()
  comp_op_valid "$id" || { fail "Неизвестное действие"; return 2; }
  [ "$id" = state ] && return 0
  info "1) $(comp_text "$id")"
  if ! "comp_apply_$id"; then fail "Действие не выполнено. Причина — в строках выше; журнал службы: scripts/logs.sh $(comp_expect "$id" | awk '{print $NF}')"; return 1; fi
  info "2) Проверка: службы запущены и отвечают"
  for svc in $(comp_expect "$id"); do
    if comp_wait "$svc"; then ok "  $svc работает"; else bad+=("$svc"); fail "  $svc не запустился за ${COMP_WAIT_S:-120} с (scripts/logs.sh $svc)"; fi
  done
  [ "${#bad[@]}" -eq 0 ]
}

# ---- состояние для веб-интерфейса: components.json
_cjesc() { printf '%s' "$1" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
# Контейнеры проекта — ОДНИМ вызовом docker ps по метке compose-проекта (проверка идёт при каждом опросе помощника и не должна его задерживать)
_COMP_PS=""
_comp_ps() { _COMP_PS="$(timeout 8 docker ps -a --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-}" --format '{{.Label "com.docker.compose.service"}} {{.ID}}' 2>/dev/null)"; }
_comp_container() { # → "состояние|health|перезапусков|время запуска" или "absent"
  local cid; cid="$(awk -v s="$1" '$1 == s {print $2; exit}' <<<"$_COMP_PS")"
  [ -n "$cid" ] || { echo absent; return; }
  docker inspect -f '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}|{{.RestartCount}}|{{.State.StartedAt}}' "$cid" 2>/dev/null || echo absent
}
_comp_obj() { # _comp_obj СЕРВИС
  local s st hl rc at; s="$(_comp_container "$1")"
  if [ "$s" = absent ]; then printf '{"state":"absent"}'; return; fi
  IFS='|' read -r st hl rc at <<<"$s"
  [[ "$rc" =~ ^[0-9]+$ ]] || rc=0
  printf '{"state":"%s","health":"%s","restarts":%s,"started_at":"%s"}' "$(_cjesc "$st")" "$(_cjesc "$hl")" "$rc" "$(_cjesc "$at")"
}
_comp_img() { # _comp_img ССЫЛКА КЛЮЧ ПЕРЕМЕННАЯ — образ есть на сервере под основным именем или под именем выбранного зеркала
  img_local_ref "$1" "$2" "$3" >/dev/null 2>&1 && echo true || echo false
}
_comp_src() { # источник образа: mirror — выбран автоматически (зеркало), primary — основной или заданный вручную
  img_auto_value "$1" "$2" && echo '"mirror"' || echo '"primary"'
}

comp_state_json() {
  local docker_ok=false free
  command -v docker >/dev/null 2>&1 && timeout 8 docker info >/dev/null 2>&1 && docker_ok=true
  free="$(df -Pk "${DATA_ROOT:-/}" 2>/dev/null | awk 'NR==2{printf "%d", $4/1048576}')"
  printf '{"ts":%s,"docker":%s,"free_gb":%s,"env":{"video_enabled":%s,"sip_enabled":%s,"livekit_redis":%s},' "$(date +%s)" "$docker_ok" "${free:-null}" \
    "$(video_enabled && echo true || echo false)" "$(sip_enabled && echo true || echo false)" "$([ -n "${LIVEKIT_REDIS_LINE:-}" ] && echo true || echo false)"
  if [ "$docker_ok" = true ]; then
    _comp_ps
    printf '"images":{"egress":%s,"sip":%s},"image_errors":{"egress":%s,"sip":%s},"image_sources":{"egress":%s,"sip":%s},"containers":{"livekit":%s,"livekit-egress":%s,"livekit-sip":%s,"web":%s,"backend":%s}}\n' \
      "$(_comp_img "$(video_image)" egress VIDEO_IMAGE)" "$(_comp_img "$(sip_image)" sip SIP_IMAGE)" "$(img_error_json egress)" "$(img_error_json sip)" \
      "$(_comp_src egress VIDEO_IMAGE)" "$(_comp_src sip SIP_IMAGE)" "$(_comp_obj livekit)" "$(_comp_obj livekit-egress)" "$(_comp_obj livekit-sip)" "$(_comp_obj web)" "$(_comp_obj backend)"
  else
    printf '"images":null,"containers":null}\n'
  fi
}
