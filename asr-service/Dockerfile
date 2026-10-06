FROM python:3.12-slim

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG GIGAAM_GIT_COMMIT
ARG APP_VERSION=0.0.0
ARG APP_GIT_COMMIT=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    APP_VERSION=${APP_VERSION} APP_GIT_COMMIT=${APP_GIT_COMMIT}

# git нужен только на время сборки (установка GigaAM из зафиксированного коммита).
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# torch (CPU или CUDA — по индексу) ставится отдельно: общий для GigaAM и Silero VAD.
RUN pip install torch torchaudio --index-url ${TORCH_INDEX_URL}

WORKDIR /srv/asr
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && pip install "gigaam @ git+https://github.com/salute-developers/GigaAM.git@${GIGAAM_GIT_COMMIT}" \
    && apt-get purge -y git && rm -rf /var/lib/apt/lists/*

COPY app ./app

# uid совпадает с backend (10001): backend читает и удаляет файлы записей, созданные ASR на общем томе.
RUN useradd --system --uid 10001 --no-create-home asr && mkdir -p /data/recordings && chown asr /data/recordings
USER asr

EXPOSE 8090
CMD ["python", "-m", "app.main"]
