ARG PYTHON_IMAGE=python:3.12-slim@sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f
FROM ${PYTHON_IMAGE}

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# Версии torch/torchaudio закреплены (scripts/deps.py apply); для GPU переопределяются вместе с ASR_TORCH_INDEX_URL.
ARG TORCH_VERSION=2.14.1
ARG TORCHAUDIO_VERSION=2.11.0
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
RUN for i in 1 2 3 4; do pip install "torch==${TORCH_VERSION}" "torchaudio==${TORCHAUDIO_VERSION}" --index-url ${TORCH_INDEX_URL} && ok=1 && break; echo "pip torch: сбой, повтор $i/4"; sleep $((i * 15)); done; [ "${ok:-0}" = 1 ]

WORKDIR /srv/asr
COPY requirements.txt constraints.txt ./
RUN for i in 1 2 3 4; do pip install -r requirements.txt -c constraints.txt && ok=1 && break; echo "pip requirements: сбой, повтор $i/4"; sleep $((i * 15)); done; [ "${ok:-0}" = 1 ]
# Установка GigaAM с GitHub может идти долго (клонирование + зависимости): это не зависание, пока есть сетевая активность.
RUN for i in 1 2 3 4; do pip install -c constraints.txt "gigaam @ git+https://github.com/salute-developers/GigaAM.git@${GIGAAM_GIT_COMMIT}" && ok=1 && break; echo "gigaam: сбой, повтор $i/4"; sleep $((i * 15)); done; [ "${ok:-0}" = 1 ] \
    && apt-get purge -y git && rm -rf /var/lib/apt/lists/*

COPY app ./app
# Сборка падает, если пакеты несовместимы (pip check) или Silero VAD не создаётся (например, нет onnxruntime): образ с таким дефектом не публикуется.
RUN python -m app.selfcheck

# Метаданные сборки — в конце (кэш слоёв torch/GigaAM не сбрасывается); запекаются в образ.
ARG APP_VERSION=0.0.0
ARG APP_GIT_COMMIT=unknown
ARG APP_BUILT_AT=unknown
ENV APP_VERSION=${APP_VERSION} APP_GIT_COMMIT=${APP_GIT_COMMIT} APP_BUILT_AT=${APP_BUILT_AT} GIGAAM_GIT_COMMIT=${GIGAAM_GIT_COMMIT}

# uid совпадает с backend (10001): backend читает и удаляет файлы записей, созданные ASR на общем томе.
RUN useradd --system --uid 10001 --no-create-home asr && mkdir -p /data/recordings && chown asr /data/recordings
USER asr

EXPOSE 8090
CMD ["python", "-m", "app.main"]
