#!/usr/bin/env bash
# repairlib.sh — «Исправить автоматически»: обнаружение известных проблем установки и их исправление СТРОГО из белого списка.
#
# Исполнитель обновлений (scripts/updater.sh, служба systemd от root) вызывает repair_scan → пишет $DATA_ROOT/updater/repairs.json
# (его показывает веб-интерфейс), а по запросу «Исправить» — repair_apply ID. ID проверяется по REPAIR_IDS: произвольная команда из
# веб-интерфейса выполниться не может; каждое исправление — фиксированная последовательность действий над объектами ЭТОГО проекта.
#
# Каждая проблема описывается тремя фразами для администратора: что случилось · что это значит · что будет сделано. Команд Linux в них нет.
# Только функции, сами ничего не выполняют. Зависит от common.sh (load_env выполнен), prereqlib.sh.

if [ -n "${_VM_REPAIRLIB_LOADED:-}" ]; then return 0; fi
_VM_REPAIRLIB_LOADED=1

REPAIR_IDS=(data_dirs nginx_site sysctl prereq_missing image_commit migrations reverify)
BACKEND_UID=10001

repair_id_valid() { local i; for i in "${REPAIR_IDS[@]}"; do [ "$i" = "$1" ] && return 0; done; return 1; }

# ---- паспорт проблемы: repair_text ID → TITLE|MEANING|FIX (значение подставляется при обнаружении, см. REPAIR_DETAIL)
REPAIR_DETAIL=""
repair_text() {
  case "$1" in
    data_dirs) echo "Неверные права на каталоги данных|Сервису не хватает прав на запись в каталоги данных (сертификаты, вложения чата, записи, обмен с помощником обновлений)${REPAIR_DETAIL:+: $REPAIR_DETAIL}. Из-за этого могут не сохраняться сертификаты и вложения.|Создать недостающие каталоги и выдать сервису нужного владельца" ;;
    nginx_site) echo "Настройка веб-сервера устарела|${REPAIR_DETAIL:-Конфигурация веб-сервера, созданная Peregovorka, отличается от актуальной.} Возможны обрывы связи в комнатах или недоступность сайта.|Обновить конфигурацию Peregovorka на веб-сервере (с проверкой и автоматическим откатом при ошибке)" ;;
    sysctl) echo "Параметры сети сервера ниже рекомендованных|${REPAIR_DETAIL:-Системные параметры для звука и видео ниже рекомендованных.} Возможны потери звука в комнатах при нагрузке.|Применить рекомендованные параметры (отдельный файл настроек Peregovorka, без чужих файлов)" ;;
    prereq_missing) echo "На сервере не хватает нужных программ|${REPAIR_DETAIL:-Не установлены программы, нужные Peregovorka.} Без них обновление и сборка могут не работать.|Установить недостающее из официальных репозиториев" ;;
    image_commit) echo "Версия работающих компонентов не определена|${REPAIR_DETAIL:-Компоненты собраны без сведений о версии.} Непонятно, какой именно код работает.|Пересобрать компоненты и перезапустить" ;;
    migrations) echo "База данных не обновлена до текущей версии|${REPAIR_DETAIL:-Структура базы данных отстаёт от программы.} Часть функций может не работать.|Применить обновление структуры базы (с резервной копией)" ;;
    reverify) echo "Нужна повторная проверка работоспособности|${REPAIR_DETAIL:-После последнего изменения полная проверка не выполнялась.} Состояние системы неизвестно.|Запустить полную проверку" ;;
  esac
}

_repair_dir_owner() { stat -c '%u' "$1" 2>/dev/null || echo x; }
_repair_dir_mode() { stat -c '%a' "$1" 2>/dev/null || echo x; }

