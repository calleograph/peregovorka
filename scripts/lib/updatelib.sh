#!/usr/bin/env bash
# updatelib.sh — функции штатного обновления (scripts/update.sh) и отката (scripts/rollback.sh). Только функции, ничего не выполняет.
# Принципы: никакого git reset --hard, удаления DATA_ROOT/моделей/томов PostgreSQL, перезаписи .env; значения секретов никогда не печатаются.

if [ -n "${_VM_UPDLIB_LOADED:-}" ]; then return 0; fi
_VM_UPDLIB_LOADED=1

UPD_SERVICES=(postgres redis livekit backend asr web)
UPD_NEW_SAFE=(); UPD_NEW_DECIDE=(); UPD_NEW_EMPTY=()
UPD_FULL_OK=0; UPD_GGUF_OK=0; UPD_UNHEALTHY=()

fsize() { stat -c '%s' "$1" 2>/dev/null || wc -c < "$1"; }
upd_as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

# ------------------------------------------------------------------------------------------------ .env
# Имена переменных файла (комментарии и пустые строки игнорируются).
upd_env_names() { grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$1" 2>/dev/null | cut -d= -f1 | awk '!seen[$0]++'; }

# Значение переменной как в файле (кавычки сняты). Для секретов вызывать только внутри кода, не для печати.
upd_env_value() {
  local line v; line="$(grep -E "^$2=" "$1" 2>/dev/null | tail -1)"; v="${line#*=}"
  if [[ "$v" == \"*\" && ${#v} -ge 2 ]]; then v="${v:1:${#v}-2}"; elif [[ "$v" == \'*\' && ${#v} -ge 2 ]]; then v="${v:1:${#v}-2}"; fi
  printf '%s' "$v"
}

upd_is_secret_name() { [[ "$1" =~ (PASSWORD|PASSWD|SECRET|TOKEN|KEY|CREDENTIAL|BIND_PW) ]]; }

# Параметры, зависящие от конкретного сервера/установки: автоматически не добавляются, решение за администратором.
upd_needs_decision() {
  case "$1" in
    COMPOSE_PROJECT_NAME|INSTALL_PROFILE|DATA_ROOT|APP_ROOT|BACKUP_DIR|TRUSTED_PROXY_HOPS|*_PORT|*_ADDR|*_IP|*_DN|*_URI|*_URIS|*_FILE|*PUBLIC_URL*|NGINX_*|LDAP_*) return 0 ;;
  esac
  return 1
}

# upd_env_classify ENV EXAMPLE → UPD_NEW_SAFE (добавим сами), UPD_NEW_DECIDE (нужно решение), UPD_NEW_EMPTY (необязательные, пустые по умолчанию).
upd_env_classify() {
  local env="$1" ex="$2" n v
  UPD_NEW_SAFE=(); UPD_NEW_DECIDE=(); UPD_NEW_EMPTY=()
  while IFS= read -r n; do
    [ -n "$n" ] || continue
    grep -qE "^$n=" "$env" 2>/dev/null && continue
    v="$(upd_env_value "$ex" "$n")"
    if upd_is_secret_name "$n" || upd_needs_decision "$n" || [[ "$v" == *CHANGE_ME* ]]; then UPD_NEW_DECIDE+=("$n")
    elif [ -z "$v" ]; then UPD_NEW_EMPTY+=("$n")
    else UPD_NEW_SAFE+=("$n"); fi
  done < <(upd_env_names "$ex")
}

# Задать ОДНУ переменную (существующую строку заменить, иначе дописать); остальное содержимое файла не меняется.
upd_env_set_key() { # ENV NAME VALUE
  local env="$1" name="$2" val="$3" q tmp
  q="$(dotenv_quote "$val")" || { fail "Значение $name не может быть записано в .env безопасно"; return 1; }
  tmp="$(mktemp)"
  if grep -qE "^$name=" "$env"; then
    NEWVAL="$q" awk -v n="$name" 'BEGIN{FS=OFS="="} $1==n && !d {print n "=" ENVIRON["NEWVAL"]; d=1; next} {print}' "$env" > "$tmp"
  else
    { cat "$env"; printf '%s=%s\n' "$name" "$q"; } > "$tmp"
  fi
  cat "$tmp" > "$env"; rm -f "$tmp"
}

# Дописать безопасные новые параметры (значения по умолчанию из .env.example) блоком в конец .env.
upd_env_append_safe() { # ENV EXAMPLE
  local env="$1" ex="$2" n q
  [ "${#UPD_NEW_SAFE[@]}" -gt 0 ] || return 0
  printf '\n# --- добавлено scripts/update.sh %s (значения по умолчанию из .env.example; при необходимости измените) ---\n' "$(date +%F)" >> "$env"
  for n in "${UPD_NEW_SAFE[@]}"; do
    q="$(dotenv_quote "$(upd_env_value "$ex" "$n")")" || continue
    printf '%s=%s\n' "$n" "$q" >> "$env"
  done
}

# Закрепить LiveKit на проверенной версии (deployment/compat.env). `latest`, пустое значение и УСТАРЕВШИЙ тег заменяются;
# более новый конкретный тег оставляется (решение администратора). Печатает результат; экспортирует LIVEKIT_IMAGE_TAG для сборки.
upd_livekit_pin() { # ENV
  local env="$1" want cur
  want="$(grep -E '^TESTED_LIVEKIT_SERVER=' "$REPO_ROOT/deployment/compat.env" 2>/dev/null | cut -d= -f2)"
  [ -n "$want" ] || { warn "deployment/compat.env: не найден TESTED_LIVEKIT_SERVER — LiveKit не закреплён"; return 0; }
  cur="$(upd_env_value "$env" LIVEKIT_IMAGE_TAG)"
  if [ -z "$cur" ] || [ "$cur" = latest ] || { [[ "$cur" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] && semver_lt "$cur" "$want"; }; then
    upd_env_set_key "$env" LIVEKIT_IMAGE_TAG "$want" || return 1
    export LIVEKIT_IMAGE_TAG="$want"
    ok "LIVEKIT_IMAGE_TAG: ${cur:-не задан} → $want (проверенная версия)"
  elif [ "$cur" = "$want" ]; then ok "LIVEKIT_IMAGE_TAG=$want (проверенная версия)"
  else warn "LIVEKIT_IMAGE_TAG=$cur новее проверенной $want — оставлен как есть (не проверялось; решает администратор)"; fi
}

# ---------------------------------------------------------------------------------------------- модели
# Проверка наличия моделей (НЕ скачивает и не удаляет). Размер — минимальная проверка целостности; контрольные суммы не сверяются
# (решение владельца: upstream-файлы могут меняться). Заполняет UPD_FULL_OK / UPD_GGUF_OK.
upd_models_check() {
  local dir="${DATA_ROOT}/models/gigaam" name="${ASR_MODEL_NAME:-v3_e2e_rnnt}" f gg
  UPD_FULL_OK=0; UPD_GGUF_OK=0
  f="$dir/$name.ckpt"
  if [ -f "$f" ] && [ "$(fsize "$f")" -ge $((1024 * 1024)) ] && [ -s "$dir/${name}_tokenizer.model" ]; then
    UPD_FULL_OK=1; ok "Модель Full: $name.ckpt ($(fsize "$f") байт) + токенайзер — на месте, повторная загрузка не нужна"
  else warn "Модель Full ($name.ckpt / ${name}_tokenizer.model) не найдена или неполная в $dir"; fi
  gg="$dir/${GGUF_MODEL_FILE:-gigaam-v3-e2e-rnnt-Q5_K_M.gguf}"
  if [ -f "$gg" ] && [ "$(fsize "$gg")" -ge $((1024 * 1024)) ]; then UPD_GGUF_OK=1; ok "Модель Q5_K_M (GGUF): $(basename "$gg") ($(fsize "$gg") байт) — доступна"
  else info "Модель Q5_K_M (GGUF) не установлена — необязательна (scripts/models.sh --gguf --skip-full)"; fi
}

# ------------------------------------------------------------------------------------- ожидание сервисов
# Состояние одного сервиса: healthy | starting | unhealthy | exited | restarting | missing
upd_svc_state() {
  local cid st h
  cid="$(dc ps -q "$1" 2>/dev/null | head -1)"
  [ -n "$cid" ] || { echo missing; return; }
  st="$(docker inspect -f '{{.State.Status}}' "$cid" 2>/dev/null)"
  h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null)"
  case "$st" in
    running) case "$h" in healthy|none) echo healthy ;; unhealthy) echo unhealthy ;; *) echo starting ;; esac ;;
    restarting) echo restarting ;;
    *) echo "${st:-exited}" ;;
  esac
}

