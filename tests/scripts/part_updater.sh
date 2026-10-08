# shellcheck shell=bash
# Подключается из run.sh после part_update.sh: исполнитель обновлений из веб-интерфейса (scripts/updater.sh). Используются: ROOT, TMP, t, mkorigin, pushnew
export PYTHONIOENCODING=utf-8
PYJ=""
for _c in python3 python py; do command -v "$_c" >/dev/null 2>&1 && "$_c" -c "import json" >/dev/null 2>&1 && { PYJ="$_c"; break; }; done  # заглушки Windows Store не подходят
jget() { # jget ФАЙЛ ВЫРАЖЕНИЕ — значение поля из JSON (через python); пусто, если файл/поле некорректны
  "$PYJ" - "$1" "$2" <<'PYX' 2>/dev/null
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
v = eval(sys.argv[2], {"d": d})
print(v if not isinstance(v, bool) else str(v).lower())
PYX
}

export -f jget; export PYJ
mkupdater() { # репозиторий с ПОДСТАВНЫМ update.sh: печатает этапы, пишет свои аргументы, код выхода берёт из файла $TMP/fake-rc
  mkorigin
  ( cd "$TMP/seed" || exit 1; cat > scripts/update.sh <<'FAKE'
#!/usr/bin/env bash
echo "args: $*" >> "${FAKE_ARGS_FILE:-/dev/null}"
printenv | grep -E '^(ASR_|NGINX_|LIVEKIT_|STRAY_VAR_TEST|ENV_FILE=|UPDATE_SOURCE=|UPDATE_BY=)' | sort >> "${FAKE_ENV_FILE:-/dev/null}"
echo "Обновление (подставное)"; echo "[1/3] Проверка"; echo "  \033[32mпорядок\033[0m"; sleep 1
echo "[2/3] Сборка образов"; sleep 1; echo "[3/3] Запуск"
exit "$(cat "${FAKE_RC_FILE:-/dev/null}" 2>/dev/null || echo 0)"
FAKE
    chmod +x scripts/update.sh; git add -A; git commit -qm "подставной update.sh"; git push -q origin main 2>/dev/null )
  git -C "$TMP/cl" pull -q --ff-only 2>/dev/null
  rm -rf "$TMP/upd-data"; mkdir -p "$TMP/upd-data"
  UP="$TMP/cl/scripts/updater.sh"; CHD="$TMP/upd-data/updater"
  export FAKE_ARGS_FILE="$TMP/fake-args" FAKE_RC_FILE="$TMP/fake-rc" FAKE_ENV_FILE="$TMP/fake-env"; : > "$FAKE_ARGS_FILE"; : > "$FAKE_ENV_FILE"; echo 0 > "$FAKE_RC_FILE"
  export UPDATER_PASS_ENV="FAKE_ARGS_FILE FAKE_RC_FILE FAKE_ENV_FILE"
}
mkreq() { # mkreq ID ДЕЙСТВИЕ FORCE PULL ВОЗРАСТ_С [BY]
  printf 'id=%s\naction=%s\nforce_build=%s\npull=%s\nby=%s\nat=%s\n' "$1" "$2" "$3" "$4" "${6:-admin}" "$(( $(date +%s) - $5 ))" > "$CHD/request.txt"
}
waitfor() { # waitfor СЕК команда... — ждать, пока команда не вернёт 0
  local n="$1" i; shift
  for ((i = 0; i < n * 5; i++)); do "$@" >/dev/null 2>&1 && return 0; sleep 0.2; done; return 1
}

