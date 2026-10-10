# shellcheck shell=bash
# Подключается из run.sh после part_updater.sh. Установка и контроль помощника обновлений на «настоящем» Linux: служба systemd подменена коротким скриптом (systemctl запускает
# команду из ExecStart как обычный процесс и ведёт учёт PID), всё остальное — настоящее: updater.sh, блокировка, /proc, фактический UID процесса, пульс, запрос связи.
# Сценарий из требований: чистая установка → helper установлен и active → обновление проекта → helper остался active → ещё одно обновление из web → helper по-прежнему доступен.
# Плюс: идемпотентность, замена постороннего процесса (прежний запуск вручную другим пользователем), проверка РЕАЛЬНОГО UID (не строки User= в файле), диагностика «упал» и «остановлен».
if [ -n "${PYJ:-}" ] && command -v flock >/dev/null 2>&1 && [ -r /proc/self/status ]; then
  unset INVOCATION_ID                 # раннер CI сам работает как служба systemd: унаследованный идентификатор принял бы чужой процесс за запущенный службой
  mkupdater
  ENVF="$TMP/cl/.env"; FSD="$TMP/fakesd"; mkdir -p "$FSD" "$TMP/fakebin" "$TMP/units"
  cat > "$TMP/fakebin/systemctl" <<'FAKESD'
