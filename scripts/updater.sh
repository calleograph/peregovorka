#!/usr/bin/env bash
# updater.sh — «исполнитель» обновлений из веб-интерфейса (Администрирование → Обновления).
#   scripts/updater.sh run [--env FILE] [--interval СЕК]   # работает постоянно: выполняет запросы из веб-интерфейса и сверяется с GitHub
#   scripts/updater.sh check [--env FILE]                  # разовая проверка: что нового в репозитории (записывает remote.json)
#   scripts/updater.sh status [--env FILE]                 # работает ли исполнитель и что он делает
#   scripts/updater.sh verify [--env FILE] [--quiet]       # проверка РЕАЛЬНОГО состояния: служба, процесс (PID/UID), пульс, связь; пишет install-state.json
#   scripts/updater.sh print-unit [--env FILE]             # показать unit systemd (ничего не устанавливает)
#   scripts/updater.sh install [--env FILE] [--yes]        # установить/восстановить службу systemd (идемпотентно; заменяет посторонний процесс; нужен root)
#   scripts/updater.sh sync-unit [--env FILE]              # (вызывает сам помощник после обновления) привести службу к новой версии
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
# Как systemd завершил службу (переменные ExecStopPost) — запоминаем до того, как общие библиотеки и сам скрипт задействуют такие же имена.
SD_RESULT="${SERVICE_RESULT:-}"; SD_EXIT_CODE="${EXIT_CODE:-}"; SD_EXIT_STATUS="${EXIT_STATUS:-}"
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CMD="${1:-run}"; [ $# -gt 0 ] && shift
INTERVAL=3; CHECK_EVERY="${UPDATER_CHECK_EVERY:-1800}"; YES=0; QUIET=0; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --quiet|-q) QUIET=1; shift ;;
    --force) FORCE=1; shift ;;
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
UNIT_DIR="${UPDATER_UNIT_DIR:-/etc/systemd/system}"           # каталог unit-файлов (переопределяется только тестами)
UNIT_FILE="$UNIT_DIR/$UNIT.service"
LOCK_FILE="$DATA_ROOT/state/updater.lock"
HELPER_FILE="$CH/helper.json"                                 # пульс отдельным процессом: пишется, пока исполнитель жив, в том числе во время долгого обновления
INSTALL_FILE="$CH/install-state.json"                         # итог последней проверки установки (служба, процесс, пользователь, связь) — для веб-интерфейса
UNITSTATE_FILE="$CH/unit-state.json"                          # как завершилась служба в последний раз (пишет systemd через ExecStopPost)
EXPECT_UID="${UPDATER_EXPECT_UID:-0}"                         # от какого пользователя ДОЛЖЕН работать процесс (root); тесты без root задают свой
PULSE_EVERY="${UPDATER_PULSE_EVERY:-10}"
HB_MAX_AGE=40                                                 # то же значение использует веб-интерфейс (HEARTBEAT_MAX_AGE)
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
ACTION=""; REPAIR_ID=""; ST_UPDATE=""; ST_DEPLOY=""; ST_HEALTH=""; ST_INTEG=""; ST_ISSUES=""; PING_ID=""; PING_AT=0
write_status() {
  printf '{"ts":%s,"pid":%s,"uid":%s,"state":"%s","action":"%s","repair_id":"%s","request_id":"%s","step_no":%s,"step_total":%s,"step_name":"%s","started_at":%s,"finished_at":%s,"exit_code":%s,"result":"%s","update_status":"%s","deploy_status":"%s","health_status":"%s","integration_status":"%s","integration_issues":"%s","by":"%s","ping_id":"%s","ping_at":%s,"project":"%s"}\n' \
    "$(date +%s)" "$$" "$(id -u)" "$STATE" "$ACTION" "$REPAIR_ID" "$REQ_ID" "$STEP_NO" "$STEP_TOTAL" "$(jesc "$STEP_NAME")" "$STARTED" "$FINISHED" "$EXIT_CODE" "$RESULT" \
    "$ST_UPDATE" "$ST_DEPLOY" "$ST_HEALTH" "$ST_INTEG" "$(jesc "$ST_ISSUES")" "$(jesc "$BY")" "$PING_ID" "$PING_AT" "$COMPOSE_PROJECT_NAME" \
    | atomic_write "$CH/status.json"
}

