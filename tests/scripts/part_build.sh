# shellcheck shell=bash
# Подключается из run.sh: надёжность сборки при обновлении — классификация сбоев, ограниченные повторы с повторной проверкой источников,
# переход сборки в сеть хоста, повтор после сбоя последнего компонента без пересборки готовых, проверка источников по компонентам.
# Docker, сборка и сеть подменены функциями; настоящие сборки не запускаются. Используются: ROOT, TMP, t
BD="$TMP/build"; mkdir -p "$BD"; export BD

# ---------------------------------------------------------------------------------- классификация сбоев сборки
cbe() { bash -c 'source "$1/scripts/lib/dockerlib.sh" 2>/dev/null; printf "%s\n" "$2" | classify_build_error' _ "$ROOT" "$1"; }
export -f cbe
t "сборка: npm ETIMEDOUT внутри RUN — сетевой сбой (повторяется), а не ошибка проекта, хотя BuildKit пишет «did not complete successfully»" \
  eq "$(cbe $'npm error code ETIMEDOUT\nnpm error network request to https://registry.npmjs.org/esbuild failed, reason: connect ETIMEDOUT 192.0.2.10:443\nprocess "/bin/sh -c npm ci" did not complete successfully: exit code: 1')" network
t "сборка: сетевые сбои pip, git и apt — сетевые" bash -c '[ "$(cbe "pip._vendor.urllib3.exceptions.ReadTimeoutError: HTTPSConnectionPool(host=pypi.org)")" = network ] &&
  [ "$(cbe "fatal: unable to access https://github.com/x/y/: Could not resolve host: github.com")" = network ] && [ "$(cbe "Temporary failure resolving deb.debian.org")" = network ]'
t "сборка: пакет не найден (npm E404) и ошибка Dockerfile — ошибка проекта, без бесполезных повторов" bash -c '[ "$(cbe $'"'"'npm error code E404\nprocess "/bin/sh -c npm ci" did not complete successfully: exit code: 1'"'"')" = project ] && [ "$(cbe "dockerfile parse error")" = project ]'
t "сборка: отказ BuildKit в разрешении network.host — отдельный класс (не сеть и не ошибка проекта), повторять ту же сборку бессмысленно" \
  eq "$(cbe $'ERROR: additional privileges requested\npass "--allow=network.host" to grant requested privileges')" privileges

# ---------------------------------------------------------------------------------- способ сборки в сети хоста (проба тем же путём, что и сборка)
# hbm СЦЕНАРИЙ [SELECTED_BUILD_MODE] → «режим|вызовы проб|причина». _hostprobe_compose подменён: настоящих сборок нет.
cat > "$BD/hbm.sh" <<'EOF'
source "$ROOT/scripts/lib/common.sh"
SC="$1"; SELECTED_BUILD_MODE="${2:-buildkit}"; HOST_BUILD_MODE=""; : > "$BD/hbm.calls"; unset DRY_RUN
docker() { :; }
_hostprobe_compose() { # РЕЖИМ ФАЙЛ ПРОЕКТ
  echo "$1" >> "$BD/hbm.calls"
  case "$1:$SC" in
    buildkit:ok|legacy:ok|legacy:priv) return 0 ;;
    buildkit:priv|buildkit:none) echo 'ERROR: additional privileges requested'; echo 'pass "--allow=network.host" to grant requested privileges'; return 1 ;;
    legacy:none) echo 'legacy builder is not supported by this Docker'; return 1 ;;
  esac
}
select_host_build_mode >/dev/null 2>&1; rc=$?
printf '%s|%s|%s|%s' "$HOST_BUILD_MODE" "$(tr '\n' ',' < "$BD/hbm.calls")" "$rc" "$HOST_BUILD_WHY"
EOF
hbm() { bash "$BD/hbm.sh" "$@"; }
export -f hbm
t "сеть хоста: BuildKit разрешает network.host — собирается BuildKit'ом, классический builder не нужен" eq "$(hbm ok)" "buildkit|buildkit,|0|"
t "сеть хоста: BuildKit требует «--allow=network.host» — шаги в сети хоста собирает классический builder (ошибки разрешений нет)" eq "$(hbm priv)" "legacy|buildkit,legacy,|0|"
t "сеть хоста: заданный BUILD_MODE=legacy — BuildKit не пробуется" eq "$(hbm priv legacy)" "legacy|legacy,|0|"
t "сеть хоста: нет ни разрешения BuildKit, ни классического builder'а — «none», причина называет network.host (а не общий сбой)" bash -c 'r="$(hbm none)"; [[ "$r" == none\|buildkit,legacy,\|1\|* ]] && [[ "$r" == *network.host* ]] && [[ "$r" == *"классический builder недоступен"* ]]'