#!/usr/bin/env bash
SD="${FAKE_SD:?}"; cmd="${1:-}"; [ $# -gt 0 ] && shift
args=("$@")
alive() { local p="$1"; [ -n "$p" ] && [ -r "/proc/$p/status" ] && [ "$(awk '/^State:/{print $2; exit}' "/proc/$p/status")" != Z ]; }
mainpid() { local p; p="$(cat "$SD/main" 2>/dev/null)"; if alive "$p"; then echo "$p"; else echo 0; fi; }
unitfile() { ls "${UPDATER_UNIT_DIR:?}"/*.service 2>/dev/null | head -1; }
do_stop() {
  local p i; p="$(cat "$SD/main" 2>/dev/null)"
  if alive "$p"; then kill -TERM "$p" 2>/dev/null; for i in $(seq 1 80); do alive "$p" || break; sleep 0.1; done; alive "$p" && kill -KILL "$p" 2>/dev/null; fi
  rm -f "$SD/main"
}
do_start() {
  local f cmdline; f="$(unitfile)"; [ -n "$f" ] || exit 5
  cmdline="$(sed -n 's/^ExecStart=//p' "$f" | head -1)"
  rm -f "$SD/failed"
  ( INVOCATION_ID="fake-$$-$RANDOM" nohup bash -c "exec $cmdline" > "$SD/service.log" 2>&1 & echo $! > "$SD/main" )
  echo $(( $(cat "$SD/starts" 2>/dev/null || echo 0) + 1 )) > "$SD/starts"
}
case "$cmd" in
  daemon-reload|disable) exit 0 ;;
  enable) : > "$SD/enabled"; exit 0 ;;
  is-enabled) if [ -f "$SD/enabled" ]; then echo enabled; exit 0; else echo disabled; exit 1; fi ;;
  is-active) if [ "$(mainpid)" != 0 ]; then echo active; exit 0; elif [ -f "$SD/failed" ]; then echo failed; exit 3; else echo inactive; exit 3; fi ;;
  show) prop=""; for ((i = 0; i < ${#args[@]}; i++)); do [ "${args[i]}" = -p ] && prop="${args[i+1]}"; done
        case "$prop" in
          MainPID) mainpid ;;
          SubState) if [ "$(mainpid)" != 0 ]; then echo running; elif [ -f "$SD/failed" ]; then echo failed; else echo dead; fi ;;
          Result) if [ -f "$SD/failed" ]; then echo exit-code; else echo success; fi ;;
          NRestarts) s="$(cat "$SD/starts" 2>/dev/null || echo 0)"; echo $(( s > 0 ? s - 1 : 0 )) ;;
        esac ;;
  restart|start) do_stop; do_start ;;
  stop) do_stop ;;
esac
exit 0
FAKESD
  cat > "$TMP/fakebin/systemd-run" <<'FAKESR'
#!/usr/bin/env bash
delay=0
while [ $# -gt 0 ]; do case "$1" in --on-active=*) delay="${1#*=}"; shift ;; --collect|--quiet) shift ;; *) break ;; esac; done
( sleep "$delay"; "$@" ) >/dev/null 2>&1 &
exit 0
FAKESR
  chmod +x "$TMP/fakebin/systemctl" "$TMP/fakebin/systemd-run"
  # окружение «как на сервере»: unit-файлы в подставном каталоге, без sudo, ожидаемый пользователь — текущий (тест идёт не от root), быстрый пульс и короткие ожидания
  export FAKE_SD="$FSD" UPDATER_UNIT_DIR="$TMP/units" UPDATER_NO_SUDO=1 UPDATER_EXPECT_UID="$(id -u)" UPDATER_PULSE_EVERY=1 UPDATER_RESTART_DELAY=1 UPDATER_START_WAIT=40 UPDATER_PING_WAIT=25
  export FAKE_SLEEP=6 UP CHD ENVF FSD TMP
  PATH_BEFORE_HELPER="$PATH"; export PATH="$TMP/fakebin:$PATH"
  uj() { jget "$CHD/install-state.json" "d[\"$1\"]"; }          # поле install-state.json (итог проверки установки для веб-интерфейса)
  hbj() { jget "$CHD/helper.json" "d[\"$1\"]"; }                # поле helper.json (пульс)
  stj() { jget "$CHD/status.json" "d[\"$1\"]"; }                # поле status.json
  mainpid() { "$TMP/fakebin/systemctl" show u.service -p MainPID --value; }
  export -f uj hbj stj mainpid

  # ---- до установки
  t "verify до установки: вердикт not_installed и ненулевой код" bash -c '! "$UP" verify --env "$ENVF" >/dev/null 2>&1 && [ "$(uj verdict)" = not_installed ]'

  # ---- чистая установка
  "$UP" install --env "$ENVF" --yes > "$TMP/inst1.out" 2>&1; INST1_RC=$?
  t "install --yes на чистой системе: успех (код 0)" test "$INST1_RC" -eq 0
  t "служба: файл создан, включена, активна" bash -c 'ls "$UPDATER_UNIT_DIR"/*.service >/dev/null 2>&1 && systemctl is-enabled u.service >/dev/null && systemctl is-active u.service >/dev/null'
  t "файл службы: User=root, ExecStopPost для диагностики остановки, Restart=always" bash -c 'f="$(ls "$UPDATER_UNIT_DIR"/*.service)"; grep -qx "User=root" "$f" && grep -q "^ExecStopPost=-.*note-stop" "$f" && grep -qx "Restart=always" "$f"'
  t "процесс службы реально запущен и принадлежит ожидаемому пользователю (UID берётся из /proc, а не из файла службы)" bash -c 'p="$(mainpid)"; [ "$p" -gt 0 ] && [ -d "/proc/$p" ] && [ "$(awk "/^Uid:/{print \$2; exit}" "/proc/$p/status")" = "$(id -u)" ]'
  t "пульс пишет именно процесс службы и сообщает, что он запущен systemd" bash -c '[ "$(hbj pid)" = "$(mainpid)" ] && [ "$(hbj under_systemd)" = true ] && [ "$(hbj uid)" = "$(id -u)" ]'
  t "итог проверки (install-state.json): ok, связь с помощником подтверждена запросом (ping=ok)" bash -c '[ "$(uj verdict)" = ok ] && [ "$(uj ping)" = ok ] && [ "$(uj active)" = active ] && [ "$(uj enabled)" = enabled ]'
  t "verify: код 0 и сообщение о работающем помощнике" bash -c '"$UP" verify --env "$ENVF" 2>&1 | grep -q "Помощник работает"'

  # ---- повторный install безопасен: ничего не перезапускается
  PID1="$(mainpid)"; ST1="$(cat "$FSD/starts")"
  "$UP" install --env "$ENVF" --yes > "$TMP/inst2.out" 2>&1; INST2_RC=$?
  t "повторный install --yes: код 0, процесс тот же, перезапуска не было" bash -c '[ "$1" -eq 0 ] && [ "$(mainpid)" = "$2" ] && [ "$(cat "$FSD/starts")" = "$3" ]' _ "$INST2_RC" "$PID1" "$ST1"
  t "повторный install сообщает «уже установлен»" grep -q "уже установлен" "$TMP/inst2.out"
  "$UP" install --env "$ENVF" --yes --quiet > /dev/null 2>&1
  t "install --quiet (как из update.sh) тоже ничего не перезапускает" bash -c '[ "$(mainpid)" = "$1" ] && [ "$(cat "$FSD/starts")" = "$2" ]' _ "$PID1" "$ST1"

  # ---- та функция, которую вызывают install.sh и update.sh
  ( cd "$TMP/cl" && ENV_FILE="$ENVF" bash -c 'source scripts/lib/common.sh; sanitize_project_env; load_env "$ENV_FILE"; DRY_RUN=0; UPDATE_SOURCE=cli; upd_ensure_helper' > "$TMP/ensure.out" 2>&1 ); ENSURE_RC=$?
  t "upd_ensure_helper (из install.sh/update.sh) на исправной системе: успех, процесс не тронут" bash -c '[ "$1" -eq 0 ] && [ "$(mainpid)" = "$2" ]' _ "$ENSURE_RC" "$PID1"
  t "upd_ensure_helper выводит итог установки" grep -q "Помощник обновлений установлен и работает" "$TMP/ensure.out"

  # ---- «обновление проекта изменило службу»: unit перезаписывается и служба перезапускается, помощник остаётся active
  UPDATER_RESTART_SEC=11 "$UP" install --env "$ENVF" --yes > "$TMP/inst3.out" 2>&1; INST3_RC=$?
  t "изменившийся файл службы: install обновляет unit и перезапускает (новый процесс, RestartSec=11, вердикт ok)" bash -c '[ "$1" -eq 0 ] && [ "$(mainpid)" != "$2" ] && [ "$(mainpid)" -gt 0 ] && grep -qx "RestartSec=11" "$UPDATER_UNIT_DIR"/*.service && [ "$(uj verdict)" = ok ]' _ "$INST3_RC" "$PID1"
  t "устаревший unit при работающем помощнике: verify — stale_unit (работает, но нужно обновить), код 0" bash -c '"$UP" verify --env "$ENVF" >/dev/null 2>&1; rc=$?; [ "$rc" -eq 0 ] && [ "$(uj verdict)" = stale_unit ]'
  "$UP" install --env "$ENVF" --yes > /dev/null 2>&1
  t "после install без отклонений: unit актуален и helper ok" bash -c '[ "$(uj verdict)" = ok ] && grep -qx "RestartSec=10" "$UPDATER_UNIT_DIR"/*.service'

  # ---- обновление из веб-интерфейса: пульс живёт, пока идёт долгая сборка (раньше интерфейс принимал это за «помощник не установлен»)
  PID2="$(mainpid)"
  mkreq "$(printf '%016x' 7001)" update 0 0 5 ivan
  waitfor 30 bash -c '[ "$(stj state)" = updating ]'
  sleep 1.5; S1="$(stj ts)"; H1="$(hbj ts)"
  sleep 3;   S2="$(stj ts)"; H2="$(hbj ts)"
  t "во время долгого обновления идёт обновление (state=updating)" bash -c '[ "$(stj state)" = updating ]'
  t "пульс helper.json обновляется, пока status.json стоит на месте (длинный этап сборки)" bash -c '[ "$2" -gt "$1" ] && [ "$4" = "$3" ]' _ "$H1" "$H2" "$S1" "$S2"
  t "обновление завершилось успешно, помощник вернулся в idle" waitfor 60 bash -c '[ "$(stj state)" = idle ] && [ "$(stj result)" = ok ]'
  t "после обновления из веб: тот же процесс службы, помощник active и отвечает" bash -c '[ "$(mainpid)" = "$1" ] && "$UP" verify --env "$ENVF" >/dev/null 2>&1 && [ "$(uj verdict)" = ok ] && [ "$(uj ping)" = ok ]' _ "$PID2"

  # ---- в новой версии изменилась служба: после веб-обновления помощник сам приводит unit в порядок и перезапускается (отложенно, обновление не прерывается)
  printf '# служба прежней версии\n' >> "$(ls "$UPDATER_UNIT_DIR"/*.service)"
  RUNS="$(wc -l < "$FAKE_ARGS_FILE")"
  mkreq "$(printf '%016x' 7002)" update 0 0 5 ivan
  t "обновление из веб при устаревшем unit завершается успешно" waitfor 90 bash -c '[ "$(wc -l < "$FAKE_ARGS_FILE")" -gt "$1" ] && grep -q "Обновление завершено.*успешно" "$CHD/update.log"' _ "$RUNS"
  t "после него unit приведён к актуальному и helper перезапущен отложенно: новый процесс, active, пульс его" waitfor 60 bash -c 'p="$(mainpid)"; [ "$p" -gt 0 ] && [ "$p" != "$1" ] && [ "$(hbj pid)" = "$p" ] && ! grep -q "служба прежней версии" "$UPDATER_UNIT_DIR"/*.service' _ "$PID2"
  PID3="$(mainpid)"
  RUNS="$(wc -l < "$FAKE_ARGS_FILE")"
  mkreq "$(printf '%016x' 7003)" update 0 0 5 ivan
  t "ещё одно обновление из web проходит" waitfor 90 bash -c '[ "$(wc -l < "$FAKE_ARGS_FILE")" -gt "$1" ] && grep -q "Обновление завершено.*успешно" "$CHD/update.log"' _ "$RUNS"
  t "и helper по-прежнему доступен: тот же процесс, пульс свежий, связь есть" bash -c '[ "$(mainpid)" = "$1" ] && "$UP" verify --env "$ENVF" >/dev/null 2>&1 && [ "$(uj ping)" = ok ]' _ "$PID3"

  # ---- остановка службы: понятная диагностика вместо «не установлен»
  "$TMP/fakebin/systemctl" stop u.service
  t "служба остановлена: вердикт installed_inactive (не «не установлен»), код ≠ 0" bash -c '! "$UP" verify --env "$ENVF" >/dev/null 2>&1 && [ "$(uj verdict)" = installed_inactive ]'
  : > "$FSD/failed"
  t "служба упала: вердикт failed, в причине есть число перезапусков" bash -c '! "$UP" verify --env "$ENVF" >/dev/null 2>&1 && [ "$(uj verdict)" = failed ] && jget "$CHD/install-state.json" "d[\"why\"]" | grep -q "перезапусков"'
  SERVICE_RESULT=exit-code EXIT_STATUS=1 EXIT_CODE=exited "$UP" note-stop --env "$ENVF" >/dev/null 2>&1
  t "note-stop (ExecStopPost) записывает, как завершилась служба" bash -c '[ "$(jget "$CHD/unit-state.json" "d[\"result\"]")" = exit-code ] && [ "$(jget "$CHD/unit-state.json" "d[\"exit_status\"]")" = 1 ]'
  rm -f "$FSD/failed"

  # ---- прежняя проблема: посторонний процесс (запущенный вручную другим пользователем) держит блокировку, а служба не может стартовать
  ( env -u INVOCATION_ID "$UP" run --env "$ENVF" > "$TMP/foreign.log" 2>&1 & echo $! > "$TMP/foreign.pid" )
  FPID="$(cat "$TMP/foreign.pid")"; export FPID
  t "посторонний экземпляр помощника запущен и пишет пульс (вне службы)" waitfor 20 bash -c '[ "$(hbj pid)" = "$FPID" ] && [ "$(hbj under_systemd)" = false ]'
  t "verify видит: работает вручную, а не службой (manual_process), и ненулевой код" bash -c '! "$UP" verify --env "$ENVF" >/dev/null 2>&1 && [ "$(uj verdict)" = manual_process ]'
  t "служба рядом с посторонним процессом не стартует: блокировка занята (сообщение называет процесс)" bash -c '"$TMP/fakebin/systemctl" start u.service; sleep 3; [ "$(mainpid)" = 0 ] && grep -q "уже запущен" "$FSD/service.log" && grep -q "$FPID" "$FSD/service.log"'
  "$UP" install --env "$ENVF" --yes > "$TMP/inst4.out" 2>&1; INST4_RC=$?
  t "install заменяет посторонний процесс: он завершён, работает процесс службы, вердикт ok" bash -c '[ "$1" -eq 0 ] || exit 1; if [ -d "/proc/$FPID" ] && [ "$(awk "/^State:/{print \$2; exit}" "/proc/$FPID/status")" != Z ]; then exit 1; fi; p="$(mainpid)"; [ "$p" -gt 0 ] && [ "$p" != "$FPID" ] && [ "$(hbj pid)" = "$p" ] && [ "$(uj verdict)" = ok ]' _ "$INST4_RC"
  t "install сообщает о завершении постороннего процесса" grep -q "посторонний экземпляр" "$TMP/inst4.out"

  # ---- проверка фактического пользователя: в файле службы User=root, но процесс принадлежит не тому, кто ожидается
  t "несовпадение пользователя процесса: вердикт wrong_user по фактическому uid, причина с uid" bash -c 'UPDATER_EXPECT_UID=$(( $(id -u) + 4242 )) "$UP" verify --env "$ENVF" >/dev/null 2>&1; rc=$?; [ "$rc" -ne 0 ] && [ "$(uj verdict)" = wrong_user ] && jget "$CHD/install-state.json" "d[\"why\"]" | grep -q "uid"'
  t "и при совпадении вердикт возвращается в ok" bash -c '"$UP" verify --env "$ENVF" >/dev/null 2>&1 && [ "$(uj verdict)" = ok ]'

  # ---- завершение: служба останавливается, окружение возвращается
  "$TMP/fakebin/systemctl" stop u.service 2>/dev/null
  [ -n "${FPID:-}" ] && kill -KILL "$FPID" 2>/dev/null
  export PATH="$PATH_BEFORE_HELPER"
  unset FAKE_SD UPDATER_UNIT_DIR UPDATER_NO_SUDO UPDATER_EXPECT_UID UPDATER_PULSE_EVERY UPDATER_RESTART_DELAY UPDATER_START_WAIT UPDATER_PING_WAIT FAKE_SLEEP FPID
fi
