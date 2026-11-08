# Официальный образ livekit-server не содержит оболочки/wget, поэтому healthcheck
# в нём невозможен. Берём статический бинарь и кладём его в минимальный alpine
# (busybox wget для healthcheck). Версия — build-arg из .env (LIVEKIT_IMAGE_TAG).
ARG LIVEKIT_IMAGE_TAG=v1.13.10
ARG ALPINE_IMAGE=alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
FROM livekit/livekit-server:${LIVEKIT_IMAGE_TAG} AS lk

FROM ${ALPINE_IMAGE}
RUN apk add --no-cache ca-certificates
COPY --from=lk /livekit-server /usr/local/bin/livekit-server
# Конфигурация приходит из переменной окружения LIVEKIT_CONFIG (см. compose.yml); адреса ICE и проверка STUN — entrypoint.sh.
COPY entrypoint.sh /usr/local/bin/peregovorka-livekit
RUN chmod 755 /usr/local/bin/peregovorka-livekit
ENTRYPOINT ["/usr/local/bin/peregovorka-livekit"]
