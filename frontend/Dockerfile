ARG NODE_VERSION=lts
ARG NGINX_VERSION=stable
ARG DRAWIO_VERSION=v32.3.0
ARG ALPINE_VERSION=3
FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
ENV NPM_CONFIG_FETCH_RETRIES=5 NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=2000 NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=60000
RUN for i in 1 2 3 4; do npm ci --no-audit --no-fund && ok=1 && break; echo "npm ci: сбой, повтор $i/4"; sleep $((i * 10)); done; [ "${ok:-0}" = 1 ]
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src
RUN npm run build
# Сведения о сборке для проверки verify.sh (должны совпадать с git HEAD): отдельный слой в конце, кэш npm не сбрасывается.
ARG APP_VERSION=0.0.0
ARG APP_GIT_COMMIT=unknown
ARG APP_BUILT_AT=unknown
RUN printf '{"version":"%s","commit":"%s","built_at":"%s"}\n' "$APP_VERSION" "$APP_GIT_COMMIT" "$APP_BUILT_AT" > dist/version.json

# Редактор схем: draw.io (jgraph/drawio, Apache-2.0) разворачивается у нас как статика и встраивается в страницу встречи iframe'ом — свой
# редактор схем не пишется. Версия закреплена тегом; клонирование с повторами (как и npm ci). Остаётся только нужное редактору (prune.sh).
FROM alpine:${ALPINE_VERSION} AS drawio
ARG DRAWIO_VERSION
RUN apk add --no-cache git
COPY drawio/prune.sh /prune.sh
RUN for i in 1 2 3 4; do rm -rf /src && git clone --quiet --depth 1 --branch "${DRAWIO_VERSION}" https://github.com/jgraph/drawio.git /src && ok=1 && break; echo "git clone drawio: сбой, повтор $i/4"; sleep $((i * 10)); done; [ "${ok:-0}" = 1 ] \
    && sh /prune.sh /src /out && echo "${DRAWIO_VERSION}" > /out/VERSION

FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine
# Конфиг уже финальный: скрипт entrypoint, пытающийся переписывать default.conf (IPv6 listen), не нужен и ругается
# на неизменяемую ФС («can not modify … read-only file system?»). Убираем именно его — права файлов и режим ФС не ослабляем.
USER root
RUN rm -f /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh
USER 101
COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
COPY --from=drawio --chown=101:101 /out /usr/share/nginx/html/drawio
EXPOSE 8080
