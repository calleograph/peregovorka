# shellcheck shell=bash
# Транзакционная часть обновления и единый отчёт.
#  * Миграции выполняются при ОСТАНОВЛЕННОМ приложении сразу после резервной копии БД: после копии в базу никто не пишет, поэтому при неудаче
#    база возвращается к копии ТОЧНО и без потери данных — текущая база не удаляется, а переименовывается (<имя>_failed_<время>) для разбора.
#  * Каждое автоматическое действие (починка, повтор, выбор сети сборки, возврат кода, восстановление БД, откат) записывается в журнал действий;
#    по нему, состоянию и статусам сборки в конце (успех или остановка) пишется отчёт $DATA_ROOT/updater/report.txt (+ report.json) с маскированием секретов.
# Подключается из common.sh.

upd_actions_file() { echo "${DATA_ROOT}/state/update-actions.log"; }
upd_actions_reset() { mkdir -p "${DATA_ROOT}/state" 2>/dev/null; : > "$(upd_actions_file)" 2>/dev/null || true; }
upd_action() { # upd_action "что сделано автоматически" — в журнал действий отчёта
  printf '%s %s\n' "$(date +%H:%M:%S)" "$*" >> "$(upd_actions_file)" 2>/dev/null || true
}

UPD_APP_SERVICES=(web backend asr)

# Остановить приложение (не базы): после этого в БД никто не пишет. Контейнеры сохраняются — upd_app_start_old запускает их же (прежние образы).
upd_app_stop() {
  [ "$(upd_svc_state backend)" = missing ] && return 0
  info "Останавливаю приложение (web, backend, asr) на время резервной копии и миграции — после копии в базу никто не пишет"
  dc stop "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1 || return 1
  upd_state_set app_stopped 1; upd_action "приложение остановлено перед резервной копией БД и миграцией"
}

upd_app_start_old() { # запустить прежние (остановленные) контейнеры приложения
  dc start "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1 || dc up -d --no-build "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1
  local UPD_SERVICES=("${UPD_APP_SERVICES[@]}")
  if upd_wait_healthy "${WAIT:-180}"; then upd_state_set app_stopped 0; upd_action "прежние контейнеры приложения запущены снова"; return 0; fi
  upd_action "прежние контейнеры приложения не стали работоспособными: ${UPD_UNHEALTHY[*]:-?}"; return 1
}

_pgq() { dc exec -T postgres psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -tAc "$1"; }

# Точное возвращение БД к копии: текущая база переименовывается (сохраняется), создаётся новая, в неё восстанавливается копия.
# Если восстановление не удалось — новая база удаляется, прежняя получает своё имя обратно (состояние как до попытки).
upd_db_restore_exact() { # upd_db_restore_exact ДАМП
  local bk="$1" db="$POSTGRES_DB" keep
  [ -s "$bk" ] || { warn "Резервная копия $bk не найдена — восстановление БД невозможно"; return 1; }
  keep="${db}_failed_$(date +%Y%m%d%H%M%S)"
  dc stop "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1
  _pgq "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid()" >/dev/null 2>&1
  _pgq "ALTER DATABASE \"$db\" RENAME TO \"$keep\"" >/dev/null || { warn "Не удалось отложить текущую базу (переименование) — восстановление не начато"; return 1; }
  if _pgq "CREATE DATABASE \"$db\" OWNER \"$POSTGRES_USER\"" >/dev/null \
     && dc exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$db" --no-owner --exit-on-error < "$bk"; then
    upd_action "БД возвращена к резервной копии $(basename "$bk") (база с результатом неудачной миграции сохранена как $keep)"
    ok "БД возвращена к состоянию до обновления; база после неудачной миграции сохранена как $keep (удалить: после разбора, вручную)"
    return 0
  fi
  _pgq "DROP DATABASE IF EXISTS \"$db\"" >/dev/null 2>&1
  _pgq "ALTER DATABASE \"$keep\" RENAME TO \"$db\"" >/dev/null 2>&1
  upd_action "восстановить БД из копии не удалось — база оставлена в состоянии после миграции"
  warn "Восстановить БД из копии не удалось — база оставлена как была после миграции"
  return 1
}

# ------------------------------------------------------------------------------------------- отчёт
_rep_running_version() { # версия и commit, которые сообщает работающий backend
  dc exec -T backend python -c "
import json,urllib.request
try:
    d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/version',timeout=5)); print(d.get('version','?'),(d.get('commit') or '?')[:12])
except Exception:
    print('нет ответа')" 2>/dev/null | tr -d '\r' | tail -1
}

