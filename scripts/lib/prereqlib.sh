#!/usr/bin/env bash
# Манифест системных зависимостей Peregovorka и их установка на «чистой» ОС (Ubuntu/Debian).
#
# Единственный источник правды о том, какие внешние программы нужны скриптам проекта (install.sh, scripts/*.sh, scripts/lib/*.sh).
# Тест tests/scripts/part_prereq.sh сверяет манифест с реальным содержимым скриптов: появилась в скрипте новая программа, которой нет в манифесте, —
# тест падает, и установщик не сможет «забыть» её поставить.
#
#   prereq_os_detect                 определяет ОС (PREREQ_ID, PREREQ_VERSION, PREREQ_CODENAME, PREREQ_FAMILY, PREREQ_SUPPORTED, PREREQ_WHY)
#   prereq_missing_bins              печатает отсутствующие команды из манифеста (по одной в строке)
#   prereq_packages_for CMD…         apt-пакеты для указанных команд
#   prereq_docker_missing            печатает, чего не хватает у Docker: engine | compose | buildx
#   prereq_install_all [standalone]  ставит ВСЁ недостающее (нужен root); для shared-host Docker и nginx не ставятся
#
# Файл только определяет функции — сам ничего не выполняет.

if [ -n "${_VM_PREREQLIB_LOADED:-}" ]; then return 0; fi
_VM_PREREQLIB_LOADED=1

# ---- манифест: команда → apt-пакет (Debian/Ubuntu). Команды из coreutils/util-linux и т. п. перечислены явно: «чистая» минимальная ОС их может не иметь.
declare -A PREREQ_PKG=(
  # сеть и сертификаты
  [git]=git [curl]=curl [wget]=wget [openssl]=openssl [update-ca-certificates]=ca-certificates [gpg]=gnupg
  [ss]=iproute2 [ip]=iproute2
  # текст и файлы
  [awk]=gawk [sed]=sed [grep]=grep [find]=findutils [xargs]=findutils [diff]=diffutils [cmp]=diffutils [tar]=tar
  [cat]=coreutils [cp]=coreutils [mv]=coreutils [rm]=coreutils [mkdir]=coreutils [ln]=coreutils [chmod]=coreutils [chown]=coreutils [mktemp]=coreutils
  [stat]=coreutils [df]=coreutils [du]=coreutils [head]=coreutils [tail]=coreutils [sort]=coreutils [uniq]=coreutils [cut]=coreutils [tr]=coreutils
  [wc]=coreutils [ls]=coreutils [seq]=coreutils [tac]=coreutils [tee]=coreutils [date]=coreutils [sleep]=coreutils [basename]=coreutils [dirname]=coreutils [readlink]=coreutils
  [base64]=coreutils [sha256sum]=coreutils [nproc]=coreutils [id]=coreutils [uname]=coreutils [env]=coreutils [timeout]=coreutils [install]=coreutils
  # система
  [flock]=util-linux [findmnt]=util-linux [mountpoint]=util-linux
  [free]=procps [ps]=procps [sysctl]=procps
  [dpkg]=dpkg [hostname]=hostname [python3]=python3
  # приложения
  [docker]=docker-ce [nginx]=nginx
)
# Что нужно только в профиле standalone (на shared-host Docker и nginx уже есть у администратора и не ставятся автоматически).
PREREQ_STANDALONE_ONLY=(docker nginx)
# Программы, которые скрипты используют, но они не нужны на хосте: выполняются в контейнерах или необязательны.
# systemctl: в скриптах всегда под проверкой `command -v systemctl`; без systemd не работает только служба-помощник (его ставят вручную: updater.sh run)
PREREQ_NOT_HOST=(pg_dump redis-cli psql alembic python pip livekit-server ufw sudo journalctl systemctl)

