# Узел CDN для нагрузочных испытаний: те же пакеты nginx.org и файлы узла, что на настоящем сервере, но без systemd (nginx — главный процесс контейнера).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg jq openssl >/dev/null \
 && curl -fsSL https://nginx.org/keys/nginx_signing.key | gpg --dearmor -o /usr/share/keyrings/nginx.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/nginx.gpg] https://nginx.org/packages/ubuntu noble nginx" > /etc/apt/sources.list.d/nginx.list \
 && printf 'Package: *\nPin: origin nginx.org\nPin-Priority: 900\n' > /etc/apt/preferences.d/99nginx \
 && apt-get update -qq && apt-get install -y -qq nginx nginx-module-njs >/dev/null && rm -rf /var/lib/apt/lists/*
COPY entry.sh /entry.sh
ENTRYPOINT ["/bin/bash", "/entry.sh"]
