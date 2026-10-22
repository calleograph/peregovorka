# Официальный образ livekit-server не содержит оболочки/wget, поэтому healthcheck
# в нём невозможен. Берём статический бинарь и кладём его в минимальный alpine
# (busybox wget для healthcheck). Версия — build-arg из .env (LIVEKIT_IMAGE_TAG).
ARG LIVEKIT_IMAGE_TAG=v1.13.10
ARG ALPINE_IMAGE=alpine:3.24@sha256:71e9b7bae160ba167fa17414401b94cbcdaf3c32feeab9298bd794a2a0614c35
FROM livekit/livekit-server:${LIVEKIT_IMAGE_TAG} AS lk

FROM ${ALPINE_IMAGE}
RUN apk add --no-cache ca-certificates
COPY --from=lk /livekit-server /usr/local/bin/livekit-server
# Конфигурация приходит из переменной окружения LIVEKIT_CONFIG (см. compose.yml).
ENTRYPOINT ["/usr/local/bin/livekit-server"]
