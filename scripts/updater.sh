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
# $DATA_ROOT/updater, а этот скрипт, запущенный на хосте от обычного пользователя проекта, выполняет ТОЛЬКО штатный
# scripts/update.sh --yes (флаги — из разрешённого списка) и пишет его вывод в update.log, который показывает окно обновления.
# Произвольные команды из веб-интерфейса выполнить нельзя. Ничего не устанавливается без вашей команды `install`.
#
# Файлы обмена ($DATA_ROOT/updater): request.txt (из веб-интерфейса), status.json, remote.json, update.log (пишет этот скрипт).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

CMD="${1:-run}"; [ $# -gt 0 ] && shift
INTERVAL=3; CHECK_EVERY="${UPDATER_CHECK_EVERY:-1800}"; YES=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
load_env "$ENV_FILE"; validate_project_name
: "${DATA_ROOT:?DATA_ROOT не задан}"
CH="$DATA_ROOT/updater"
LOG="$CH/update.log"
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

atomic_write() { # atomic_write ФАЙЛ  (содержимое — со stdin)
  local f="$1" tmp="$1.$$.tmp"
  cat > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$f"
}

STATE="idle"; REQ_ID=""; STEP_NO=0; STEP_TOTAL=$STEPS_DEFAULT; STEP_NAME=""; STARTED=0; FINISHED=0; EXIT_CODE=null; RESULT=""; BY=""
write_status() {
  printf '{"ts":%s,"pid":%s,"state":"%s","request_id":"%s","step_no":%s,"step_total":%s,"step_name":"%s","started_at":%s,"finished_at":%s,"exit_code":%s,"result":"%s","by":"%s","project":"%s"}\n' \
    "$(date +%s)" "$$" "$STATE" "$REQ_ID" "$STEP_NO" "$STEP_TOTAL" "$(jesc "$STEP_NAME")" "$STARTED" "$FINISHED" "$EXIT_CODE" "$RESULT" "$(jesc "$BY")" "$COMPOSE_PROJECT_NAME" \
    | atomic_write "$CH/status.json"
}

# ------------------------------------------------------------------------------------------------ проверка репозитория
do_check() {
  local prev_state="$STATE" cur remote up ahead behind ff local_changes commits mig envchg first=1 ok=true err=""
  STATE="checking"; write_status
  if ! git -C "$REPO_ROOT" fetch --tags --prune origin >/dev/null 2>"$CH/.fetch.err"; then ok=false; err="$(head -c 300 "$CH/.fetch.err" | tr '\n' ' ')"; fi
  rm -f "$CH/.fetch.err"
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
    chg="$(changelog_since "$cltmp" "$cur_ver" | head -c 6000)"
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
do_update() { # do_update FORCE PULL
  local force="$1" pull="$2" line rc args=(--yes --env "$ENV_FILE")
  [ "$force" = 1 ] && args+=(--force-build)
  [ "$pull" = 1 ] && args+=(--pull)
  STATE="updating"; STARTED="$(date +%s)"; FINISHED=0; EXIT_CODE=null; RESULT=""; STEP_NO=0; STEP_NAME="запуск"; write_status
  : > "$LOG"; chmod 644 "$LOG"
  printf '=== Обновление запущено %s (запросил: %s) ===\n$ scripts/update.sh %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "${BY:-?}" "${args[*]}" >> "$LOG"
  (cd "$REPO_ROOT" && "$REPO_ROOT/scripts/update.sh" "${args[@]}" 2>&1) \
    | sed -u -e 's/\x1b\[[0-9;]*[A-Za-z]//g' -e 's/\r$//' \
    | while IFS= read -r line; do
        printf '%s\n' "$line" >> "$LOG"
        if [[ "$line" =~ ^\[([0-9]+)/([0-9]+)\][[:space:]]+(.*)$ ]]; then
          STEP_NO="${BASH_REMATCH[1]}"; STEP_TOTAL="${BASH_REMATCH[2]}"; STEP_NAME="${BASH_REMATCH[3]}"; write_status
        fi
      done
  rc="${PIPESTATUS[0]}"
  # цикл разбора вывода выполнялся в подоболочке: последний этап берём из журнала
  line="$(grep -E '^\[[0-9]+/[0-9]+\][[:space:]]' "$LOG" 2>/dev/null | tail -1)"
  if [[ "$line" =~ ^\[([0-9]+)/([0-9]+)\][[:space:]]+(.*)$ ]]; then STEP_NO="${BASH_REMATCH[1]}"; STEP_TOTAL="${BASH_REMATCH[2]}"; STEP_NAME="${BASH_REMATCH[3]}"; fi
  FINISHED="$(date +%s)"; EXIT_CODE="$rc"; STATE="idle"
  if [ "$rc" -eq 0 ]; then RESULT="ok"; else RESULT="failed"; fi
  printf '=== Обновление завершено %s: %s (код %s) ===\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$([ "$rc" -eq 0 ] && echo 'успешно' || echo 'С ОШИБКОЙ')" "$rc" >> "$LOG"
  write_status
  do_check || true
  return "$rc"
}

# ------------------------------------------------------------------------------------------------ запросы
req_get() { sed -n "s/^$1=//p" "$CH/request.txt" 2>/dev/null | head -1 | tr -d '\r'; }

handle_request() {
  local f="$CH/request.txt" action rid force pull by at now
  [ -f "$f" ] || return 0
  rid="$(req_get id)"; action="$(req_get action)"; force="$(req_get force_build)"; pull="$(req_get pull)"; by="$(req_get by)"; at="$(req_get at)"
  rm -f "$f"   # запрос выполняется один раз
  [[ "$rid" =~ ^[a-f0-9]{8,32}$ ]] || { warn "Запрос отклонён: некорректный id"; return 0; }
  [[ "$at" =~ ^[0-9]+$ ]] || { warn "Запрос отклонён: нет времени"; return 0; }
  now="$(date +%s)"
  if [ $((now - at)) -gt 600 ] || [ $((at - now)) -gt 120 ]; then warn "Запрос $rid устарел или из будущего — пропущен"; return 0; fi
  [[ "$by" =~ ^[A-Za-z0-9._-]{0,60}$ ]] || by="?"
  [[ "$force" =~ ^[01]$ ]] || force=0; [[ "$pull" =~ ^[01]$ ]] || pull=0
  REQ_ID="$rid"; BY="$by"
  case "$action" in
    check) info "Проверка по запросу ($by)"; do_check || true ;;
    update) info "Обновление по запросу ($by): пересборка=$force базовые образы=$pull"; do_update "$force" "$pull" || warn "Обновление завершилось с ошибкой — см. $LOG" ;;
    *) warn "Неизвестное действие запроса: $action" ;;
  esac
}

cmd_run() {
  ensure_channel
  if command -v flock >/dev/null 2>&1; then
    mkdir -p "$DATA_ROOT/state" 2>/dev/null || true
    exec 9>"$DATA_ROOT/state/updater.lock"
    flock -n 9 || die "Исполнитель обновлений уже запущен (проект $COMPOSE_PROJECT_NAME)."
  fi
  info "Исполнитель обновлений запущен: проект $COMPOSE_PROJECT_NAME, каталог обмена $CH. Остановка — Ctrl+C."
  trap 'STATE="stopped"; write_status; exit 0' INT TERM
  local last_check=0 now
  STATE="idle"; write_status
  while :; do
    now="$(date +%s)"
    if [ -f "$CH/request.txt" ]; then handle_request; last_check="$(date +%s)"
    elif [ $((now - last_check)) -ge "$CHECK_EVERY" ]; then do_check || true; last_check="$(date +%s)"
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
User=$(id -un)
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
  log "Будет создан файл /etc/systemd/system/$UNIT.service (запуск от пользователя $(id -un)):"; unit_text | sed 's/^/    /'
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