# repair_detect_ID → код 0, если проблема есть (и заполняет REPAIR_DETAIL)
repair_detect_data_dirs() {
  local d bad=() base="${DATA_ROOT:-}"
  [ -n "$base" ] || return 1
  for d in recordings exports ca chat-files; do
    if [ ! -d "$base/$d" ]; then bad+=("$d: нет каталога")
    elif [ "$(_repair_dir_owner "$base/$d")" != "$BACKEND_UID" ]; then bad+=("$d: чужой владелец"); fi
  done
  for d in updater state; do [ -d "$base/$d" ] || bad+=("$d: нет каталога"); done
  [ -d "$base/updater" ] && [ "$(_repair_dir_mode "$base/updater")" != 1777 ] && bad+=("updater: неверный режим")
  # главный признак — может ли ПРОЦЕСС сервиса (uid 10001) записать в каталог; владелец на хосте этого не гарантирует (ACL, режим, read-only mount)
  local w=""
  if repair_docker_up; then w="$( { writable_probe backend "${WRITABLE_BACKEND_DIRS[@]}"; writable_probe asr "${WRITABLE_ASR_DIRS[@]}"; } 2>/dev/null | tr '
' ' ')"; fi
  [ -n "${w// /}" ] && bad+=("запись невозможна: ${w% }")
  [ "${#bad[@]}" -eq 0 ] && return 1
  REPAIR_DETAIL="$(IFS=,; echo "${bad[*]}")"
  return 0
}

repair_detect_nginx_site() {
  [ "${NGINX_MANAGE:-no}" = yes ] || return 1
  command -v nginx >/dev/null 2>&1 || return 1
  local site="${NGINX_SITES_AVAILABLE:-}/${NGINX_SITE_NAME:-}"
  [ -n "${NGINX_SITE_NAME:-}" ] || return 1
  if [ ! -e "$site" ]; then REPAIR_DETAIL="Конфигурация сайта Peregovorka отсутствует на веб-сервере."; return 0; fi
  grep -qx "# managed-by: peregovorka:${COMPOSE_PROJECT_NAME}" "$site" 2>/dev/null || return 1     # чужой файл не наш и не трогаем
  [ "$(cat "$site" 2>/dev/null)" = "$(render_nginx_site)" ] && return 1
  REPAIR_DETAIL="Конфигурация сайта Peregovorka на веб-сервере создана более старой версией."
  return 0
}

repair_detect_sysctl() {
  [ "${INSTALL_PROFILE:-}" = standalone ] || return 1      # на общем сервере параметры ядра меняет только человек
  [ "$(uname -s)" = Linux ] || return 1
  local kv=""; _rk() { kv="$kv $1"; }
  kernel_tuning_check : _rk 2>/dev/null
  [ -n "$kv" ] || return 1
  REPAIR_DETAIL="Размеры сетевых буферов или режим памяти ниже рекомендаций."
  return 0
}

repair_detect_prereq_missing() {
  [ "${INSTALL_PROFILE:-}" = standalone ] || return 1
  declare -F prereq_verify >/dev/null 2>&1 || return 1
  local out; out="$(prereq_verify standalone 2>/dev/null)" && return 1
  local names=() l
  while IFS= read -r l; do
    case "$l" in
      "Docker: не хватает buildx") names+=("Docker Buildx") ;;
      "Docker: не хватает compose") names+=("Docker Compose") ;;
      "Docker: не хватает engine") names+=("Docker") ;;
      "нет команды: "*) names+=("${l#нет команды: }") ;;
    esac
  done <<<"$out"
  REPAIR_DETAIL="Не хватает: $(IFS=,; echo "${names[*]}" | sed 's/,/, /g')."
  return 0
}

# Запущены ли контейнеры проекта: один быстрый запрос вместо десятков `docker compose …` (на сервере без контейнеров — мгновенный выход из проверок).
repair_docker_up() {
  command -v docker >/dev/null 2>&1 || return 1
  [ -n "$(timeout 8 docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-}" 2>/dev/null | head -1)" ]
}

