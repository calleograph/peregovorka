#!/usr/bin/env bash
# setup.sh — «мастер» первой установки на сервере одной командой. Ничего не делает молча.
#
#   scripts/setup.sh [--profile shared-host|standalone] [--env-only]
#
# Шаги: (1) вопросы → .env (все значения безопасно квотируются, секреты генерируются, порты подбираются
# и ПРОВЕРЯЮТСЯ на занятость); (2) модель ASR; (3) preflight (read-only); (4) план install --dry-run;
# (5) только после вашего «y» — install и smoke-test.
# --env-only — только создать и проверить .env (для тестов и ручной настройки), дальше не идти.
# Гарантии install.sh сохраняются: только объекты этого проекта, без upgrade/prune/reboot, чужие контейнеры,
# nginx-сайты, Apache/PHP/Moodle и файрвол не затрагиваются.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROFILE="shared-host"; ENV_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    --env-only) ENV_ONLY=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
case "$PROFILE" in shared-host|standalone) ;; *) die "--profile: shared-host|standalone" ;; esac
command -v openssl >/dev/null || die "Нужен openssl"

ENV="${ENV_FILE:-$REPO_ROOT/.env}"
ask_yn() { local a; read -r -p "$1" a || die "Ввод завершён"; [[ "${a:-$2}" =~ ^[Yy]$ ]]; }
if [ -f "$ENV" ]; then
  ask_yn ".env уже существует. Использовать как есть и перейти к проверкам? [Y/n] " Y || die "Остановлено: удалите/переименуйте .env, чтобы создать заново."
fi