# ---------------------------------------------------------------------------------- build_images с подменённой сборкой
# bld СЦЕНАРИЙ КАТАЛОГ_ДАННЫХ → «код|сбойный сервис|сборки сервис@сеть:число|BUILD_NETWORK»; вывод сборки — $BD/СЦЕНАРИЙ.out
cat > "$BD/bld.sh" <<'EOF'
source "$ROOT/scripts/lib/common.sh"
SC="$1"; DATA_ROOT="$BD/$2"; mkdir -p "$DATA_ROOT/state"; COMPOSE_PROJECT_NAME=pgb; IMAGE_TAG=abc123abc123; PULL_RETRY_PAUSE=0; SELECTED_BUILD_MODE=buildkit
unset BUILD_NETWORK; : > "$BD/$SC.calls"; : > "$BD/$SC.modes"; HOST_BUILD_MODE=""
case "$SC" in hb_*) export BUILD_NETWORK=host ;; esac      # сборка уже переведена в сеть хоста (проверкой источников)
probe_host_build() { # РЕЖИМ — пробы: hb_leg/hb_none — BuildKit отказывает в network.host; hb_none — и классический builder недоступен
  PROBE_TEXT=ok
  case "$1:$SC" in
    buildkit:hb_leg|buildkit:hb_none) PROBE_TEXT='additional privileges requested: pass "--allow=network.host" to grant requested privileges'; return 1 ;;
    legacy:hb_none) PROBE_TEXT='legacy builder недоступен'; return 1 ;;
  esac; return 0; }
docker() { [ "$1 $2" = "image inspect" ] && [ -e "$DATA_ROOT/img-$3" ]; }
src_fingerprint() { echo "fp-$1"; }
_compose_build_one() { # СЕРВИС РЕЖИМ ЛОГ
  echo "$1@$BUILD_NETWORK" >> "$BD/$SC.calls"; echo "$1:$2:$BUILD_NETWORK" >> "$BD/$SC.modes"
  local fail=""
  # hb_priv: проба прошла, но сама сборка получила отказ BuildKit в network.host
  if [ "$SC" = hb_priv ] && [ "$BUILD_NETWORK" = host ] && [ "$2" = buildkit ]; then printf 'ERROR: additional privileges requested\npass "--allow=network.host" to grant requested privileges\n' > "$3"; return 1; fi
  case "$SC:$1" in
    webdown:web|bounded:web) fail=1 ;;
    transient:web) [ "$(grep -c '^web' "$BD/$SC.calls")" -lt 2 ] && fail=1 ;;
    hostonly:web) [ "$BUILD_NETWORK" = host ] || fail=1 ;;
  esac
  if [ -n "$fail" ]; then printf 'npm error code ETIMEDOUT\nprocess "/bin/sh -c npm ci" did not complete successfully: exit code: 1\n' > "$3"; return 1; fi
  touch "$DATA_ROOT/img-$(svc_image "$1")"
}
netcheck_build_sources() { NETCHECK_LINES=()
  case "$SC" in webdown) NETCHECK_LINES=("FAIL web/npm: https://registry.npmjs.org/esbuild — этап «передача данных»"); return 1 ;; hostonly) export BUILD_NETWORK=host ;; esac; return 0; }
build_images > "$BD/$SC.out" 2>&1; rc=$?
printf '%s|%s|%s|%s' "$rc" "${BUILD_FAILED_SVC:-}" "$(sort "$BD/$SC.calls" | uniq -c | awk '{printf "%s:%s ", $2, $1}' | sed 's/ $//')" "$BUILD_NETWORK"
EOF
bld() { bash "$BD/bld.sh" "$@"; }
export -f bld

R1="$(bld webdown d1)"
t "сбой последнего компонента (web: npm недоступен и при повторной проверке): остановка без бесполезных повторов, готовые компоненты собраны" \
  bash -c '[ "$1" = "1|web|asr@default:1 backend@default:1 livekit@default:1 web@default:1|default" ] && grep -q "повторы бесполезны" "$2/webdown.out" && grep -q "registry.npmjs.org" "$2/webdown.out"' _ "$R1" "$BD"
