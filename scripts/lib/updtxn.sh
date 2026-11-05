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

upd_app_start_any() { # схема БД уже новая: прежний код поверх неё нельзя, поднимаем то, что соответствует текущему коду
  dc up -d --no-build "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1 || dc start "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1
  local UPD_SERVICES=("${UPD_APP_SERVICES[@]}")
  if upd_wait_healthy "${WAIT:-180}"; then upd_state_set app_stopped 0; upd_action "приложение запущено снова на текущей схеме БД"; return 0; fi
  upd_action "приложение на текущей схеме БД не стало работоспособным: ${UPD_UNHEALTHY[*]:-?}"; return 1
}

upd_app_start_old() { # запустить прежние (остановленные) контейнеры приложения
  dc start "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1 || dc up -d --no-build "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1
  local UPD_SERVICES=("${UPD_APP_SERVICES[@]}")
  if upd_wait_healthy "${WAIT:-180}"; then upd_state_set app_stopped 0; upd_action "прежние контейнеры приложения запущены снова"; return 0; fi
  upd_action "прежние контейнеры приложения не стали работоспособными: ${UPD_UNHEALTHY[*]:-?}"; return 1
}

_pgq() { dc exec -T postgres psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -tAc "$1"; }

# Восстановление БД без риска для рабочей базы: копия разворачивается в отдельную базу, проверяется, и только затем базы меняются именами. Рабочая база до этого момента не тронута,
# а после него остаётся под именем DB_KEPT до приёмки. Приложение должно быть остановлено. Код: 0 — переключено; 1 — ничего не изменилось.
_db_count() { dc exec -T postgres psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -tAc "$2"; }
db_restore_verify() { # db_restore_verify БАЗА ДАМП → пусто, код 0; иначе причина на stdout
  local db="$1" bk="$2" toc tables fks rev
  toc="$(dc exec -T postgres pg_restore --list < "$bk")" || { echo "оглавление дампа не читается"; return 1; }
  tables="$(_db_count "$db" "SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")" || { echo "база не отвечает"; return 1; }
  [ "$tables" = "$(printf '%s\n' "$toc" | grep -c ' TABLE public ')" ] || { echo "таблиц $tables, в дампе $(printf '%s\n' "$toc" | grep -c ' TABLE public ')"; return 1; }
  fks="$(_db_count "$db" "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace WHERE n.nspname = 'public' AND c.contype = 'f'")"
  [ "$fks" = "$(printf '%s\n' "$toc" | grep -c ' FK CONSTRAINT public ')" ] || { echo "внешних ключей $fks, в дампе $(printf '%s\n' "$toc" | grep -c ' FK CONSTRAINT public ')"; return 1; }
  rev="$(_db_count "$db" "SELECT version_num FROM alembic_version")" || { echo "нет отметки версии схемы"; return 1; }
  [ -n "$rev" ] || { echo "пустая отметка версии схемы"; return 1; }
}
db_restore_swap() { # db_restore_swap ДАМП ОТМЕТКА — DB_KEPT: имя отложенной рабочей базы, DB_RESTORE_ERROR: причина отказа
  local bk="$1" db="$POSTGRES_DB" side="${POSTGRES_DB}_restoring" why
  DB_KEPT=""; DB_RESTORE_ERROR=""
  _pgq "DROP DATABASE IF EXISTS \"$side\" WITH (FORCE)" >/dev/null && _pgq "CREATE DATABASE \"$side\" OWNER \"$POSTGRES_USER\"" >/dev/null || { DB_RESTORE_ERROR="не удалось создать базу $side"; return 1; }
  if ! dc exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$side" --no-owner --exit-on-error < "$bk"; then
    _pgq "DROP DATABASE IF EXISTS \"$side\" WITH (FORCE)" >/dev/null 2>&1; DB_RESTORE_ERROR="pg_restore завершился с ошибкой: дамп повреждён или места недостаточно"; return 1
  fi
  if ! why="$(db_restore_verify "$side" "$bk")"; then
    _pgq "DROP DATABASE IF EXISTS \"$side\" WITH (FORCE)" >/dev/null 2>&1; DB_RESTORE_ERROR="проверка восстановленной базы: $why"; return 1
  fi
  local keep; keep="${db}_$2_$(date +%Y%m%d%H%M%S)"
  _pgq "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '$db' AND pid <> pg_backend_pid()" >/dev/null 2>&1
  _pgq "ALTER DATABASE \"$db\" RENAME TO \"$keep\"" >/dev/null || { _pgq "DROP DATABASE IF EXISTS \"$side\" WITH (FORCE)" >/dev/null 2>&1; DB_RESTORE_ERROR="не удалось отложить рабочую базу"; return 1; }
  if ! _pgq "ALTER DATABASE \"$side\" RENAME TO \"$db\"" >/dev/null; then
    _pgq "ALTER DATABASE \"$keep\" RENAME TO \"$db\"" >/dev/null; DB_RESTORE_ERROR="не удалось переключить базу"; return 1
  fi
  DB_KEPT="$keep"
}
db_restore_back() { # вернуть отложенную базу на место, восстановленную убрать: приёмка не пройдена
  local bad; bad="${POSTGRES_DB}_rejected_$(date +%Y%m%d%H%M%S)"
  [ -n "${DB_KEPT:-}" ] || return 1
  _pgq "SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity WHERE datname = '$POSTGRES_DB' AND pid <> pg_backend_pid()" >/dev/null 2>&1
  _pgq "ALTER DATABASE \"$POSTGRES_DB\" RENAME TO \"$bad\"" >/dev/null && _pgq "ALTER DATABASE \"$DB_KEPT\" RENAME TO \"$POSTGRES_DB\"" >/dev/null \
    && { _pgq "DROP DATABASE IF EXISTS \"$bad\" WITH (FORCE)" >/dev/null 2>&1; DB_KEPT=""; }
}

