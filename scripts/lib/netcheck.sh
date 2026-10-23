# shellcheck shell=bash
# Проверка внешних источников сборки ДО длительной сборки — из той среды, где она выполняется (базовые образы сборки, сеть шагов RUN — мост Docker).
# Проверяется загрузка реального объёма данных (scripts/netprobe.py), а не только DNS/TCP/код ответа: обрыв или «застревание» передачи большого
# ответа обычной проверкой «интернет есть» не видно. Системные DNS, маршруты, MTU, прокси и правила безопасности НЕ меняются; единственная
# автоматическая настройка — сеть шагов сборки (BUILD_NETWORK=host), если источник доступен из сети хоста, а из моста Docker — нет.
# Подключается из common.sh. Результат: NETCHECK_STATUS ok|degraded|fail, NETCHECK_LINES (строки отчёта), NETCHECK_BLOCKING (компоненты).

_nc_arg() { # _nc_arg ФАЙЛ_В_КОММИТЕ ИМЯ_ARG COMMIT — значение ARG по умолчанию из Dockerfile целевой версии
  git -C "$REPO_ROOT" show "$3:$1" 2>/dev/null | sed -n "s/^ARG $2=//p" | head -1
}

# Источники компонента в целевой версии: строки «метка|адрес_или_спец»
_nc_sources() { # _nc_sources СЕРВИС COMMIT
  local svc="$1" c="$2" lock gig torch
  case "$svc" in
    web)
      lock="$(git -C "$REPO_ROOT" show "$c:frontend/package-lock.json" 2>/dev/null)"
      printf '%s' "$lock" | python3 -c '
import json,sys
try: pk=json.load(sys.stdin).get("packages",{})
except Exception: pk={}
res=[v["resolved"] for k,v in pk.items() if v.get("resolved","").startswith("http")]
big=[r for r in res if any(x in r for x in ("/@esbuild/linux-x64","/typescript/-/","/@rollup/rollup-linux-x64-gnu","/livekit-client/-/"))]
for r in (big or res)[:3]: print("npm|"+r)' 2>/dev/null
      echo "github|https://github.com/jgraph/drawio.git/info/refs?service=git-upload-pack" ;;
    backend)
      git -C "$REPO_ROOT" show "$c:backend/constraints.txt" 2>/dev/null | grep -E '^(sqlalchemy|pydantic-core|uvloop)==' | head -2 | sed 's/^/pypi|pypi:/'
      echo "apt|apt:" ;;
    asr)
      torch="${ASR_TORCH_INDEX_URL:-$(_nc_arg asr-service/Dockerfile TORCH_INDEX_URL "$c")}"
      [ -n "$torch" ] && echo "torch|${torch%/}/torch/"
      git -C "$REPO_ROOT" show "$c:asr-service/constraints.txt" 2>/dev/null | grep -E '^(numpy|onnxruntime)==' | head -1 | sed 's/^/pypi|pypi:/'
      gig="${GIGAAM_GIT_COMMIT:-}"; [ -n "$gig" ] && echo "github|https://codeload.github.com/salute-developers/GigaAM/tar.gz/$gig" ;;
  esac
}

# Базовый образ, в котором выполняется проверка компонента (тот же, где выполняются шаги RUN)
_nc_image() { # _nc_image СЕРВИС COMMIT
  case "$1" in
    web) _nc_arg frontend/Dockerfile NODE_IMAGE "$2" ;;
    backend) _nc_arg backend/Dockerfile PYTHON_IMAGE "$2" ;;
    asr) _nc_arg asr-service/Dockerfile PYTHON_IMAGE "$2" ;;
  esac
}

_nc_pull() { # базовый образ: уже есть — ничего; нет — загрузка (до 3 попыток); его отсутствие — тоже недоступный источник (Docker Hub)
  local img="$1" i
  docker image inspect "$img" >/dev/null 2>&1 && return 0
  [ "${DRY_RUN:-0}" = 1 ] && return 2                          # пробный прогон ничего не загружает
  for i in 1 2 3; do docker pull -q "$img" >/dev/null 2>&1 && return 0; sleep $((i * 5)); done
  return 1
}

