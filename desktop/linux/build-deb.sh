#!/usr/bin/env bash
# build-deb.sh — пакет .deb Peregovorka для Linux из распакованной программы electron-builder (`--linux dir`).
#   desktop/linux/build-deb.sh <распакованная-программа> <версия X.Y.Z> <amd64|arm64> <каталог-результата>
# Проверяет архитектуру исполняемого файла, приводит права (файлы 644, программы 755, chrome-sandbox 4755 root), собирает пакет от имени root
# без fakeroot (dpkg-deb --root-owner-group), сжимает xz (читает dpkg любой поддерживаемой системы). Закрытых ключей не касается.
set -euo pipefail
umask 022                                   # права в пакете не зависят от umask машины сборки
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:?распакованная программа}"; VER="${2:?версия}"; ARCH="${3:?amd64|arm64}"; OUT="${4:?каталог результата}"
REPO_URL="${PEREGOVORKA_APT_URL:-https://calleograph.github.io/peregovorka-apt}"

[[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "версия должна быть X.Y.Z: $VER" >&2; exit 2; }
[[ "$REPO_URL" =~ ^https://[A-Za-z0-9./_-]+$ ]] || { echo "адрес репозитория должен быть https: $REPO_URL" >&2; exit 2; }
[ -x "$SRC/peregovorka" ] && [ -f "$SRC/resources/app.asar" ] && [ -f "$SRC/chrome-sandbox" ] || { echo "$SRC — не распакованная программа Peregovorka" >&2; exit 2; }

# версия программы внутри app.asar (package.json) должна совпадать с версией пакета: по ней работающая программа понимает, что APT поставил новую
asar_version="$(node -e '
  const fs = require("fs"); const b = fs.readFileSync(process.argv[1]);
  const n = b.readUInt32LE(12); const h = JSON.parse(b.subarray(16, 16 + n).toString());           // заголовок asar: pickle(size) + pickle(json)
  const f = h.files["package.json"]; const base = 8 + b.readUInt32LE(4);
  process.stdout.write(JSON.parse(b.subarray(base + Number(f.offset), base + Number(f.offset) + f.size)).version);' "$SRC/resources/app.asar")"
[ "$asar_version" = "$VER" ] || { echo "версия программы в app.asar ($asar_version) не совпадает с версией пакета ($VER)" >&2; exit 2; }

# архитектура: поле e_machine заголовка ELF (0x3e — x86-64, 0xb7 — AArch64)
machine="$(od -An -tx2 -j18 -N2 "$SRC/peregovorka" | tr -d ' ')"
case "$ARCH:$machine" in
  amd64:003e|arm64:00b7) ;;
  *) echo "исполняемый файл не для $ARCH (e_machine=$machine)" >&2; exit 2 ;;
esac

ST="$(mktemp -d)"; trap 'rm -rf "$ST"' EXIT
P="$ST/peregovorka"
install -d "$P/DEBIAN" "$P/opt" "$P/usr/bin" "$P/usr/share/applications" "$P/usr/share/metainfo" "$P/usr/share/peregovorka" "$P/usr/share/doc/peregovorka"
cp -a "$SRC" "$P/opt/Peregovorka"
A="$P/opt/Peregovorka"
printf '%s\n' "$VER" > "$A/resources/package-version"           # по нему работающая программа видит, что APT поставил новую версию (нужен перезапуск)

find "$A" -type d -exec chmod 0755 {} +
find "$A" -type f -exec chmod 0644 {} +
chmod 0755 "$A/peregovorka" "$A/chrome_crashpad_handler"
chmod 4755 "$A/chrome-sandbox"
ln -s /opt/Peregovorka/peregovorka "$P/usr/bin/peregovorka"

install -m 0644 "$HERE/peregovorka.desktop" "$P/usr/share/applications/peregovorka.desktop"
install -m 0644 "$HERE/org.peregovorka.Peregovorka.metainfo.xml" "$P/usr/share/metainfo/org.peregovorka.Peregovorka.metainfo.xml"
( cd "$HERE/icons" && find hicolor -type f ) | while read -r f; do install -D -m 0644 "$HERE/icons/$f" "$P/usr/share/icons/$f"; done
install -m 0644 "$HERE/keyring/peregovorka-archive-keyring.gpg" "$P/usr/share/peregovorka/peregovorka-archive-keyring.gpg"
install -m 0644 "$HERE/apparmor-peregovorka" "$P/usr/share/peregovorka/apparmor-peregovorka"
install -m 0644 "$HERE/default-peregovorka" "$P/usr/share/peregovorka/default-peregovorka.example"
install -m 0644 "$HERE/debian/copyright" "$P/usr/share/doc/peregovorka/copyright"
install -D -m 0644 "$HERE/debian/lintian-overrides" "$P/usr/share/lintian/overrides/peregovorka"
install -d "$P/usr/share/man/man1" && gzip -9nc "$HERE/peregovorka.1" > "$P/usr/share/man/man1/peregovorka.1.gz"
{
  printf 'peregovorka (%s) stable; urgency=medium\n\n' "$VER"
  printf '  * Peregovorka %s: изменения — /usr/share/doc/peregovorka/CHANGELOG.md.gz\n\n' "$VER"
  printf ' -- Peregovorka <130656503+calleograph@users.noreply.github.com>  %s\n' "$(LC_ALL=C date -u -R)"
} | gzip -9n > "$P/usr/share/doc/peregovorka/changelog.gz"
gzip -9nc "$HERE/../CHANGELOG.md" > "$P/usr/share/doc/peregovorka/CHANGELOG.md.gz"

sed "s#@REPO_URL@#$REPO_URL#" "$HERE/debian/postinst" > "$P/DEBIAN/postinst"
install -m 0755 "$HERE/debian/postrm" "$P/DEBIAN/postrm"
chmod 0755 "$P/DEBIAN/postinst"

# Зависимости: библиотеки, с которыми связаны программа и Chromium (проверено ldd в чистых Ubuntu 22.04/24.04 и Debian 12/13, docs/LINUX.md).
# Имена с суффиксом t64 — Ubuntu 24.04 и Debian 13 (переход на 64-битное время), прежние — Ubuntu 22.04 и Debian 12. Пакет t64 — ПЕРВЫМ в альтернативе:
# иначе в Ubuntu 24.04 «libasound2» удовлетворяет заглушка liboss4-salsa-asound2 (Provides: libasound2) и программа падает при запуске.
DEPENDS="$(tr '\n' ' ' < "$HERE/debian/depends" | sed 's/ *$//')"
RECOMMENDS="$(tr '\n' ' ' < "$HERE/debian/recommends" | sed 's/ *$//')"
SIZE="$(du -sk --exclude=DEBIAN "$P" | cut -f1)"
cat > "$P/DEBIAN/control" <<EOF
Package: peregovorka
Version: $VER
Architecture: $ARCH
Maintainer: Peregovorka <130656503+calleograph@users.noreply.github.com>
Installed-Size: $SIZE
Depends: $DEPENDS
Recommends: $RECOMMENDS
Section: net
Priority: optional
Homepage: https://github.com/calleograph/peregovorka
Description: video meetings and chats of your organization (desktop client)
 Desktop client for corporate Peregovorka servers: meetings with voice, video
 and screen sharing (X11 and Wayland via XDG Desktop Portal and PipeWire),
 chats, meeting history and materials, desktop notifications and tray icon.
 Several organizations' servers can be connected and switched in one window.
 .
 Приложение для корпоративных серверов Peregovorka: встречи, чаты,
 история и материалы встреч, уведомления. Обновления — через APT.
EOF

mkdir -p "$OUT"
DEB="$OUT/peregovorka_${VER}_${ARCH}.deb"
dpkg-deb --root-owner-group -Zxz -z"${PEREGOVORKA_DEB_XZ:-9}" --build "$P" "$DEB" >/dev/null
# самопроверка: поля, setuid песочницы, файлы интеграции
[ "$(dpkg-deb -f "$DEB" Package)/$(dpkg-deb -f "$DEB" Version)/$(dpkg-deb -f "$DEB" Architecture)" = "peregovorka/$VER/$ARCH" ] || { echo "поля пакета не те" >&2; exit 1; }
dpkg-deb -c "$DEB" > "$ST/list"
grep -Eq '^-rwsr-xr-x root/root .* \./opt/Peregovorka/chrome-sandbox$' "$ST/list" || { echo "chrome-sandbox без setuid root" >&2; exit 1; }
OK_MODES='^(drwxr-xr-x|lrwxrwxrwx|-rw-r--r--|-rwxr-xr-x) root/root |^-rwsr-xr-x root/root .* \./opt/Peregovorka/chrome-sandbox$'
if grep -Evq "$OK_MODES" "$ST/list"; then echo "в пакете файлы с необычными правами или владельцем:" >&2; grep -Ev "$OK_MODES" "$ST/list" >&2; exit 1; fi
for f in usr/bin/peregovorka usr/share/applications/peregovorka.desktop usr/share/icons/hicolor/256x256/apps/peregovorka.png usr/share/peregovorka/peregovorka-archive-keyring.gpg opt/Peregovorka/resources/app.asar; do
  grep -q " \./$f" "$ST/list" || { echo "в пакете нет $f" >&2; exit 1; }
done
# закрытые ключи в пакет не попадают (проверка содержимого, а не только имён)
mkdir "$ST/x" && dpkg-deb -x "$DEB" "$ST/x"
if grep -rlaE -- "-----BEGIN (PGP |RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY( BLOCK)?-----" "$ST/x" | grep -q .; then echo "в пакете закрытый ключ" >&2; exit 1; fi
echo "$DEB"
