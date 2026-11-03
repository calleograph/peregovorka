#!/usr/bin/env bash
# deb-check.sh — полный круг пакета в чистой системе (контейнер Ubuntu/Debian или виртуальная машина CI), от root:
#   deb-check.sh <пакет-1.deb> [<пакет-2.deb>]
# 1) установка apt без рекомендуемых пакетов (доказывает, что Depends достаточно): файлы, права песочницы, репозиторий, ключ, интеграция;
# 2) запуск от обычного пользователя под Xvfb (с песочницей Chromium) — отчёт --diagnostics: версия, оболочка отрисована, установка «deb»;
# 3) обновление поверх второй версией (если дана): настройки пользователя на месте, источник APT не продублирован;
# 4) apt remove: программы нет, данные и репозиторий остались; повторная установка — настройки на месте;
# 5) apt purge: репозиторий, ключ и профиль AppArmor убраны, apt update не ломается.
# Код возврата 0 — всё прошло; иначе — первая ошибка с пояснением.
set -euo pipefail
DEB1="${1:?пакет}"; DEB2="${2:-}"
export DEBIAN_FRONTEND=noninteractive
U=tester
fail() { echo "ОШИБКА: $*" >&2; exit 1; }
ok() { echo "OK   $*"; }
ver() { dpkg-deb -f "$1" Version; }
run_app() {   # $1 — ожидаемая версия; отчёт диагностики пишет сама программа и выходит
  local out="/tmp/diag-$1.json"
  rm -f "$out"
  su - "$U" -c "cd ~ && timeout 150 xvfb-run -a -s '-screen 0 1600x900x24' /usr/bin/peregovorka --diagnostics=$out >/tmp/app-$1.log 2>&1" || true
  [ -s "$out" ] || { tail -30 "/tmp/app-$1.log" >&2; fail "программа $1 не записала отчёт диагностики"; }
  python3 - "$out" "$1" <<'PY' || { tail -20 "/tmp/app-$1.log" >&2; exit 1; }
import json, sys
d = json.load(open(sys.argv[1])); c = d["client"]
errs = []
if c["version"] != sys.argv[2]: errs.append(f"версия {c['version']} вместо {sys.argv[2]}")
if not d["shell"]["ok"]: errs.append(f"оболочка не отрисовалась: {d['shell']}")
if c["install"] != "deb" or not c["packaged"]: errs.append(f"вид установки {c['install']}")
if not c["os"].startswith("Linux"): errs.append(c["os"])
if not d["app_files"]: errs.append("файлы программы повреждены")
if d["update"]["status"] != "managed": errs.append(f"обновления: {d['update']['status']} (должен управлять APT)")
if errs: print("ОШИБКА: " + "; ".join(errs), file=sys.stderr); sys.exit(1)
print(f"OK   запуск {c['version']}: оболочка отрисована за {c['startup_ms']} мс, Electron {c['electron']}, {c['os']} {c['arch']}")
PY
}

. /etc/os-release; echo "=== $PRETTY_NAME ($(dpkg --print-architecture))"
apt-get update -qq
id "$U" >/dev/null 2>&1 || useradd -m -s /bin/bash "$U"

# ---------------------------------------------------------------- 1. установка
apt-get install -y -qq --no-install-recommends "$DEB1" >/tmp/install.log 2>&1 || { tail -20 /tmp/install.log; fail "apt не установил пакет"; }
V1="$(ver "$DEB1")"
[ "$(dpkg-query -W -f '${Status} ${Version}' peregovorka)" = "install ok installed $V1" ] || fail "пакет не в состоянии installed"
missing="$(ldd /opt/Peregovorka/peregovorka | grep 'not found' || true)"; [ -z "$missing" ] || fail "не хватает библиотек (Depends): $missing"
ok "установлен $V1, все библиотеки найдены (только Depends, без рекомендуемых)"
[ "$(stat -c '%a %U' /opt/Peregovorka/chrome-sandbox)" = "4755 root" ] || fail "chrome-sandbox: $(stat -c '%a %U' /opt/Peregovorka/chrome-sandbox)"
[ "$(readlink -f /usr/bin/peregovorka)" = /opt/Peregovorka/peregovorka ] || fail "/usr/bin/peregovorka"
S=/etc/apt/sources.list.d/peregovorka.sources
grep -qx "Architectures: $(dpkg --print-architecture)" "$S" && grep -qx "Signed-By: /usr/share/keyrings/peregovorka-archive-keyring.gpg" "$S" || fail "источник APT: $(cat "$S" 2>/dev/null)"
cmp -s /usr/share/keyrings/peregovorka-archive-keyring.gpg /usr/share/peregovorka/peregovorka-archive-keyring.gpg || fail "ключ репозитория не установлен"
for f in /usr/share/applications/peregovorka.desktop /usr/share/icons/hicolor/256x256/apps/peregovorka.png /usr/share/icons/hicolor/scalable/apps/peregovorka.svg \
         /usr/share/metainfo/org.peregovorka.Peregovorka.metainfo.xml /usr/share/doc/peregovorka/copyright; do [ -f "$f" ] || fail "нет $f"; done