# Прогон пробника в образе: _nc_run ОБРАЗ СЕТЬ адрес… → JSON-строки. Python-образ — netprobe.py; node-образ — node (как npm) и python-заменитель недоступен,
# поэтому для npm используется сам Node.js с теми же NODE_OPTIONS, что в frontend/Dockerfile (поведение выбора IPv4/IPv6 — как у npm).
_nc_run() {
  local img="$1" net="$2"; shift 2
  local penv=() k
  for k in HTTPS_PROXY HTTP_PROXY NO_PROXY https_proxy http_proxy no_proxy; do [ -n "${!k:-}" ] && penv+=(-e "$k=${!k}"); done
  if [[ "$img" == node:* ]]; then
    local nopt; nopt="$(git -C "$REPO_ROOT" show "${NETCHECK_COMMIT:-HEAD}:frontend/Dockerfile" 2>/dev/null | sed -n 's/^ENV NODE_OPTIONS="\(.*\)"/\1/p' | head -1)"
    timeout 300 docker run --rm --network "$net" "${penv[@]}" -e NODE_OPTIONS="$nopt" --entrypoint node "$img" -e "$NETCHECK_NODE_JS" "$@" 2>/dev/null
  else
    timeout 300 docker run --rm --network "$net" "${penv[@]}" -v "$REPO_ROOT/scripts/netprobe.py:/netprobe.py:ro" --entrypoint python3 "$img" /netprobe.py "$@" 2>/dev/null
  fi
}

# Загрузка как у npm (через Node.js): первые 4 МБ, пауза без данных 20 с — остановка; итог в формате netprobe.py
NETCHECK_NODE_JS='
const urls=process.argv.slice(1);
(async()=>{let bad=0;for(const u of urls){const t0=Date.now();let got=0,last=Date.now();const ac=new AbortController();
const iv=setInterval(()=>{if(Date.now()-last>20000||Date.now()-t0>60000)ac.abort()},500);
try{const r=await fetch(u,{headers:{Range:"bytes=0-4194303","User-Agent":"peregovorka-netprobe/1"},signal:ac.signal});
if(r.status>=400){console.log(JSON.stringify({url:u,result:"fail",stage:"http",status:r.status,detail:"HTTP "+r.status}));bad++;continue}
const rd=r.body.getReader();for(;;){const {done,value}=await rd.read();if(done)break;got+=value.length;last=Date.now();if(got>=4194304)break}
const s=(Date.now()-t0)/1000;console.log(JSON.stringify({url:u,result:"ok",stage:"none",status:r.status,bytes:got,seconds:s,rate_kbps:Math.round(got/1024/s),detail:Math.round(got/1024)+" КБ за "+s.toFixed(2)+" с (Node.js, как npm)"}))}
catch(e){const c=(e.cause&&(e.cause.code||e.cause.name))||e.name;const stage=/ENOTFOUND|EAI_AGAIN/.test(c)?"dns":/CERT|SSL|TLS/.test(c)?"tls":(got>0||e.name==="AbortError")?"stall":"tcp";
console.log(JSON.stringify({url:u,result:"fail",stage,bytes:got,detail:"Node.js (как npm): "+c+" "+String((e.cause&&e.cause.message)||e.message).slice(0,160)+" — получено "+Math.round(got/1024)+" КБ"}));bad++}
finally{clearInterval(iv)}}process.exitCode=bad?1:0})();'

_nc_field() { printf '%s' "$1" | python3 -c "import json,sys; d=json.loads(sys.stdin.read() or '{}'); print(d.get('$2',''))" 2>/dev/null; }

