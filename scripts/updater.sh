#!/usr/bin/env bash
# updater.sh — «исполнитель» обновлений из веб-интерфейса (Администрирование → Обновления).
#
#   scripts/updater.sh run [--env FILE] [--interval СЕК]   # работает постоянно: выполняет запросы из веб-интерфейса и сверяется с GitHub
#   scripts/updater.sh check [--env FILE]                  # разовая проверка: что нового в репозитории (записывает remote.json)
#   scripts/updater.sh status [--env FILE]                 # работает ли исполнитель и что он делает
#   scripts/updater.sh print-unit [--env FILE]             # показать unit systemd (ничего не устанавливает)
#   scripts/updater.sh install [--env FILE] [--yes]        # установить и запустить как службу systemd (нужен sudo; спрашивает подтверждение)
#   scripts/updater.sh uninstall [--env FILE] [--yes]      # остановить и удалить службу
#
# Зачем: контейнер backend не имеет доступа к Docker и git хоста (и не должен). Веб-интерфейс кладёт запрос в каталог
# $DATA_ROOT/updater, а этот скрипт — «помощник» на хосте, служба systemd ОТ ROOT (иначе без пароля sudo нельзя ни выдать права на
# каталоги, ни обновить свой nginx-site) — выполняет ТОЛЬКО фиксированный набор действий:
#   check   — сверка с GitHub;      update — штатный scripts/update.sh --yes (флаги из белого списка);
#   repair  — одно исправление из белого списка scripts/lib/repairlib.sh («Исправить автоматически»);   scan — обнаружить проблемы.
# Произвольные команды и пути из веб-интерфейса выполнить нельзя: запрос — простые строки key=value, каждое значение проверяется.
# Дочерние команды запускаются в СТЕРИЛЬНОМ окружении (env -i): настройки берутся только из .env, а не из унаследованных ASR_*/NGINX_*/LIVEKIT_*.
#
# Файлы обмена ($DATA_ROOT/updater): request.txt (из веб-интерфейса), status.json, remote.json, repairs.json, update.log (пишет этот скрипт).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CMD="${1:-run}"; [ $# -gt 0 ] && shift
INTERVAL=3; CHECK_EVERY="${UPDATER_CHECK_EVERY:-1800}"; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
: "${DATA_ROOT:?DATA_ROOT не задан}"
CH="$DATA_ROOT/updater"
LOG="$CH/update.log"
# root в каталоге проекта, принадлежащем другому пользователю: git без этого отказывает («dubious ownership»)
if [ "$(id -u)" -eq 0 ]; then export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0="$REPO_ROOT"; fi
UNIT="peregovorka-updater-${COMPOSE_PROJECT_NAME}"
STEPS_DEFAULT=16

# ------------------------------------------------------------------------------------------------ служебное
ensure_channel() {
  mkdir -p "$CH" 2>/dev/null || die "Не удалось создать $CH"
  # sticky + для всех: контейнер backend (uid 10001) создаёт request.txt, а читать status/log/remote может любой; удалять чужие файлы нельзя.
  chmod 1777 "$CH" 2>/dev/null || warn "Не удалось выставить права на $CH — веб-интерфейс может не суметь передать запрос"
}

