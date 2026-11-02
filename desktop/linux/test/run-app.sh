#!/bin/bash
# Запуск установленной Peregovorka от пользователя tester в испытательной системе: X-сервер :$N (доступен хосту по TCP для снимков экрана),
# сеанс D-Bus с сервером уведомлений (вызовы Notify пишутся в ~/notify.log), доверие тестовому центру сертификации стенда — только в NSS
# этого пользователя (~/.pki/nssdb, как у Chromium в Linux). Остальные аргументы — программе.
#   run-app.sh <номер-экрана> <ширина>x<высота> <порт-CDP> [аргументы peregovorka…]
set -e
N="$1"; RES="$2"; CDP="$3"; shift 3
export DISPLAY=":$N"
export LANG="${LANG:-C.UTF-8}"                      # как на рабочем столе: UTF-8 (русские имена файлов)
pgrep -f "Xvfb :$N " >/dev/null || { Xvfb ":$N" -screen 0 "${RES}x24" -ac -listen tcp >/tmp/xvfb-$N.log 2>&1 & sleep 1; }
if [ ! -f ~/.dbus-env ]; then dbus-launch --sh-syntax > ~/.dbus-env; fi
. ~/.dbus-env; export DBUS_SESSION_BUS_ADDRESS
pgrep -u "$(id -u)" openbox >/dev/null || (openbox >/tmp/openbox.log 2>&1 &)
pgrep -u "$(id -u)" -f sni-watcher.py >/dev/null || (python3 /t/sni-watcher.py ~/tray.log >/tmp/sni.log 2>&1 & sleep 0.5)
pgrep -u "$(id -u)" dunst >/dev/null || (dunst >/tmp/dunst.log 2>&1 &)
# связка ключей GNOME (Secret Service): токен входа приложение хранит только в ней. В испытательной системе — коллекция «login» по умолчанию
# в открытом формате gnome-keyring (на настоящем рабочем столе она зашифрована и открывается при входе в систему; для приложения это тот же Secret Service)
if [ ! -f ~/.local/share/keyrings/login.keyring ]; then
  mkdir -p ~/.local/share/keyrings
  printf '[keyring]\ndisplay-name=login\nctime=0\nmtime=0\nlock-on-idle=false\nlock-after=false\n' > ~/.local/share/keyrings/login.keyring
  printf login > ~/.local/share/keyrings/default; chmod 600 ~/.local/share/keyrings/*
fi
pgrep -u "$(id -u)" gnome-keyring-d >/dev/null || gnome-keyring-daemon --start --components=secrets >/dev/null 2>&1
export XDG_CURRENT_DESKTOP="${XDG_CURRENT_DESKTOP:-GNOME}"
pgrep -u "$(id -u)" -f "dbus-monitor --session" >/dev/null || (dbus-monitor --session "interface='org.freedesktop.Notifications',member='Notify'" > ~/notify.log 2>&1 &)
if [ ! -f ~/.pki/nssdb/cert9.db ]; then mkdir -p ~/.pki/nssdb && certutil -d sql:"$HOME/.pki/nssdb" -N --empty-password; fi
certutil -d sql:"$HOME/.pki/nssdb" -L -n pg-test >/dev/null 2>&1 || certutil -d sql:"$HOME/.pki/nssdb" -A -t "C,," -n pg-test -i /tls/ca.crt
exec /usr/bin/peregovorka --remote-debugging-port="$CDP" --use-fake-device-for-media-stream "$@" >>~/app.log 2>&1
