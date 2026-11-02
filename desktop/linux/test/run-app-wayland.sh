#!/bin/bash
# Запуск установленной Peregovorka в сеансе Wayland (sway без экрана): D-Bus, PipeWire, WirePlumber, XDG Desktop Portal (wlr), связка ключей.
#   run-app-wayland.sh <ширина>x<высота> <порт-CDP> [аргументы peregovorka…]
set -e
RES="$1"; CDP="$2"; shift 2
export XDG_RUNTIME_DIR=/tmp/xdg-$(id -u); mkdir -p "$XDG_RUNTIME_DIR"; chmod 700 "$XDG_RUNTIME_DIR"
export LANG="${LANG:-C.UTF-8}" XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=sway
if [ ! -f ~/.dbus-env ]; then dbus-launch --sh-syntax > ~/.dbus-env; fi
. ~/.dbus-env; export DBUS_SESSION_BUS_ADDRESS
mkdir -p ~/.config/sway ~/.config/xdg-desktop-portal-wlr ~/.config/xdg-desktop-portal
printf 'output HEADLESS-1 resolution %s\nexec dbus-update-activation-environment WAYLAND_DISPLAY XDG_CURRENT_DESKTOP\n' "$RES" > ~/.config/sway/config
printf '[screencast]\nchooser_type=none\noutput_name=HEADLESS-1\nmax_fps=15\n' > ~/.config/xdg-desktop-portal-wlr/config
printf '[preferred]\ndefault=wlr;gtk\norg.freedesktop.impl.portal.ScreenCast=wlr\norg.freedesktop.impl.portal.Screenshot=wlr\n' > ~/.config/xdg-desktop-portal/sway-portals.conf
pgrep -u "$(id -u)" -x pipewire >/dev/null || (pipewire >/tmp/pipewire.log 2>&1 & sleep 0.5)
pgrep -u "$(id -u)" -x wireplumber >/dev/null || (wireplumber >/tmp/wireplumber.log 2>&1 & sleep 0.5)
if ! pgrep -u "$(id -u)" -x sway >/dev/null; then
  WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 WLR_RENDERER=pixman sway >/tmp/sway.log 2>&1 &
  for i in $(seq 40); do ls "$XDG_RUNTIME_DIR"/wayland-* >/dev/null 2>&1 && break; sleep 0.25; done
fi
export WAYLAND_DISPLAY="$(basename "$(ls "$XDG_RUNTIME_DIR"/wayland-? | head -1)")"
pgrep -u "$(id -u)" -f xdg-desktop-portal-wlr >/dev/null || (/usr/libexec/xdg-desktop-portal-wlr >/tmp/xdpw.log 2>&1 &)
pgrep -u "$(id -u)" -f "libexec/xdg-desktop-portal$" >/dev/null || (/usr/libexec/xdg-desktop-portal -r >/tmp/xdp.log 2>&1 &)
sleep 1
if [ ! -f ~/.local/share/keyrings/login.keyring ]; then
  mkdir -p ~/.local/share/keyrings; printf '[keyring]\ndisplay-name=login\nctime=0\nmtime=0\nlock-on-idle=false\nlock-after=false\n' > ~/.local/share/keyrings/login.keyring
  printf login > ~/.local/share/keyrings/default; chmod 600 ~/.local/share/keyrings/*
fi
pgrep -u "$(id -u)" gnome-keyring-d >/dev/null || gnome-keyring-daemon --start --components=secrets >/dev/null 2>&1
if [ ! -f ~/.pki/nssdb/cert9.db ]; then mkdir -p ~/.pki/nssdb && certutil -d sql:"$HOME/.pki/nssdb" -N --empty-password; fi
certutil -d sql:"$HOME/.pki/nssdb" -L -n pg-test >/dev/null 2>&1 || certutil -d sql:"$HOME/.pki/nssdb" -A -t "C,," -n pg-test -i /tls/ca.crt
printf 'WAYLAND_DISPLAY=%s XDG_RUNTIME_DIR=%s\n' "$WAYLAND_DISPLAY" "$XDG_RUNTIME_DIR" > ~/.wayland-env
exec /usr/bin/peregovorka --remote-debugging-port="$CDP" --use-fake-device-for-media-stream "$@" >>~/app.log 2>&1
