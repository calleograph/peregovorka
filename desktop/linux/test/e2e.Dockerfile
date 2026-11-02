# Испытательная система для установленного пакета Peregovorka: чистая Ubuntu/Debian + средства испытания (X-сервер, D-Bus, сервер уведомлений,
# xdotool/xclip, certutil). Сам пакет ставится внутри при испытании — install.sh из репозитория APT, как у пользователя.
ARG BASE=ubuntu:24.04
FROM ${BASE}
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends xvfb xauth xdotool xclip x11-utils dbus dbus-x11 dunst libnss3-tools \
      ca-certificates curl python3 procps psmisc libnotify-bin openbox gnome-keyring libsecret-tools python3-dbus python3-gi >/dev/null && rm -rf /var/lib/apt/lists/* \
 && (getent passwd 1000 >/dev/null && userdel -r "$(getent passwd 1000 | cut -d: -f1)" 2>/dev/null || true) \
 && useradd -m -u 1000 -s /bin/bash tester
