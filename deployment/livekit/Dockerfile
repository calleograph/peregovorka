# Официальный образ livekit-server не содержит оболочки/wget, поэтому healthcheck
# в нём невозможен. Берём статический бинарь и кладём его в минимальный alpine
# (busybox wget для healthcheck). Версия — build-arg из .env (LIVEKIT_IMAGE_TAG).
ARG LIVEKIT_IMAGE_TAG=v1.13.7
FROM livekit/livekit-server:${LIVEKIT_IMAGE_TAG} AS lk

FROM alpine:3
RUN apk add --no-cache ca-certificates
COPY --from=lk /livekit-server /usr/local/bin/livekit-server
# Конфигурация приходит из переменной окружения LIVEKIT_CONFIG (см. compose.yml).
ENTRYPOINT ["/usr/local/bin/livekit-server"]
