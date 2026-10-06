ARG NODE_VERSION=lts
ARG NGINX_VERSION=stable
FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src
RUN npm run build

FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine
# Конфиг уже финальный: скрипт entrypoint, пытающийся переписывать default.conf (IPv6 listen), не нужен и ругается
# на неизменяемую ФС («can not modify … read-only file system?»). Убираем именно его — права файлов и режим ФС не ослабляем.
USER root
RUN rm -f /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh
USER 101
COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
EXPOSE 8080
