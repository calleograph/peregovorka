#!/usr/bin/env bash
# setup.sh — «мастер» первой установки на сервере одной командой. Интерактивный; ничего не делает молча.
#
#   scripts/setup.sh [--profile shared-host|standalone]
#
# Шаги: (1) задаёт вопросы и создаёт .env со случайными секретами и ПРОВЕРЕННЫМИ свободными портами;
# (2) готовит модель ASR; (3) preflight (read-only); (4) показывает план install --dry-run;
# (5) только после вашего «y» выполняет install и smoke-test.
# Все гарантии install.sh сохраняются: только объекты этого проекта, без upgrade/prune/reboot,
# чужие контейнеры, nginx-сайты, Apache/PHP/Moodle и файрвол не затрагиваются.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

PROFILE="shared-host"
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) PROFILE="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) die "Неизвестный аргумент: $1" ;;
  esac
done
case "$PROFILE" in shared-host|standalone) ;; *) die "--profile: shared-host|standalone" ;; esac
[ -t 0 ] || die "Мастер интерактивный: запустите в терминале (или заполните .env вручную: docs/INSTALL_AND_UPDATE.md)."
command -v openssl >/dev/null || die "Нужен openssl"

ENV="$REPO_ROOT/.env"
[ ! -f "$ENV" ] || { read -r -p ".env уже существует. Использовать как есть и перейти к проверкам? [Y/n] " a; [[ "${a:-Y}" =~ ^[Yy]$ ]] || die "Остановлено: удалите/переименуйте .env, чтобы создать заново."; }

set_var() { # set_var KEY VALUE  — без eval и экранирования
  local k="$1" v="$2" tmp; tmp="$(mktemp)"
  K="$k" V="$v" awk 'BEGIN{k=ENVIRON["K"]; v=ENVIRON["V"]; done=0}
    $0 ~ "^"k"=" && !done {print k"="v; done=1; next} {print} END{if(!done) print k"="v}' "$ENV" > "$tmp" && cat "$tmp" > "$ENV"; rm -f "$tmp"
}
ask() { # ask "вопрос" "по умолчанию" -> REPLY
  local d="${2:-}"; read -r -p "$1${d:+ [$d]}: " REPLY; REPLY="${REPLY:-$d}"
}
port_free() { # tcp|udp port
  ! port_busy "$1" "$2" && ! { command -v docker >/dev/null && docker ps --format '{{.Ports}}' 2>/dev/null | grep -Eq ":$2->"; }
}
find_port() { # proto start
  local p="$2"; while ! port_free "$1" "$p"; do p=$((p+1)); done; echo "$p"
}

