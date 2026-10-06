#!/usr/bin/env bash
# envlib.sh — чистые функции для работы с .env, путями, LDAP URI и ресурсами.
# Подключается из setup.sh / preflight.sh / models.sh / install.sh; покрыта тестами tests/scripts/run.sh.
# Функции ничего не меняют в системе.

# --------------------------------------------------------------------------- .env
# Безопасная запись значения в .env: результат одинаково читается bash (`source`),
# docker compose (--env-file) и нашим load_env, без интерполяции и исполнения.
#   простое значение → как есть;  без «'» → '…' (литерал);  без $ " \ ` → "…";  иначе — отказ (return 1).
dotenv_quote() {
  local v="$1"
  case "$v" in *$'\n'*|*$'\r'*) return 1 ;; esac
  if [[ "$v" =~ ^[A-Za-z0-9_./:@%+=,-]*$ ]]; then printf '%s' "$v"; return 0; fi
  if [[ "$v" != *"'"* ]]; then printf "'%s'" "$v"; return 0; fi
  if [[ "$v" != *[\$\"\\\`]* ]]; then printf '"%s"' "$v"; return 0; fi
  return 1
}

# Реальные присваивания-заглушки CHANGE_ME (комментарии игнорируются).
env_placeholders() {
  grep -nE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=.*CHANGE_ME' "$1" 2>/dev/null || true
}

# ------------------------------------------------------------------------- пути
# DATA_ROOT и подобные: ЛОКАЛЬНЫЙ абсолютный Linux-каталог. Сообщение об ошибке — в stdout, код 1.
validate_local_dir() {
  local v="$1" name="${2:-DATA_ROOT}"
  case "$v" in
    '') printf '%s
' "$name не задан"; return 1 ;;
    \\\\*|*'\'*) printf '%s
' "Это Windows/SMB-путь ($v). $name должен быть локальным Linux-каталогом, например /srv/peregovorka-data. Сетевое SMB-хранилище протоколов настраивается отдельно: админка → «Хранилище» после установки."; return 1 ;;
    smb://*|cifs://*|nfs://*|*://*) printf '%s
' "Это URL/сетевой адрес ($v). $name должен быть локальным Linux-каталогом. SMB-хранилище настраивается в админке → «Хранилище»."; return 1 ;;
    /*) ;;
    *) printf '%s
' "$name должен быть абсолютным путём, начинающимся с «/» (сейчас: $v)"; return 1 ;;
  esac
  if [[ "$v" =~ [[:space:][:cntrl:]] ]]; then printf '%s
' "$name не должен содержать пробелов и управляющих символов (сейчас: $v)"; return 1; fi
  case "/$v/" in */../*) printf '%s
' "$name не должен содержать «..»"; return 1 ;; esac
  [ "$v" != "/" ] || { printf '%s
' "$name не может быть «/»"; return 1; }
  return 0
}

# ------------------------------------------------------------------------- LDAP
# «ldaps://host[:port][/…]» → LDAP_HOST, LDAP_PORT (636 по умолчанию). Завершающий «/» и путь игнорируются.
parse_ldap_uri() {
  local u="${1//[[:space:]]/}"
  LDAP_HOST=""; LDAP_PORT=""
  [[ "$u" =~ ^[Ll][Dd][Aa][Pp][Ss]://([A-Za-z0-9._-]+)(:([0-9]{1,5}))?(/[^[:space:]]*)?$ ]] || return 1
  LDAP_HOST="${BASH_REMATCH[1]}"; LDAP_PORT="${BASH_REMATCH[3]:-636}"
  [ "$LDAP_PORT" -ge 1 ] && [ "$LDAP_PORT" -le 65535 ]
}

# Список через запятую → нормализованный список «ldaps://host:port» (без «/»). Ошибка: сообщение в stdout, код 1.
normalize_ldap_uris() {
  local out="" item
  local IFS=','
  for item in $1; do
    item="${item//[[:space:]]/}"; [ -n "$item" ] || continue
    case "$item" in [Ll][Dd][Aa][Pp]://*) echo "Разрешён только ldaps:// (шифрованный LDAP): $item"; return 1 ;; esac
    parse_ldap_uri "$item" || { echo "Некорректный адрес LDAPS: $item (ожидается ldaps://имя:636)"; return 1; }
    out+="${out:+,}ldaps://${LDAP_HOST}:${LDAP_PORT}"
  done
  [ -n "$out" ] || { echo "Не указан ни один сервер LDAPS"; return 1; }
  printf '%s' "$out"
}

# --------------------------------------------------------------------------- RAM
# Сравнение в KiB, без округления вниз до ГБ. nominal_gb — требуемый объём «по паспорту» ВМ.
#   >= 90% номинала → ok   (запас на накладные расходы гипервизора/ядра: ВМ «8 ГБ» показывает ~7.8)
#   75–90%          → warn
#   < 75%           → fail
ram_verdict() {
  local kib="$1" gb="$2" nominal_kib
  nominal_kib=$(( gb * 1024 * 1024 ))
  if   [ $(( kib * 100 )) -ge $(( nominal_kib * 90 )) ]; then echo ok
  elif [ $(( kib * 100 )) -ge $(( nominal_kib * 75 )) ]; then echo warn
  else echo fail; fi
}

# ----------------------------------------------------------------- сводка по сети
print_network_summary() { # читает переменные окружения (после load_env)
  local host="${NGINX_SERVER_NAME:-<домен>}"
  cat <<EOF

================ Сетевая схема экземпляра ${COMPOSE_PROJECT_NAME:-?} ================
 Браузер → HTTPS (внешний reverse proxy / Nginx Proxy Manager)
        → host nginx :${NGINX_LISTEN_PORT:-?}   (отдельный site-файл «${NGINX_SITE_NAME:-?}»; имя сайта: ${host})
        → контейнер web 127.0.0.1:${WEB_PORT:-?}  (интерфейс, /api, WebSocket, сигналинг /livekit)
        → LiveKit API 127.0.0.1:${LIVEKIT_HTTP_PORT:-?} (только loopback, для диагностики)

 Звук/видео идут МИМО HTTP-прокси — напрямую клиент → сервер (${LIVEKIT_NODE_IP:-?}):
   ICE/TCP  ${LIVEKIT_TCP_PORT:-?}/tcp   — запасной канал
   ICE/UDP  ${LIVEKIT_UDP_PORT:-?}/udp   — основной канал (один mux-порт)
 ВНИМАНИЕ: HTTP reverse proxy (в т.ч. Nginx Proxy Manager) НЕ заменяет доступ клиентов к
 ${LIVEKIT_TCP_PORT:-?}/tcp и ${LIVEKIT_UDP_PORT:-?}/udp сервера. Откройте их для клиентов сети (файрвол установщик не меняет).

 Внешний прокси должен: вести https://${host} → http://<IP сервера>:${NGINX_LISTEN_PORT:-?};
 передавать Upgrade/WebSocket; ставить X-Forwarded-Proto: https.
 Существующие site-файлы nginx установщик не изменяет: если нужно «вписать» новый сайт в уже
 занятый порт, сделайте это отдельно — направьте его на 127.0.0.1:${NGINX_LISTEN_PORT:-?}.

 Зачем три уровня: внешний прокси (HTTPS, внешний порт, который вам выделили) → host nginx :${NGINX_LISTEN_PORT:-?} (управляемый
 Peregovorka ingress: свой site-файл, WebSocket, закрытый /internal) → web 127.0.0.1:${WEB_PORT:-?} (контейнер: SPA, /api,
 /livekit). Пример для существующего site (WebSocket обязателен):
     location / {
         proxy_pass http://127.0.0.1:${NGINX_LISTEN_PORT:-?};
         proxy_http_version 1.1;
         proxy_set_header Host \$host;
         proxy_set_header Upgrade \$http_upgrade;
         proxy_set_header Connection "upgrade";
         proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
         proxy_set_header X-Forwarded-Proto \$http_x_forwarded_proto;
         proxy_read_timeout 3600s;
     }
================================================================================
EOF
}

# ---------------------------------------------------------------- nginx: собственный site
# Рендер site-файла из шаблона (те же подстановки, что использует install.sh). Печатает результат.
render_nginx_site() {
  local tpl pid
  if [ -n "${NGINX_TLS_CERT:-}" ]; then tpl="$REPO_ROOT/deployment/nginx/site.tls.conf.tpl"; else tpl="$REPO_ROOT/deployment/nginx/site.http.conf.tpl"; fi
  pid="$(printf '%s' "$COMPOSE_PROJECT_NAME" | tr -c 'a-zA-Z0-9' '_')"
  sed -e "s|@@PROJECT@@|${COMPOSE_PROJECT_NAME}|g" -e "s|@@PROJECT_ID@@|${pid}|g" \
      -e "s|@@LISTEN_PORT@@|${NGINX_LISTEN_PORT}|g" -e "s|@@SERVER_NAME@@|${NGINX_SERVER_NAME}|g" \
      -e "s|@@WEB_PORT@@|${WEB_PORT}|g" -e "s|@@TLS_CERT@@|${NGINX_TLS_CERT:-}|g" -e "s|@@TLS_KEY@@|${NGINX_TLS_KEY:-}|g" "$tpl"
}

# Наш ли это уже установленный site: файл с marker'ом ЭТОГО проекта, нужный listen и корректный symlink.
# Не зависит от прав на просмотр процессов (ss -p) и от root. Вызывать: own_nginx_site_ok [strict]
# strict — дополнительно: содержимое файла совпадает с тем, что установщик сгенерировал бы сейчас.
own_nginx_site_ok() {
  local site="$NGINX_SITES_AVAILABLE/$NGINX_SITE_NAME" link="$NGINX_SITES_ENABLED/$NGINX_SITE_NAME"
  [ -f "$site" ] || return 1
  grep -qx "# managed-by: peregovorka:${COMPOSE_PROJECT_NAME}" "$site" 2>/dev/null || return 1
  grep -Eq "^[[:space:]]*listen[[:space:]]+(\[::\]:|[0-9.]+:)?${NGINX_LISTEN_PORT}([[:space:];]|\$)" "$site" 2>/dev/null || return 1
  { [ -L "$link" ] || [ -f "$link" ]; } || return 1
  if [ -L "$link" ]; then
    [ "$(readlink -f "$link" 2>/dev/null)" = "$(readlink -f "$site" 2>/dev/null)" ] || return 1
  else
    cmp -s "$site" "$link" || return 1   # не symlink (копия) — принимаем только если содержимое идентично
  fi
  if [ "${1:-}" = "strict" ]; then
    [ "$(cat "$site")" = "$(render_nginx_site)" ] || return 1
  fi
  return 0
}

# ------------------------------------------------------------ параметры ядра (только чтение)
# proc_sys vm.overcommit_memory → значение из /proc/sys (PROC_SYS_ROOT — для тестов). Пусто, если недоступно.
proc_sys() { local f="${PROC_SYS_ROOT:-/proc/sys}/${1//.//}"; [ -r "$f" ] && tr -d '[:space:]' < "$f"; }

# Рекомендуемые значения для WebRTC (LiveKit сам предупреждает о малом буфере приёма UDP).
KERNEL_RECOMMENDED_RMEM=5000000
KERNEL_RECOMMENDED_WMEM=5000000
KERNEL_RECOMMENDED_BACKLOG=5000

# kernel_tuning_check ok_fn warn_fn — WARN, не отказ: установщик/preflight sysctl НЕ меняют (на shared-host это глобальные
# настройки хоста, их применяет администратор осознанно; для standalone есть отдельный scripts/tune-kernel.sh).
kernel_tuning_check() {
  local ok="$1" warn="$2" v name want
  v="$(proc_sys vm.overcommit_memory)"
  if [ -z "$v" ]; then "$warn" "vm.overcommit_memory: значение недоступно для чтения (не Linux или нет /proc/sys)"
  elif [ "$v" = "1" ]; then "$ok" "vm.overcommit_memory = 1 (рекомендация Redis выполнена)"
  else
    "$warn" "vm.overcommit_memory = $v (рекомендуется 1). Redis при фоновом сохранении (fork) может получить «Cannot allocate memory» и не сохранить данные при нехватке памяти; Redis пишет WARNING при старте. Параметр глобален для хоста — влияет на все приложения сервера. Команды администратору: sudo sysctl -w vm.overcommit_memory=1 ; постоянно: echo 'vm.overcommit_memory = 1' | sudo tee /etc/sysctl.d/99-peregovorka.conf (установщик это НЕ делает)."
  fi
  for name in net.core.rmem_max net.core.wmem_max net.core.netdev_max_backlog; do
    case "$name" in net.core.rmem_max) want=$KERNEL_RECOMMENDED_RMEM ;; net.core.wmem_max) want=$KERNEL_RECOMMENDED_WMEM ;; *) want=$KERNEL_RECOMMENDED_BACKLOG ;; esac
    v="$(proc_sys "$name")"
    if [ -z "$v" ]; then "$warn" "$name: значение недоступно для чтения"
    elif [ "$v" -ge "$want" ] 2>/dev/null; then "$ok" "$name = $v (рекомендация ≥ $want выполнена)"
    else
      "$warn" "$name = $v ниже рекомендованного $want. LiveKit предупреждает о малом буфере приёма UDP; под нагрузкой (участники, видео, экран) возможны потери пакетов, треск и подвисание медиа. Параметры глобальны для хоста. Команда администратору: sudo sysctl -w $name=$want (постоянно — через scripts/tune-kernel.sh --apply с подтверждением; установщик sysctl сам НЕ меняет)."
    fi
  done
}