# netcheck_build_sources COMMIT [СЕРВИС…] — проверка источников для сборки компонентов (по умолчанию web backend asr).
# Компонент блокирует обновление, только если его источник недоступен И зависимости компонента изменились относительно работающей версии
# (OLD) — иначе сборка возьмёт слои из кэша Docker, и недоступность — предупреждение.
netcheck_build_sources() {
  local c="$1"; shift
  local svcs=("$@") s img line label spec out url res stage detail okhost changed
  [ ${#svcs[@]} -gt 0 ] || svcs=(web backend asr)
  NETCHECK_STATUS=ok; NETCHECK_LINES=(); NETCHECK_BLOCKING=(); NETCHECK_COMMIT="$c"
  command -v docker >/dev/null 2>&1 || { NETCHECK_LINES+=("пропущено: Docker недоступен"); return 0; }
  for s in "${svcs[@]}"; do
    img="$(_nc_image "$s" "$c")"; [ -n "$img" ] || continue
    _nc_pull "$img"; local prc=$?
    if [ "$prc" -eq 2 ]; then NETCHECK_LINES+=("SKIP $s: базового образа $img нет локально, пробный прогон его не загружает — источники не проверены"); continue; fi
    if [ "$prc" -ne 0 ]; then
      NETCHECK_LINES+=("FAIL $s: базовый образ $img не загружается из реестра Docker (Docker Hub/зеркало реестра недоступны с этого сервера)")
      NETCHECK_STATUS=fail; NETCHECK_BLOCKING+=("$s"); continue
    fi
    changed=1
    [ -n "${OLD:-}" ] && git -C "$REPO_ROOT" diff --quiet "$OLD" "$c" -- "$(svc_context "$s")/Dockerfile" "$(svc_context "$s")/package-lock.json" \
      "$(svc_context "$s")/requirements.txt" "$(svc_context "$s")/constraints.txt" 2>/dev/null && changed=0
    while IFS='|' read -r label spec; do
      [ -n "$spec" ] || continue
      out="$(_nc_run "$img" default "$spec" | tail -1)"
      res="$(_nc_field "$out" result)"; stage="$(_nc_field "$out" stage)"; detail="$(_nc_field "$out" detail)"; url="$(_nc_field "$out" url)"
      if [ -z "$res" ]; then         # пробник не запустился — это не вывод о сети; сборка покажет сама
        NETCHECK_LINES+=("SKIP $s/$label: проверку выполнить не удалось (docker run в образе $img) — источник не проверен"); continue
      fi
      case "$res" in
        ok) NETCHECK_LINES+=("OK   $s/$label: ${url%%\?*} — $detail") ;;
        slow) NETCHECK_LINES+=("WARN $s/$label: ${url%%\?*} — $detail"); [ "$NETCHECK_STATUS" = ok ] && NETCHECK_STATUS=degraded ;;
        *)
          okhost=""
          out="$(_nc_run "$img" host "$spec" | tail -1)"
          [ "$(_nc_field "$out" result)" = ok ] && okhost=1
          if [ -n "$okhost" ]; then
            NETCHECK_LINES+=("WARN $s/$label: из сети сборки (мост Docker) — $stage: $detail; из сети хоста — доступно. Сборка пойдёт в сети хоста (BUILD_NETWORK=host)")
            export BUILD_NETWORK=host; [ "$NETCHECK_STATUS" = ok ] && NETCHECK_STATUS=degraded
          elif [ "$changed" -eq 0 ]; then
            NETCHECK_LINES+=("WARN $s/$label: ${url%%\?*} недоступен ($stage: $detail), но зависимости компонента не менялись — сборка возьмёт их из кэша Docker")
            [ "$NETCHECK_STATUS" = ok ] && NETCHECK_STATUS=degraded
          else
            NETCHECK_LINES+=("FAIL $s/$label: ${url%%\?*} — этап «$(_nc_stage_ru "$stage")»: $detail")
            NETCHECK_STATUS=fail; NETCHECK_BLOCKING+=("$s")
          fi ;;
      esac
    done < <(_nc_sources "$s" "$c")
  done
  [ "$NETCHECK_STATUS" = fail ] && return 1
  return 0
}

_nc_stage_ru() {
  case "$1" in dns) echo "DNS (имя не разрешается)" ;; tcp) echo "TCP (нет соединения)" ;; proxy) echo "прокси" ;; tls) echo "TLS" ;;
    http) echo "ответ репозитория" ;; stall) echo "передача данных (останавливается или обрывается)" ;; *) echo "${1:-?}" ;; esac
}

# Рекомендация администратору по типу сбоя (без изменения системных настроек)
netcheck_advice() {
  local l all="${NETCHECK_LINES[*]}"
  case "$all" in *"передача данных"*) echo "Передача больших ответов останавливается, хотя соединение устанавливается: так бывает при ограничении скорости, DPI или потере крупных пакетов (MTU) на пути. Проверьте прокси/межсетевой экран организации для указанных адресов или задайте прокси сборки (HTTPS_PROXY в окружении запуска обновления)." ;; esac
  case "$all" in *"DNS"*) echo "Имя репозитория не разрешается из контейнера: проверьте DNS-серверы Docker (/etc/docker/daemon.json → dns) или разрешение имён на сервере." ;; esac
  case "$all" in *"этап «прокси»"*) echo "Прокси отказывает: проверьте адрес и учётные данные HTTPS_PROXY." ;; esac
  case "$all" in *"этап «TLS»"*) echo "Сертификат репозитория не принимается: в сети, вероятно, прокси с подменой сертификатов — нужен корневой сертификат организации в образах сборки." ;; esac
  case "$all" in *"реестра Docker"*) echo "Базовые образы не загружаются: проверьте доступ сервера к Docker Hub (или настройте зеркало реестра в Docker)." ;; esac
}