prereq_os_detect() {
  local f="${PREREQ_OS_RELEASE:-/etc/os-release}"
  PREREQ_ID=""; PREREQ_VERSION=""; PREREQ_CODENAME=""; PREREQ_FAMILY="other"; PREREQ_SUPPORTED="no"; PREREQ_WHY=""
  if [ ! -r "$f" ]; then PREREQ_WHY="не найден $f — не Linux или нестандартная система"; return 1; fi
  local ID="" ID_LIKE="" VERSION_ID="" VERSION_CODENAME="" UBUNTU_CODENAME=""
  # shellcheck disable=SC1090
  . "$f"
  PREREQ_ID="$ID"; PREREQ_VERSION="$VERSION_ID"; PREREQ_CODENAME="${VERSION_CODENAME:-$UBUNTU_CODENAME}"
  case " $ID $ID_LIKE " in
    *" ubuntu "*) PREREQ_FAMILY="ubuntu"; PREREQ_CODENAME="${UBUNTU_CODENAME:-$VERSION_CODENAME}" ;;
    *" debian "*) PREREQ_FAMILY="debian" ;;
  esac
  case "$PREREQ_FAMILY:$ID" in
    ubuntu:ubuntu) local major="${VERSION_ID%%.*}"
      if [ "${major:-0}" -ge 22 ] 2>/dev/null; then PREREQ_SUPPORTED="yes"; else PREREQ_WHY="Ubuntu $VERSION_ID слишком старая (нужна 22.04 или новее; рекомендуется 24.04)"; fi ;;
    debian:debian) local dm="${VERSION_ID%%.*}"
      if [ "${dm:-0}" -ge 12 ] 2>/dev/null; then PREREQ_SUPPORTED="yes"; else PREREQ_WHY="Debian $VERSION_ID слишком старый (нужен 12 «bookworm» или новее)"; fi ;;
    ubuntu:*|debian:*) PREREQ_SUPPORTED="yes"; PREREQ_WHY="производная система ($ID, семейство $PREREQ_FAMILY): установка по правилам $PREREQ_FAMILY, без гарантий" ;;
    *) PREREQ_WHY="система $ID не поддерживается автоматической установкой (нужны Ubuntu 22.04+/24.04 или Debian 12+); установите зависимости вручную и используйте профиль shared-host" ;;
  esac
  [ "$PREREQ_SUPPORTED" = yes ]
}

prereq_missing_bins() {
  local profile="${1:-standalone}" c skip
  for c in "${!PREREQ_PKG[@]}"; do
    if [ "$profile" != standalone ]; then
      for skip in "${PREREQ_STANDALONE_ONLY[@]}"; do [ "$c" = "$skip" ] && continue 2; done
    fi
    command -v "$c" >/dev/null 2>&1 || printf '%s\n' "$c"
  done | sort
}

prereq_packages_for() {
  local c; for c in "$@"; do printf '%s\n' "${PREREQ_PKG[$c]:-$c}"; done | sort -u
}

# Недостающее у Docker: engine, compose (plugin v2), buildx (plugin). Пусто — всё есть.
prereq_docker_missing() {
  if ! command -v docker >/dev/null 2>&1; then printf '%s\n' engine compose buildx; return 0; fi
  docker compose version >/dev/null 2>&1 || printf '%s\n' compose
  docker buildx version >/dev/null 2>&1 || printf '%s\n' buildx
  return 0
}

# Ключ и репозиторий Docker для семейства ОС. Печатает URL репозитория.
prereq_docker_repo_url() {
  case "$PREREQ_FAMILY" in ubuntu) echo "https://download.docker.com/linux/ubuntu" ;; debian) echo "https://download.docker.com/linux/debian" ;; *) return 1 ;; esac
}

prereq_log()  { if declare -F info >/dev/null 2>&1; then info "$*"; else printf '[i] %s\n' "$*"; fi; }
prereq_warn() { if declare -F warn >/dev/null 2>&1; then warn "$*"; else printf '[warn] %s\n' "$*" >&2; fi; }
prereq_run()  { if declare -F run >/dev/null 2>&1; then run "$@"; else "$@"; fi; }

# NEEDRESTART_MODE=a: на Ubuntu 22.04+ needrestart иначе спрашивает про перезапуск служб; Lock::Timeout: сразу после старта сервера apt занят unattended-upgrades
_apt() { prereq_run env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get -o DPkg::Lock::Timeout=300 "$@"; }

