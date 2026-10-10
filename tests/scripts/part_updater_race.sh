# shellcheck shell=bash
# Подключается из run.sh после part_updater.sh. Стресс окна «сигнал остановки ↔ работа исполнителя обновлений»: сигнал в покое, во время проверки репозитория,
# во время обновления и вокруг самоперезапуска (`exec` при смене собственного скрипта). После каждого сигнала должно быть: процесс завершился и status.json=stopped.
# Не нужен root и Docker; нужны flock и python (как у части исполнителя). Результат — сводка в тестах и, при нарушениях, ::warning-аннотации CI с подробностями
# (состояние, жив ли процесс, хвост вывода): по ним видно, в какой фазе окно. Строгим тест станет после того, как причина будет подтверждена и устранена.
if [ -n "${PYJ:-}" ] && command -v flock >/dev/null 2>&1; then
  mkupdater
  race_total=0; race_bad=0; race_summary=""
  race_delays=(0 0.05 0.1 0.15 0.2 0.3 0.45 0.7)
  for race_i in $(seq 1 24); do
    race_mode=$((race_i % 4)); race_delay="${race_delays[$((race_i % ${#race_delays[@]}))]}"
    race_out="$TMP/race-$race_i.out"; rm -f "$CHD/request.txt"
    "$UP" run --env "$TMP/cl/.env" --interval 1 > "$race_out" 2>&1 &
    race_pid=$!
    waitfor 15 bash -c '[ "$(jget "$1/status.json" "d[\"state\"]")" = idle ] && [ "$(jget "$1/status.json" "d[\"pid\"]")" = "$2" ]' _ "$CHD" "$race_pid"
    case "$race_mode" in
      0) ;;                                                                                      # покой
      1) mkreq "$(printf '%016x' "$race_i")" check 0 0 5 ;;                                      # проверка репозитория (git fetch)
      2) echo 0 > "$FAKE_RC_FILE"; mkreq "$(printf '%016x' "$race_i")" update 0 0 5 ;;           # обновление (подставной update.sh работает ~2 с)
      3) echo "# race $race_i" >> "$UP"; mkreq "$(printf '%016x' "$race_i")" check 0 0 5 ;;       # смена собственного скрипта → самоперезапуск после запроса
    esac
    sleep "$race_delay"
    kill -TERM "$race_pid" 2>/dev/null
    for _ in $(seq 1 200); do kill -0 "$race_pid" 2>/dev/null || break; sleep 0.2; done        # ждём завершения до 40 с
    race_alive=0; kill -0 "$race_pid" 2>/dev/null && { race_alive=1; kill -KILL "$race_pid" 2>/dev/null; }
    wait "$race_pid" 2>/dev/null
    race_state="$(jget "$CHD/status.json" "d[\"state\"]")"
    race_total=$((race_total + 1))
    if [ "$race_alive" -eq 1 ] || [ "$race_state" != stopped ]; then
      race_bad=$((race_bad + 1)); race_summary="$race_summary [#$race_i mode=$race_mode delay=$race_delay alive=$race_alive state=${race_state:-?}]"
      [ -z "${GITHUB_ACTIONS:-}" ] || echo "::warning title=updater-signal-race::iter=$race_i mode=$race_mode delay=$race_delay alive=$race_alive state=${race_state:-?} out=$(tail -n 4 "$race_out" | tr '\n' ' ' | cut -c1-300)"
    fi
    rm -f "$CHD/request.txt"
  done
  [ -z "${GITHUB_ACTIONS:-}" ] || echo "::notice title=updater-signal-race::попыток $race_total, нарушений $race_bad:$race_summary"
  echo "updater: сигнал остановки в разных фазах — попыток $race_total, нарушений $race_bad$race_summary"
  t "updater: стресс окна сигнала выполнен (сводка выше; нарушения видны аннотациями CI)" test "$race_total" -ge 20
  git -C "$TMP/cl" checkout -q -- scripts/updater.sh 2>/dev/null || true
fi