# ------------------------------------------------------------------------------------------------ пульс и состояние установки
# status.json обновляется при смене этапа, а сборка образов длится минуты — по нему одному интерфейс принимал работающий помощник за «не установленный».
# Поэтому живость подтверждает отдельный лёгкий процесс: каждые PULSE_EVERY секунд пишет helper.json (PID, пользователь, запущен ли службой systemd), пока жив исполнитель.
# Описатель блокировки (9) в нём закрыт — иначе после гибели исполнителя пульс продолжал бы держать блокировку и новый экземпляр не запустился бы.
HELPER_STARTED="$(date +%s)"
helper_json() { # helper_json STOPPED(true|false)
  printf '{"ts":%s,"pid":%s,"uid":%s,"user":"%s","under_systemd":%s,"unit":"%s","started_at":%s,"version":"%s","script_sum":"%s","stopped":%s}\n' \
    "$(date +%s)" "$$" "$(id -u)" "$(jesc "$(id -un 2>/dev/null)")" "$([ -n "${INVOCATION_ID:-}" ] && echo true || echo false)" "$UNIT" "$HELPER_STARTED" \
    "$(jesc "$(tr -d '[:space:]' < "$REPO_ROOT/VERSION" 2>/dev/null)")" "${SELF_SUM:0:12}" "$1"
}
PULSE_PID=""
start_pulse() {
  local parent=$$
  helper_json false | atomic_write "$HELPER_FILE"
  (
    exec 9>&-
    trap - INT TERM
    while kill -0 "$parent" 2>/dev/null; do
      sleep "$PULSE_EVERY"
      helper_json false | atomic_write "$HELPER_FILE"
    done
  ) >/dev/null 2>&1 &
  PULSE_PID=$!
}
stop_pulse() { # stop_pulse [final] — final: пометить «остановлен» (штатная остановка); без аргумента — тихо (перед самоперезапуском)
  if [ -n "$PULSE_PID" ]; then kill "$PULSE_PID" 2>/dev/null; wait "$PULSE_PID" 2>/dev/null; PULSE_PID=""; fi
  [ "${1:-}" = final ] && helper_json true | atomic_write "$HELPER_FILE"
  return 0
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
  # служба systemd могла измениться в новой версии (пользователь, команда запуска): приводим её в порядок НОВЫМ кодом; перезапуск при необходимости — отложенный и вне
  # нашей группы процессов (сейчас мы сами работаем внутри службы), поэтому обновление не прерывается
  "$REPO_ROOT/scripts/updater.sh" sync-unit --env "$ENV_FILE" --quiet >> "$LOG" 2>&1 || warn "Не удалось сверить службу помощника с новой версией (см. $LOG): sudo ./scripts/updater.sh install --yes"
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
  # Каталог обмена открыт на запись (1777: контейнер backend пишет туда от uid 10001), поэтому на общем сервере положить файл мог бы и любой локальный пользователь.
  # Берём запрос только от своих: символьная ссылка отклоняется, владелец — root, сам исполнитель или пользователь контейнера backend.
  local owner; owner="$(stat -c %u "$f" 2>/dev/null || echo -1)"
  if [ -L "$f" ] || ! [[ "$owner" =~ ^(0|$(id -u)|${BACKEND_UID:-10001})$ ]]; then
    warn "Запрос отклонён: файл — символьная ссылка или создан посторонним пользователем (uid $owner)"; rm -f "$f"; return 0
  fi
  rid="$(req_get id)"; action="$(req_get action)"; force="$(req_get force_build)"; pull="$(req_get pull)"; by="$(req_get by)"; at="$(req_get at)"; repair="$(req_get repair)"
  rm -f "$f"   # запрос выполняется один раз (rm удаляет и symlink, не следуя за ним)
  [[ "$rid" =~ ^[a-f0-9]{8,32}$ ]] || { warn "Запрос отклонён: некорректный id"; return 0; }
  [[ "$at" =~ ^[0-9]+$ ]] || { warn "Запрос отклонён: нет времени"; return 0; }
  now="$(date +%s)"
  if [ $((now - at)) -gt 600 ] || [ $((at - now)) -gt 120 ]; then warn "Запрос $rid устарел или из будущего — пропущен"; return 0; fi
  [[ "$by" =~ ^[A-Za-z0-9._-]{0,60}$ ]] || by="?"
  [[ "$force" =~ ^[01]$ ]] || force=0; [[ "$pull" =~ ^[01]$ ]] || pull=0
  if [ "$action" = ping ]; then            # проверка связи: ничего не выполняет, лишь отмечает ответ в status.json (не трогая итог последнего обновления)
    PING_ID="$rid"; PING_AT="$(date +%s)"; write_status; return 0
  fi
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
  stop_pulse                      # пульс прежнего кода: новый экземпляр запустит свой (PID при exec тот же — старый пульс иначе жил бы вечно)
  exec 9>&-
  exec bash "$REPO_ROOT/scripts/updater.sh" run --env "$ENV_FILE" --interval "$INTERVAL"
}

