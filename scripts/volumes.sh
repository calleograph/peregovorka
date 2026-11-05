#!/usr/bin/env bash
# volumes.sh — внешние тома из командной строки: то же, что делает помощник по запросу из веб-интерфейса (Администрирование → Файловые хранилища). Нужен root.
#   scripts/volumes.sh attach ПУТЬ [--env FILE]    # проверить каталог на сервере и открыть его приложению как /data/ext/ИД
#   scripts/volumes.sh detach ИД [--env FILE]      # снять подключение (данные на ресурсе не трогаются)
#   scripts/volumes.sh list [--env FILE]           # состояние томов (JSON)
#   scripts/volumes.sh reconcile [--env FILE]      # сверить подключения с фактическим монтированием
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
CMD="${1:-}"; [ -n "$CMD" ] || die "Использование: scripts/volumes.sh attach ПУТЬ | detach ИД | list | reconcile [--env FILE]"; shift
ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    *) ARG="$1"; shift ;;
  esac
done
sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
jesc() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
atomic_write() { local t="$1.$$"; cat > "$t" && mv "$t" "$1"; }
source "$REPO_ROOT/scripts/lib/vollib.sh"
case "$CMD" in
  attach) [ "$(id -u)" -eq 0 ] || die "Нужен root"; vol_attach "$ARG" || die "$VOL_ERROR"; vol_publish; ok "Подключён: $ARG → /data/ext/$VOL_ID ($VOL_NOTE)" ;;
  detach) [ "$(id -u)" -eq 0 ] || die "Нужен root"; vol_detach "$ARG" || die "$VOL_ERROR"; vol_publish; ok "Подключение $ARG снято" ;;
  reconcile) [ "$(id -u)" -eq 0 ] || die "Нужен root"; vol_reconcile; ok "Сверено" ;;
  list) vol_state_json ;;
  *) die "Неизвестная команда: $CMD" ;;
esac