t "сбой последнего компонента: состояние сборки по компонентам записано (для отчёта)" bash -c 'f="$1/d1/state/last-update.state"; grep -q "^build_backend=собран" "$f" && grep -q "^build_web=ОШИБКА (network)" "$f"' _ "$BD"
R2="$(bld ok d1)"
t "повтор после сбоя последнего компонента: собирается только web, готовые образы livekit/backend/asr используются без пересборки" \
  bash -c '[ "$1" = "0||web@default:1|default" ] && grep -q "^build_backend=готов, использован без пересборки" "$2/d1/state/last-update.state"' _ "$R2" "$BD"
t "временный сетевой сбой: источник доступен при повторной проверке — повтор и успех со 2-й попытки" eq "$(bld transient d2)" "0||asr@default:1 backend@default:1 livekit@default:1 web@default:2|default"
t "повторы ограничены (BUILD_ATTEMPTS=3): после 3 сетевых сбоев — остановка с названием компонента" bash -c 'r="$(bld bounded d3)"; [ "$r" = "1|web|asr@default:1 backend@default:1 livekit@default:1 web@default:3|default" ] && grep -q "FAIL stage=build service=web subsystem=registry-network" "$1/bounded.out"' _ "$BD"
t "источник доступен только из сети хоста: повтор сборки в сети хоста (системные настройки не меняются)" bash -c 'r="$(bld hostonly d4)"; [ "$r" = "0||asr@default:1 backend@default:1 livekit@default:1 web@default:1 web@host:1|host" ] && grep -q "BUILD_NETWORK=host" "$1/hostonly.out"' _ "$BD"

# сборка в сети хоста: BuildKit разрешает / не разрешает network.host / не разрешает и нет классического builder'а
t "сборка в сети хоста: BuildKit разрешает network.host — все компоненты собраны BuildKit'ом в сети хоста" \
  bash -c 'r="$(bld hb_bk h1)"; [ "$r" = "0||asr@host:1 backend@host:1 livekit@host:1 web@host:1|host" ] && ! grep -qv ":buildkit:host$" "$1/hb_bk.modes"' _ "$BD"
t "сборка в сети хоста: BuildKit требует «--allow=network.host» — сборка идёт классическим builder'ом, без ошибки разрешений" \
  bash -c 'r="$(bld hb_leg h2)"; [ "$r" = "0||asr@host:1 backend@host:1 livekit@host:1 web@host:1|host" ] && ! grep -qv ":legacy:host$" "$1/hb_leg.modes" && grep -q "FALLBACK" "$1/hb_leg.out" && ! grep -q "additional privileges" "$1/hb_leg.out"' _ "$BD"
t "сборка в сети хоста: проба прошла, но сама сборка получила отказ BuildKit — повтор классическим builder'ом, остальные компоненты тоже" \
  bash -c 'r="$(bld hb_priv h3)"; [ "$r" = "0||asr@host:1 backend@host:1 livekit@host:2 web@host:1|host" ] && [ "$(sed -n 1p "$1/hb_priv.modes")" = "livekit:buildkit:host" ] && [ "$(sed -n 2p "$1/hb_priv.modes")" = "livekit:legacy:host" ] && ! grep -q "buildkit" <(sed -n "3,\$p" "$1/hb_priv.modes") && grep -q "классическим builder" "$1/hb_priv.out"' _ "$BD"
t "сборка в сети хоста: нет ни разрешения, ни классического builder'а — остановка до сборки с конкретной причиной и рекомендацией, без бесполезных повторов" \
  bash -c 'r="$(bld hb_none h4)"; [ "${r%%|*}" = 1 ] && [ ! -s "$1/hb_none.modes" ] && grep -q "subsystem=build-network-host" "$1/hb_none.out" && grep -q "network.host" "$1/hb_none.out" && grep -q "allow-insecure-entitlement" "$1/hb_none.out" && grep -q "^build_livekit=ОШИБКА (privileges)" "$1/h4/state/last-update.state"' _ "$BD"