jesc() { # экранирование строки для JSON
  printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037' | tr '\t\r' '  ' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

atomic_write() { # atomic_write ФАЙЛ  (содержимое — со stdin). Случайное имя временного файла: каталог открыт на запись всем (контейнер пишет
  local f="$1" tmp                       # request.txt), а служба работает от root — предсказуемое имя позволило бы подложить symlink на системный файл.
  tmp="$(mktemp "$CH/.w.XXXXXXXX")" || return 1
  cat > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$f" || { rm -f "$tmp"; return 1; }
}
reset_log() { rm -f "$LOG"; ( set -C; umask 022; : > "$LOG" ) 2>/dev/null || :; }

# Окружение дочерних команд: только нужное для работы; всё остальное (в том числе случайные ASR_*, NGINX_*, LIVEKIT_*) отбрасывается.
# Тесты и нестандартные установки могут добавить имена через UPDATER_PASS_ENV.
clean_exec() {
  local v pass=(PATH HOME LANG LC_ALL TZ DOCKER_HOST DOCKER_CONFIG GIT_CONFIG_COUNT GIT_CONFIG_KEY_0 GIT_CONFIG_VALUE_0 ${UPDATER_PASS_ENV:-}) args=()
  for v in "${pass[@]}"; do [ -n "${!v+x}" ] && args+=("$v=${!v}"); done
  args+=("ENV_FILE=$ENV_FILE")
  env -i "${args[@]}" "$@"
}
fresh_env() { sanitize_project_env; load_env "$ENV_FILE"; }

STATE="idle"; REQ_ID=""; STEP_NO=0; STEP_TOTAL=$STEPS_DEFAULT; STEP_NAME=""; STARTED=0; FINISHED=0; EXIT_CODE=null; RESULT=""; BY=""
ACTION=""; REPAIR_ID=""; ST_UPDATE=""; ST_DEPLOY=""; ST_HEALTH=""; ST_INTEG=""; ST_ISSUES=""
write_status() {
  printf '{"ts":%s,"pid":%s,"uid":%s,"state":"%s","action":"%s","repair_id":"%s","request_id":"%s","step_no":%s,"step_total":%s,"step_name":"%s","started_at":%s,"finished_at":%s,"exit_code":%s,"result":"%s","update_status":"%s","deploy_status":"%s","health_status":"%s","integration_status":"%s","integration_issues":"%s","by":"%s","project":"%s"}\n' \
    "$(date +%s)" "$$" "$(id -u)" "$STATE" "$ACTION" "$REPAIR_ID" "$REQ_ID" "$STEP_NO" "$STEP_TOTAL" "$(jesc "$STEP_NAME")" "$STARTED" "$FINISHED" "$EXIT_CODE" "$RESULT" \
    "$ST_UPDATE" "$ST_DEPLOY" "$ST_HEALTH" "$ST_INTEG" "$(jesc "$ST_ISSUES")" "$(jesc "$BY")" "$COMPOSE_PROJECT_NAME" \
    | atomic_write "$CH/status.json"
}

# ------------------------------------------------------------------------------------------------ проверка репозитория
do_check() {
  local prev_state="$STATE" cur remote up ahead behind ff local_changes commits mig envchg first=1 ok=true err=""
  STATE="checking"; write_status
  local ferr; ferr="$(mktemp)"
  if ! git -C "$REPO_ROOT" fetch --tags --prune origin >/dev/null 2>"$ferr"; then ok=false; err="$(head -c 300 "$ferr" | tr '\n' ' ')"; fi
  rm -f "$ferr"
  cur="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)"
  up="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref '@{u}' 2>/dev/null || echo origin/main)"
  remote="$(git -C "$REPO_ROOT" rev-parse "$up" 2>/dev/null || echo "")"
  [ -n "$remote" ] || { ok=false; [ -n "$err" ] || err="не найдена ветка $up"; remote="$cur"; }
  behind="$(git -C "$REPO_ROOT" rev-list --count "HEAD..$remote" 2>/dev/null || echo 0)"
  ahead="$(git -C "$REPO_ROOT" rev-list --count "$remote..HEAD" 2>/dev/null || echo 0)"
  if git -C "$REPO_ROOT" merge-base --is-ancestor HEAD "$remote" 2>/dev/null; then ff=true; else ff=false; fi
  local_changes="$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  mig="$(git -C "$REPO_ROOT" diff --name-only "HEAD..$remote" -- backend/migrations/versions 2>/dev/null | wc -l | tr -d ' ')"
  if git -C "$REPO_ROOT" diff --quiet "HEAD..$remote" -- .env.example 2>/dev/null; then envchg=false; else envchg=true; fi
  # версии и «что изменилось»: VERSION у установленной и у опубликованной редакции, разделы CHANGELOG новее установленной
  local cur_ver rem_ver chg="" chg_esc="" cltmp=""
  cur_ver="$(git -C "$REPO_ROOT" show HEAD:VERSION 2>/dev/null | tr -d '[:space:]')"
  rem_ver="$(git -C "$REPO_ROOT" show "$remote:VERSION" 2>/dev/null | tr -d '[:space:]')"
  cltmp="$(mktemp)"
  if ver_valid "$cur_ver" && git -C "$REPO_ROOT" show "$remote:CHANGELOG.md" > "$cltmp" 2>/dev/null; then
    # полный текст разделов новее установленной версии (для окна «Что нового» и истории обновлений); оборванный на середине символ отбрасывается
    chg="$(changelog_since "$cltmp" "$cur_ver" | head -c 60000 | { iconv -c -f UTF-8 -t UTF-8 2>/dev/null || cat; })"
  fi
  rm -f "$cltmp"
  chg_esc="$(jesc "$chg" | sed -e ':a' -e 'N' -e '$!ba' -e 's/\n/\\n/g')"
  commits=""
  while IFS=$'\t' read -r sha day subj; do
    [ -n "$sha" ] || continue
    [ "$first" = 1 ] || commits+=","
    first=0
    commits+="{\"sha\":\"$(jesc "$sha")\",\"date\":\"$(jesc "$day")\",\"subject\":\"$(jesc "$subj")\"}"
  done < <(git -C "$REPO_ROOT" log -n 30 --pretty='%h%x09%ad%x09%s' --date=short "HEAD..$remote" 2>/dev/null)
  printf '{"checked_at":%s,"ok":%s,"error":"%s","branch":"%s","current":"%s","remote":"%s","behind":%s,"ahead":%s,"ff_possible":%s,"local_changes":%s,"migrations_changed":%s,"env_example_changed":%s,"current_version":"%s","remote_version":"%s","changelog":"%s","commits":[%s]}\n' \
    "$(date +%s)" "$ok" "$(jesc "$err")" "$(jesc "$up")" "${cur:0:12}" "${remote:0:12}" "$behind" "$ahead" "$ff" "$local_changes" "$mig" "$envchg" "$(jesc "$cur_ver")" "$(jesc "$rem_ver")" "$chg_esc" "$commits" | atomic_write "$CH/remote.json"
  STATE="$prev_state"; [ "$STATE" = "checking" ] && STATE="idle"; write_status
  [ "$ok" = true ]
}

# ------------------------------------------------------------------------------------------------ запуск обновления
# Построчно переносит вывод команды в журнал окна; строки вида «[3/16] Этап» обновляют статус.
pump_log() {
  local line
  while IFS= read -r line; do
    printf '%s\n' "$line" >> "$LOG"
    if [[ "$line" =~ ^\[([0-9]+)/([0-9]+)\][[:space:]]+(.*)$ ]]; then STEP_NO="${BASH_REMATCH[1]}"; STEP_TOTAL="${BASH_REMATCH[2]}"; STEP_NAME="${BASH_REMATCH[3]}"; write_status; fi
  done
}
strip_ansi() { sed -u -e 's/\x1b\[[0-9;]*[A-Za-z]//g' -e 's/\r$//'; }

# Итог раздельно: обновление · развёртывание · работоспособность · интеграции (их пишет update.sh в last-update.state).
read_update_statuses() {
  ST_UPDATE="$(upd_state_get update_status)"; ST_DEPLOY="$(upd_state_get deploy_status)"; ST_HEALTH="$(upd_state_get health_status)"
  ST_INTEG="$(upd_state_get integration_status)"; ST_ISSUES="$(upd_state_get integration_issues)"
}

do_update() { # do_update FORCE PULL
  local force="$1" pull="$2" rc line args=(--yes --env "$ENV_FILE")
  [ "$force" = 1 ] && args+=(--force-build)
  [ "$pull" = 1 ] && args+=(--pull)
  ACTION="update"; REPAIR_ID=""; ST_UPDATE=""; ST_DEPLOY=""; ST_HEALTH=""; ST_INTEG=""; ST_ISSUES=""
  STATE="updating"; STARTED="$(date +%s)"; FINISHED=0; EXIT_CODE=null; RESULT=""; STEP_NO=0; STEP_NAME="запуск"; write_status
  reset_log
  printf '=== Обновление запущено %s (запросил: %s) ===\n$ scripts/update.sh %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${BY:-?}" "${args[*]}" >> "$LOG"
  (cd "$REPO_ROOT" && clean_exec UPDATE_SOURCE=web UPDATE_BY="$BY" "$REPO_ROOT/scripts/update.sh" "${args[@]}" 2>&1) | strip_ansi | pump_log
  rc="${PIPESTATUS[0]}"
  # последний этап берём из журнала (цикл разбора выполнялся в подоболочке)
  line="$(grep -E '^\[[0-9]+/[0-9]+\][[:space:]]' "$LOG" 2>/dev/null | tail -1)"
  if [[ "$line" =~ ^\[([0-9]+)/([0-9]+)\][[:space:]]+(.*)$ ]]; then STEP_NO="${BASH_REMATCH[1]}"; STEP_TOTAL="${BASH_REMATCH[2]}"; STEP_NAME="${BASH_REMATCH[3]}"; fi
  FINISHED="$(date +%s)"; EXIT_CODE="$rc"; STATE="idle"
  read_update_statuses
  # 0 — всё хорошо; 3 — обновление и работоспособность в порядке, но проверка интеграций (LDAP и т. п.) нашла проблемы: это НЕ сбой обновления
  case "$rc" in 0|3) RESULT="ok" ;; *) RESULT="failed" ;; esac
  printf '=== Обновление завершено %s: %s (код %s) ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" \
    "$([ "$rc" -eq 0 ] && echo 'успешно' || { [ "$rc" -eq 3 ] && echo 'успешно, но интеграции требуют внимания' || echo 'С ОШИБКОЙ'; })" "$rc" >> "$LOG"
  write_status
  do_scan || true
  do_check || true
  return "$rc"
}

