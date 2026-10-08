#!/usr/bin/env bash
# Peregovorka — установка одной командой.
#
#   wget -O install.sh https://raw.githubusercontent.com/leonheard/peregovorka/main/install.sh
#   chmod +x install.sh
#   sudo ./install.sh
#
# Скрипт сам: проверит сервер, поставит недостающее (git, curl, openssl, Docker), скачает проект с GitHub в /opt/peregovorka,
# соберёт и запустит сервисы (база, контейнеры, миграции), создаст локального администратора и в конце покажет в терминале адрес,
# логин и первичный пароль. Дальше всё — в браузере: LDAPS, сертификаты, группы администраторов, SMB и почта.
#
# Параметры (все необязательны):
#   --host ИМЯ_ИЛИ_IP   под каким адресом сервер открывают в браузере (по умолчанию — основной IP сервера)
#   --https-port N      порт HTTPS (по умолчанию 443)
#   --dir КАТАЛОГ       куда установить проект (по умолчанию /opt/peregovorka)
#   --data КАТАЛОГ      где хранить данные: база, записи, сертификаты (по умолчанию /srv/peregovorka-data)
#   --ref ВЕРСИЯ        версия или ветка для установки (по умолчанию — последний релиз; main — самая свежая разработка)
#   --skip-models       не скачивать модель распознавания речи сейчас (позже: scripts/models.sh)
#   --yes               не задавать вопросов
#
# Установка ничего не удаляет и не обновляет на сервере без необходимости: устанавливаются только Docker (если его нет) и nginx для HTTPS.
# Повторный запуск безопасен: уже установленный экземпляр не затрагивается — скрипт подскажет, как обновиться.
set -euo pipefail

REPO_URL="${PEREGOVORKA_REPO:-https://github.com/leonheard/peregovorka.git}"
DIR="/opt/peregovorka"; DATA=""; HOST=""; PORT="443"; REF=""; SKIP_MODELS=0; YES=0

if [ -t 1 ]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; O=$'\033[0m'; else B=''; G=''; Y=''; R=''; O=''; fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s== %s ==%s\n' "$B" "$*" "$O"; }
ok()   { printf '%s[ok]%s %s\n' "$G" "$O" "$*"; }
warn() { printf '%s[!]%s %s\n' "$Y" "$O" "$*" >&2; }
die()  { printf '%s[ОШИБКА]%s %s\n' "$R" "$O" "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?}"; shift 2 ;;
    --https-port) PORT="${2:?}"; shift 2 ;;
    --dir) DIR="${2:?}"; shift 2 ;;
    --data) DATA="${2:?}"; shift 2 ;;
    --ref) REF="${2:?}"; shift 2 ;;
    --skip-models) SKIP_MODELS=1; shift ;;
    --yes|-y) YES=1; shift ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) die "Неизвестный параметр: $1 (справка: ./install.sh --help)" ;;
  esac
done

# --- права: установка требует root (Docker, nginx, каталоги данных)
if [ "$(id -u)" -ne 0 ]; then
  command -v sudo >/dev/null 2>&1 || die "Запустите от root: sudo ./install.sh (команда sudo не найдена — войдите под root)"
  say "Нужны права администратора сервера — перезапускаю через sudo…"
  exec sudo -E bash "$0" "$@"
fi

step "Проверка сервера"
[ "$(uname -s)" = "Linux" ] || die "Поддерживается Linux (рекомендуется Ubuntu 24.04)."
APT=0
if [ -r /etc/os-release ]; then
  . /etc/os-release
  case "${ID:-}:${ID_LIKE:-}" in *ubuntu*|*debian*) APT=1 ;; esac
  ok "Система: ${PRETTY_NAME:-Linux}"
fi
CPUS="$(nproc 2>/dev/null || echo 1)"; MEM_MB="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
FREE_GB="$(df -Pk "$(dirname "$DIR")" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}' || echo 0)"
say "    процессоров: $CPUS, памяти: ${MEM_MB} МБ, свободно на диске: ${FREE_GB} ГБ"
[ "$CPUS" -ge 2 ] || warn "Мало процессоров ($CPUS): распознавание речи будет медленным (рекомендуется от 4)."
[ "$MEM_MB" -ge 6000 ] || warn "Мало памяти (${MEM_MB} МБ): рекомендуется от 8 ГБ."
[ "$FREE_GB" -ge 15 ] || warn "Мало свободного места (${FREE_GB} ГБ): нужно не менее 20 ГБ (образы, модель, записи)."
command -v systemctl >/dev/null 2>&1 || warn "systemd не найден — автозапуск Docker и nginx придётся настроить самостоятельно."