# Ждёт, пока ВСЕ сервисы проекта станут healthy; печатает прогресс «Waiting for web... 12/180 sec».
# Контейнер в состоянии exited/missing (после запуска) — немедленный отказ. Возврат 0/1; UPD_UNHEALTHY — проблемные сервисы.
upd_wait_healthy() { # [timeout_sec]
  local timeout="${1:-180}" poll="${UPD_POLL:-5}" start now s st pending last_print=-1000
  start=$(date +%s)
  while :; do
    now=$(( $(date +%s) - start )); pending=(); UPD_UNHEALTHY=()
    for s in "${UPD_SERVICES[@]}"; do
      st="$(upd_svc_state "$s")"
      case "$st" in
        healthy) ;;
        exited|dead|missing|created) UPD_UNHEALTHY+=("$s:$st") ;;
        *) pending+=("$s:$st") ;;
      esac
    done
    if [ "${#UPD_UNHEALTHY[@]}" -gt 0 ]; then fail "Сервис(ы) не работают: ${UPD_UNHEALTHY[*]} — scripts/logs.sh <сервис>"; return 1; fi
    if [ "${#pending[@]}" -eq 0 ]; then ok "Все сервисы healthy (за ${now} с)"; return 0; fi
    if [ $((now - last_print)) -ge "${UPD_REPORT_EVERY:-10}" ]; then
      for s in "${pending[@]}"; do printf 'Waiting for %s... %d/%d sec (%s)\n' "${s%%:*}" "$now" "$timeout" "${s#*:}"; done
      last_print=$now
    fi
    if [ "$now" -ge "$timeout" ]; then UPD_UNHEALTHY=("${pending[@]}"); fail "За ${timeout} с не стали healthy: ${pending[*]}"; return 1; fi
    sleep "$poll"
  done
}