declare -A EXPECT=(); KEYS=()
set_var() { # set_var KEY VALUE — единая безопасная запись (квотирование по dotenv_quote), без eval
  local k="$1" v="$2" q tmp
  q="$(dotenv_quote "$v")" || die "Значение $k содержит символы, которые нельзя безопасно записать в .env (перевод строки или сочетание ' \" \$ \\ \`). Измените значение."
  tmp="$(mktemp)"
  K="$k" Q="$q" awk 'BEGIN{k=ENVIRON["K"]; q=ENVIRON["Q"]; done=0}
    $0 ~ "^"k"=" && !done {print k"="q; done=1; next} {print} END{if(!done) print k"="q}' "$ENV" > "$tmp" && cat "$tmp" > "$ENV"; rm -f "$tmp"
  EXPECT[$k]="$v"; KEYS+=("$k")
}
ask() { # ask "вопрос" "по умолчанию" -> REPLY
  local d="${2:-}"; read -r -p "$1${d:+ [$d]}: " REPLY || die "Ввод завершён"; REPLY="${REPLY:-$d}"
}
ask_valid() { # ask_valid "вопрос" "по умолчанию" функция-проверка(печатает причину, код 1)
  local why
  while :; do ask "$1" "$2"; if why="$("$3" "$REPLY")"; then return 0; fi; warn "$why"; done
}
v_name()  { [[ "$1" =~ ^[a-z][a-z0-9_-]{2,40}$ ]] || { echo "Только a-z, 0-9, «-», «_»; с буквы; 3–41 символ"; return 1; }
            if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${1}[-_]"; then echo "Контейнеры «$1…» уже есть на сервере — выберите другое имя"; return 1; fi; }
v_host()  { [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ ]] || { echo "Нужно DNS-имя без схемы и слэшей (например meet.corp.local)"; return 1; }; }
v_ip()    { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "Нужен IPv4-адрес (например 192.0.2.10)"; return 1; }; }
v_data()  { validate_local_dir "$1" DATA_ROOT; }
v_hops()  { [[ "$1" =~ ^[1-9]$ ]] || { echo "Число от 1 до 9"; return 1; }; }
v_nonempty() { [ -n "$1" ] || { echo "Обязательное поле"; return 1; }; }
v_ldap()  { normalize_ldap_uris "$1" >/dev/null; }
v_abs()   { [[ "$1" == /* && "$1" != *'\'* ]] || { echo "Нужен абсолютный путь Linux (например /etc/peregovorka/ad-ca.pem)"; return 1; }; }
port_free() { ! port_busy "$1" "$2" && ! { command -v docker >/dev/null && docker ps --format '{{.Ports}}' 2>/dev/null | grep -Eq ":$2->"; }; }
find_port() { local p="$2"; while ! port_free "$1" "$p"; do p=$((p+1)); done; echo "$p"; }

if [ ! -f "$ENV" ]; then
  cp "$REPO_ROOT/.env.example" "$ENV"; chmod 600 "$ENV"
  log "== Основные параметры =="
  existing="$(docker ps -a --format '{{.Names}}' 2>/dev/null | head -50 | tr '\n' ' ' || true)"
  [ -n "$existing" ] && info "На сервере уже есть контейнеры (не затрагиваются): $existing"
  ask_valid "Имя экземпляра (уникальное на сервере)" "peregovorka-prod" v_name; NAME="$REPLY"
  ask_valid "DNS-имя сайта (HTTPS обеспечивает внешний прокси/nginx)" "meet.example.org" v_host; HOSTN="$REPLY"
  defip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)"
  ask_valid "IP сервера, по которому до него доходят клиенты (для звука)" "${defip:-}" v_ip; NODEIP="$REPLY"
  info "DATA_ROOT — ЛОКАЛЬНЫЙ каталог Linux для БД, моделей, записей и бэкапов (не сетевой путь)."
  info "Сетевое SMB-хранилище готовых протоколов настраивается ПОСЛЕ установки: админка → «Хранилище»."
  ask_valid "Каталог постоянных данных (вне репозитория)" "/srv/${NAME}-data" v_data; DATA="$REPLY"
  ask_valid "Сколько прокси стоит перед сервисом (nginx сервера = 1; внешний proxy + nginx = 2)" "2" v_hops; HOPS="$REPLY"
  WEB="$(find_port tcp 18480)"; LKH="$(find_port tcp 17880)"; LKT="$(find_port tcp 17881)"; LKU="$(find_port udp 17882)"; NGP="$(find_port tcp 18400)"
  info "Подобраны СВОБОДНЫЕ порты (проверено): host nginx=$NGP, web=$WEB, livekit-api(loopback)=$LKH, ICE/TCP=$LKT, ICE/UDP=$LKU"
  if ! ask_yn "Принять порты? [Y/n] " Y; then
    ask "host nginx TCP (сюда ведёт внешний прокси)" "$NGP"; NGP="$REPLY"; ask "web TCP (loopback)" "$WEB"; WEB="$REPLY"
    ask "livekit API TCP (loopback)" "$LKH"; LKH="$REPLY"; ask "livekit ICE TCP (для клиентов)" "$LKT"; LKT="$REPLY"; ask "livekit ICE UDP (для клиентов)" "$LKU"; LKU="$REPLY"
  fi
  log; log "== Active Directory =="
  ask_valid "LDAPS-серверы через запятую (ldaps://dc1.corp.local:636)" "" v_ldap; LDAPU="$(normalize_ldap_uris "$REPLY")"
  ask_valid "Base DN (DC=corp,DC=local)" "" v_nonempty; BASEDN="$REPLY"
  ask_valid "DN сервисной учётки (только чтение)" "" v_nonempty; BINDDN="$REPLY"
  read -r -s -p "Пароль сервисной учётки: " BINDPW || die "Ввод завершён"; echo
  ask_valid "Путь к PEM с CA контроллеров домена" "/etc/${NAME}/ad-ca.pem" v_abs; CA="$REPLY"
  ask_valid "DN группы администраторов системы" "" v_nonempty; ADMING="$REPLY"

  set_var COMPOSE_PROJECT_NAME "$NAME"; set_var INSTALL_PROFILE "$PROFILE"
  set_var APP_PUBLIC_URL "https://$HOSTN"; set_var LIVEKIT_PUBLIC_URL "wss://$HOSTN/livekit"
  set_var NGINX_SERVER_NAME "$HOSTN"; set_var NGINX_SITE_NAME "$NAME"; set_var NGINX_LISTEN_PORT "$NGP"
  set_var APP_ROOT "$REPO_ROOT"; set_var DATA_ROOT "$DATA"; set_var BACKUP_DIR "$DATA/backups"; set_var TRUSTED_PROXY_HOPS "$HOPS"
  set_var WEB_PORT "$WEB"; set_var LIVEKIT_HTTP_PORT "$LKH"; set_var LIVEKIT_TCP_PORT "$LKT"; set_var LIVEKIT_UDP_PORT "$LKU"
  set_var LIVEKIT_NODE_IP "$NODEIP"
  # Потоки ASR: на общем сервере распознавание не должно занимать все ядра хоста (по умолчанию torch берёт все).
  # Старт: половина vCPU (не меньше 1), inter-op 1 (до 4 vCPU) или 2; точное значение подбирается по задержке — scripts/asr-bench.sh.
  if [ "$PROFILE" = "shared-host" ]; then
    CPUS="$(nproc 2>/dev/null || echo 4)"; ASR_T=$(( CPUS / 2 )); [ "$ASR_T" -lt 1 ] && ASR_T=1; ASR_I=1; [ "$CPUS" -ge 8 ] && ASR_I=2
    set_var ASR_CPU_THREADS "$ASR_T"; set_var ASR_INTEROP_THREADS "$ASR_I"
    info "ASR на общем сервере: ASR_CPU_THREADS=$ASR_T, ASR_INTEROP_THREADS=$ASR_I (по $CPUS vCPU). Уточнить по задержке: scripts/asr-bench.sh"
  fi
  set_var LDAP_URIS "$LDAPU"; set_var LDAP_BASE_DN "$BASEDN"; set_var LDAP_BIND_DN "$BINDDN"; set_var LDAP_BIND_PASSWORD "$BINDPW"
  set_var LDAP_CA_FILE "$CA"; set_var LDAP_ADMIN_GROUP_DN "$ADMING"
  set_var POSTGRES_DB "${NAME//-/_}"; set_var POSTGRES_USER "${NAME//-/_}"
  set_var POSTGRES_PASSWORD "$(openssl rand -hex 24)"; set_var REDIS_PASSWORD "$(openssl rand -hex 24)"
  set_var LIVEKIT_API_KEY "pg$(openssl rand -hex 6)"; set_var LIVEKIT_API_SECRET "$(openssl rand -hex 32)"
  set_var INTERNAL_API_TOKEN "$(openssl rand -hex 32)"; set_var APP_MASTER_KEY "$(openssl rand -base64 32)"
  chmod 600 "$ENV"

  # Самопроверка: файл читается и bash (source), и нашим load_env ТОЧНО теми значениями, что введены; ничего не исполняется.
  for k in "${KEYS[@]}"; do
    err="$(mktemp)"
    got="$(env -i PATH="$PATH" bash -c 'set -a; . "$1"; printf "%s" "${!2}"' _ "$ENV" "$k" 2>"$err")" || true
    [ ! -s "$err" ] && [ "$got" = "${EXPECT[$k]}" ] || { rm -f "$err"; die "Проверка .env: значение $k читается неверно (bash source). Файл: $ENV"; }
    got="$(env -i PATH="$PATH" bash -c 'source "$1"; load_env "$2"; printf "%s" "${!3}"' _ "$REPO_ROOT/scripts/lib/common.sh" "$ENV" "$k" 2>"$err")" || true
    [ ! -s "$err" ] && [ "$got" = "${EXPECT[$k]}" ] || { rm -f "$err"; die "Проверка .env: значение $k читается неверно (load_env). Файл: $ENV"; }
    rm -f "$err"
  done
  ok ".env создан и проверен (секреты сгенерированы, права 600)."
  warn "СОХРАНИТЕ копию APP_MASTER_KEY отдельно (без него зашифрованные настройки не восстановить): $(grep '^APP_MASTER_KEY=' "$ENV")"
  [ -r "$CA" ] || warn "Файл CA $CA пока не найден — положите туда PEM цепочки CA, иначе preflight откажет."
fi

load_env "$ENV"
print_network_summary
[ "$ENV_ONLY" -eq 0 ] || { ok "--env-only: .env готов, дальше — scripts/preflight.sh и scripts/install.sh."; exit 0; }

log; log "== Модель распознавания =="
if [ -f "$DATA_ROOT/models/gigaam/${ASR_MODEL_NAME:-v3_e2e_rnnt}.ckpt" ]; then ok "Модель уже на месте."
else
  if ask_yn "Скачать модель сейчас (нужен интернет, ~0.5–2 ГБ)? [Y/n] (n — позже: scripts/models.sh [--from-dir …]) " Y; then "$REPO_ROOT/scripts/models.sh"; fi
fi

log; log "== Проверка (ничего не меняет) =="
"$REPO_ROOT/scripts/preflight.sh" --profile "$PROFILE" || die "Исправьте пункты FAIL и запустите scripts/setup.sh снова."

log; log "== План установки =="
"$REPO_ROOT/scripts/install.sh" --profile "$PROFILE" --dry-run --skip-preflight
ask_yn "Применить установку? Затрагиваются только объекты проекта (см. план выше) [y/N] " N || { info "Отменено. Повторный запуск безопасен."; exit 0; }
"$REPO_ROOT/scripts/install.sh" --profile "$PROFILE"
"$REPO_ROOT/scripts/smoke-test.sh" || warn "Smoke-test не пройден: scripts/status.sh, scripts/logs.sh"
ok "Готово. Откройте https://${NGINX_SERVER_NAME:-<домен>} и войдите доменной учёткой из группы администраторов."