# ------------------------------------------------------------------------------------------------ «Исправить автоматически»
do_scan() { # обнаружить известные проблемы → repairs.json (читает веб-интерфейс)
  # в фоне + wait: сигнал остановки обрабатывается сразу, а не после окончания долгой проверки
  ( ( fresh_env; repair_scan; repair_json ) 2>/dev/null | atomic_write "$CH/repairs.json" ) &
  wait $! 2>/dev/null
}

do_repair() { # do_repair ID — одно исправление из белого списка; проверка ID ещё раз здесь, а не только в веб-интерфейсе
  local id="$1" rc
  repair_id_valid "$id" || { warn "Исправление «$id» не из белого списка — отклонено"; return 2; }
  ACTION="repair"; REPAIR_ID="$id"; ST_UPDATE=""; ST_DEPLOY=""; ST_HEALTH=""; ST_INTEG=""; ST_ISSUES=""
  STATE="repairing"; STARTED="$(date +%s)"; FINISHED=0; EXIT_CODE=null; RESULT=""; STEP_NO=0; STEP_TOTAL=1; STEP_NAME="Исправление"; write_status
  reset_log
  printf '=== Исправление «%s» запущено %s (запросил: %s) ===\n' "$id" "$(date '+%Y-%m-%d %H:%M:%S')" "${BY:-?}" >> "$LOG"
  ( fresh_env; DRY_RUN=0; repair_apply "$id" 2>&1 ) | strip_ansi | pump_log
  rc="${PIPESTATUS[0]}"
  FINISHED="$(date +%s)"; EXIT_CODE="$rc"; STATE="idle"; STEP_NO=1
  if [ "$rc" -eq 0 ]; then RESULT="ok"; else RESULT="failed"; fi
  printf '=== Исправление завершено %s: %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$([ "$rc" -eq 0 ] && echo 'успешно' || echo 'не удалось')" >> "$LOG"
  write_status
  do_scan || true      # повторная проверка: проблема должна исчезнуть из списка
  return "$rc"
}