cmd_run() {
  ensure_channel
  SELF_SUM="$(self_sum)"
  if command -v flock >/dev/null 2>&1; then
    mkdir -p "$DATA_ROOT/state" 2>/dev/null || true
    exec 9>"$DATA_ROOT/state/updater.lock"
    if ! flock -n 9; then
      local other; other="$(lock_holders | head -1)"; [ -n "$other" ] || other="$(hb_field pid)"
      die "Исполнитель обновлений уже запущен (проект $COMPOSE_PROJECT_NAME${other:+; процесс $other, пользователь $(ps -o user= -p "$other" 2>/dev/null | tr -d ' ')}). Чтобы заменить его службой: sudo ./scripts/updater.sh install --yes"
    fi
  fi
  info "Исполнитель обновлений запущен: проект $COMPOSE_PROJECT_NAME, каталог обмена $CH, процесс $$ (uid $(id -u)${INVOCATION_ID:+, служба systemd}). Остановка — Ctrl+C."
  trap 'STATE="stopped"; write_status; stop_pulse final; exit 0' INT TERM
  local last_check=0 last_scan=0 now
  start_pulse
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

hb_field() { # hb_field ПОЛЕ — значение поля из helper.json (пусто, если файла или поля нет)
  sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\).*/\1/p" "$HELPER_FILE" 2>/dev/null | head -1
}
st_field() { # st_field ПОЛЕ — то же для status.json
  sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\).*/\1/p" "$CH/status.json" 2>/dev/null | head -1
}
pid_alive() { # процесс существует и не «зомби» (завершённый, но ещё не принятый родителем)
  [ -r "/proc/$1/status" ] && [ "$(awk '/^State:/{print $2; exit}' "/proc/$1/status" 2>/dev/null)" != Z ]
}
proc_uid() { awk '/^Uid:/{print $2; exit}' "/proc/$1/status" 2>/dev/null; }
proc_user() { ps -o user= -p "$1" 2>/dev/null | tr -d ' '; }
unit_prop() { systemctl show "$UNIT.service" -p "$1" --value 2>/dev/null | head -1; }

