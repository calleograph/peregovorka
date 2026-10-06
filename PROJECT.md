# PROJECT.md — Peregovorka: текущее состояние

Единственное актуальное описание проекта. Только факты; запланированное и непроверенное помечено явно. Целевая архитектура и
причины решений — `ARCHITECTURE.md`. Журнал изменений — `HISTORY.md` (только дописывается). Старый проект — `legacy/`, `docs/legacy/`.

## 1. Назначение

Локальная корпоративная система голосовых переговорок: вход доменной учёткой AD (LDAPS), звук/видео/экран через локальный
LiveKit, транскрибация каждого участника отдельным микрофонным треком (GigaAM v3 E2E RNNT + Silero VAD), история встреч,
протоколы и резюме по инструкции пользователя (после обезличивания по API, через LLM), выгрузка в локальное/SMB-хранилище.
Внешние облака для авторизации, медиа и распознавания не используются. Создан поверх старого PHP-проекта (`legacy/php/`,
локально, вне Git); новый код от него не зависит.

## 2. Фактическая архитектура

Сервисы compose (`deployment/compose.yml`): `postgres`, `redis`, `livekit`, `backend` (FastAPI), `asr` (воркер + health),
`web` (nginx + React SPA). Медиа — LiveKit ⇄ браузер напрямую (UDP mux + TCP). Backend ⇄ ASR — Redis (`asr:sessions`,
`asr:control`, `asr:segments`, `asr:heartbeat`; `docs/ASR_CONTRACT.md`); запись аудио — общий том.

Сквозной путь: логин AD → список комнат по ACL → `join` (встреча + команда ASR + токен LiveKit; **не ждёт готовности ASR**) →
браузер: LiveKit и канал событий (WebSocket) открываются параллельно, микрофон публикуется асинхронно → ASR (скрытый участник,
микрофонные треки) → VAD → очередь → GigaAM → `asr:segments` → реплика в БД → pub/sub → WebSocket → панель.

**Доступ к завершённой встрече** (`services/access.py`): админ; участник идущей встречи; «аренда» (lease) у тех, кто был в комнате при
завершении, пока открыта страница встречи (`/release` при уходе; срок — `post_meeting_access_minutes`); политика комнаты
`history_access=participants`; явные `meeting_grants`. Прямой URL/ID доступа не даёт. WebSocket подписка — те же правила.

**Протоколы** (`services/protocols.py`): `POST /meetings/{id}/protocols {kind: protocol|summary, instruction}` — инструкцию
подтверждает пользователь в окне; обезличивание → LLM (fail closed); просмотр/правка/удаление (удаление — админ, аудит); экспорт
md/txt/docx/pdf; шаблоны инструкций (общие — админ, личные). Выгрузка `official-protocol.md` / `summary.md` в хранилище.

**Хранилища**: протоколы (`storage`) и записи (`audio_storage`) — раздельные настройки в БД, `local|smb`; недоступность
хранилища записей не теряет файл (статус `failed`, повторная выгрузка автоматически и кнопкой).

**Диагностика** (`services/diagnostics.py`, `timings.py`, `api/client.py`): отчёт (версии, ядро, WebSocket-проба `/rtc/v1`,
RTC TCP/UDP, зависимости, ASR, тайминги) с маскированием секретов; метрики времени входа по этапам (backend, браузер, ASR);
события и статистика WebRTC из браузера (screen share).

## 3. Важные каталоги и файлы

| Путь | Назначение |
| --- | --- |
| `backend/app/auth/` | LDAPS, вход, throttle, сессии, CSRF/роли |
| `backend/app/models/entities.py`, `backend/migrations/versions/` | схема (миграции 0001–0003: + `history_access`, шаблоны, `meeting_grants`, поля экспорта записей и протоколов) |
| `backend/app/services/` | `meetings`, `access`, `protocols`, `export_docs` (md→docx/pdf), `storage`, `recordings`, `settings` (группы: storage, audio_storage, anonymizer, llm, protocol, screen, general), `diagnostics`, `timings`, `livekit` |
| `backend/app/api/` | `meetings`, `templates`, `client`, `admin`, `admin_system`, `internal` (webhook, diag, smoke), `ws`, `health` |
| `asr-service/app/` | GigaAM-провайдер (потоки torch: `ASR_CPU_THREADS`, `ASR_INTEROP_THREADS`), VAD, очередь (тайминги каждого сегмента в журнале), воркер комнаты, `bench.py` |
| `frontend/src/` | `pages/RoomPage` (этапы входа, плитки, показ экрана, ресайз панели), `MeetingPage`/`HistoryPage`, админка (`pages/admin/*`, поля с примерами в `fields.ts`), `markdown.ts` (безопасный разбор), `diagnostics.ts`, `mediaErrors.ts`, `liveSocket.ts` |
| `scripts/` | `setup`, `install`, `preflight`, `deploy [--pull]`, `smoke-test` (таблица), `diag`, `tune-kernel`, `asr-bench`, `collect-metrics`, `check-updates`, `verify`, `ctl`, `backup/restore/rollback`; `lib/mask.sh` (маскирование секретов) |
| `deployment/` | `compose.yml`, `compat.env` (проверенные версии), образ LiveKit, шаблоны nginx (`X-Forwarded-Proto` сохраняется) |
| `docs/` | `INSTALL_AND_UPDATE`, `COMPATIBILITY`, `ACCEPTANCE_TEST`, `ASR_CONTRACT`, `MIGRATIONS`, `AUDIT`; корень: `DEPLOYMENT.md` (в т.ч. требования к reverse proxy §3.1) |

