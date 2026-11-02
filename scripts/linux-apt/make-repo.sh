#!/usr/bin/env bash
# make-repo.sh — неподписанный репозиторий APT (стандартная структура Debian) из набора пакетов .deb:
#   scripts/linux-apt/make-repo.sh <каталог-с-пакетами> <каталог-сайта>
# Результат: <сайт>/pool/main/p/peregovorka/*.deb и <сайт>/dists/stable/{Release, main/binary-<arch>/Packages{,.gz,.xz}, …/by-hash/SHA256/<хэш>}.
# Индексы строит apt-ftparchive (Debian, пакет apt-utils) — в контейнере debian:13, если на машине его нет. Подпись — sign-repo.sh, проверка — verify-repo.py.
# Без состояния: репозиторий целиком пересобирается из набора пакетов (их история хранится в выпусках GitHub, а состояния репозитория — в Git).
set -euo pipefail
umask 022
DEBS="$(cd "${1:?каталог с пакетами}" && pwd)"; SITE="${2:?каталог сайта}"
mkdir -p "$SITE"; SITE="$(cd "$SITE" && pwd)"
rm -rf "$SITE/dists" "$SITE/pool"
POOL="$SITE/pool/main/p/peregovorka"
mkdir -p "$POOL"
shopt -s nullglob
n=0
for d in "$DEBS"/*.deb; do
  pkg="$(dpkg-deb -f "$d" Package)"; ver="$(dpkg-deb -f "$d" Version)"; arch="$(dpkg-deb -f "$d" Architecture)"
  [ "$pkg" = peregovorka ] || { echo "чужой пакет: $d ($pkg)" >&2; exit 1; }
  [ "$(basename "$d")" = "peregovorka_${ver}_${arch}.deb" ] || { echo "имя файла не совпадает с полями пакета: $d" >&2; exit 1; }
  cp "$d" "$POOL/"; n=$((n + 1))
done
[ "$n" -gt 0 ] || { echo "нет пакетов в $DEBS" >&2; exit 1; }
ARCHS="$(for d in "$POOL"/*.deb; do dpkg-deb -f "$d" Architecture; done | sort -u | tr '\n' ' ' | sed 's/ $//')"

# индексы и Release: apt-ftparchive на машине или в чистом контейнере Debian (версия образа закреплена в .github/workflows/linux.yml и здесь)
run_ftp() {
  if command -v apt-ftparchive >/dev/null 2>&1; then (cd "$SITE" && "$@")
  else docker run --rm -u "$(id -u):$(id -g)" -v "$SITE:/site" -w /site -e HOME=/tmp debian-ftparchive:13 "$@"; fi
}
if ! command -v apt-ftparchive >/dev/null 2>&1 && ! docker image inspect debian-ftparchive:13 >/dev/null 2>&1; then
  printf 'FROM debian:13\nRUN apt-get update -qq && apt-get install -y -qq --no-install-recommends apt-utils && rm -rf /var/lib/apt/lists/*\n' | docker build -q -t debian-ftparchive:13 - >/dev/null
fi
for a in $ARCHS; do
  B="dists/stable/main/binary-$a"; mkdir -p "$SITE/$B"
  run_ftp apt-ftparchive --arch "$a" packages pool/main > "$SITE/$B/Packages"
  gzip -9nk "$SITE/$B/Packages"; xz -9k "$SITE/$B/Packages"
  printf 'Archive: stable\nComponent: main\nOrigin: Peregovorka\nLabel: Peregovorka\nArchitecture: %s\n' "$a" > "$SITE/$B/Release"
  # by-hash: клиент, начавший обновление во время смены индексов, получит согласованную пару Release/Packages (Acquire-By-Hash)
  mkdir -p "$SITE/$B/by-hash/SHA256"
  for f in Packages Packages.gz Packages.xz; do cp "$SITE/$B/$f" "$SITE/$B/by-hash/SHA256/$(sha256sum "$SITE/$B/$f" | cut -d' ' -f1)"; done
done
run_ftp apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=Peregovorka -o APT::FTPArchive::Release::Label=Peregovorka \
  -o APT::FTPArchive::Release::Suite=stable -o APT::FTPArchive::Release::Codename=stable \
  -o "APT::FTPArchive::Release::Architectures=$ARCHS" -o APT::FTPArchive::Release::Components=main \
  -o "APT::FTPArchive::Release::Description=Peregovorka для Linux (приложение для корпоративных серверов Peregovorka)" \
  -o APT::FTPArchive::Release::Acquire-By-Hash=yes \
  release dists/stable > "$SITE/dists/stable/Release.new"
# apt-ftparchive включает в список и by-hash-копии — они не индексы: убираем их строки (размер и хэш те же, что у оригиналов)
grep -v '/by-hash/' "$SITE/dists/stable/Release.new" > "$SITE/dists/stable/Release"; rm "$SITE/dists/stable/Release.new"
echo "пакетов: $n; архитектуры: $ARCHS; $SITE/dists/stable/Release"
