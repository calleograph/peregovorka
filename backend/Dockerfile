ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

# Версия/commit/время сборки — в КОНЦЕ файла: их изменение не должно сбрасывать кэш слоёв с зависимостями.
# Сетевые операции (apt, pip) повторяются при временных сбоях сети/зеркал.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_RETRIES=5 PIP_DEFAULT_TIMEOUT=60

# Шрифт с кириллицей для экспорта протоколов в PDF; ffmpeg — сведение общей аудиозаписи встречи
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends fonts-dejavu-core ffmpeg \
    && rm -rf /var/lib/apt/lists/*

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
