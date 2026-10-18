# Базовые образы закреплены тегом и digest (scripts/deps.py apply); меняются только через ревизию релиза.
ARG NODE_IMAGE=node:24-alpine@sha256:44fce696f38ba8c987c4db89954051000bbd05ffd8d8659ba15ed4a8602e937f
ARG NGINX_IMAGE=nginxinc/nginx-unprivileged:1.30-alpine@sha256:782bb89768cc87b7f95c18b95baa1af250e5d451f26794f59363540161147c01
ARG ALPINE_IMAGE=alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
ARG DRAWIO_VERSION=v32.5.5
FROM ${NODE_IMAGE} AS build
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
RUN printf '{"version":"%s","commit":"%s","built_at":"%s"}\n' "$APP_VERSION" "$APP_GIT_COMMIT" "$APP_BUILT_AT" > dist/version.json \
    && node -e "const fs=require('fs');const l=require('./package-lock.json').packages;const v=JSON.parse(fs.readFileSync('dist/version.json','utf8'));v.node=process.version.slice(1);v.components={};for(const k of ['livekit-client','@sapphi-red/web-noise-suppressor','react','react-dom','react-router-dom','vite']){v.components[k]=l['node_modules/'+k].version}fs.writeFileSync('dist/version.json',JSON.stringify(v)+'\n')"

# Редактор схем: draw.io (jgraph/drawio, Apache-2.0) разворачивается у нас как статика и встраивается в страницу встречи iframe'ом — свой
# редактор схем не пишется. Версия закреплена тегом; клонирование с повторами (как и npm ci). Остаётся только нужное редактору (prune.sh).
FROM ${ALPINE_IMAGE} AS drawio
ARG DRAWIO_VERSION
RUN apk add --no-cache git
COPY drawio/prune.sh /prune.sh
RUN for i in 1 2 3 4; do rm -rf /src && git clone --quiet --depth 1 --branch "${DRAWIO_VERSION}" https://github.com/jgraph/drawio.git /src && ok=1 && break; echo "git clone drawio: сбой, повтор $i/4"; sleep $((i * 10)); done; [ "${ok:-0}" = 1 ] \
    && sh /prune.sh /src /out && echo "${DRAWIO_VERSION}" > /out/VERSION

FROM ${NGINX_IMAGE}
# Конфиг уже финальный: скрипт entrypoint, пытающийся переписывать default.conf (IPv6 listen), не нужен и ругается
# на неизменяемую ФС («can not modify … read-only file system?»). Убираем именно его — права файлов и режим ФС не ослабляем.
USER root
RUN rm -f /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh \
    && nginx -v 2>&1 | sed -n 's#.*nginx/\([0-9][0-9.]*\).*#\1#p' > /usr/share/nginx/html/nginx-version.txt
USER 101
COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
COPY --from=drawio --chown=101:101 /out /usr/share/nginx/html/drawio
EXPOSE 8080
