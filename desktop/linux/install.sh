#!/bin/sh
# Установка Peregovorka для Linux (Ubuntu 22.04+, Debian 12+ и совместимые) из репозитория APT Peregovorka:
#   curl -fsSL https://calleograph.github.io/peregovorka-apt/install.sh | sudo sh
# Что делает: проверяет систему и архитектуру; ставит недостающие curl и сертификаты; скачивает открытый ключ репозитория и сверяет его
# с закреплённым здесь отпечатком (SHA-256 файла и, если есть gpg, отпечаток ключа); кладёт ключ в /usr/share/keyrings, описывает источник
# с signed-by (ключ действует только для этого репозитория); ставит пакет peregovorka. Дальше обновления — apt update && apt upgrade.
# Ничего не меняет в автоматических обновлениях системы (unattended-upgrades) — это решение администратора, см. docs/LINUX.md.
set -eu

BASE="${PEREGOVORKA_APT_URL:-https://calleograph.github.io/peregovorka-apt}"
FINGERPRINT="48A75B62BFA817D7AAD3E84E2B00ADE5127C5DF9"
KEY_SHA256="6a8d14a04a6705e52394435c6f0961ff015f58450e3bf2da11c810db0d32445e"
ARCHS="amd64"                                   # архитектуры, проверенные на настоящих системах (docs/LINUX.md)
KEYRING=/usr/share/keyrings/peregovorka-archive-keyring.gpg
SOURCES=/etc/apt/sources.list.d/peregovorka.sources

say() { printf 'Peregovorka: %s\n' "$*"; }
die() { printf 'Peregovorka: ошибка: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" = 0 ] || die "нужны права администратора: curl -fsSL $BASE/install.sh | sudo sh"
case "$BASE" in
  https://*) ;;
  http://127.0.0.1:*|http://localhost:*) [ "${PEREGOVORKA_APT_INSECURE_TEST:-}" = 1 ] || die "адрес репозитория должен быть https: $BASE" ;;
  *) die "адрес репозитория должен быть https: $BASE" ;;
esac

# --------------------------------------------------------------- система
[ -r /etc/os-release ] || die "не удалось определить систему (/etc/os-release)"
. /etc/os-release
command -v apt-get >/dev/null 2>&1 && command -v dpkg >/dev/null 2>&1 || die "нужна система с APT (Ubuntu, Debian и совместимые)"
case " ${ID:-} ${ID_LIKE:-} " in *" debian "*|*" ubuntu "*) ;; *) die "система ${PRETTY_NAME:-неизвестна} не основана на Debian или Ubuntu" ;; esac
major="${VERSION_ID%%.*}"
case "${ID:-}" in
  ubuntu) [ "${major:-0}" -ge 22 ] 2>/dev/null || die "нужна Ubuntu 22.04 или новее (сейчас ${VERSION_ID:-?})" ;;
  debian) [ "${major:-0}" -ge 12 ] 2>/dev/null || die "нужен Debian 12 или новее (сейчас ${VERSION_ID:-?})" ;;
  *) say "система ${PRETTY_NAME:-?} основана на ${ID_LIKE:-Debian}; проверены Ubuntu 22.04/24.04 и Debian 12/13 — продолжаю" ;;
esac
arch="$(dpkg --print-architecture)"
case " $ARCHS " in *" $arch "*) ;; *) die "архитектура $arch пока не поддерживается (поддерживаются: $ARCHS)" ;; esac

# --------------------------------------------------------------- инструменты
export DEBIAN_FRONTEND=noninteractive
if ! command -v curl >/dev/null 2>&1 || [ ! -s /etc/ssl/certs/ca-certificates.crt ]; then
  say "ставлю curl и корневые сертификаты"
  apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates >/dev/null || die "не удалось установить curl"
fi

# --------------------------------------------------------------- ключ репозитория
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL --proto '=https,http' --retry 3 -o "$tmp/key.gpg" "$BASE/peregovorka-archive-keyring.gpg" || die "не удалось скачать ключ репозитория ($BASE)"
got="$(sha256sum "$tmp/key.gpg" | cut -d' ' -f1)"
[ "$got" = "$KEY_SHA256" ] || die "ключ репозитория не совпадает с ожидаемым (SHA-256 $got) — установка остановлена. Проверьте адрес и сообщите администратору."
if command -v gpg >/dev/null 2>&1; then
  gpg --batch --show-keys --with-colons "$tmp/key.gpg" 2>/dev/null | grep -q "^fpr:::::::::$FINGERPRINT:" || die "отпечаток ключа не $FINGERPRINT — установка остановлена"
fi
install -m 0644 "$tmp/key.gpg" "$KEYRING"
say "ключ репозитория: $FINGERPRINT → $KEYRING"

# --------------------------------------------------------------- источник и установка
cat > "$SOURCES" <<EOF
# Репозиторий Peregovorka: обновления приложения через apt update && apt upgrade. Удалить: sudo rm $SOURCES
Types: deb
URIs: $BASE
Suites: stable
Components: main
Architectures: $arch
Signed-By: $KEYRING
EOF
chmod 0644 "$SOURCES"
say "репозиторий: $SOURCES"
apt-get update -qq || say "apt update сообщил об ошибках (возможно, в других источниках системы) — проверяю репозиторий Peregovorka"
apt-cache policy peregovorka 2>/dev/null | grep -Eq 'Candidate: [0-9]' || die "apt не смог прочитать репозиторий Peregovorka (подпись или сеть) — подробности выше"
apt-get install -y peregovorka || die "установка пакета не удалась — подробности выше"
say "установлено: $(dpkg-query -W -f '${Version}' peregovorka). Запуск — «Peregovorka» в меню приложений или команда peregovorka."
say "обновление: sudo apt update && sudo apt upgrade; удаление: sudo apt remove peregovorka (с репозиторием и ключом — sudo apt purge peregovorka)"