# upd_report_write ИТОГ ПРИЧИНА РЕКОМЕНДАЦИЯ — пишет report.txt и report.json в каталог обмена с веб-интерфейсом (всё маскируется)
upd_report_write() {
  local result="$1" cause="${2:-}" advice="${3:-}" dir="${DATA_ROOT}/updater" txt json s st b running oldv newv
  mkdir -p "$dir" 2>/dev/null || return 0
  txt="$dir/report.txt"; json="$dir/report.json"
  oldv="$(git -C "$REPO_ROOT" show "$(upd_state_get previous_git_commit 2>/dev/null || echo HEAD):VERSION" 2>/dev/null | tr -d '[:space:]')"
  newv="$(git -C "$REPO_ROOT" show "$(upd_state_get target_git_commit 2>/dev/null || echo HEAD):VERSION" 2>/dev/null | tr -d '[:space:]')"
  running="$(_rep_running_version)"
  {
    echo "=== Отчёт об обновлении Peregovorka ==="
    echo "Начато: $(upd_state_get started_at) · завершено: $(date -Is) · запуск: ${UPDATE_SOURCE:-терминал}${UPDATE_BY:+ ($UPDATE_BY)}"
    echo "Версия до обновления: ${oldv:-?} ($(upd_state_get previous_git_commit | cut -c1-12))"
    echo "Целевая версия:       ${newv:-?} ($(upd_state_get target_git_commit | cut -c1-12))"
    echo "Код на сервере сейчас: $(git -C "$REPO_ROOT" rev-parse --short=12 HEAD 2>/dev/null)"
    echo "Работает сейчас:      ${running:-нет ответа}"
    echo "Схема БД:             $(upd_state_get alembic_after || true)${ALEMBIC_HEAD:+ (ожидается ${ALEMBIC_HEAD})}"
    echo
    echo "ИТОГ: $result"
    [ -n "$cause" ] && echo "Причина: $cause"
    [ -n "$(upd_state_get stage)" ] && [ "$result" != "успешно" ] && echo "Этап остановки: $(upd_state_get stage)"
    echo
    echo "Автоматические действия:"
    if [ -s "$(upd_actions_file)" ]; then sed 's/^/  /' "$(upd_actions_file)"; else echo "  нет"; fi
    echo
    echo "Сборка компонентов:"
    for s in livekit backend asr web; do b="$(upd_state_get "build_$s")"; echo "  $s: ${b:-не выполнялась}"; done
    if [ -s "${DATA_ROOT}/state/netcheck.txt" ]; then echo; echo "Источники сборки (проверка из среды сборки):"; sed 's/^/  /' "${DATA_ROOT}/state/netcheck.txt"; fi
    echo
    echo "Состояние сервисов:"
    for s in postgres redis backend asr livekit web; do st="$(upd_svc_state "$s" 2>/dev/null)"; echo "  $s: ${st:-?}"; done
    [ -n "$advice" ] && { echo; echo "Что сделать: $advice"; }
    echo
    echo "Подробные журналы: ${DATA_ROOT}/updater/update.log (из веб-интерфейса), ${DATA_ROOT}/state/build-<компонент>.log, ${DATA_ROOT}/state/last-update.state"
  } 2>/dev/null | mask_stream > "$txt.tmp" && mv -f "$txt.tmp" "$txt" && chmod 644 "$txt" 2>/dev/null
  python3 - "$txt" "$json" "$result" <<'PY' 2>/dev/null || true
import json, sys
txt, out, result = sys.argv[1:4]
lines = open(txt, encoding="utf-8").read().splitlines()
def after(prefix):
    for l in lines:
        if l.startswith(prefix):
            return l[len(prefix):].strip()
    return ""
def block(title):
    res, on = [], False
    for l in lines:
        if l.startswith(title):
            on = True; continue
        if on:
            if not l.startswith("  "):
                break
            res.append(l.strip())
    return res
json.dump({"result": result, "ok": result == "успешно", "cause": after("Причина:"), "stage": after("Этап остановки:"), "advice": after("Что сделать:"),
           "running": after("Работает сейчас:"), "target": after("Целевая версия:"), "previous": after("Версия до обновления:"),
           "actions": block("Автоматические действия:"), "build": block("Сборка компонентов:"), "services": block("Состояние сервисов:"),
           "sources": block("Источники сборки"), "text": "\n".join(lines)}, open(out, "w", encoding="utf-8"), ensure_ascii=False)
PY
  chmod 644 "$json" 2>/dev/null || true
}
