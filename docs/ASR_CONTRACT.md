# Контракт backend ⇄ ASR (Redis)

Backend не импортирует код ASR, ASR не обращается к PostgreSQL. Обмен — только
через Redis (пароль из `REDIS_PASSWORD`, сеть проекта).

## Управление: backend → ASR

- Hash `asr:sessions` — активные сессии: поле = `meeting_id`, значение = JSON
  `{"meeting_id","room_name","room_id","transcribe":true,"record_audio":false,"started_at"}`.
  Backend пишет при старте встречи и удаляет при завершении.
- Stream `asr:control` — команды (идемпотентны):
  `type=start|stop|config`, `meeting_id`, `room_name`, `payload` (JSON как выше). `config` меняет флаги
  идущей сессии (`transcribe`, `record_audio`) — кнопки «начать/остановить запись».
- ASR при запуске обрабатывает весь hash `asr:sessions` (восстановление после
  рестарта), затем читает `asr:control` (XREAD с запомненной позицией; позиция берётся ДО чтения hash, команды не теряются).

## Результаты: ASR → backend

Stream `asr:segments`, consumer group `backend`. Поля сообщения:

| Поле | Описание |
| --- | --- |
| `segment_uid` | UUID сегмента; уникален, backend дедуплицирует по нему |
| `meeting_id` | идентификатор встречи (из `room_name` = `m-<uuid>`) |
| `identity` | LiveKit identity трека (`u-<uuid>`); по ней backend определяет пользователя |
| `started_at`, `ended_at` | UTC ISO-8601 с миллисекундами |
| `text` | распознанный текст |
| `language` | `ru` и т.п. |
| `model` | JSON `{"provider","name","device","version"}` |
| `duration_ms`, `infer_ms`, `queue_ms` | технические метрики |

Идентичности, начинающиеся с `asr-`, никогда не публикуются как реплики.
Backend подтверждает (`XACK`) сообщение только после записи в БД.

## Heartbeat

ASR раз в 10 с пишет ключ `asr:heartbeat` (JSON: `model_loaded`, `queue_depth`,
`active_meetings`, `version`) с TTL 30 с. Readiness backend учитывает этот ключ.

## Запись аудио (общий том)

При `record_audio=true` ASR дописывает сырой PCM (int16, 16 кГц, моно, без заголовка) в
`$RECORDINGS_DIR/<room_name>/<identity>.pcm`. Backend (тот же uid 10001) по завершении встречи превращает файлы в WAV,
раскладывает по структуре комната/дата/время и удаляет PCM. Файлы короче 1 с отбрасываются.