# ------------------------------------------------------------------------------------------------ запросы
req_get() { sed -n "s/^$1=//p" "$CH/request.txt" 2>/dev/null | head -1 | tr -d '\r'; }

handle_request() {
  local f="$CH/request.txt" action rid force pull by at now repair
  [ -f "$f" ] || return 0
  rid="$(req_get id)"; action="$(req_get action)"; force="$(req_get force_build)"; pull="$(req_get pull)"; by="$(req_get by)"; at="$(req_get at)"; repair="$(req_get repair)"
  rm -f "$f"   # запрос выполняется один раз (rm удаляет и symlink, не следуя за ним)
  [[ "$rid" =~ ^[a-f0-9]{8,32}$ ]] || { warn "Запрос отклонён: некорректный id"; return 0; }
  [[ "$at" =~ ^[0-9]+$ ]] || { warn "Запрос отклонён: нет времени"; return 0; }
  now="$(date +%s)"
  if [ $((now - at)) -gt 600 ] || [ $((at - now)) -gt 120 ]; then warn "Запрос $rid устарел или из будущего — пропущен"; return 0; fi
  [[ "$by" =~ ^[A-Za-z0-9._-]{0,60}$ ]] || by="?"
  [[ "$force" =~ ^[01]$ ]] || force=0; [[ "$pull" =~ ^[01]$ ]] || pull=0
  REQ_ID="$rid"; BY="$by"
  case "$action" in
    check) info "Проверка по запросу ($by)"; do_check || true ;;
    update) info "Обновление по запросу ($by): пересборка=$force базовые образы=$pull"; do_update "$force" "$pull" || warn "Обновление завершилось с кодом $? — см. $LOG" ;;
    scan) info "Поиск проблем по запросу ($by)"; do_scan || true ;;
    repair) if [[ "$repair" =~ ^[a-z_]{3,30}$ ]] && repair_id_valid "$repair"; then info "Исправление «$repair» по запросу ($by)"; do_repair "$repair" || warn "Исправление «$repair» не удалось — см. $LOG"
            else warn "Запрос $rid: исправление «$repair» не из белого списка — отклонён"; fi ;;
    *) warn "Неизвестное действие запроса: $action" ;;
  esac
}

