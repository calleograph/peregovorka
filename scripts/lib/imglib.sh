#!/usr/bin/env bash
# imglib.sh — загрузка и проверка Docker-образов дополнительных компонентов (Egress, SIP, локальная LLM): одна общая логика вместо `docker pull … >/dev/null`.
#
# Что делает img_ensure КЛЮЧ ПЕРЕМЕННАЯ ОБРАЗ_ПО_УМОЛЧАНИЮ [soft]:
#   1. Образ уже есть на сервере (под основным именем или под именем зеркала, загруженным раньше) — сеть не трогается.
#   2. Иначе — до IMAGE_PULL_ATTEMPTS попыток загрузки основным источником с паузами; причина каждого отказа определяется по тексту docker pull
#      (DNS, соединение, таймаут, TLS, авторизация, лимит, нет образа, контрольная сумма, диск, служба Docker) и показывается короткой фразой.
#   3. Основной источник не отвечает, а ссылка закреплена дайджестом — по очереди пробуются РАЗРЕШЁННЫЕ зеркала (IMAGE_MIRRORS; по умолчанию mirror.gcr.io
#      для Docker Hub): сначала проверка манифеста по тому же дайджесту, затем загрузка. Подмены тега или версии нет: зеркало отдаёт тот же дайджест
#      индекса OCI (его и проверяет Docker при загрузке по `@sha256:…`), мы дополнительно сверяем архитектуру и дайджест в RepoDigests.
#   4. Образ из зеркала записывается в .env как ПЕРЕМЕННАЯ=адрес_зеркала@дайджест (compose берёт ровно его), а в state/image-sources — пометка «выбрано
#      автоматически для закреплённой ссылки X». Когда закреплённая ссылка меняется (новая версия), пометка снимается и загрузка снова идёт с основного
#      источника; значение, заданное администратором вручную, не трогается.
#   5. Итог последней неудачи (код, краткая причина, этап, пробованные источники, техническая выдержка без секретов) пишется в state/image-error-КЛЮЧ.json,
#      успех стирает его; помощник обновлений отдаёт это в components.json, а панель показывает причину и «Подробности».
#
# Три разных «идентификатора» не смешиваются: дайджест ИНДЕКСА (OCI index/manifest list) — то, что закреплено в ссылке и что сверяется с RepoDigests;
# дайджест манифеста платформы (amd64) — внутри индекса, отдельно не сверяется; локальный ID образа — дайджест конфигурации, с закреплением не сравнивается.
# Только функции; зависит от common.sh (info/warn/fail) и updatelib.sh (upd_env_set_key).

if [ -n "${_VM_IMGLIB_LOADED:-}" ]; then return 0; fi
_VM_IMGLIB_LOADED=1

IMG_DEFAULT_MIRRORS="mirror.gcr.io"

_img_state_dir() { printf '%s' "${DATA_ROOT:-/tmp}/state"; }

