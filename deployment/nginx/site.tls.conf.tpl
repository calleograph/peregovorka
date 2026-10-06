# managed-by: peregovorka:@@PROJECT@@
#
# Отдельный site-файл экземпляра "@@PROJECT@@" с TLS на этом nginx (standalone).
# Создан scripts/install.sh. Сертификат и ключ задаются в .env и не копируются.

server {
    listen @@LISTEN_PORT@@ ssl;
    server_name @@SERVER_NAME@@;

    ssl_certificate     @@TLS_CERT@@;
    ssl_certificate_key @@TLS_KEY@@;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:vm_@@PROJECT_ID@@_ssl:5m;

    client_max_body_size 20m;

    location /internal/ { return 404; }

    location /api/v1/ws {
        proxy_pass http://127.0.0.1:@@WEB_PORT@@;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        # realtime: без буферизации и с отключённым алгоритмом Нейгла — задержка сигналинга/событий минимальна (у Jitsi тот же tcp_nodelay на WebSocket)
        proxy_buffering off;
        proxy_request_buffering off;
        tcp_nodelay on;
    }

    location /livekit/ {
        proxy_pass http://127.0.0.1:@@WEB_PORT@@;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        # realtime: без буферизации и с отключённым алгоритмом Нейгла — задержка сигналинга/событий минимальна (у Jitsi тот же tcp_nodelay на WebSocket)
        proxy_buffering off;
        proxy_request_buffering off;
        tcp_nodelay on;
    }

    location / {
        proxy_pass http://127.0.0.1:@@WEB_PORT@@;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}