if [ -n "$PYJ" ]; then
  mkupdater
  t "check: remote.json корректен, без новых изменений behind=0" bash -c '"$1" check --env "$2/cl/.env" >/dev/null 2>&1 && [ "$(jget "$3/remote.json" "d[\"behind\"]")" = 0 ]' _ "$UP" "$TMP" "$CHD"
  t "check: ok=true, ветка и commit указаны, права на чтение у всех" bash -c 'jget "$1/remote.json" "d[\"ok\"]" | grep -q true && [ -n "$(jget "$1/remote.json" "d[\"current\"]")" ] && [ "$(stat -c %a "$1/remote.json" 2>/dev/null || echo 644)" = 644 ]' _ "$CHD"
  pushnew
  ( cd "$TMP/seed" || exit 1; echo '"кавычки" и \ слэш' > q.txt && git add -A && git commit -qm 'Тема с "кавычками" и \ слэшем' && git push -q origin main 2>/dev/null )
  t "check: новые commit'ы, миграции и параметры .env распознаны; кавычки в теме не ломают JSON" bash -c '"$1" check --env "$2/cl/.env" >/dev/null 2>&1 \
     && [ "$(jget "$3/remote.json" "d[\"behind\"]")" = 2 ] && [ "$(jget "$3/remote.json" "d[\"migrations_changed\"]")" = 1 ] \
     && [ "$(jget "$3/remote.json" "d[\"env_example_changed\"]")" = true ] && [ "$(jget "$3/remote.json" "d[\"ff_possible\"]")" = true ] \
     && jget "$3/remote.json" "d[\"commits\"][0][\"subject\"]" | grep -q "кавычками"' _ "$UP" "$TMP" "$CHD"
  t "check: нет сети/репозитория — ok=false и понятная ошибка, а не падение" bash -c 'git -C "$1/cl" remote set-url origin /нет/такого; "$2" check --env "$1/cl/.env" >/dev/null 2>&1; [ "$(jget "$3/remote.json" "d[\"ok\"]")" = false ] && [ -n "$(jget "$3/remote.json" "d[\"error\"]")" ]; r=$?; git -C "$1/cl" remote set-url origin "$1/o"; exit $r' _ "$TMP" "$UP" "$CHD"
  t "print-unit: служба названа по проекту, запуск через updater.sh run, от root" bash -c '"$1" print-unit --env "$2/cl/.env" | grep -q "ExecStart=.*updater.sh run" && "$1" print-unit --env "$2/cl/.env" | grep -q "pg-upd" && "$1" print-unit --env "$2/cl/.env" | grep -qx "User=root"' _ "$UP" "$TMP"
  t "install без подтверждения в не-терминале отказывает и ничего не ставит" bash -c '! "$1" install --env "$2/cl/.env" </dev/null >/dev/null 2>&1' _ "$UP" "$TMP"
  t "status без запущенного исполнителя — ненулевой код" bash -c 'rm -f "$1/status.json"; ! "$2" status --env "$3/cl/.env" >/dev/null 2>&1' _ "$CHD" "$UP" "$TMP"

  # ---- рабочий цикл: запрос из «веб-интерфейса» → update.sh --yes
  # «грязное» окружение запускающего (профиль пользователя, sudo -E, systemd): оно НЕ должно доходить до update.sh
  export ASR_MAX_CONCURRENT_INFERENCE=2 NGINX_LISTEN_PORT=9999 STRAY_VAR_TEST=1
  "$UP" run --env "$TMP/cl/.env" --interval 1 > "$TMP/updater.out" 2>&1 &
  unset ASR_MAX_CONCURRENT_INFERENCE NGINX_LISTEN_PORT STRAY_VAR_TEST
  UPPID=$!
  t "run: пульс появился (status.json, state=idle), каталог обмена доступен всем" waitfor 15 bash -c '[ "$(jget "$1/status.json" "d[\"state\"]")" = idle ]' _ "$CHD"
  t "run: второй экземпляр для того же проекта не стартует" bash -c 'command -v flock >/dev/null || exit 0; ! timeout 10 "$1" run --env "$2/cl/.env" --interval 1 >/dev/null 2>&1' _ "$UP" "$TMP"
  mkreq aaaaaaaaaaaaaaaa update 1 0 5 ivanov
  t "update: запрос выполнен, update.sh получил --yes и --force-build, request.txt удалён" waitfor 30 bash -c '[ "$(jget "$1/status.json" "d[\"result\"]")" = ok ] && grep -q -- "--yes" "$2" && grep -q -- "--force-build" "$2" && [ ! -f "$1/request.txt" ]' _ "$CHD" "$FAKE_ARGS_FILE"
  t "update: журнал содержит этапы без управляющих последовательностей, шапку и итог" bash -c 'grep -q "^\[2/3\] Сборка образов" "$1/update.log" && grep -q "Обновление завершено.*успешно" "$1/update.log" && ! grep -q $'"'"'\x1b'"'"' "$1/update.log" && grep -q "запросил: ivanov" "$1/update.log"' _ "$CHD"
  t "update: статус — этап 3/3, код 0, кто запросил" bash -c '[ "$(jget "$1/status.json" "d[\"step_no\"]")" = 3 ] && [ "$(jget "$1/status.json" "d[\"exit_code\"]")" = 0 ] && [ "$(jget "$1/status.json" "d[\"by\"]")" = ivanov ]' _ "$CHD"
  t "update: после обновления remote.json пересчитан автоматически" waitfor 15 bash -c '[ "$(jget "$1/remote.json" "d[\"checked_at\"]")" -ge "$(jget "$1/status.json" "d[\"finished_at\"]")" ] || [ "$(jget "$1/remote.json" "d[\"ok\"]")" = false ]' _ "$CHD"
  t "update: окружение update.sh стерильное — посторонние ASR_*, NGINX_*, STRAY_* не унаследованы, .env задан через ENV_FILE" bash -c 'grep -q "^ENV_FILE=" "$1" && grep -q "^UPDATE_SOURCE=web" "$1" && ! grep -Eq "^(ASR_|NGINX_|LIVEKIT_|STRAY_VAR_TEST)" "$1"' _ "$FAKE_ENV_FILE"
  t "status.json: указан uid исполнителя (веб-интерфейс по нему судит о правах помощника)" bash -c '[ "$(jget "$1/status.json" "d[\"uid\"]")" = "$(id -u)" ]' _ "$CHD"
  t "scan: repairs.json создан исполнителем при запуске и после обновления" waitfor 15 bash -c '[ "$(jget "$1/repairs.json" "type(d[\"items\"]).__name__")" = list ] && [ "$(jget "$1/repairs.json" "d[\"uid\"]")" = "$(id -u)" ]' _ "$CHD"
  # код 3 = обновление и сервисы в порядке, но интеграции (LDAP) не прошли проверку: это успех обновления, а не сбой
  echo 3 > "$FAKE_RC_FILE"; : > "$FAKE_ARGS_FILE"
  mkreq e3e3e3e3e3e3e3e3 update 0 0 5 petrov
  t "update: код 3 (проблема интеграции) → result=ok, exit_code=3" waitfor 30 bash -c '[ "$(jget "$1/status.json" "d[\"result\"]")" = ok ] && [ "$(jget "$1/status.json" "d[\"exit_code\"]")" = 3 ] && [ "$(jget "$1/status.json" "d[\"state\"]")" = idle ]' _ "$CHD"
  echo 0 > "$FAKE_RC_FILE"
  # исправления: ID вне белого списка не выполняются; запрос выполняется ровно один раз
  : > "$FAKE_ARGS_FILE"
  printf 'id=%s\naction=repair\nrepair=%s\nforce_build=0\npull=0\nby=admin\nat=%s\n' f1f1f1f1f1f1f1f1 'x; touch /tmp/pwned-repair' "$(date +%s)" > "$CHD/request.txt"
  t "repair: ID вне белого списка отклонён, request.txt удалён, ничего не запущено" waitfor 20 bash -c '[ ! -f "$1/request.txt" ] && [ ! -e /tmp/pwned-repair ] && [ "$(jget "$1/status.json" "d[\"action\"]")" != repair ]' _ "$CHD"
  printf 'id=%s\naction=format-disk\nforce_build=0\npull=0\nby=admin\nat=%s\n' f2f2f2f2f2f2f2f2 "$(date +%s)" > "$CHD/request.txt"
  t "неизвестное действие запроса игнорируется" waitfor 20 bash -c '[ ! -f "$1/request.txt" ]' _ "$CHD"


  echo 7 > "$FAKE_RC_FILE"; : > "$FAKE_ARGS_FILE"
  mkreq bbbbbbbbbbbbbbbb update 0 1 5 petrov
  t "ошибка update.sh: result=failed и код выхода передан в статус, исполнитель продолжает работать" waitfor 30 bash -c '[ "$(jget "$1/status.json" "d[\"result\"]")" = failed ] && [ "$(jget "$1/status.json" "d[\"exit_code\"]")" = 7 ] && grep -q -- "--pull" "$2" && ! grep -q -- "--force-build" "$2"' _ "$CHD" "$FAKE_ARGS_FILE"
  t "после сбоя исполнитель жив: процесс работает и возвращается в состояние idle" waitfor 15 bash -c 'kill -0 "$1" && [ "$(jget "$2/status.json" "d[\"state\"]")" = idle ]' _ "$UPPID" "$CHD"

  : > "$FAKE_ARGS_FILE"; echo 0 > "$FAKE_RC_FILE"
  mkreq cccccccccccccccc update 0 0 4000      # старше 10 минут
  t "устаревший запрос не выполняется и удаляется" waitfor 20 bash -c '[ ! -f "$2/request.txt" ] && [ ! -s "$1" ]' _ "$FAKE_ARGS_FILE" "$CHD"
  mkreq "x;touch /tmp/pwned" update 0 0 5
  t "запрос с некорректным id отклоняется" waitfor 20 bash -c '[ ! -f "$2/request.txt" ] && [ ! -s "$1" ]' _ "$FAKE_ARGS_FILE" "$CHD"
  mkreq dddddddddddddddd "update; id" 0 0 5
  t "запрос с неизвестным действием не запускает ничего" waitfor 20 bash -c '[ ! -f "$2/request.txt" ] && [ ! -s "$1" ]' _ "$FAKE_ARGS_FILE" "$CHD"
  mkreq eeeeeeeeeeeeeeee update "1 --rm" "--x" 5 'a b;c'
  t "флаги вне списка отбрасываются: update.sh получил только --yes/--env" waitfor 30 bash -c 'grep -q -- "--yes" "$1" && ! grep -q -- "--rm\|--x\|;" "$1"' _ "$FAKE_ARGS_FILE"
  waitfor 30 bash -c '[ "$(jget "$1/status.json" "d[\"state\"]")" = idle ]' _ "$CHD"   # дождаться окончания предыдущего обновления
  : > "$FAKE_ARGS_FILE"; T0="$(date +%s)"
  mkreq ffffffffffffffff check 0 0 5
  t "check по запросу обновляет remote.json, update.sh не запускается" waitfor 20 bash -c '[ ! -f "$2/request.txt" ] && [ "$(jget "$2/remote.json" "d[\"checked_at\"]")" -ge "$3" ] && [ ! -s "$1" ]' _ "$FAKE_ARGS_FILE" "$CHD" "$T0"
  t "status: исполнитель работает, ненулевой код при остановленном" bash -c '"$1" status --env "$2/cl/.env" >/dev/null 2>&1' _ "$UP" "$TMP"
  kill "$UPPID" 2>/dev/null; wait "$UPPID" 2>/dev/null
  t "остановка по сигналу: state=stopped" waitfor 10 bash -c '[ "$(jget "$1/status.json" "d[\"state\"]")" = stopped ]' _ "$CHD"
else
  echo "(пропущено: нет python для проверки JSON исполнителя обновлений)"
fi

t "updater.sh и update.sh не выполняют ничего произвольного из запроса (нет eval/source request)" bash -c '! grep -nE "eval |source .*request|bash -c .*\\$\\(req" "$1/scripts/updater.sh" | grep -v "^[0-9]*:[[:space:]]*#"' _ "$ROOT"
t "каталог обмена создаётся до запуска контейнеров (update.sh и install.sh)" bash -c 'grep -q "upd_ensure_updater_dir" "$1/scripts/update.sh" && grep -q "updater" "$1/scripts/install.sh" && grep -q "/data/updater" "$1/deployment/compose.yml"' _ "$ROOT"