repair_detect_image_commit() {
  repair_docker_up || return 1
  local s cid raw
  for s in backend asr web; do
    cid="$(dc ps -q "$s" 2>/dev/null | head -1)"; [ -n "$cid" ] || continue
    raw="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$cid" 2>/dev/null | sed -n 's/^APP_GIT_COMMIT=//p' | head -1)"
    if [ "$raw" = unknown ]; then REPAIR_DETAIL="Компонент «$s» собран без сведений о версии."; return 0; fi
  done
  return 1
}

repair_detect_migrations() {
  repair_docker_up || return 1
  [ -n "$(dc ps -q backend 2>/dev/null | head -1)" ] || return 1
  alembic_verify exec >/dev/null 2>&1 && return 1
  [ -n "${ALEMBIC_CUR:-}" ] || [ -n "${ALEMBIC_HEAD:-}" ] || return 1       # не удалось определить — это не «нужна миграция»
  REPAIR_DETAIL="База данных на версии «${ALEMBIC_CUR:-?}», программа ожидает «${ALEMBIC_HEAD:-?}»."
  return 0
}

repair_verify_file() { echo "${DATA_ROOT}/state/last-verify.state"; }
# Запись итога проверки (вызывается из update.sh, verify и исправления «reverify»): ВЕРСИЯ_КОДА результат время
repair_verify_record() { # RESULT
  local f; f="$(repair_verify_file)"; mkdir -p "$(dirname "$f")" 2>/dev/null || return 0
  printf '%s %s %s\n' "$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)" "$1" "$(date +%s)" > "$f" 2>/dev/null || true
}
repair_detect_reverify() {
  local f head last; f="$(repair_verify_file)"
  head="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  if [ ! -s "$f" ]; then REPAIR_DETAIL="Полная проверка ещё ни разу не выполнялась."; return 0; fi
  last="$(cut -d' ' -f1 "$f")"
  [ "$last" = "$head" ] && return 1
  REPAIR_DETAIL="Последняя проверка была для другой версии программы."
  return 0
}

# ---- сканирование: заполняет REPAIR_FOUND (список ID); repair_write_json ФАЙЛ пишет repairs.json
REPAIR_FOUND=()
repair_scan() {
  local id; REPAIR_FOUND=(); REPAIR_DETAILS=()
  for id in "${REPAIR_IDS[@]}"; do
    REPAIR_DETAIL=""
    if "repair_detect_$id" 2>/dev/null; then REPAIR_FOUND+=("$id"); REPAIR_DETAILS+=("$(repair_text "$id")"); fi
  done
}

