FROM python:3.12-slim

ARG APP_VERSION=0.0.0
ARG APP_GIT_COMMIT=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_VERSION=${APP_VERSION} APP_GIT_COMMIT=${APP_GIT_COMMIT}

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# Метаданные сборки — в конце, чтобы смена коммита/времени не сбрасывала кэш слоёв с зависимостями.
# Версия «запекается» в образ и не переопределяется окружением при запуске: она всегда соответствует работающему коду.
ARG APP_VERSION=0.0.0
ARG APP_GIT_COMMIT=unknown
ARG APP_BUILT_AT=unknown
ENV APP_VERSION=${APP_VERSION} APP_GIT_COMMIT=${APP_GIT_COMMIT} APP_BUILT_AT=${APP_BUILT_AT}

RUN useradd --system --uid 10001 --no-create-home appuser && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 8000
# Слушаем только внутри docker-сети проекта; заголовки прокси разбирает приложение (TRUSTED_PROXY_HOPS).
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--ws", "websockets-sansio"]