lock_holders() { # PID исполнителей (`updater.sh run`), держащих блокировку этого проекта. Чужие процессы видны только от root.
  local fd p t real; real="$(readlink -f "$LOCK_FILE" 2>/dev/null || echo "$LOCK_FILE")"      # путь в /proc/…/fd — уже без символических ссылок
  for fd in /proc/[0-9]*/fd/*; do
    t="$(readlink "$fd" 2>/dev/null)"; [ "$t" = "$LOCK_FILE" ] || [ "$t" = "$real" ] || continue
    p="${fd#/proc/}"; p="${p%%/*}"
    case "$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)" in *updater.sh*\ run*) echo "$p" ;; esac
  done | sort -u
}

helper_busy() { # идёт обновление или исправление (по свежему состоянию)
  case "$(st_field state)" in updating|repairing) ;; *) return 1 ;; esac
  local ts; ts="$(st_field ts)"; [ -n "$ts" ] || return 1
  [ $(( $(date +%s) - ts )) -le 1800 ]
}

cmd_status() {
  ensure_channel
  [ -f "$CH/status.json" ] || [ -f "$HELPER_FILE" ] || { warn "Исполнитель не запускался (нет $CH/status.json). Запустите: sudo scripts/updater.sh install --yes"; exit 1; }
  local sts hts age_s age_h age
  sts="$(sed -n 's/.*"ts":\([0-9]*\).*/\1/p' "$CH/status.json" 2>/dev/null | head -1)"; hts="$(hb_field ts)"
  age_s=$(( $(date +%s) - ${sts:-0} )); age_h=$(( $(date +%s) - ${hts:-0} )); age=$(( age_s < age_h ? age_s : age_h ))
  [ -f "$CH/status.json" ] && cat "$CH/status.json"
  if [ "$age" -le "$HB_MAX_AGE" ]; then ok "Исполнитель работает (пульс $age с назад)"; else warn "Исполнитель НЕ отвечает (пульс $age с назад). Подробности: scripts/updater.sh verify"; exit 1; fi
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
ExecStopPost=-$REPO_ROOT/scripts/updater.sh note-stop --env $ENV_FILE
Restart=always
RestartSec=${UPDATER_RESTART_SEC:-10}

[Install]
WantedBy=multi-user.target
EOF
}

confirm() { # confirm ВОПРОС
  [ "$YES" -eq 1 ] && return 0
  [ -t 0 ] || die "Нужно подтверждение: запустите с --yes или в терминале."
  local a; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^[YyДд] ]]
}

# ------------------------------------------------------------------------------------------------ проверка РЕАЛЬНОГО состояния
# Недостаточно, что файл службы существует: проверяется, что служба включена и активна, процесс с PID службы существует, работает от ОЖИДАЕМОГО пользователя
# (фактический UID из /proc, а не строка User= в файле: процесс мог остаться от прежнего запуска под другим пользователем), пульс пишет именно этот процесс,
# а исполнитель отвечает на запрос связи. Итог — один из вердиктов (см. verdict_text); тот же вердикт читает веб-интерфейс из install-state.json.
do_ping() { # 0 — ответил; 1 — нет ответа; 2 — не проверялось (идёт обновление, есть другой запрос или нет прав положить запрос)
  helper_busy && return 2
  [ -e "$CH/request.txt" ] && return 2
  [ "$(id -u)" -eq 0 ] || [ "$(id -u)" = "$(hb_field uid)" ] || return 2           # запрос принимается от root, самого исполнителя или контейнера backend
  local rid i; rid="$(head -c 8 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  printf 'id=%s\naction=ping\nforce_build=0\npull=0\nby=verify\nat=%s\n' "$rid" "$(date +%s)" > "$CH/request.txt" 2>/dev/null || return 2
  for i in $(seq 1 $(( ${UPDATER_PING_WAIT:-20} * 2 ))); do
    [ "$(st_field ping_id)" = "$rid" ] && return 0
    sleep 0.5
  done
  rm -f "$CH/request.txt" 2>/dev/null
  return 1
}

