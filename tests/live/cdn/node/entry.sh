#!/bin/bash
# /pkg — пакет узла (backend/app/assets/cdn_node), /node.json — настройки из «Скачать настройки» администрирования
set -eu
mkdir -p /etc/pgcdn/tls /var/lib/pgcdn/acme /var/cache/pgcdn; chown nginx:nginx /var/cache/pgcdn
cp /pkg/pgcdn.js /pkg/nginx.conf.tpl /pkg/VERSION /etc/pgcdn/; install -m 755 /pkg/pgcdn-ctl /usr/local/sbin/pgcdn-ctl
printf '#!/bin/sh\ncase "$*" in *is-active*) pgrep -x nginx >/dev/null;; *reload*) nginx -s reload;; *) exit 0;; esac\n' > /usr/local/bin/systemctl; chmod +x /usr/local/bin/systemctl
N=$(jq -r .public_name /node.json)
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes -days 30 -subj "/CN=$N" -addext "subjectAltName=DNS:$N" -keyout /etc/pgcdn/tls/node.key -out /etc/pgcdn/tls/node.crt 2>/dev/null
pgcdn-ctl apply < /node.json >/dev/null 2>&1 || true
ls /etc/nginx/nginx.conf >/dev/null; nginx -t
(while sleep 30; do pgcdn-ctl diskstat; done) &
pgrep -x nginx >/dev/null && nginx -s quit; sleep 1
exec nginx -g 'daemon off;'