dpkg -L peregovorka | grep -qx /usr/share/man/man1/peregovorka.1.gz || fail "в пакете нет man-страницы"     # минимальные образы не распаковывают man (path-exclude)
if [ -f /etc/apparmor.d/abi/4.0 ]; then
  [ -f /etc/apparmor.d/peregovorka ] || fail "AppArmor 4 есть, профиля нет"
  apparmor_parser -Q -T -K /etc/apparmor.d/peregovorka 2>/dev/null || fail "профиль AppArmor не разбирается"
else [ ! -e /etc/apparmor.d/peregovorka ] || fail "профиль AppArmor без AppArmor 4"; fi
ok "песочница setuid, команда, репозиторий ($S), ключ, меню, значки, man, AppArmor"

# средства испытания — после установки пакета, чтобы не подменять его зависимости
apt-get install -y -qq --no-install-recommends xvfb xauth python3 desktop-file-utils >/dev/null 2>&1 || fail "не установились средства испытания"
update-desktop-database -q /usr/share/applications
[ "$(su - "$U" -c 'xdg-mime query default x-scheme-handler/peregovorka')" = peregovorka.desktop ] || fail "ссылки peregovorka:// не связаны с приложением"
ok "ссылки peregovorka:// открывает Peregovorka (xdg-mime)"

# ---------------------------------------------------------------- 2. запуск от обычного пользователя
run_app "$V1"
CFG="/home/$U/.config/Peregovorka"
[ -d "$CFG" ] || fail "нет каталога данных $CFG"
su - "$U" -c "python3 -c \"import json,os; p=os.path.expanduser('~/.config/Peregovorka/settings.json'); d=json.load(open(p)) if os.path.exists(p) else {}; d['theme']='dark'; d['firstRunDone']=True; json.dump(d,open(p,'w'))\""
ok "данные пользователя: $CFG (тема «тёмная» — метка для проверки сохранности)"
keep() { su - "$U" -c "python3 -c \"import json,os; print(json.load(open(os.path.expanduser('~/.config/Peregovorka/settings.json'))).get('theme'))\"" | grep -qx dark || fail "настройки пользователя потеряны ($1)"; }

# ---------------------------------------------------------------- 3. обновление поверх
if [ -n "$DEB2" ]; then
  V2="$(ver "$DEB2")"
  dpkg --compare-versions "$V2" gt "$V1" || fail "вторая версия $V2 не новее $V1"
  apt-get install -y -qq --no-install-recommends "$DEB2" >/tmp/upgrade.log 2>&1 || { tail -20 /tmp/upgrade.log; fail "обновление не установилось"; }
  [ "$(dpkg-query -W -f '${Version}' peregovorka)" = "$V2" ] || fail "после обновления версия не $V2"
  [ "$(cat /opt/Peregovorka/resources/package-version)" = "$V2" ] || fail "package-version не $V2"
  [ "$(ls /etc/apt/sources.list.d/ | grep -c peregovorka)" = 1 ] || fail "источник APT продублирован: $(ls /etc/apt/sources.list.d/)"
  keep "обновление"
  run_app "$V2"
  ok "обновление $V1 → $V2 поверх: настройки сохранены, источник один"
fi
CUR="$(dpkg-query -W -f '${Version}' peregovorka)"

# ---------------------------------------------------------------- 4. удаление и повторная установка
apt-get remove -y -qq peregovorka >/dev/null 2>&1 || fail "apt remove"
[ ! -e /opt/Peregovorka ] && [ ! -e /usr/bin/peregovorka ] || fail "после remove остались файлы программы"
[ -f "$S" ] && [ -f /usr/share/keyrings/peregovorka-archive-keyring.gpg ] || fail "remove убрал репозиторий (должен оставлять до purge)"
[ ! -e /etc/apparmor.d/peregovorka ] || fail "remove оставил профиль AppArmor"
keep "remove"
ok "apt remove: программа удалена, данные пользователя и репозиторий на месте"
apt-get install -y -qq --no-install-recommends "${DEB2:-$DEB1}" >/dev/null 2>&1 || fail "повторная установка"
keep "повторная установка"
run_app "$CUR"
ok "повторная установка: запуск, настройки пользователя сохранены"

# ---------------------------------------------------------------- 5. полное удаление
apt-get purge -y -qq peregovorka >/dev/null 2>&1 || fail "apt purge"
[ ! -e "$S" ] && [ ! -e /usr/share/keyrings/peregovorka-archive-keyring.gpg ] && [ ! -e /etc/apparmor.d/peregovorka ] || fail "purge оставил репозиторий, ключ или профиль"
apt-get update -qq >/dev/null 2>&1 || fail "apt update после purge"
dpkg -l peregovorka 2>/dev/null | grep -q '^ii' && fail "пакет всё ещё установлен"
ok "apt purge: репозиторий, ключ и профиль убраны, apt update работает; данные пользователя ($CFG) не тронуты — их удаляет сам пользователь"
echo "ИТОГО: пакет прошёл все проверки на $PRETTY_NAME"
