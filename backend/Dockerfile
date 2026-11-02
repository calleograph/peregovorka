# Базовый образ закреплён тегом и digest (scripts/deps.py apply): сборка воспроизводима, исправления образа приходят через ревизию релиза и Dependabot.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30
FROM ${PYTHON_IMAGE}

# Версия/commit/время сборки — в КОНЦЕ файла: их изменение не должно сбрасывать кэш слоёв с зависимостями.
# Сетевые операции (apt, pip) повторяются при временных сбоях сети/зеркал.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_RETRIES=5 PIP_DEFAULT_TIMEOUT=60

# Шрифт с кириллицей для экспорта протоколов в PDF; ffmpeg — сведение общей аудиозаписи встречи
# libgssapi-krb5-2/libkrb5-3 — рабочие библиотеки Kerberos (аутентификация EWS); устанавливаются явно, чтобы не удалиться вместе со сборочными пакетами ниже
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends fonts-dejavu-core ffmpeg libgssapi-krb5-2 libkrb5-3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app
# constraints.txt — точные версии ВСЕХ зависимостей (scripts/deps.py freeze): повторная сборка даёт тот же набор, а не «самые новые в диапазоне».
COPY requirements.txt constraints.txt ./
# gssapi и krb5 собираются из исходников (колёс для Linux нет): сборочные пакеты ставятся на время установки и удаляются
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends gcc libkrb5-dev \
    && pip install -r requirements.txt -c constraints.txt \
    && apt-get purge -y --auto-remove gcc libkrb5-dev \
    && rm -rf /var/lib/apt/lists/*

# Модели распознавания имён для обезличивания (navec + slovnet NER, ~50 МБ): адрес и SHA-256 закреплены в fetch_models.py; отдельный слой — смена кода его не сбрасывает
COPY app/privacy/fetch_models.py /tmp/fetch_models.py
RUN python /tmp/fetch_models.py /opt/peregovorka/nlp && rm /tmp/fetch_models.py
ENV PRIVACY_NLP_DIR=/opt/peregovorka/nlp

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