diagnose_helper() { # diagnose_helper [ping] → V_*: факты и вердикт
  V_EXISTS=0; V_ENABLED=""; V_ACTIVE=""; V_SUB=""; V_RESULT=""; V_MAIN=0; V_RESTARTS=0; V_UNIT_USER=""; V_STALE=0
  V_PUID=""; V_PUSER=""; V_HB_PID=""; V_HB_AGE=""; V_HB_SYSD=""; V_PING=""; V_VERDICT=""; V_WHY=""; V_FIX=""
  local hb_fresh=0 pid want have rc
  [ -f "$UNIT_FILE" ] && V_EXISTS=1
  if command -v systemctl >/dev/null 2>&1; then
    V_ENABLED="$(systemctl is-enabled "$UNIT.service" 2>/dev/null)"; V_ACTIVE="$(systemctl is-active "$UNIT.service" 2>/dev/null)"
    V_SUB="$(unit_prop SubState)"; V_RESULT="$(unit_prop Result)"; V_MAIN="$(unit_prop MainPID)"; V_RESTARTS="$(unit_prop NRestarts)"
  fi
  [[ "$V_MAIN" =~ ^[0-9]+$ ]] || V_MAIN=0; [[ "$V_RESTARTS" =~ ^[0-9]+$ ]] || V_RESTARTS=0
  if [ "$V_EXISTS" = 1 ]; then
    V_UNIT_USER="$(sed -n 's/^User=//p' "$UNIT_FILE" | head -1)"
    want="$(unit_text)"; have="$(cat "$UNIT_FILE" 2>/dev/null)"; [ "$have" = "$want" ] || V_STALE=1
  fi
  local hts; hts="$(hb_field ts)"
  [ -n "$hts" ] && V_HB_AGE=$(( $(date +%s) - hts ))
  V_HB_PID="$(hb_field pid)"; V_HB_SYSD="$(hb_field under_systemd)"
  [ -n "$V_HB_AGE" ] && [ "$V_HB_AGE" -le "$HB_MAX_AGE" ] && [ "$(hb_field stopped)" != true ] && hb_fresh=1
  pid="$V_MAIN"; [ "$pid" -gt 0 ] || pid="${V_HB_PID:-0}"
  if [[ "$pid" =~ ^[0-9]+$ ]] && [ "$pid" -gt 0 ] && pid_alive "$pid"; then V_PUID="$(proc_uid "$pid")"; V_PUSER="$(proc_user "$pid")"; fi

  if [ "$V_EXISTS" != 1 ]; then
    if [ "$hb_fresh" = 1 ]; then
      V_VERDICT=manual_process; V_WHY="Помощник работает вручную (процесс $V_HB_PID, пользователь ${V_PUSER:-?}), служба systemd не установлена: после перезагрузки сервера он не запустится."
    else
      V_VERDICT=not_installed; V_WHY="Служба помощника не установлена: файла $UNIT_FILE нет."
    fi
    V_FIX="sudo ./scripts/updater.sh install --yes"
  elif [ "$V_ACTIVE" != active ]; then
    if [ "$V_ACTIVE" = failed ] || { [ -n "$V_RESULT" ] && [ "$V_RESULT" != success ]; } || [ "$V_SUB" = auto-restart ]; then
      V_VERDICT=failed; V_WHY="Служба установлена, но не работает: состояние «$V_ACTIVE/$V_SUB», результат «${V_RESULT:-?}», перезапусков: $V_RESTARTS. Подробности: journalctl -u $UNIT -n 30"
    elif [ "$hb_fresh" = 1 ]; then
      V_VERDICT=manual_process; V_WHY="Служба не запущена, но помощник работает отдельным процессом $V_HB_PID (пользователь ${V_PUSER:-?}) — не как служба."
    else
      V_VERDICT=installed_inactive; V_WHY="Служба установлена, но не запущена (состояние «${V_ACTIVE:-неизвестно}»)."
    fi
    V_FIX="sudo ./scripts/updater.sh install --yes"
  elif [ "$V_MAIN" = 0 ] || ! pid_alive "$V_MAIN"; then
    V_VERDICT=installed_inactive; V_WHY="Служба числится активной, но процесса нет (PID $V_MAIN)."; V_FIX="sudo ./scripts/updater.sh install --yes"
  elif [ "$V_PUID" != "$EXPECT_UID" ]; then
    V_VERDICT=wrong_user; V_WHY="Процесс $V_MAIN работает от пользователя ${V_PUSER:-?} (uid ${V_PUID:-?}), а должен — от uid $EXPECT_UID (в файле службы User=${V_UNIT_USER:-?}). Прав на каталоги и настройки системы у него нет."
    V_FIX="sudo ./scripts/updater.sh install --yes"
  elif [ "$hb_fresh" = 1 ] && [ -n "$V_HB_PID" ] && [ "$V_HB_PID" != "$V_MAIN" ]; then
    V_VERDICT=manual_process; V_WHY="Служба активна (PID $V_MAIN), но запросы обслуживает другой процесс $V_HB_PID (пользователь $(proc_user "$V_HB_PID"))."; V_FIX="sudo ./scripts/updater.sh install --yes"
  elif [ "$hb_fresh" != 1 ]; then
    V_VERDICT=unresponsive; V_WHY="Процесс $V_MAIN запущен, но пульс не обновляется ${V_HB_AGE:+уже $V_HB_AGE с}: помощник завис."; V_FIX="sudo systemctl restart $UNIT"
  else
    if [ "${1:-}" = ping ]; then do_ping; rc=$?; case "$rc" in 0) V_PING=ok ;; 1) V_PING=fail ;; *) V_PING=skipped ;; esac; fi
    if [ "$V_PING" = fail ]; then
      V_VERDICT=unresponsive; V_WHY="Процесс $V_MAIN запущен и пишет пульс, но на запрос связи не ответил за ${UPDATER_PING_WAIT:-20} с: основной цикл не работает."; V_FIX="sudo systemctl restart $UNIT"
    elif [ "$(stat -c %a "$CH" 2>/dev/null)" != 1777 ]; then
      V_VERDICT=permission_error; V_WHY="У каталога обмена $CH неверные права (нужно 1777): контейнер веб-приложения не сможет передать запрос."; V_FIX="sudo chmod 1777 $CH"
    elif [ "$V_STALE" = 1 ]; then
      V_VERDICT=stale_unit; V_WHY="Помощник работает, но файл службы отличается от актуального для этой версии."; V_FIX="sudo ./scripts/updater.sh install --yes"
    elif [ -n "$V_ENABLED" ] && [ "$V_ENABLED" != enabled ]; then
      V_VERDICT=not_enabled; V_WHY="Помощник работает, но автозапуск службы не включён (после перезагрузки он не запустится)."; V_FIX="sudo ./scripts/updater.sh install --yes"
    else
      V_VERDICT=ok; V_WHY="Помощник работает: служба активна, процесс $V_MAIN (пользователь ${V_PUSER:-?}, uid $V_PUID), пульс ${V_HB_AGE:-?} с назад${V_PING:+, связь: $V_PING}."
    fi
  fi
}