## 4. Технологии и версии

Python 3.12 (образы), FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16 и Redis 7 (мажор закреплён намеренно), LiveKit Server
(`LIVEKIT_IMAGE_TAG`, по умолчанию `latest`, проверен v1.13.7), `livekit` 1.1.20 / `livekit-api` 1.2.1 (Python), GigaAM (закреплённый коммит),
silero-vad, torch; frontend: React 19, React Router 7, Vite 8, TypeScript 7, Vitest 5, `livekit-client` 2.22.3. Python-зависимости заданы диапазонами
(≥ проверенной, < следующего мажора), npm — `^` + lock; базовые образы — через ARG (`docs/COMPATIBILITY.md`).

## 5. Конфигурация

Инфраструктура — `.env` (`.env.example`). Бизнес-настройки — в админке (БД), секреты шифруются `APP_MASTER_KEY`. Админка сгруппирована:
Обзор · Встречи · Комнаты и люди · Протоколы · Интеграции · Система; у каждого поля название, пояснение и пример. Таймауты комнаты LiveKit
(`LIVEKIT_ROOM_DEPARTURE_TIMEOUT`, `LIVEKIT_ROOM_EMPTY_TIMEOUT`) и `MEETING_END_GRACE_SECONDS` (60) задают льготный период: webhook
`room_finished` встречу сразу не завершает.

## 6. Проверено (что именно и как)

| Область | Состояние |
| --- | --- |
| Backend | 127 тестов pytest проходят (SQLite + fakeredis + подставной каталог; HTTP-мокирование LLM/обезличивания): доступ после завершения (lease/release/политика/grants), протоколы (инструкция, правка, экспорт docx/pdf/md/txt), шаблоны, удаления с аудитом, выгрузка записей и недоступность хранилища, диагностика/маскирование/WebSocket-проба, тайминги, `room_finished` внутри льготного периода |
| ASR | 31 тест (потоки torch через подставной torch, тайминги сегментов, порядок закрытия потоков/комнаты); реальная модель/LiveKit **не запускались** |
| Frontend | 34 теста vitest (Markdown/XSS, ошибки устройств, тайминги, backoff), `tsc`, `vite build`; прогнан в Chrome против локального стенда (подставной AD, без LiveKit/ASR): админка, страница встречи, окно протокола, просмотр/правка, подтверждение удаления, ветка отказа входа; макет комнаты проверен на статичной разметке с боевым CSS |
| Shell | 139 тестов (маскирование секретов, трактовка кодов WS, параметры ядра, согласованность версий, nginx `X-Forwarded-Proto`, мастер, `.env`, docker-подсистема на подставном docker) |
| Миграции | 0001–0003, тест сверки схемы с моделями |

## 7. Известные ограничения и непроверенное

- **Не проверялось на реальных LiveKit/GigaAM/SMB/клиентах:** живая комната, показ экрана (причины остановки, статистика, повторный запуск),
  автоматический повторный вход, ASR-инференс, реальный SMB, `smoke-test`/`diag.sh`/`tune-kernel.sh` на сервере (проверены функции и логика на подставных данных),
  синтаксис `room: {departure_timeout, empty_timeout}` в конфигурации LiveKit (написан по документации, не запускался).
- **Замер времени входа после обновления LiveKit не выполнен.** Гипотеза (404 на `/rtc/v1` → запасной путь) подтверждена наблюдением владельца;
  устранение — новый сервер, проверка — `smoke-test.sh`. Второй вклад в задержку устранён в коде: комната и WebSocket открываются сразу после `/join`,
  микрофон публикуется асинхронно (раньше ждал ответа на запрос разрешения).
- **`Attempted to drop unknown FFI handle` (ASR):** причина не подтверждена. Вероятные: двойное отключение комнаты и закрытие потоков после
  освобождения комнаты — порядок исправлен (потоки дожидаются, уже отключённая комната не отключается повторно). Нужен повторный тест на сервере;
  пока warning не считать безобидным.
- Бенчмарк потоков ASR (2 vs 4) не выполнен: инструмент готов (`scripts/asr-bench.sh`), результатов нет.
- Приёмочный тест (`docs/ACCEPTANCE_TEST.md`) не выполнен; системные требования предварительные.
- Права на хранилище SMB, TLS-проверка и RTC-порты проверяются скриптами только на целевом сервере.
- Группы пользователя фиксируются в сессии при входе. «Один показывающий» — правило клиента. Выгруженные во внешнее хранилище файлы по срокам
  хранения не удаляются. Контрольные суммы модели не проверяются (решение владельца).
- BuildKit на Ubuntu 24.04 (docker.io 29.1.3) нестабилен — установщик проверяет builder и переключается на legacy.

## 8. Незавершённые задачи

1. Реальная проверка на сервере: `smoke-test.sh`, замер входа, показ экрана 20–30 минут, FFI-warning, приёмочный сценарий; затем фиксация системных требований.
2. Выбор ASR-модели/runtime в админке (запрошено владельцем, см. HISTORY.md).
3. Публикация релизного тега; удаление `legacy/php/` по решению владельца.
4. Метрики Prometheus; E2E-тесты UI (Playwright) в CI; серверное принуждение «один показывающий»; контроль места под записи.