# scripts/restore.sh. Логика здесь, чтобы её можно было проверить на настоящем PostgreSQL без Docker Compose (tests/scripts/part_restore.sh).
restore_preflight() { # restore_preflight ДАМП: места хватает, дамп читается
  local need free
  RESTORE_ERROR=""
  dc exec -T postgres pg_restore --list < "$1" >/dev/null 2>&1 || { RESTORE_ERROR="Дамп не читается: файл повреждён или это не дамп backup.sh"; return 1; }
  need="$(_pgq "SELECT pg_database_size(current_database())" 2>/dev/null)"
  free="$(df -B1 --output=avail "${DATA_ROOT:-/}/postgres" 2>/dev/null | tail -1 | tr -d ' ')"
  if [ -n "$need" ] && [ -n "$free" ] && [ "$free" -lt "$need" ]; then RESTORE_ERROR="На диске базы данных свободно меньше ($free байт), чем занимает база ($need байт): копия не поместится рядом"; return 1; fi
}
restore_from_dump() { # restore_from_dump ДАМП: приложение остановлено. Код 0 — приложение работает на восстановленной базе; иначе оно поднято на прежней, RESTORE_ERROR — причина
  local bk="$1" before after UPD_SERVICES=(backend asr web)
  RESTORE_ERROR=""
  before="$(_db_count "$POSTGRES_DB" "SELECT version_num FROM alembic_version" 2>/dev/null)"
  info "Копия разворачивается в отдельную базу…"
  db_restore_swap "$bk" before_restore || { RESTORE_ERROR="$DB_RESTORE_ERROR. Рабочая база не изменена."; dc up -d --no-build backend asr web >/dev/null 2>&1; return 1; }
  after="$(_db_count "$POSTGRES_DB" "SELECT version_num FROM alembic_version" 2>/dev/null)"
  info "Запуск сервисов на восстановленной базе…"
  dc up -d --no-build backend asr web >/dev/null 2>&1
  upd_wait_healthy "${WAIT:-180}" && return 0
  if [ "$before" != "$after" ]; then
    warn "Схема копии ($after) отличается от прежней ($before): приложение этой версии может не работать на ней. Нужный код: scripts/rollback.sh или scripts/update.sh. Прежняя база: $DB_KEPT"
    return 0
  fi
  warn "Приложение не заработало на восстановленной базе: возвращаю прежнюю"
  dc stop web backend asr >/dev/null 2>&1
  db_restore_back || { RESTORE_ERROR="Вернуть прежнюю базу не удалось, она сохранена как $DB_KEPT"; return 1; }
  dc up -d --no-build backend asr web >/dev/null 2>&1
  RESTORE_ERROR="Приложение не заработало на восстановленной базе. Прежняя база возвращена на место."
  return 1
}
restore_files() { # restore_files АРХИВ: распаковка рядом, замена каталогов только после полной распаковки. Прежнее откладывается с отметкой времени, удаляет его администратор
  local arc="$1" tmp d ts
  RESTORE_ERROR=""; ts="$(date +%Y%m%d%H%M%S)"
  tmp="$(mktemp -d "$DATA_ROOT/.restore.XXXXXX")" || { RESTORE_ERROR="Не удалось создать временный каталог в $DATA_ROOT"; return 1; }
  if ! tar --no-same-owner -xzf "$arc" -C "$tmp"; then rm -rf -- "${tmp:?}"; RESTORE_ERROR="Архив файлов не распакован: он повреждён или не хватает места"; return 1; fi
  for d in "$tmp"/*; do
    [ -d "$d" ] || continue
    d="${d##*/}"
    case "$d" in recordings|exports|chat-files|avatars|branding|ca|tls) ;; *) continue ;; esac
    [ ! -e "$DATA_ROOT/$d" ] || mv "$DATA_ROOT/$d" "$DATA_ROOT/$d.before-restore-$ts" || { RESTORE_ERROR="Не удалось отложить каталог $d"; rm -rf -- "${tmp:?}"; return 1; }
    mv "$tmp/$d" "$DATA_ROOT/$d" || { RESTORE_ERROR="Не удалось поставить каталог $d на место"; rm -rf -- "${tmp:?}"; return 1; }
  done
  rm -rf -- "${tmp:?}"
  upd_ensure_data_dirs >/dev/null 2>&1 || true
}