# ------------------------------------------------------------------------------------------ состояние
upd_state_file() { echo "${DATA_ROOT}/state/last-update.state"; }
upd_state_set() { # KEY VALUE (значение — одна строка без секретов)
  local f; f="$(upd_state_file)"; mkdir -p "$(dirname "$f")"
  { grep -v "^$1=" "$f" 2>/dev/null || true; printf '%s=%s\n' "$1" "$2"; } > "$f.tmp" && mv "$f.tmp" "$f"
}
upd_state_get() { grep -E "^$1=" "$(upd_state_file)" 2>/dev/null | tail -1 | cut -d= -f2-; }
upd_marker_file() { echo "${DATA_ROOT}/state/update-in-progress"; }

# Сохранить ссылки на работающие образы (тегом prev-<commit>): откат возможен даже если старый тег перезаписан сборкой.
upd_save_prev_images() { # OLD12
  local s cid id tag
  for s in backend asr web; do
    cid="$(dc ps -q "$s" 2>/dev/null | head -1)"; [ -n "$cid" ] || continue
    id="$(docker inspect -f '{{.Image}}' "$cid" 2>/dev/null)"; [ -n "$id" ] || continue
    tag="${COMPOSE_PROJECT_NAME}-${s}:prev-$1"
    docker tag "$id" "$tag" 2>/dev/null && upd_state_set "prev_image_$s" "$tag"
  done
}

