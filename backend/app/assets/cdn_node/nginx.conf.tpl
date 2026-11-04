# Узел CDN Peregovorka — файл создаёт pgcdn-ctl из /etc/pgcdn/node.json. Ручные правки перезапишутся при следующем применении настроек.
user nginx;
worker_processes auto;
worker_rlimit_nofile 200000;
pid /run/nginx.pid;
error_log /var/log/nginx/error.log warn;
load_module modules/ngx_http_js_module.so;

events {
    worker_connections 16384;
    multi_accept on;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    server_tokens off;
    sendfile on;
    sendfile_max_chunk 2m;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 30s;
    keepalive_requests 10000;
    reset_timedout_connection on;
    send_timeout 20s;
    client_max_body_size 1k;
    resolver @RESOLVER@ valid=60s ipv6=off;
    resolver_timeout 5s;

    js_path /etc/pgcdn/;
    js_import pgcdn from pgcdn.js;
    js_shared_dict_zone zone=pgcdn_tok:8m timeout=7200s evict;     # проверенные подписи (до конца их срока)
    js_shared_dict_zone zone=pgcdn_bad:4m type=number timeout=60s evict;     # неверные подписи по адресам за минуту
    js_shared_dict_zone zone=pgcdn_stat:4m type=number;            # счётчики для основного сервера
    js_shared_dict_zone zone=pgcdn_seen:32m timeout=40s evict;     # адреса зрителей эфира за последние 40 с
    js_shared_dict_zone zone=pgcdn_pl:16m timeout=15s evict;       # плейлисты эфиров: микрокеш 0,3 с и склейка запросов к источнику
    js_set $pgcdn_log pgcdn.log;
    access_log /var/log/nginx/pgcdn-never.log combined if=$pgcdn_log;   # переменная считает и всегда пуста — журнал не пишется

    # Кеш на диске: ключ — доступ и имя файла, без подписи, поэтому все зрители делят одну копию сегмента.
    proxy_cache_path @CACHE_DIR@ levels=1:2 keys_zone=pgcdn:64m max_size=@CACHE_MAX@ inactive=20m use_temp_path=off;

    upstream pgcdn_origin {
        zone pgcdn_origin 64k;
        server @ORIGIN_HOST@:@ORIGIN_PORT@ resolve max_fails=0;
        keepalive 64;
        keepalive_timeout 60s;
    }

    server {
        listen 80 reuseport;
        listen [::]:80 reuseport;
        server_name _;
        location ^~ /.well-known/acme-challenge/ { root /var/lib/pgcdn/acme; default_type text/plain; }
        location = /.pgcdn/healthz { access_log off; default_type text/plain; return 200 "ok\n"; }
        location / { return 301 https://$host$request_uri; }
    }

    server {
        listen 443 ssl reuseport;
        listen [::]:443 ssl reuseport;
        http2 on;
        server_name @PUBLIC_NAME@ _;
        ssl_certificate @TLS_CERT@;
        ssl_certificate_key @TLS_KEY@;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_session_cache shared:pgcdn_ssl:32m;
        ssl_session_timeout 4h;
        ssl_session_tickets off;
        ssl_prefer_server_ciphers off;

        set $pgcdn_reason "";
        error_page 403 = @pgcdn_denied;

        # Плейлист — из микрокеша njs (pgcdn.playlist): 0,3 с, за обновлением к источнику идёт один запрос, остальные ждут его шагами по 25 мс.
        # Кеш nginx здесь не годится: его срок — не меньше 1 с (плейлист опаздывал, у зрителей появлялись остановки: замер docs/BROADCAST_CDN.md),
        # а ожидание блокировки — шагами по 500 мс; без кеша же каждый опрос каждого зрителя уходил бы на источник.
        location ~ "^/hls/(?<acc>[0-9a-f]{24})/[0-9]{9,11}/[0-9a-f]{8}\.[A-Za-z0-9_-]{86}/index\.m3u8$" {
            auth_request /_pgcdn_auth;
            auth_request_set $pgcdn_reason $sent_http_x_pgcdn_reason;
            js_content pgcdn.playlist;
        }
        location ~ "^/_pgcdn_pl/(?<acc>[0-9a-f]{24})$" {
            internal;
            proxy_pass @ORIGIN_SCHEME@://pgcdn_origin/cdn-origin/$acc/index.m3u8;
            include /etc/pgcdn/proxy.conf;
            subrequest_output_buffer_size 256k;
        }

        # Сегменты неизменяемы: первый зритель вызывает один запрос к источнику (lock), остальные ждут его и получают копию с диска.
        location ~ "^/hls/(?<acc>[0-9a-f]{24})/[0-9]{9,11}/[0-9a-f]{8}\.[A-Za-z0-9_-]{86}/(?<file>seg_[0-9]+\.ts)$" {
            auth_request /_pgcdn_auth;
            auth_request_set $pgcdn_reason $sent_http_x_pgcdn_reason;
            proxy_pass @ORIGIN_SCHEME@://pgcdn_origin/cdn-origin/$acc/$file;
            include /etc/pgcdn/proxy.conf;
            proxy_cache pgcdn;
            proxy_cache_key $acc/$file;
            proxy_cache_lock on;
            proxy_cache_lock_timeout 5s;
            proxy_cache_lock_age 5s;
            proxy_ignore_headers Cache-Control Expires Set-Cookie Vary X-Accel-Expires;
            proxy_cache_valid 200 30m;
            proxy_cache_valid 404 1s;
            proxy_cache_use_stale error timeout updating http_500 http_502 http_503 http_504;
            add_header Cache-Control "public, max-age=300, immutable" always;
            add_header Access-Control-Allow-Origin "*" always;
            add_header X-Cache $upstream_cache_status always;
        }

        location = /_pgcdn_auth { internal; js_content pgcdn.auth; }
        location @pgcdn_denied { js_content pgcdn.denied; }
        location = /.pgcdn/stats { access_log off; js_content pgcdn.stats; }
        location = /.pgcdn/healthz { access_log off; default_type text/plain; return 200 "ok\n"; }
        location / { return 404; }
    }
}