if [ ! -f "$ENV" ]; then
  cp "$REPO_ROOT/.env.example" "$ENV"; chmod 600 "$ENV"
  log "== Основные параметры =="
  host_version_info
  existing="$(docker ps -a --format '{{.Names}}' 2>/dev/null | head -50 | tr '\n' ' ' || true)"
  [ -n "$existing" ] && info "На сервере уже есть контейнеры (не затрагиваются): $existing"
  ask "Имя экземпляра (уникальное на сервере; a-z0-9-)" "peregovorka-prod"; NAME="$REPLY"
  [[ "$NAME" =~ ^[a-z][a-z0-9_-]{2,40}$ ]] || die "Недопустимое имя"
  if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${NAME}[-_]"; then die "Контейнеры с префиксом ${NAME} уже существуют — выберите другое имя"; fi
  ask "DNS-имя сайта (HTTPS на внешнем прокси/nginx)" "meet.example.org"; HOSTN="$REPLY"
  defip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
  ask "IP сервера, по которому до него доходят клиенты (для звука)" "${defip:-}"; NODEIP="$REPLY"
  ask "Каталог постоянных данных (вне репозитория)" "/srv/${NAME}-data"; DATA="$REPLY"
  ask "Сколько прокси стоит перед сервисом (nginx сервера = 1; внешний proxy + nginx = 2)" "2"; HOPS="$REPLY"
  WEB="$(find_port tcp 18480)"; LKH="$(find_port tcp 17880)"; LKT="$(find_port tcp 17881)"; LKU="$(find_port udp 17882)"; NGP="$(find_port tcp 18400)"
  info "Подобраны СВОБОДНЫЕ порты (проверено): web=$WEB livekit-api=$LKH ice-tcp=$LKT ice-udp=$LKU nginx=$NGP"
  ask "Принять порты? (n — задать вручную)" "y"
  if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    ask "web TCP" "$WEB"; WEB="$REPLY"; ask "livekit api TCP (loopback)" "$LKH"; LKH="$REPLY"
    ask "livekit ICE TCP" "$LKT"; LKT="$REPLY"; ask "livekit ICE UDP" "$LKU"; LKU="$REPLY"; ask "nginx TCP" "$NGP"; NGP="$REPLY"
  fi
  log; log "== Active Directory =="
  ask "LDAPS-серверы через запятую (ldaps://dc1.corp.local:636,...)" ""; LDAPU="$REPLY"
  ask "Base DN (DC=corp,DC=local)" ""; BASEDN="$REPLY"
  ask "DN сервисной учётки (только чтение)" ""; BINDDN="$REPLY"
  read -r -s -p "Пароль сервисной учётки: " BINDPW; echo
  ask "Путь к PEM с CA контроллеров домена" "/etc/${NAME}/ad-ca.pem"; CA="$REPLY"
  ask "DN группы администраторов системы" ""; ADMING="$REPLY"

  set_var COMPOSE_PROJECT_NAME "$NAME"; set_var INSTALL_PROFILE "$PROFILE"
  set_var APP_PUBLIC_URL "https://$HOSTN"; set_var LIVEKIT_PUBLIC_URL "wss://$HOSTN/livekit"
  set_var NGINX_SERVER_NAME "$HOSTN"; set_var NGINX_SITE_NAME "$NAME"; set_var NGINX_LISTEN_PORT "$NGP"
  set_var APP_ROOT "$REPO_ROOT"; set_var DATA_ROOT "$DATA"; set_var BACKUP_DIR "$DATA/backups"; set_var TRUSTED_PROXY_HOPS "$HOPS"
  set_var WEB_PORT "$WEB"; set_var LIVEKIT_HTTP_PORT "$LKH"; set_var LIVEKIT_TCP_PORT "$LKT"; set_var LIVEKIT_UDP_PORT "$LKU"
  set_var LIVEKIT_NODE_IP "$NODEIP"
  set_var LDAP_URIS "$LDAPU"; set_var LDAP_BASE_DN "$BASEDN"; set_var LDAP_BIND_DN "$BINDDN"; set_var LDAP_BIND_PASSWORD "$BINDPW"
  set_var LDAP_CA_FILE "$CA"; set_var LDAP_ADMIN_GROUP_DN "$ADMING"
  set_var POSTGRES_DB "${NAME//-/_}"; set_var POSTGRES_USER "${NAME//-/_}"
  set_var POSTGRES_PASSWORD "$(openssl rand -hex 24)"; set_var REDIS_PASSWORD "$(openssl rand -hex 24)"
  set_var LIVEKIT_API_KEY "pg$(openssl rand -hex 6)"; set_var LIVEKIT_API_SECRET "$(openssl rand -hex 32)"
  set_var INTERNAL_API_TOKEN "$(openssl rand -hex 32)"; set_var APP_MASTER_KEY "$(openssl rand -base64 32)"
  chmod 600 "$ENV"
  ok ".env создан (секреты сгенерированы, права 600)."
  warn "СОХРАНИТЕ копию APP_MASTER_KEY отдельно (без него зашифрованные настройки не восстановить): $(grep '^APP_MASTER_KEY=' "$ENV")"
  [ -r "$CA" ] || warn "Файл CA $CA пока не найден — положите туда PEM цепочки CA, иначе preflight откажет."
fi

log; log "== Модель распознавания =="
load_env "$ENV"
if [ -f "$DATA_ROOT/models/gigaam/${ASR_MODEL_NAME:-v3_e2e_rnnt}.ckpt" ]; then ok "Модель уже на месте."
else ask "Скачать модель сейчас (нужен интернет, ~1–2 ГБ)? (n — позже: scripts/models.sh [--from-dir …])" "y"
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then "$REPO_ROOT/scripts/models.sh"; fi; fi

log; log "== Проверка (ничего не меняет) =="
"$REPO_ROOT/scripts/preflight.sh" --profile "$PROFILE" || die "Исправьте пункты FAIL и запустите scripts/setup.sh снова."

log; log "== План установки =="
"$REPO_ROOT/scripts/install.sh" --profile "$PROFILE" --dry-run --skip-preflight
ask "Применить установку? Затрагиваются только объекты проекта (см. план выше) (y/N)" "N"
[[ "$REPLY" =~ ^[Yy]$ ]] || { info "Отменено. Повторный запуск безопасен."; exit 0; }
"$REPO_ROOT/scripts/install.sh" --profile "$PROFILE"
"$REPO_ROOT/scripts/smoke-test.sh" || warn "Smoke-test не пройден: scripts/status.sh, scripts/logs.sh"
ok "Готово. Откройте https://${NGINX_SERVER_NAME:-<домен>} и войдите доменной учёткой из группы администраторов."