_rjesc() { printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037' | tr '\t\r\n' '   ' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
repair_json() { # печатает JSON последнего repair_scan
  local i first=1 id title meaning fix
  printf '{"checked_at":%s,"uid":%s,"profile":"%s","items":[' "$(date +%s)" "$(id -u)" "$(_rjesc "${INSTALL_PROFILE:-}")"
  for i in "${!REPAIR_FOUND[@]}"; do
    IFS='|' read -r title meaning fix <<<"${REPAIR_DETAILS[$i]}"
    [ "$first" = 1 ] || printf ','
    first=0
    printf '{"id":"%s","title":"%s","meaning":"%s","fix":"%s"}' "${REPAIR_FOUND[$i]}" "$(_rjesc "$title")" "$(_rjesc "$meaning")" "$(_rjesc "$fix")"
  done
  printf ']}\n'
}

# ---- исправления. Каждое идемпотентно, меняет только объекты проекта, печатает ход работы (попадает в журнал окна). Код 0 — исправлено.
repair_apply_data_dirs() {
  local base="$DATA_ROOT" d rc=0
  for d in postgres redis models/gigaam recordings exports backups state updater ca chat-files; do
    [ -d "$base/$d" ] || mkdir -p "$base/$d" || { fail "Не удалось создать $base/$d"; rc=1; }
  done
  for d in recordings exports ca chat-files; do
    chown "$BACKEND_UID:$BACKEND_UID" "$base/$d" || { fail "Не удалось назначить владельца $base/$d"; rc=1; }
  done
  chmod 1777 "$base/updater" || rc=1
  # содержимое тоже должно принадлежать сервису: каталог мог быть создан от root вместе с файлами внутри
  for d in ca chat-files; do chown -R "$BACKEND_UID:$BACKEND_UID" "$base/$d" 2>/dev/null || true; done
  [ "$rc" -eq 0 ] && ok "Каталоги данных приведены в порядок" || return 1
  # Перезапускаем ТОЛЬКО затронутые сервисы (не весь стек): backend перечитывает сертификаты и каталог; asr — только если ему нужна запись в recordings.
  if command -v docker >/dev/null 2>&1 && [ -n "$(dc ps -q backend 2>/dev/null | head -1)" ]; then
    dc restart backend >/dev/null 2>&1 && ok "Backend перезапущен, чтобы подхватить сертификаты и вложения"
    local UPD_SERVICES=(backend); upd_wait_healthy 120 || { fail "Backend не стал healthy после перезапуска"; return 1; }
    # повторная проверка: сертификаты и вход по домену (то, что ломалось)
    "$REPO_ROOT/scripts/smoke-test.sh" --env "$ENV_FILE" --no-public || info "Проверка после исправления: см. таблицу выше (интеграции настраиваются в браузере)"
  fi
  if repair_detect_data_dirs; then fail "Права всё ещё неверны: $REPAIR_DETAIL"; return 1; fi
  return 0
}

repair_apply_nginx_site() {
  if [ -e "$NGINX_SITES_AVAILABLE/$NGINX_SITE_NAME" ]; then upd_nginx_migrate
    repair_detect_nginx_site && { fail "Конфигурация веб-сервера не обновилась (проверка nginx отклонила её; прежняя оставлена)"; return 1; }
    return 0
  fi
  "$REPO_ROOT/scripts/install.sh" --profile "${INSTALL_PROFILE:-standalone}" --env "$ENV_FILE" --from nginx || return 1
}

repair_apply_sysctl() { "$REPO_ROOT/scripts/tune-kernel.sh" --env "$ENV_FILE" --apply --yes && ! repair_detect_sysctl; }

repair_apply_prereq_missing() { prereq_install_all standalone && prereq_verify standalone >/dev/null; }

repair_apply_image_commit() {
  export FORCE_BUILD=1
  host_version_info
  export IMAGE_TAG="$APP_GIT_COMMIT"
  [ "$IMAGE_TAG" != unknown ] || { fail "Не удалось определить версию кода (нет сведений git)"; return 1; }
  build_images || return 1
  dc up -d --no-build || return 1
  upd_wait_healthy "${WAIT:-180}" || return 1
  repair_detect_image_commit && return 1
  return 0
}

repair_apply_migrations() {
  [ -x "$REPO_ROOT/scripts/backup.sh" ] && { "$REPO_ROOT/scripts/backup.sh" --env "$ENV_FILE" --label pre-repair || { fail "Резервная копия БД не создана — миграции не выполняются"; return 1; }; }
  dc run --rm --no-deps backend alembic upgrade head || return 1
  alembic_verify run >/dev/null 2>&1 || { fail "После миграции ревизия БД не совпала с ожидаемой"; return 1; }
  dc up -d --no-build backend >/dev/null 2>&1 || true
  ok "База данных обновлена до версии ${ALEMBIC_CUR}"
}

repair_apply_reverify() {
  local vf=0 sm=0
  verify_deployment; vf=$?
  "$REPO_ROOT/scripts/smoke-test.sh" --env "$ENV_FILE"; sm=$?
  if [ "$vf" -eq 0 ]; then repair_verify_record "$([ "$sm" -eq 0 ] && echo pass || echo integration)"; return 0; fi
  repair_verify_record fail; return 1
}

repair_apply() { # repair_apply ID
  repair_id_valid "$1" || { fail "Неизвестное исправление"; return 2; }
  "repair_apply_$1"
}