# ---------------------------------------------------------------------------------- проверка источников по компонентам
# ncs СЦЕНАРИЙ → «статус|блокирующие|BUILD_NETWORK» и строки проверки. npm из сети сборки: ok | fail | только из сети хоста; pypi всегда доступен.
cat > "$BD/ncs.sh" <<'EOF'
source "$ROOT/scripts/lib/common.sh"
SC="$1"; unset BUILD_NETWORK OLD; NETCHECK_RETRY_PAUSE=0; : > "$BD/ncs.bridge"
docker() { :; }
select_host_build_mode() { [ "$SC" != hostnosup ] || { HOST_BUILD_WHY="BuildKit не выдаёт разрешение network.host, классический builder недоступен"; return 1; }; }
_nc_image() { echo "img-$1"; }
_nc_pull() { return 0; }
_nc_sources() { case "$1" in web) echo "npm|npm:esbuild" ;; backend) echo "pypi|pypi:fastapi" ;; esac; }
_nc_run() { # ОБРАЗ СЕТЬ СПЕЦ
  [ "$2" = default ] && [ "$3" = npm:esbuild ] && echo x >> "$BD/ncs.bridge"
  case "$3:$SC:$2" in
    npm:*:flaky:default) [ "$(wc -l < "$BD/ncs.bridge")" -ge "${FLAKY_OK_AT:-2}" ] && echo '{"result":"ok","stage":"","detail":"4 МБ за 1.1 с","url":"https://registry.npmjs.org/esbuild"}' || echo '{"result":"fail","stage":"stall","detail":"тайм-аут","url":"https://registry.npmjs.org/esbuild"}' ;;
    npm:*:flaky:host) echo '{"result":"ok","stage":"","detail":"4 МБ","url":"https://example.org/x"}' ;;
    npm:*:ok:*|npm:*:hostonly:host|npm:*:hostnosup:host|pypi:*) echo '{"result":"ok","stage":"","detail":"4 МБ за 0.4 с","url":"https://example.org/x"}' ;;
    npm:*) echo '{"result":"fail","stage":"stall","detail":"189 КБ за 30 с, передача остановилась","url":"https://registry.npmjs.org/esbuild"}' ;;
  esac
}
netcheck_build_sources HEAD web backend; printf '%s|%s|%s\n' "$NETCHECK_STATUS" "${NETCHECK_BLOCKING[*]}" "${BUILD_NETWORK:-default}"; printf '%s\n' "${NETCHECK_LINES[@]}"; netcheck_advice
EOF
ncs() { bash "$BD/ncs.sh" "$@"; }
export -f ncs
t "источники сборки: всё доступно — ok, без блокировок" bash -c '[ "$(ncs ok | head -1)" = "ok||default" ]'
t "источники сборки: npm недоступен (передача останавливается), PyPI доступен — блокирует только web, этап назван, совет без смены системных настроек" \
  bash -c 'r="$(ncs down)"; [ "$(head -1 <<<"$r")" = "fail|web|default" ] && grep -q "^FAIL web/npm: https://registry.npmjs.org/esbuild — этап «передача данных" <<<"$r" && grep -q "^OK   backend/pypi" <<<"$r" && grep -q "прокси сборки" <<<"$r"'
t "источники сборки: npm доступен только из сети хоста — не блокирует, сборка пойдёт в сети хоста" bash -c 'r="$(ncs hostonly)"; [ "$(head -1 <<<"$r")" = "degraded||host" ] && grep -q "из сети хоста — доступно" <<<"$r"'
t "источники сборки: единичный тайм-аут моста — повторная проверка проходит, сеть хоста НЕ выбирается" bash -c 'r="$(ncs flaky)"; [ "$(head -1 <<<"$r")" = "degraded||default" ] && grep -q "повторная №1 прошла" <<<"$r" && grep -q "временный сбой" <<<"$r"'
t "источники сборки: мост не отвечает и при повторных проверках, сеть хоста работает — тогда (и только тогда) сборка идёт в сети хоста" bash -c 'r="$(FLAKY_OK_AT=99 ncs flaky)"; [ "$(head -1 <<<"$r")" = "degraded||host" ] && grep -q "не прошли и 2 повторные" <<<"$r"'
t "источники сборки: сеть хоста доступна, но BuildKit не разрешает в ней собирать — не «сеть хоста» и не общий сбой, а конкретная причина (блокирует web)" bash -c 'r="$(ncs hostnosup)"; [ "$(head -1 <<<"$r")" = "fail|web|default" ] && grep -q "собрать в сети хоста нельзя: BuildKit не выдаёт разрешение network.host" <<<"$r"'
t "netprobe: пробник источников (DNS/TCP/прокси/TLS/HTTP/остановка передачи) — собственные тесты проходят" python3 -I "$ROOT/tests/scripts/netprobe_test.py"
