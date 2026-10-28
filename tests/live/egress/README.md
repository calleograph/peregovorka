# Проверка видеозаписи на настоящем LiveKit Egress

Воспроизводит проверку 0.48.3 (результаты — `docs/VIDEO_RECORDING.md`). Нужно ≈ 5 ГБ на образ Egress (≈ 3,8 ГБ распакованный) и Docker; проект не трогает: контейнеры `pgtest-*`, порты 6390/7890–7892/8092/8002/5184.

1. Каталоги: `mkdir -p ~/egress-test/{rec,cfg,data}; chmod 777 ~/egress-test/rec; ln -sfn ~/egress-test/rec ~/egress-test/data/recordings`; в `cfg/` — `livekit.yaml` из `livekit.yaml.tmpl` (подставьте NODE_IP) и `egress.yaml`.
2. Контейнеры (host-сеть): Redis `redis-server --port 6390 --bind 127.0.0.1`; `livekit/livekit-server:<тег из compose>` с `--config`; `livekit/egress@<digest из compose>` с `--cap-add SYS_ADMIN --shm-size 1g --user "$(id -u):0" --tmpfs /home/egress:uid=$(id -u),gid=0,mode=0770,size=2g -e HOME=/home/egress -e EGRESS_CONFIG_FILE=/etc/egress.yaml` и томом `~/egress-test/rec:/out`.
3. Стенд backend: `LIVE_PORT=8002 LIVE_LK_PORT=7890 VIDEO_ENABLED=yes VIDEO_EGRESS_OUT=/out LIVE_DATA=~/egress-test/data ALLOWED_ORIGINS=http://127.0.0.1:5184 python tests/live/devserver_live.py` (свежая база на каждый запуск) и Vite `DEV_BACKEND_URL=http://127.0.0.1:8002 npx vite --port 5184`.
4. `python tests/live/egress/run_video.py 720p` (и `1080p`): запись 45 с с показом экрана, ffprobe, кадры, нагрузка; `python tests/live/egress/run_more.py` — падение службы, режим «автоматически», пустая комната, удаление. Нужны `BROWSER_PATH`, `BROWSER_FLAGS=--no-sandbox`, `PEREGOVORKA_URL=http://127.0.0.1:5184`, пакет `livekit` (rtc) и Pillow.

Материал синтетический (фейковые камеры Chrome и сгенерированный слайд): по размеру файла и нагрузке на живых людях — только ориентир.