# После обновления файл этого скрипта мог измениться (git merge): запущенный процесс помнит старый код, поэтому перезапускаем себя новой версией
# (иначе новые исправления и проверки заработали бы только после перезапуска службы). Блокировка снимается перед exec.
SELF_SUM=""
self_sum() { sha256sum "${BASH_SOURCE[0]}" "$REPO_ROOT/scripts/lib/repairlib.sh" "$REPO_ROOT/scripts/lib/prereqlib.sh" 2>/dev/null | cut -d' ' -f1 | tr -d '\n'; }
reload_if_changed() {
  [ -n "$SELF_SUM" ] || return 0
  [ "$(self_sum)" = "$SELF_SUM" ] && return 0
  info "Скрипт помощника обновился — перезапускаюсь новой версией"
  STATE="idle"; write_status
  exec 9>&-
  exec bash "$REPO_ROOT/scripts/updater.sh" run --env "$ENV_FILE" --interval "$INTERVAL"
}

cmd_run() {
  ensure_channel
  SELF_SUM="$(self_sum)"
  if command -v flock >/dev/null 2>&1; then
    mkdir -p "$DATA_ROOT/state" 2>/dev/null || true
    exec 9>"$DATA_ROOT/state/updater.lock"
    flock -n 9 || die "Исполнитель обновлений уже запущен (проект $COMPOSE_PROJECT_NAME)."
  fi
  info "Исполнитель обновлений запущен: проект $COMPOSE_PROJECT_NAME, каталог обмена $CH. Остановка — Ctrl+C."
  trap 'STATE="stopped"; write_status; exit 0' INT TERM
  local last_check=0 last_scan=0 now
  STATE="idle"; write_status
  do_scan || true; last_scan="$(date +%s)"
  while :; do
    now="$(date +%s)"
    if [ -f "$CH/request.txt" ]; then handle_request; last_check="$(date +%s)"; reload_if_changed
    elif [ $((now - last_check)) -ge "$CHECK_EVERY" ]; then do_check || true; last_check="$(date +%s)"
    elif [ $((now - last_scan)) -ge "${REPAIR_SCAN_EVERY:-300}" ]; then do_scan || true; last_scan="$(date +%s)"
    else write_status; fi
    sleep "$INTERVAL"
  done
}