write_install_state() {
  printf '{"checked_at":%s,"verdict":"%s","why":"%s","fix":"%s","unit_exists":%s,"unit_user":"%s","unit_stale":%s,"enabled":"%s","active":"%s","substate":"%s","result":"%s","restarts":%s,"main_pid":%s,"proc_uid":"%s","proc_user":"%s","expected_uid":%s,"hb_pid":"%s","hb_age":"%s","hb_under_systemd":"%s","ping":"%s"}\n' \
    "$(date +%s)" "$V_VERDICT" "$(jesc "$V_WHY")" "$(jesc "$V_FIX")" "$([ "$V_EXISTS" = 1 ] && echo true || echo false)" "$(jesc "$V_UNIT_USER")" "$([ "$V_STALE" = 1 ] && echo true || echo false)" \
    "$(jesc "$V_ENABLED")" "$(jesc "$V_ACTIVE")" "$(jesc "$V_SUB")" "$(jesc "$V_RESULT")" "$V_RESTARTS" "$V_MAIN" "$(jesc "$V_PUID")" "$(jesc "$V_PUSER")" "$EXPECT_UID" \
    "$(jesc "$V_HB_PID")" "$(jesc "$V_HB_AGE")" "$(jesc "$V_HB_SYSD")" "$V_PING" | atomic_write "$INSTALL_FILE" 2>/dev/null || true
}

print_verdict() {
  case "$V_VERDICT" in
    ok) ok "$V_WHY" ;;
    stale_unit|not_enabled|manual_process) warn "$V_WHY${V_FIX:+ Исправление: $V_FIX}" ;;
    *) fail "$V_WHY${V_FIX:+ Исправление: $V_FIX}" ;;
  esac
}

