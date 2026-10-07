# shellcheck shell=bash
# Подключается из run.sh: статические проверки nginx-конфигураций и самопроверки обновления. Живой nginx проверяет tests/nginx/run.sh (CI, задача web-image).

# map не ссылается сам на себя: `default $xfp;` внутри `map … $xfp` даёт «cycle while evaluating variable» и 499 при непустом X-Forwarded-Proto
mapcycle() { # файл → 0, если ни один map не использует собственную переменную в значениях
  awk '/^[[:space:]]*map[[:space:]]/ { v = $3; sub(/[[:space:]]*\{.*/, "", v); inmap = 1; next }
       inmap && /^[[:space:]]*\}/ { inmap = 0; next }
       inmap && index($0, v) { found = 1 }
       END { exit found ? 1 : 0 }' "$1"
}
export -f mapcycle
for f in frontend/nginx.conf deployment/nginx/site.http.conf.tpl deployment/nginx/site.tls.conf.tpl; do
  t "$f: map не ссылается на собственную переменную (цикл)" bash -c 'mapcycle "$1/$2"' _ "$ROOT" "$f"
done
t "mapcycle ловит прежнюю ошибку (default \$xfp внутри map … \$xfp)" bash -c 'printf "map \$http_x_forwarded_proto \$xfp {\n    default \$xfp;\n    \"\"      \$scheme;\n}\n" > "$1/bad.conf"; ! mapcycle "$1/bad.conf"' _ "$TMP"
t "web nginx: схема из map — default берёт заголовок" grep -q 'default \$http_x_forwarded_proto;' "$ROOT/frontend/nginx.conf"
t "verify: запрос через прокси со схемой https и nginx -t в web; update/rebuild проверяют конфигурацию нового образа" bash -c 'grep -q "X-Forwarded-Proto: https" "$1/scripts/lib/verifylib.sh" && grep -q "nginx -t" "$1/scripts/lib/verifylib.sh" && grep -q "check_web_image_config" "$1/scripts/update.sh" && grep -q "check_web_image_config" "$1/scripts/rebuild.sh"' _ "$ROOT"
t "verify: итог по этапам (PASS/FAIL) печатается verify.sh и update.sh" bash -c 'grep -q print_verify_stages "$1/scripts/verify.sh" && grep -q VSTAGES "$1/scripts/update.sh"' _ "$ROOT"
t "CI: сборка образа web и проверка на живом nginx" grep -q "tests/nginx/run.sh" "$ROOT/.github/workflows/ci.yml"

# ---- история попыток обновления (history.ndjson): пишет update.sh при любом запуске, веб-интерфейс различает текущее и прошлое
HR="$TMP/hist-repo"; rm -rf "$HR"; mkdir -p "$HR"; ( cd "$HR" || exit 1; git init -q . && git config user.email t@t && git config user.name t && echo 0.1.3 > VERSION && git add -A && git commit -qm a && echo 0.1.4 > VERSION && git add -A && git commit -qm b )
HA="$(git -C "$HR" rev-parse HEAD~1)"; HB="$(git -C "$HR" rev-parse HEAD)"
histrun() { # histrun РЕЗУЛЬТАТ ЭТАП [ENV...] — вызов upd_history_append в подоболочке с настроенным окружением
  local res="$1" stage="$2"; shift 2
  env "$@" bash -c 'source "$1/scripts/lib/common.sh" >/dev/null 2>&1; REPO_ROOT="$2"; DATA_ROOT="$3"; OLD="$4"; TARGET="$5"; UPD_STARTED=100; DRY_RUN=0; upd_history_append "$6" "$7"' _ "$ROOT" "$HR" "$TMP/hist-data" "$HA" "$HB" "$res" "$stage"
}
rm -rf "$TMP/hist-data"; mkdir -p "$TMP/hist-data/updater"
histrun failed 'Сборка "образов"' UPDATE_SOURCE=web UPDATE_BY=root
histrun ok "" UPDATE_SOURCE=cli
HF="$TMP/hist-data/updater/history.ndjson"
t "история: две записи, версии «откуда → куда» и коммиты" bash -c '[ "$(wc -l < "$1")" = 2 ] && head -1 "$1" | grep -q "\"from_version\":\"0.1.3\",\"to_version\":\"0.1.4\"" && head -1 "$1" | grep -q "\"from_commit\":\"${2:0:12}\",\"to_commit\":\"${3:0:12}\""' _ "$HF" "$HA" "$HB"
t "история: источник (веб/терминал), автор, результат; кавычки в этапе не ломают JSON" bash -c 'head -1 "$1" | grep -q "\"source\":\"web\",\"by\":\"root\"" && head -1 "$1" | grep -q "\"result\":\"failed\"" && tail -1 "$1" | grep -q "\"source\":\"cli\"" && tail -1 "$1" | grep -q "\"result\":\"ok\"" && ! head -1 "$1" | grep -q "\\\\\\\\\""' _ "$HF"
t "история: JSON каждой строки корректен" bash -c 'for p in python3 python py; do command -v $p >/dev/null 2>&1 && $p -c "import json" 2>/dev/null && { while IFS= read -r l; do printf "%s" "$l" | $p -c "import json,sys; json.loads(sys.stdin.read())" || exit 1; done < "$1"; exit 0; }; done; exit 0' _ "$HF"
for _i in $(seq 1 105); do histrun ok "" UPDATE_SOURCE=cli; done
t "история: хранятся последние 100 попыток" bash -c '[ "$(wc -l < "$1")" = 100 ]' _ "$HF"
t "история: dry-run ничего не пишет" bash -c 'before="$(wc -l < "$2")"; source "$1/scripts/lib/common.sh" >/dev/null 2>&1; REPO_ROOT="$3"; DATA_ROOT="$4"; DRY_RUN=1; upd_history_append ok ""; [ "$(wc -l < "$2")" = "$before" ]' _ "$ROOT" "$HF" "$HR" "$TMP/hist-data"
t "update.sh: пишет историю при остановке и при успехе; updater.sh передаёт источник «веб»" bash -c 'grep -c "upd_history_append" "$1/scripts/update.sh" | grep -q 3 && grep -q "UPDATE_SOURCE=web" "$1/scripts/updater.sh"' _ "$ROOT"