cmd_status() {
  ensure_channel
  if [ ! -f "$CH/status.json" ]; then warn "Исполнитель не запускался (нет $CH/status.json). Запустите: scripts/updater.sh install  (или run)"; exit 1; fi
  local ts age
  ts="$(sed -n 's/.*"ts":\([0-9]*\).*/\1/p' "$CH/status.json" | head -1)"; age=$(( $(date +%s) - ${ts:-0} ))
  cat "$CH/status.json"
  if [ "$age" -le 40 ]; then ok "Исполнитель работает (пульс $age с назад)"; else warn "Исполнитель НЕ отвечает (пульс $age с назад)"; exit 1; fi
}

unit_text() {
  cat <<EOF
[Unit]
Description=Peregovorka updater (${COMPOSE_PROJECT_NAME})
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$REPO_ROOT
ExecStart=$REPO_ROOT/scripts/updater.sh run --env $ENV_FILE
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
}

confirm() { # confirm ВОПРОС
  [ "$YES" -eq 1 ] && return 0
  [ -t 0 ] || die "Нужно подтверждение: запустите с --yes или в терминале."
  local a; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[YyДд] ]]
}

cmd_install() {
  command -v systemctl >/dev/null 2>&1 || die "systemd не найден. Запустите исполнитель вручную: nohup $REPO_ROOT/scripts/updater.sh run &"
  ensure_channel
  log "Будет создан файл /etc/systemd/system/$UNIT.service (служба работает от root и выполняет только фиксированный набор действий — см. заголовок scripts/updater.sh):"; unit_text | sed 's/^/    /'
  confirm "Установить и запустить службу $UNIT (потребуется sudo)?" || die "Отменено."
  unit_text | upd_as_root tee "/etc/systemd/system/$UNIT.service" >/dev/null || die "Не удалось записать unit"
  upd_as_root systemctl daemon-reload && upd_as_root systemctl enable --now "$UNIT.service" || die "Не удалось запустить службу"
  ok "Служба $UNIT запущена. Проверка: scripts/updater.sh status. Теперь кнопка «Обновить» в веб-интерфейсе доступна."
}

cmd_uninstall() {
  confirm "Остановить и удалить службу $UNIT?" || die "Отменено."
  upd_as_root systemctl disable --now "$UNIT.service" 2>/dev/null || true
  upd_as_root rm -f "/etc/systemd/system/$UNIT.service" && upd_as_root systemctl daemon-reload
  ok "Служба удалена. Обновление командой scripts/update.sh работает как раньше."
}

case "$CMD" in
  run) cmd_run ;;
  check) ensure_channel; do_check && ok "Проверка выполнена: $CH/remote.json" || { warn "Проверка завершилась с ошибкой (нет доступа к GitHub?) — см. remote.json"; exit 1; } ;;
  status) cmd_status ;;
  print-unit) unit_text ;;
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  *) die "Неизвестная команда: $CMD (run | check | status | print-unit | install | uninstall)" ;;
esac