# Точное возвращение БД к копии при неудачном обновлении (та же безопасная замена): если не удалось, база остаётся как была после миграции.
upd_db_restore_exact() { # upd_db_restore_exact ДАМП
  local bk="$1"
  [ -s "$bk" ] || { warn "Резервная копия $bk не найдена — восстановление БД невозможно"; return 1; }
  dc stop "${UPD_APP_SERVICES[@]}" >/dev/null 2>&1
  if db_restore_swap "$bk" failed; then
    upd_action "БД возвращена к резервной копии $(basename "$bk") (база с результатом неудачной миграции сохранена как $DB_KEPT)"
    ok "БД возвращена к состоянию до обновления; база после неудачной миграции сохранена как $DB_KEPT (удалить: после разбора, вручную)"
    return 0
  fi
  upd_action "восстановить БД из копии не удалось (${DB_RESTORE_ERROR}) — база оставлена в состоянии после миграции"
  warn "Восстановить БД из копии не удалось (${DB_RESTORE_ERROR}) — база оставлена как была после миграции"
  return 1
}

# Безопасное самоисправление после неудачной ПЕРВОЙ проверки. Только обратимые действия над объектами этого проекта: права на каталоги данных и запуск
# сервисов проекта без пересборки. Прокси, сертификаты, сетевые правила, .env и настройки безопасности не затрагиваются. Каждое действие — в журнал отчёта.
# Затем пауза на прогрев (временные состояния: модель ASR ещё грузится, сервис только что перезапущен). Проверку повторяет вызывающий.
upd_self_heal() {
  local s bad=()
  REPAIR_DETAIL=""
  if repair_detect_data_dirs 2>/dev/null; then
    info "Права на каталоги данных неверны (${REPAIR_DETAIL:-?}) — исправляю"
    if repair_apply_data_dirs && ! repair_detect_data_dirs 2>/dev/null; then upd_action "исправлены права на каталоги данных"
    else upd_action "права на каталоги данных исправить не удалось (нужен запуск от root: «Исправить автоматически» в разделе «Обзор»)"; fi
  fi
  for s in "${UPD_SERVICES[@]}"; do [ "$(upd_svc_state "$s")" = healthy ] || bad+=("$s"); done
  if [ "${#bad[@]}" -gt 0 ]; then
    info "Не готовы сервисы: ${bad[*]} — запускаю без пересборки"
    dc up -d --no-build "${bad[@]}" >/dev/null 2>&1
    if upd_wait_healthy "${UPD_HEAL_WAIT:-90}"; then upd_action "запущены не готовые сервисы: ${bad[*]}"; else upd_action "сервисы не стали готовы: ${UPD_UNHEALTHY[*]:-${bad[*]}}"; fi
  fi
  sleep "${UPD_SETTLE:-10}"
}

# Повторная проверка после ЗАВЕРШИВШЕГОСЯ ошибкой обновления прошла: состояние, история и отчёт приводятся в соответствие. Запись о первой попытке
# в истории остаётся (она достоверна), добавляется новая запись о результате исправления; отчёт перезаписывается итогом «успешно».
upd_recovered() { # ПОЯСНЕНИЕ_В_ЖУРНАЛ ИНТЕГРАЦИИ_СТАТУС ПРОБЛЕМЫ
  upd_state_set health_status ok; upd_state_set integration_status "${2:-ok}"; upd_state_set integration_issues "${3:-}"; upd_state_set health_reason ""
  upd_state_set result ok; upd_state_set completed_at "$(date -Is)"
  upd_action "$1"
  OLD="$(upd_state_get previous_git_commit)"; TARGET="$(upd_state_get target_git_commit)"
  upd_history_append ok "повторная проверка пройдена после ошибки первой"
  upd_report_write "успешно" "" "$([ "${2:-ok}" = fail ] && echo "Требует внимания администратора: ${3:-см. проверку}")"
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
  local result="$1" cause="${2:-}" advice="${3:-}" dir="${DATA_ROOT}/updater" txt json s st b running oldv newv tmp
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
  } 2>/dev/null | { tmp="$(mktemp "$dir/.r.XXXXXXXX")" && mask_stream > "$tmp" && chmod 644 "$tmp" && mv -f "$tmp" "$txt"; }   # случайное имя: каталог открыт на запись всем
  tmp="$(mktemp "$dir/.r.XXXXXXXX")" || return 0
  python3 - "$txt" "$tmp" "$result" <<'PY' 2>/dev/null && chmod 644 "$tmp" && mv -f "$tmp" "$json" || rm -f "$tmp"
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
}
