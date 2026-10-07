ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG GIGAAM_GIT_COMMIT
# Версия/commit/время сборки — в КОНЦЕ файла: их изменение не должно сбрасывать кэш слоёв torch/GigaAM (их скачивание занимает десятки минут).
# Сетевые операции (apt, pip, git) повторяются при временных сбоях сети/зеркал/GitHub.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_RETRIES=5 PIP_DEFAULT_TIMEOUT=120

# git нужен только на время сборки (установка GigaAM из зафиксированного коммита).
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends libgomp1 libstdc++6 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# torch (CPU или CUDA — по индексу) ставится отдельно: общий для GigaAM и Silero VAD.
# Повтор до 4 раз с растущей паузой: единичный сбой загрузки не должен обнулять многочасовую сборку (слои уже скачанных пакетов кэшируются).
RUN for i in 1 2 3 4; do pip install torch torchaudio --index-url ${TORCH_INDEX_URL} && ok=1 && break; echo "pip torch: сбой, повтор $i/4"; sleep $((i * 15)); done; [ "${ok:-0}" = 1 ]

WORKDIR /srv/asr
COPY requirements.txt .
RUN for i in 1 2 3 4; do pip install -r requirements.txt && ok=1 && break; echo "pip requirements: сбой, повтор $i/4"; sleep $((i * 15)); done; [ "${ok:-0}" = 1 ]
# Установка GigaAM с GitHub может идти долго (клонирование + зависимости): это не зависание, пока есть сетевая активность.
RUN for i in 1 2 3 4; do pip install "gigaam @ git+https://github.com/salute-developers/GigaAM.git@${GIGAAM_GIT_COMMIT}" && ok=1 && break; echo "gigaam: сбой, повтор $i/4"; sleep $((i * 15)); done; [ "${ok:-0}" = 1 ] \
    && apt-get purge -y git && rm -rf /var/lib/apt/lists/*

COPY app ./app

# Метаданные сборки — в конце (кэш слоёв torch/GigaAM не сбрасывается); запекаются в образ.
ARG APP_VERSION=0.0.0
ARG APP_GIT_COMMIT=unknown
ARG APP_BUILT_AT=unknown
ENV APP_VERSION=${APP_VERSION} APP_GIT_COMMIT=${APP_GIT_COMMIT} APP_BUILT_AT=${APP_BUILT_AT}

# uid совпадает с backend (10001): backend читает и удаляет файлы записей, созданные ASR на общем томе.
RUN useradd --system --uid 10001 --no-create-home asr && mkdir -p /data/recordings && chown asr /data/recordings
USER asr

EXPOSE 8090
CMD ["python", "-m", "app.main"]