# ---- разбор ссылки: IMG_HOST IMG_REPO IMG_TAG IMG_DIGEST
img_parse() {
  local ref="$1" rest first
  IMG_DIGEST=""; IMG_TAG=""; IMG_HOST="docker.io"; IMG_REPO=""
  case "$ref" in *@sha256:*) IMG_DIGEST="sha256:${ref##*@sha256:}"; ref="${ref%%@sha256:*}" ;; esac
  first="${ref%%/*}"
  if [ "$first" != "$ref" ] && { [[ "$first" == *.* ]] || [[ "$first" == *:* ]] || [ "$first" = localhost ]; }; then IMG_HOST="$first"; rest="${ref#*/}"; else rest="$ref"; fi
  case "${rest##*/}" in *:*) IMG_TAG="${rest##*:}"; rest="${rest%:*}" ;; esac
  [ "$IMG_HOST" = docker.io ] && [[ "$rest" != */* ]] && rest="library/$rest"
  IMG_REPO="$rest"
}

# ---- разрешённые зеркала для ссылки: по строке на кандидата (только при закреплении дайджестом: подмена версии исключена)
# IMAGE_MIRRORS="mirror.gcr.io registry.example.org/hub ghcr.io=registry.example.org/ghcr"; «none» — зеркала выключены.
# Запись без «=» — зеркало Docker Hub; «ИСТОЧНИК=ПРЕФИКС» — для образов указанного реестра.
img_mirror_refs() {
  local ref="$1" spec entry src pfx
  img_parse "$ref"
  [ -n "$IMG_DIGEST" ] || return 0
  spec="${IMAGE_MIRRORS-$IMG_DEFAULT_MIRRORS}"
  [ "$spec" = none ] && return 0
  for entry in ${spec//,/ }; do
    case "$entry" in *=*) src="${entry%%=*}"; pfx="${entry#*=}" ;; *) src="docker.io"; pfx="$entry" ;; esac
    [ "$src" = "$IMG_HOST" ] || continue
    pfx="${pfx%/}"
    [ "$pfx" = "$IMG_HOST" ] && continue
    printf '%s/%s@%s\n' "$pfx" "$IMG_REPO" "$IMG_DIGEST"
  done
}

# ---- скрыть секреты в тексте (stdin → stdout): токены, подписанные адреса, пароли в адресах и заголовках
img_redact() {
  sed -E \
    -e 's#(Authorization:[[:space:]]*)[^[:space:]]+([[:space:]]+[^[:space:]]+)?#\1***#Ig' \
    -e 's#(Bearer|Basic)[[:space:]]+[A-Za-z0-9._~+/=-]{8,}#\1 ***#g' \
    -e 's#(https?://)[^/@[:space:]]+:[^/@[:space:]]+@#\1***:***@#g' \
    -e 's#((^|[?&;,"[:space:]])(X-Amz-[A-Za-z-]+|Signature|Sig|sig|token|access_token|id_token|password|passwd|secret|key|Expires|se|sv|sp|sr|skoid|AWSAccessKeyId|Policy|Key-Pair-Id)=)[^&"[:space:]]*#\1***#g' \
    -e 's#((password|passwd|secret|token|api[_-]?key)[=:][[:space:]]*)[^[:space:]"]+#\1***#Ig'
}

# ---- причина отказа docker pull по тексту → код
img_classify() {
  local t; t="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$t" in
    *"no space left on device"*|*"disk quota exceeded"*) echo disk ;;
    *toomanyrequests*|*"rate limit"*|*"too many requests"*) echo ratelimit ;;
    *x509*|*"tls: "*|*"certificate"*) echo tls ;;
    *"digest mismatch"*|*"content digest"*"does not match"*|*"failed to verify layer"*|*"unexpected digest"*) echo digest ;;
    *"no matching manifest"*|*"does not provide the specified platform"*|*"platform"*"does not match"*) echo arch ;;
    *"no such host"*|*"temporary failure in name resolution"*|*"server misbehaving"*|*"name resolution"*) echo dns ;;
    *"manifest unknown"*|*"no such manifest"*|*"name unknown"*|*"repository does not exist"*|*"not found"*|*"404"*) echo notfound ;;
    *unauthorized*|*"authentication required"*|*"access to the resource is denied"*|*"denied:"*|*" 401"*|*" 403"*) echo auth ;;
    *"cannot connect to the docker daemon"*|*"is the docker daemon running"*|*containerd*|*"error during connect"*|*"no such file or directory"*"docker.sock"*) echo daemon ;;
    *timeout*|*"timed out"*|*"deadline exceeded"*|*"awaiting response headers"*|*"context canceled"*) echo timeout ;;
    *"connection refused"*|*"connection reset"*|*"network is unreachable"*|*"no route to host"*|*"unexpected eof"*|*"broken pipe"*|*"i/o error"*|*eof*) echo connect ;;
    *) echo unknown ;;
  esac
}

# код → короткое объяснение администратору (без команд и внутренних имён скриптов)
img_summary() {
  case "$1" in
    disk) echo "На сервере не хватает места на диске для образа." ;;
    ratelimit) echo "Реестр временно ограничил число загрузок с этого адреса." ;;
    digest) echo "Контрольная сумма скачанного образа не совпала с ожидаемой: образ отклонён." ;;
    arch) echo "В реестре нет образа для архитектуры этого сервера." ;;
    tls) echo "Не удалось установить защищённое соединение с реестром (сертификат или время на сервере)." ;;
    dns) echo "Сервер не смог найти адрес реестра (DNS)." ;;
    notfound) echo "Реестр не знает такого образа или версии." ;;
    auth) echo "Реестр отказал в доступе к образу (нужна авторизация или доступ закрыт)." ;;
    daemon) echo "Служба Docker (containerd) на сервере не отвечает или вернула ошибку." ;;
    timeout) echo "Реестр доступен, но передача слоёв образа оборвалась по таймауту (медленная сеть, прокси или сбой CDN)." ;;
    connect) echo "Соединение с реестром оборвано или недоступно." ;;
    *) echo "Не удалось скачать образ по неопознанной причине (см. подробности)." ;;
  esac
}
# повтор имеет смысл только для сетевых и временных причин
img_retryable() { case "$1" in timeout|connect|dns|ratelimit|unknown) return 0 ;; *) return 1 ;; esac; }
# переход на зеркало — когда дело в источнике, а не в самом сервере или образе
img_mirror_allowed() { case "$1" in disk|daemon|digest|arch) return 1 ;; *) return 0 ;; esac; }

# ---- проверка локального образа: архитектура сервера и дайджест индекса в RepoDigests (или в дескрипторе при хранилище containerd)
img_verify() { # img_verify ССЫЛКА ДАЙДЖЕСТ → 0; при отказе IMG_VERIFY_WHY
  local ref="$1" want="$2" info arch sarch
  IMG_VERIFY_WHY=""
  info="$(docker image inspect -f '{{.Architecture}}|{{json .RepoDigests}}|{{if .Descriptor}}{{.Descriptor.digest}}{{end}}' "$ref" 2>/dev/null)" || { IMG_VERIFY_WHY="образ не найден после загрузки"; return 1; }
  arch="${info%%|*}"
  sarch="$(docker version -f '{{.Server.Arch}}' 2>/dev/null)"; [ -n "$sarch" ] || sarch="$(uname -m)"
  case "$sarch" in x86_64) sarch=amd64 ;; aarch64) sarch=arm64 ;; esac
  if [ -n "$arch" ] && [ "$arch" != "$sarch" ]; then IMG_VERIFY_WHY="архитектура образа $arch, сервера $sarch"; return 1; fi
  [ -n "$want" ] || return 0
  case "$info" in *"$want"*) return 0 ;; esac
  IMG_VERIFY_WHY="дайджест $want не найден у загруженного образа"
  return 1
}

# ---- запись состояния
_img_state_file() { printf '%s/image-sources' "$(_img_state_dir)"; }
_img_err_file() { printf '%s/image-error-%s.json' "$(_img_state_dir)" "$1"; }
_img_jesc() { printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/\t/\\t/g' | awk 'BEGIN{ORS="\\n"} {print}' | sed 's/\\n$//'; }

img_state_get() { # КЛЮЧ → "ПЕРЕМЕННАЯ<TAB>закреплённая<TAB>фактическая"
  [ -f "$(_img_state_file)" ] && awk -F'\t' -v k="$1" '$1 == k {print $2 "\t" $3 "\t" $4; exit}' "$(_img_state_file)"
}
img_state_set() { # КЛЮЧ ПЕРЕМЕННАЯ ЗАКРЕПЛЁННАЯ ФАКТИЧЕСКАЯ
  local f; f="$(_img_state_file)"; mkdir -p "$(dirname "$f")" 2>/dev/null || return 0
  { grep -v -P "^$1\t" "$f" 2>/dev/null || true; printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$(date +%F)"; } > "$f.tmp" && mv "$f.tmp" "$f"
}
img_state_del() { local f; f="$(_img_state_file)"; [ -f "$f" ] || return 0; { grep -v -P "^$1\t" "$f" || true; } > "$f.tmp" && mv "$f.tmp" "$f"; }

img_error_write() { # КЛЮЧ КОД ЭТАП ССЫЛКА ПРОБОВАЛИ ПОДРОБНОСТИ
  local f; f="$(_img_err_file "$1")"; mkdir -p "$(dirname "$f")" 2>/dev/null || return 0
  printf '{"ts":%s,"code":"%s","summary":"%s","stage":"%s","image":"%s","tried":"%s","details":"%s"}\n' "$(date +%s)" "$2" "$(_img_jesc "$(img_summary "$2")")" "$(_img_jesc "$3")" \
    "$(_img_jesc "$(printf '%s' "$4" | img_redact)")" "$(_img_jesc "$(printf '%s' "$5" | img_redact)")" "$(_img_jesc "$(printf '%s' "$6" | img_redact | tail -c 3000)")" > "$f"
}
img_error_clear() { rm -f "$(_img_err_file "$1")" 2>/dev/null || true; }
img_error_json() { local f; f="$(_img_err_file "$1")"; if [ -s "$f" ]; then cat "$f"; else printf 'null'; fi; }

# ---- «значение переменной образа задано автоматически (зеркало)»: есть пометка с тем же значением
img_auto_value() { # КЛЮЧ ПЕРЕМЕННАЯ → 0, если текущее значение выбрано нами
  local st cur pinned actual
  st="$(img_state_get "$1")"; [ -n "$st" ] || return 1
  IFS=$'\t' read -r _ pinned actual <<<"$st"
  cur="${!2:-}"
  [ -n "$cur" ] && [ "$cur" = "$actual" ]
}

_img_set_var() { # ПЕРЕМЕННАЯ ЗНАЧЕНИЕ — в процесс и в .env (пустое значение = вернуться к ссылке по умолчанию)
  export "$1=$2"
  [ -n "${ENV_FILE:-}" ] && [ -f "${ENV_FILE:-}" ] && { upd_env_set_key "$ENV_FILE" "$1" "$2" >/dev/null 2>&1 || warn "Не удалось записать $1 в .env: выбранный источник образа не сохранён"; }
  return 0
}

# Наличие манифеста у зеркала по дайджесту (только проверка, образ не скачивается). Внутренние реестры часто работают без TLS или с собственным центром сертификации:
# `docker manifest inspect` для них отвечает вводящим в заблуждение «no such manifest», поэтому для не публичных адресов проверка повторяется с --insecure
# (это касается только пробы наличия; саму загрузку по-прежнему ведёт служба Docker по своим настройкам доверия).
_img_manifest_check() { # ССЫЛКА → 0/1, вывод ошибки — в IMG_MOUT
  local ref="$1" host="${1%%/*}"
  IMG_MOUT="$(timeout 90 docker manifest inspect "$ref" 2>&1 >/dev/null)" && return 0
  case "$host" in mirror.gcr.io|docker.io|ghcr.io|quay.io|registry-1.docker.io|*.gcr.io|*.pkg.dev) ;; *)
    IMG_MOUT="$(timeout 90 docker manifest inspect --insecure "$ref" 2>&1 >/dev/null)" && return 0 ;; esac
  IMG_MOUT="$(printf '%s' "$IMG_MOUT" | img_redact | tail -n 5)"
  return 1
}

# ---- одна загрузка (без повторов): IMG_OUT — выдержка вывода без секретов; IMG_CODE — причина
img_pull_once() { # img_pull_once ССЫЛКА
  local ref="$1" out rc
  out="$(timeout "${IMAGE_PULL_TIMEOUT:-1800}" docker pull "$ref" 2>&1)"; rc=$?
  [ "$rc" -eq 124 ] && out="$out"$'\n'"timeout: загрузка не завершилась за ${IMAGE_PULL_TIMEOUT:-1800} с"
  IMG_OUT="$(printf '%s' "$out" | img_redact | tail -n 25)"
  if [ "$rc" -eq 0 ]; then IMG_CODE=""; return 0; fi
  IMG_CODE="$(img_classify "$out")"
  return 1
}

_img_pause() { local n="$1"; sleep $(( ${IMAGE_PULL_PAUSE:-5} * n * n )); }

img_pull_retry() { # img_pull_retry ССЫЛКА ПОПЫТОК → 0/1; при отказе IMG_CODE, IMG_OUT, IMG_TRIES
  local ref="$1" n="$2" i=1
  IMG_TRIES=0
  while :; do
    IMG_TRIES=$i
    info "  загрузка $(printf '%s' "$ref" | img_redact) (попытка $i из $n)…"
    img_pull_once "$ref" && return 0
    warn "  не получилось: $(img_summary "$IMG_CODE")"
    img_retryable "$IMG_CODE" || return 1
    [ "$i" -ge "$n" ] && return 1
    _img_pause "$i"; i=$((i + 1))
  done
}

# Образ уже на сервере: основное имя, выбранное ранее зеркало или любое разрешённое зеркало (по дайджесту). Печатает ссылку, под которой образ лежит.
img_local_ref() { # img_local_ref ССЫЛКА [КЛЮЧ ПЕРЕМЕННАЯ]
  local ref="$1" cand st actual
  img_parse "$ref"; local dg="$IMG_DIGEST"
  if docker image inspect "$ref" >/dev/null 2>&1; then printf '%s' "$ref"; return 0; fi
  [ -n "$dg" ] || return 1
  if [ -n "${2:-}" ]; then st="$(img_state_get "$2")"; actual="$(printf '%s' "$st" | cut -f3)"; [ -n "$actual" ] && docker image inspect "$actual" >/dev/null 2>&1 && { printf '%s' "$actual"; return 0; }; fi
  while IFS= read -r cand; do
    [ -n "$cand" ] || continue
    docker image inspect "$cand" >/dev/null 2>&1 && { printf '%s' "$cand"; return 0; }
  done < <(img_mirror_refs "$ref")
  return 1
}

# img_ensure КЛЮЧ ПЕРЕМЕННАЯ ОБРАЗ_ПО_УМОЛЧАНИЮ [soft] — см. описание в начале файла. Код 0 — образ готов (или soft и сбой), 1 — не скачан.
img_ensure() {
  local key="$1" var="$2" default="$3" soft="${4:-}" cur st pinned actual have cand tried="" code="" out="" stage="" attempts="${IMAGE_PULL_ATTEMPTS:-3}"
  command -v docker >/dev/null 2>&1 || return 0

  # 1) значение, выбранное автоматически для прежней закреплённой ссылки, при выходе новой версии снимается: загрузка снова идёт с основного источника
  if img_auto_value "$key" "$var"; then
    st="$(img_state_get "$key")"; pinned="$(printf '%s' "$st" | cut -f2)"
    if [ "$pinned" != "$default" ]; then
      info "Закреплена новая версия образа: возвращаюсь к основному источнику (раньше использовалось зеркало)."
      _img_set_var "$var" ""; unset "$var"; img_state_del "$key"
    fi
  fi
  cur="${!var:-$default}"

  # 2) уже на сервере — без сети
  if have="$(img_local_ref "$cur" "$key" "$var")"; then
    if [ "$have" != "$cur" ]; then
      img_parse "$cur"; img_verify "$have" "$IMG_DIGEST" || { warn "Найденный локально образ $have не прошёл проверку: $IMG_VERIFY_WHY"; have=""; }
      if [ -n "$have" ]; then
        info "Образ уже загружен из зеркала ($have): использую его без обращения к сети."
        _img_set_var "$var" "$have"; img_state_set "$key" "$var" "$default" "$have"
      fi
    fi
    if [ -n "$have" ]; then img_error_clear "$key"; return 0; fi
  fi

  # 3) основной источник
  info "Загрузка образа: $(printf '%s' "$cur" | img_redact)"
  stage="основной источник"; local pstage="основной источник"
  if img_pull_retry "$cur" "$attempts"; then
    img_parse "$cur"
    if img_verify "$cur" "$IMG_DIGEST"; then img_error_clear "$key"; ok "Образ загружен и проверен."; return 0; fi
    IMG_CODE=digest; IMG_OUT="$IMG_VERIFY_WHY"
  fi
  code="$IMG_CODE"; out="$IMG_OUT"; tried="основной ($(img_summary "$code" | sed 's/\.$//'))"

  # 4) зеркала (только по дайджесту и только если причина — в источнике)
  if img_mirror_allowed "$code"; then
    while IFS= read -r cand; do
      [ -n "$cand" ] || continue
      stage="зеркало ${cand%%/*}"
      info "Основной источник недоступен — пробую разрешённое зеркало ${cand%%/*} (тот же дайджест)…"
      if ! _img_manifest_check "$cand"; then
        local mo mc; mo="$IMG_MOUT"; mc="$(img_classify "$mo")"
        warn "  у зеркала ${cand%%/*} нет этого образа или оно недоступно ($(img_summary "$mc"))"
        tried="$tried; ${cand%%/*} ($(img_summary "$mc" | sed 's/\.$//'))"; out="$out"$'\n'"[${cand%%/*}] $mo"; continue
      fi
      if img_pull_retry "$cand" "$(( attempts > 2 ? 2 : attempts ))"; then
        img_parse "$cand"
        if img_verify "$cand" "$IMG_DIGEST"; then
          _img_set_var "$var" "$cand"; img_state_set "$key" "$var" "$default" "$cand"; img_error_clear "$key"
          ok "Образ загружен из зеркала ${cand%%/*} и проверен (дайджест совпал); он будет использован при запуске."
          return 0
        fi
        warn "  образ из зеркала отклонён: $IMG_VERIFY_WHY"
        tried="$tried; ${cand%%/*} (проверка: $IMG_VERIFY_WHY)"; out="$out"$'\n'"[${cand%%/*}] $IMG_VERIFY_WHY"
        continue
      fi
      tried="$tried; ${cand%%/*} ($(img_summary "$IMG_CODE" | sed 's/\.$//'))"; out="$out"$'\n'"[${cand%%/*}] $IMG_OUT"
    done < <(img_mirror_refs "$cur")
  fi

  # 5) неудача
  img_error_write "$key" "$code" "$pstage" "$cur" "$tried" "$out"
  fail "Образ не скачан: $(img_summary "$code") Этап: основной источник${tried:+; пробовали: $tried}."
  [ "$code" = timeout ] || [ "$code" = connect ] && info "Подсказка: если в организации выход в интернет идёт через прокси, настройте его для Docker; можно указать внутренний реестр (IMAGE_MIRRORS) — см. вики «Решение проблем: видеозапись и трансляции»."
  [ "$soft" = soft ] && return 0 || return 1
}