cmd_verify() {
  command -v systemctl >/dev/null 2>&1 || warn "systemd не найден: проверяется только процесс помощника"
  ensure_channel
  diagnose_helper ping
  write_install_state
  [ "$QUIET" -eq 1 ] || print_verdict
  case "$V_VERDICT" in ok|stale_unit|not_enabled) exit 0 ;; *) exit 1 ;; esac
}

# ------------------------------------------------------------------------------------------------ установка (идемпотентная)
evict_foreign() { # завершает посторонние экземпляры исполнителя (не процесс службы): они держат блокировку, и служба не может стартовать. 10 — кого-то завершили
  local main p evicted=0
  main="$(unit_prop MainPID)"
  for p in $(lock_holders) "$(hb_field pid)"; do
    [[ "$p" =~ ^[0-9]+$ ]] && [ "$p" -gt 1 ] && [ "$p" != "$main" ] && [ "$p" != "$$" ] && pid_alive "$p" || continue
    case "$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)" in *updater.sh*\ run*) ;; *) continue ;; esac
    if helper_busy && [ "$FORCE" -ne 1 ] && [ "$(st_field pid)" = "$p" ]; then
      die "Сейчас выполняется обновление или исправление (процесс $p). Дождитесь окончания и повторите команду (или добавьте --force, если оно зависло)."
    fi
    warn "Найден посторонний экземпляр помощника: процесс $p, пользователь $(proc_user "$p") — он держит блокировку и мешает службе. Завершаю его."
    upd_as_root kill -TERM "$p" 2>/dev/null
    local i; for i in $(seq 1 40); do pid_alive "$p" || break; sleep 0.5; done
    pid_alive "$p" && upd_as_root kill -KILL "$p" 2>/dev/null
    evicted=1
  done
  [ "$evicted" -eq 1 ] && return 10
  return 0
}

wait_helper() { # ждём, пока служба поднимет исполнителя: свежий пульс именно её процесса
  local i m hts hp
  for i in $(seq 1 $(( ${UPDATER_START_WAIT:-45} * 2 ))); do
    m="$(unit_prop MainPID)"; hts="$(hb_field ts)"; hp="$(hb_field pid)"
    if [ -n "$hts" ] && [ $(( $(date +%s) - hts )) -le 15 ] && [ "$m" != 0 ] && [ "$hp" = "$m" ] && [ "$(hb_field stopped)" != true ]; then return 0; fi
    sleep 0.5
  done
  return 1
}

cmd_install() {
  command -v systemctl >/dev/null 2>&1 || die "systemd не найден. Запустите исполнитель вручную: nohup $REPO_ROOT/scripts/updater.sh run &"
  ensure_channel
  local changed=0 evicted=0 want have
  want="$(unit_text)"; have="$(cat "$UNIT_FILE" 2>/dev/null || true)"
  if [ "$QUIET" -ne 1 ]; then
    log "Служба $UNIT (работает от root, выполняет только фиксированный набор действий — см. заголовок scripts/updater.sh):"; printf '%s\n' "$want" | sed 's/^/    /'
  fi
  confirm "Установить или проверить службу $UNIT (потребуется sudo)?" || die "Отменено."
  if [ "$have" != "$want" ]; then
    printf '%s\n' "$want" | upd_as_root tee "$UNIT_FILE" >/dev/null || die "Не удалось записать $UNIT_FILE"
    upd_as_root systemctl daemon-reload || die "systemctl daemon-reload не выполнен"
    changed=1; [ "$QUIET" -eq 1 ] || info "Файл службы ${have:+обновлён}${have:-создан}"
  fi
  upd_as_root systemctl enable "$UNIT.service" >/dev/null 2>&1 || die "Не удалось включить автозапуск службы $UNIT"
  evict_foreign; [ $? -eq 10 ] && evicted=1
  diagnose_helper
  if [ "$changed" -eq 0 ] && [ "$evicted" -eq 0 ] && [ "$V_VERDICT" = ok ]; then
    diagnose_helper ping; write_install_state
    [ "$QUIET" -eq 1 ] || ok "Помощник уже установлен и работает — ничего менять не нужно. $V_WHY"
    [ "$V_VERDICT" = ok ] && return 0
  fi
  if helper_busy && [ "$FORCE" -ne 1 ]; then
    write_install_state
    die "Идёт обновление или исправление: служба обновлена в файле, но перезапуск отложен. Повторите команду после его окончания."
  fi
  upd_as_root systemctl restart "$UNIT.service" || warn "systemctl restart вернул ошибку — проверяю состояние"
  wait_helper || warn "Помощник не подтвердил запуск за ${UPDATER_START_WAIT:-45} с"
  diagnose_helper ping; write_install_state
  print_verdict
  case "$V_VERDICT" in ok|stale_unit|not_enabled) [ "$QUIET" -eq 1 ] || ok "Теперь кнопки «Обновить» и «Исправить автоматически» в веб-интерфейсе доступны."; return 0 ;; esac
  exit 1
}

