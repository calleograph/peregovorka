# shellcheck shell=bash
# Подключается из run.sh: аварийный сброс ограничения по подсетям (scripts/security-reset.sh). Docker подменён заглушкой: проверяем, что именно будет запущено в backend.
SR="$ROOT/scripts/security-reset.sh"
SRB="$TMP/sr-bin"; mkdir -p "$SRB"
cat > "$SRB/docker" <<'EOS'
#!/usr/bin/env bash
# заглушка: info и «exec … python -c» успешны (backend «запущен»), остальные вызовы пишутся в журнал
case " $* " in
  *" info "*) exit 0 ;;
  *" python -c "*) exit 0 ;;
  *) printf '%s\n' "$*" >> "$SR_LOG"; exit 0 ;;
esac
EOS
chmod +x "$SRB/docker"
SRENV="$TMP/sr.env"; printf 'COMPOSE_PROJECT_NAME=srtest\nDATA_ROOT=%s/data\nAPP_ROOT=%s\n' "$TMP" "$ROOT" > "$SRENV"
t "security-reset.sh: синтаксис корректен и справка печатается без Docker" bash -c 'bash -n "$1" && bash "$1" --help | grep -q "АВАРИЙНОЕ"' _ "$SR"
t "security-reset.sh: неизвестный аргумент отвергается" bash -c '! bash "$1" --bogus' _ "$SR"
t "security-reset.sh --yes: запускает в backend только security-reset от имени пользователя консоли, без --unblock-all" bash -c 'export SR_LOG="$3"; : > "$SR_LOG"; PATH="$2:$PATH" bash "$1" --env "$4" --yes && grep -q "python -m app.cli security-reset --actor " "$SR_LOG" && ! grep -q -- "--unblock-all" "$SR_LOG"' _ "$SR" "$SRB" "$TMP/sr.log" "$SRENV"
t "security-reset.sh --yes --unblock-all: передаёт снятие блокировок" bash -c 'export SR_LOG="$3"; : > "$SR_LOG"; PATH="$2:$PATH" bash "$1" --env "$4" --yes --unblock-all && grep -q -- "security-reset --actor .* --unblock-all" "$SR_LOG"' _ "$SR" "$SRB" "$TMP/sr.log" "$SRENV"
t "security-reset.sh без --yes и без ввода: ничего не выполняет" bash -c 'export SR_LOG="$3"; : > "$SR_LOG"; ! PATH="$2:$PATH" bash "$1" --env "$4" </dev/null; [ ! -s "$SR_LOG" ]' _ "$SR" "$SRB" "$TMP/sr.log" "$SRENV"
