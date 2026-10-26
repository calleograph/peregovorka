#!/usr/bin/env bash
# ports.sh — перечень сетевых портов этого экземпляра для сетевого администратора (read-only, ничего не меняет и не открывает).
#   scripts/ports.sh [--env FILE] [--format text|md|csv|json|request] [--out ФАЙЛ] [--live]
# По умолчанию читает только `.env` и работает без Docker и без запущенного веб-интерфейса: правила строит тот же модуль, что раздел
# «Администрирование → Система → Сетевая доступность» (backend/app/services/netrules.py).
# --live  дополнительно берёт из базы адреса интеграций (каталог, почта, SMB, Exchange…): запускает команду в контейнере backend.
# --format request — текст заявки сетевому администратору. Код возврата 0; 2 — нет .env или неверные аргументы.
# Проверка «порт открыт из интернета» здесь не выполняется: с самого сервера её достоверно не сделать (см. раздел «Ограничения» в выводе).
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

FORMAT=text; OUT=""; LIVE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --env) ENV_FILE="$2"; shift 2 ;;
    --format) FORMAT="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --live) LIVE=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) fail "Неизвестный аргумент: $1"; exit 2 ;;
  esac
done
case "$FORMAT" in text|md|csv|json|request) ;; *) fail "Формат: text, md, csv, json или request"; exit 2 ;; esac
[ -r "$ENV_FILE" ] || { fail "Не найден файл настроек: $ENV_FILE (укажите --env)"; exit 2; }

emit() { if [ -n "$OUT" ]; then cat > "$OUT" && ok "Сохранено: $OUT" >&2; else cat; fi; }

if [ "$LIVE" = 1 ]; then
  sanitize_project_env; load_env "$ENV_FILE"; validate_project_name
  dc exec -T backend python -m app.cli network --format "$FORMAT" | emit
  exit "${PIPESTATUS[0]}"
fi

python3 -I - "$REPO_ROOT/backend/app/services/netrules.py" "$ENV_FILE" "$FORMAT" <<'PY' | emit
import importlib.util, sys
spec = importlib.util.spec_from_file_location("netrules", sys.argv[1])
m = importlib.util.module_from_spec(spec); sys.modules["netrules"] = m; spec.loader.exec_module(m)
env = m.parse_dotenv(open(sys.argv[2], encoding="utf-8").read())
n = m.from_env(env, unknown=["адреса интеграций из настроек в базе (почта, Exchange, SMB и др.): добавьте --live"])
sys.stdout.write(m.FORMATS[sys.argv[3]](m.build(n)))
PY
exit "${PIPESTATUS[0]}"