cmd_sync_unit() { # после обновления проекта: привести службу в соответствие с новой версией (вызывает сам помощник); перезапуск — отложенный, вне группы процессов службы
  command -v systemctl >/dev/null 2>&1 || return 0
  ensure_channel
  if [ -f "$UNIT_FILE" ]; then
    local want have; want="$(unit_text)"; have="$(cat "$UNIT_FILE" 2>/dev/null)"
    if [ "$have" != "$want" ]; then
      if [ "$(id -u)" -ne 0 ] && [ -z "${UPDATER_NO_SUDO:-}" ] && ! sudo -n true 2>/dev/null; then
        warn "Служба помощника устарела, но у процесса нет прав root, чтобы её обновить. Выполните на сервере: sudo ./scripts/updater.sh install --yes"
      else
        printf '%s\n' "$want" | upd_as_root tee "$UNIT_FILE" >/dev/null || die "Не удалось записать $UNIT_FILE"
        upd_as_root systemctl daemon-reload; upd_as_root systemctl enable "$UNIT.service" >/dev/null 2>&1
        info "Служба помощника обновлена под новую версию; перезапуск запланирован через несколько секунд"
        if command -v systemd-run >/dev/null 2>&1; then
          upd_as_root systemd-run --on-active="${UPDATER_RESTART_DELAY:-5}" --collect --quiet systemctl restart "$UNIT.service" || warn "Не удалось запланировать перезапуск: sudo systemctl restart $UNIT"
        else warn "systemd-run не найден: перезапустите вручную: sudo systemctl restart $UNIT"; fi
      fi
    fi
  fi
  diagnose_helper; write_install_state
}

cmd_note_stop() { # ExecStopPost службы: как именно она завершилась (для диагностики «установлена, но не запущена»)
  ensure_channel
  printf '{"ts":%s,"result":"%s","exit_code":"%s","exit_status":"%s","unit":"%s"}\n' "$(date +%s)" "$(jesc "$SD_RESULT")" "$(jesc "$SD_EXIT_CODE")" "$(jesc "$SD_EXIT_STATUS")" "$UNIT" | atomic_write "$UNITSTATE_FILE"
  return 0
}

cmd_uninstall() {
  confirm "Остановить и удалить службу $UNIT?" || die "Отменено."
  upd_as_root systemctl disable --now "$UNIT.service" 2>/dev/null || true
  upd_as_root rm -f "$UNIT_FILE" && upd_as_root systemctl daemon-reload
  rm -f "$HELPER_FILE" "$INSTALL_FILE" "$UNITSTATE_FILE" 2>/dev/null || true
  ok "Служба удалена. Обновление командой scripts/update.sh работает как раньше."
}

case "$CMD" in
  run) cmd_run ;;
  check) ensure_channel; do_check && ok "Проверка выполнена: $CH/remote.json" || { warn "Проверка завершилась с ошибкой (нет доступа к GitHub?) — см. remote.json"; exit 1; } ;;
  status) cmd_status ;;
  verify) cmd_verify ;;
  print-unit) unit_text ;;
  install) cmd_install ;;
  sync-unit) cmd_sync_unit ;;
  note-stop) cmd_note_stop ;;
  uninstall) cmd_uninstall ;;
  *) die "Неизвестная команда: $CMD (run | check | status | verify | print-unit | install | sync-unit | uninstall)" ;;
esac