# --- уже установлено? Признак завершённой установки — запись установщика в DATA_ROOT/state (её делает scripts/install.sh в самом конце)
RESUME=0
if [ -d "$DIR/.git" ] && [ -f "$DIR/.env" ]; then
  droot="$(sed -n 's/^DATA_ROOT=//p' "$DIR/.env" | head -1 | tr -d "\"'")"
  if [ -n "$droot" ] && [ -s "$droot/state/install-history.log" ]; then DONE=1; else DONE=0; fi
else
  DONE=0
fi
if [ "$DONE" -eq 0 ] && [ -d "$DIR/.git" ] && [ -f "$DIR/.env" ]; then RESUME=1; warn "Найдена прерванная установка в $DIR — продолжаю с того места, где остановились."; fi
if [ "$DONE" -eq 1 ]; then
  ok "Peregovorka уже установлена в $DIR — повторная установка не нужна."
  say ""
  say "  Обновить до новой версии:      cd $DIR && sudo ./scripts/update.sh"
  say "  Состояние системы:             cd $DIR && ./scripts/status.sh"
  say "  Потерян пароль администратора: cd $DIR && sudo ./scripts/admin-reset.sh"
  exit 0
fi
if [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ] && [ ! -d "$DIR/.git" ]; then
  die "Каталог $DIR уже существует и не пуст. Укажите другой: --dir /путь, или освободите этот."
fi

# --- что будет сделано
HOST_SHOWN="${HOST:-<основной IP сервера>}"
step "Что будет сделано"
say "  • установка в:        $DIR"
say "  • данные (БД, записи): ${DATA:-/srv/peregovorka-data}"
say "  • адрес для входа:    https://$HOST_SHOWN$([ "$PORT" = "443" ] || printf ':%s' "$PORT")  (сертификат HTTPS — самоподписанный, заменить можно позже)"
say "  • будут установлены при необходимости: git, curl, openssl, Docker, nginx"
say "  • будет скачана модель распознавания речи (до ~2 ГБ) и собраны образы — это займёт 10–30 минут"
if [ "$YES" -ne 1 ] && [ -t 0 ]; then
  read -r -p "Продолжить? [Y/n] " a || exit 1
  [[ "${a:-Y}" =~ ^[Yy]$ ]] || { say "Отменено. Ничего не изменено."; exit 0; }
fi

# --- зависимости
step "Подготовка"
need=()
for c in git curl openssl; do command -v "$c" >/dev/null 2>&1 || need+=("$c"); done
if [ "${#need[@]}" -gt 0 ]; then
  [ "$APT" -eq 1 ] || die "Не найдены: ${need[*]}. Установите их средствами вашей системы и запустите установку снова."
  say "Устанавливаю: ${need[*]} ca-certificates…"
  DEBIAN_FRONTEND=noninteractive apt-get update -y >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${need[@]}" ca-certificates >/dev/null
fi
ok "git, curl, openssl на месте"

# --- версия для установки: последний релиз (тег vX.Y.Z), иначе main
if [ -z "$REF" ]; then
  REF="$(git ls-remote --tags --refs --sort=-v:refname "$REPO_URL" 'v[0-9]*.[0-9]*.[0-9]*' 2>/dev/null | head -1 | sed 's|.*refs/tags/||' || true)"
  [ -n "$REF" ] || { warn "Не удалось определить последний релиз — беру ветку main."; REF="main"; }
fi
step "Загрузка проекта ($REF)"
if [ "$RESUME" -eq 0 ]; then
  mkdir -p "$(dirname "$DIR")"
  git clone --quiet --depth 1 --branch "$REF" "$REPO_URL" "$DIR" || die "Не удалось скачать $REPO_URL (версия $REF). Проверьте доступ к github.com с этого сервера."
fi
ok "Проект: $DIR ($(git -C "$DIR" describe --tags --always 2>/dev/null || echo "$REF"))"

# --- установка
step "Установка и запуск сервисов"
args=(--auto --https-port "$PORT" --name peregovorka)
[ -n "$HOST" ] && args+=(--host "$HOST")
[ -n "$DATA" ] && args+=(--data "$DATA") || args+=(--data /srv/peregovorka-data)
[ "$SKIP_MODELS" -eq 1 ] && args+=(--skip-models)
cd "$DIR"
chmod +x scripts/*.sh scripts/lib/*.sh 2>/dev/null || true
if ! ./scripts/setup.sh "${args[@]}"; then
  say ""
  die "Установка прервана. Повторный запуск безопасен: sudo $0 — завершённые шаги будут пропущены (подробности выше)."
fi
