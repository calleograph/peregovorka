# managed-by: voicemeet:@@PROJECT@@
#
# Отдельный site-файл экземпляра "@@PROJECT@@". Создан scripts/install.sh.
# Независим от остальных site-файлов сервера (в т.ч. sites-available/projects).
# Никаких map{}/upstream{} в глобальном http-контексте — чтобы не конфликтовать
# с чужими конфигурациями; имена zone/upstream не используются.
#
# Режим: HTTP-слушатель за внешним reverse proxy (TLS завершается выше и
# передаёт X-Forwarded-Proto: https). Для прямого TLS см. site.tls.conf.tpl.

server {
    listen @@LISTEN_PORT@@;
    server_name @@SERVER_NAME@@;

    client_max_body_size 20m;

    # Внутренние API наружу не отдаются.
    location /internal/ { return 404; }

    # WebSocket приложения и сигналинг LiveKit.
    location /api/v1/ws {
        proxy_pass http://127.0.0.1:@@WEB_PORT@@;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location /livekit/ {
        proxy_pass http://127.0.0.1:@@WEB_PORT@@;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:@@WEB_PORT@@;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
        proxy_read_timeout 120s;
    }
}
