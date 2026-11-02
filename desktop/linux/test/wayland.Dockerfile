# Испытательная система Wayland: композитор sway без экрана (headless), PipeWire и WirePlumber, XDG Desktop Portal с бэкендом wlroots —
# показ экрана в Wayland идёт только через портал и PipeWire. Портал настроен выбирать экран сам (chooser_type=none), в настоящей системе выбор — за человеком.
ARG BASE=ubuntu:24.04
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends sway xwayland grim xdg-desktop-portal xdg-desktop-portal-wlr pipewire pipewire-bin wireplumber \
      dbus dbus-x11 dunst gnome-keyring libnss3-tools ca-certificates curl python3 procps psmisc libnotify-bin >/dev/null && rm -rf /var/lib/apt/lists/* \
 && (getent passwd 1000 >/dev/null && userdel -r "$(getent passwd 1000 | cut -d: -f1)" 2>/dev/null || true) \
 && useradd -m -u 1000 -s /bin/bash tester