prereq_install_docker() {
  local missing; missing="$(prereq_docker_missing | tr '\n' ' ')"
  [ -n "${missing// /}" ] || { prereq_log "Docker, Compose и Buildx уже установлены."; return 0; }
  prereq_log "Устанавливаю Docker (не хватает: $missing) из официального репозитория Docker…"
  local url codename arch
  url="$(prereq_docker_repo_url)" || { prereq_warn "Репозиторий Docker для этой ОС неизвестен"; return 1; }
  codename="$PREREQ_CODENAME"; arch="$(dpkg --print-architecture 2>/dev/null || echo amd64)"
  _apt install -y ca-certificates curl gnupg || return 1
  prereq_run install -m 0755 -d /etc/apt/keyrings
  if [ "${DRY_RUN:-0}" != 1 ]; then
    curl -fsSL "$url/gpg" -o /etc/apt/keyrings/docker.asc && chmod a+r /etc/apt/keyrings/docker.asc || { prereq_warn "Не удалось скачать ключ Docker ($url/gpg)"; }
    printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] %s %s stable\n' "$arch" "$url" "$codename" > /etc/apt/sources.list.d/docker.list
  else
    prereq_log "[dry-run] будет создан /etc/apt/sources.list.d/docker.list ($url $codename)"
  fi
  _apt update -y || true
  if ! _apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
    prereq_warn "Пакеты из репозитория Docker не поставились — пробую пакеты дистрибутива (docker.io, docker-compose-v2, docker-buildx)."
    rm -f /etc/apt/sources.list.d/docker.list 2>/dev/null || true
    _apt update -y || true
    _apt install -y docker.io docker-compose-v2 docker-buildx || _apt install -y docker.io docker-compose docker-buildx || return 1
  fi
  if command -v systemctl >/dev/null 2>&1; then prereq_run systemctl enable --now docker >/dev/null 2>&1 || true; fi
  return 0
}

# prereq_install_all [standalone|shared-host] — всё необходимое. Не делает upgrade/dist-upgrade/autoremove и не трогает чужие пакеты.
prereq_install_all() {
  local profile="${1:-standalone}"
  [ "$(id -u)" -eq 0 ] || [ "${DRY_RUN:-0}" = 1 ] || { prereq_warn "Нужны права root для установки зависимостей"; return 1; }
  prereq_os_detect || { prereq_warn "${PREREQ_WHY}"; [ "$PREREQ_SUPPORTED" = yes ] || return 1; }
  [ -z "$PREREQ_WHY" ] || prereq_warn "$PREREQ_WHY"
  command -v apt-get >/dev/null 2>&1 || { prereq_warn "apt-get не найден"; return 1; }
  local miss pkgs
  miss="$(prereq_missing_bins "$profile" | grep -vxE 'docker' | tr '\n' ' ')"
  if [ -n "${miss// /}" ]; then
    # shellcheck disable=SC2086
    pkgs="$(prereq_packages_for $miss | tr '\n' ' ')"
    prereq_log "Устанавливаю недостающие системные пакеты: $pkgs"
    _apt update -y || prereq_warn "apt update завершился с ошибкой — продолжаю с имеющимся кэшем пакетов"
    # shellcheck disable=SC2086
    _apt install -y --no-install-recommends $pkgs || { prereq_warn "Не удалось установить: $pkgs"; return 1; }
  else
    prereq_log "Системные пакеты на месте."
  fi
  if [ "$profile" = standalone ]; then
    prereq_install_docker || return 1
    if command -v systemctl >/dev/null 2>&1 && command -v nginx >/dev/null 2>&1; then prereq_run systemctl enable --now nginx >/dev/null 2>&1 || true; fi
  fi
  return 0
}

# Итоговая проверка: печатает по строке на проблему; код 0 — всё есть.
prereq_verify() {
  local profile="${1:-standalone}" bad=0 m
  while IFS= read -r m; do [ -n "$m" ] && { printf 'нет команды: %s\n' "$m"; bad=1; }; done < <(prereq_missing_bins "$profile")
  if [ "$profile" = standalone ]; then
    while IFS= read -r m; do [ -n "$m" ] && { printf 'Docker: не хватает %s\n' "$m"; bad=1; }; done < <(prereq_docker_missing)
  fi
  return "$bad"
}