# ---------------------------------------------------------------------------------- миграции и откат
# Известна ли ревизия БД коду указанного commit (есть ли файл миграции с такой ревизией в backend/migrations/versions)
upd_rev_in_commit() { # REV COMMIT
  local f
  [ -n "$1" ] || return 0
  while IFS= read -r f; do
    git -C "$REPO_ROOT" show "$2:$f" 2>/dev/null | grep -Eq "^revision[^=]*=[[:space:]]*['\"]$1['\"]" && return 0
  done < <(git -C "$REPO_ROOT" ls-tree -r --name-only "$2" -- backend/migrations/versions 2>/dev/null | grep '\.py$')
  return 1
}

# ------------------------------------------------------------------------------------- nginx (свой site)
# Мигрировать СОБСТВЕННЫЙ site (marker этого проекта) на актуальный шаблон: резервная копия, nginx -t, reload; при ошибке — откат.
# Чужие файлы не трогаются никогда. Возврат 0 всегда (ошибка миграции — предупреждение, приложение продолжает работать).
upd_nginx_migrate() {
  [ "${NGINX_MANAGE:-no}" = "yes" ] || { info "NGINX_MANAGE!=yes — host nginx не управляется этим проектом (директивы realtime: DEPLOYMENT.md §3.1)"; return 0; }
  local site="$NGINX_SITES_AVAILABLE/$NGINX_SITE_NAME" rendered ts nb="nginx" tlog
  [ -e "$site" ] || { info "nginx-site $site не установлен — пропуск (первичная настройка: scripts/install.sh --from nginx)"; return 0; }
  if ! grep -qx "# managed-by: peregovorka:${COMPOSE_PROJECT_NAME}" "$site" 2>/dev/null; then
    warn "$site принадлежит не этому проекту (нет marker) — НЕ изменяю. Нужные директивы для /api/v1/ws и /livekit/: proxy_buffering off; proxy_request_buffering off; tcp_nodelay on; и X-Forwarded-Proto без подмены на http"
    return 0
  fi
  rendered="$(render_nginx_site)"
  if [ "$(cat "$site")" = "$rendered" ]; then ok "nginx-site актуален — без изменений"; return 0; fi
  [ "$(id -u)" -eq 0 ] || nb="sudo nginx"
  if [ "${DRY_RUN:-0}" = "1" ]; then info "[dry-run] nginx-site будет обновлён по шаблону (резервная копия, nginx -t, reload)"; return 0; fi
  ts="$(date +%Y%m%d-%H%M%S)"; tlog="$(mktemp)"
  info "nginx-site устарел (создан до realtime-настроек или иного шаблона): обновляю собственный site, копия: $site.bak-$ts"
  upd_as_root cp -p "$site" "$site.bak-$ts" || { warn "Не удалось сделать копию site — пропуск"; rm -f "$tlog"; return 0; }
  printf '%s\n' "$rendered" | upd_as_root tee "$site" >/dev/null
  if $nb -t 2>"$tlog"; then
    if command -v systemctl >/dev/null 2>&1; then upd_as_root systemctl reload nginx; else upd_as_root nginx -s reload; fi
    ok "nginx-site обновлён, nginx перезагружен"
  else
    warn "nginx -t не прошёл ($(tail -3 "$tlog" | tr '\n' ' ')) — возвращаю прежний site, reload не выполнялся"
    upd_as_root cp -p "$site.bak-$ts" "$site"
  fi
  rm -f "$tlog"; return 0
}

# ------------------------------------------------------------------------------------------------- git
# git fetch с повтором при временных сетевых сбоях (GitHub/DNS/прокси).
upd_git_fetch() {
  local i=1 n="${GIT_RETRIES:-4}"
  while :; do
    git -C "$REPO_ROOT" fetch --tags --prune origin 2>&1 && return 0
    [ "$i" -ge "$n" ] && return 1
    warn "git fetch: сбой (попытка $i/$n) — повтор через $((i * 5)) с"; sleep $((i * 5)); i=$((i + 1))
  done
}
