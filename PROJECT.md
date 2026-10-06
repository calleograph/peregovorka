# PROJECT.md — Peregovorka: текущее состояние

Единственное актуальное описание проекта. Только факты; запланированное помечено как
незавершённое. Целевая архитектура и причины решений — `ARCHITECTURE.md`.
Журнал изменений — `HISTORY.md` (только дописывается). Старый проект — `legacy/`, `docs/legacy/`.

## 1. Назначение

Локальная корпоративная система голосовых переговорок: вход доменной учёткой AD (LDAPS), звук/видео/экран через
локальный LiveKit, транскрибация каждого участника отдельным микрофонным треком (GigaAM v3 E2E RNNT + Silero VAD),
история встреч, полный протокол по времени и участникам, выгрузка в локальное/SMB-хранилище, краткий протокол
через LLM после обезличивания по API. Внешние облака для авторизации, медиа и распознавания не используются.

Создан **поверх** старого PHP-проекта «Корпоративный AI-чат»: логика LDAPS-входа, шифрования секретов, защиты от
перебора, клиент обезличивателя DocClean, LLM-адаптеры и состав админки портированы на Python/React. PHP-код лежит в
`legacy/php/` как справочный; **новый код от него не зависит** (остались только комментарии о происхождении).

## 2. Фактическая архитектура

Сервисы compose (`deployment/compose.yml`): `postgres`, `redis`, `livekit`, `backend` (FastAPI), `asr` (воркер + health),
`web` (nginx + React SPA). Медиа — LiveKit ⇄ браузер напрямую (UDP mux + TCP). Backend ⇄ ASR — Redis
(`asr:sessions`, `asr:control` (start/stop/config), `asr:segments`, `asr:heartbeat`; `docs/ASR_CONTRACT.md`);
запись аудио — общий том (`/data/recordings`, uid 10001 у обоих контейнеров).

Сквозной путь: логин AD → список комнат по ACL → `join` (встреча + команда ASR + LiveKit-токен `u-<uuid>`) → микрофон
→ ASR (скрытый участник, только микрофонные треки) → VAD → очередь → GigaAM → `asr:segments` → backend пишет реплику
(пользователь по identity трека) → pub/sub → WebSocket → панель транскрипции. При завершении встречи —
`ProtocolService.finalize`: WAV из PCM, выгрузка стенограммы в хранилище, автопротокол (если включён).

## 3. Важные каталоги и файлы

| Путь | Назначение |
| --- | --- |
| `backend/app/auth/` | `directory.py` (LDAPS, группы IN_CHAIN, поиск групп/пользователей для ACL), `service.py` (вход), `throttle.py`, `sessions.py`, `deps.py` (CSRF/роли; отключённый пользователь теряет сессию) |
| `backend/app/models/entities.py` | users, rooms, room_acl, meetings, meeting_participants, transcript_segments, recordings, protocols, audit_log, app_settings |
| `backend/migrations/versions/` | `0001` (основная схема), `0002` (recordings, protocols) — схема только через Alembic |
| `backend/app/services/meetings.py` | вход/выход, вместимость, пароль комнаты, reaper (сверка с LiveKit), завершение, запись вкл/выкл |
| `backend/app/services/settings.py` | настройки из админки (хранилище, обезличивание, LLM, протокол, экран, общие), секреты шифруются `SecretBox` |
| `backend/app/services/storage.py` | `LocalStorage`, `SmbStorage`, структура папок `<комната>/<дата, день недели>/<время>` |
| `backend/app/services/protocols.py` | стенограмма, выгрузка, регистрация записей, краткий протокол (обезличивание → LLM, fail closed) |
| `backend/app/services/recordings.py` | PCM → WAV и раскладка по папкам |
| `backend/app/integrations/{anonymizer,llm}.py` | клиент API обезличивания (DocClean/generic) и LLM (OpenAI/Anthropic/совместимые) |
| `backend/app/workers/` | `segment_consumer` (приём реплик), `reaper`, `retention` (сроки хранения текста/аудио) |
| `backend/app/api/` | REST `/api/v1`: auth, rooms, meetings (протоколы, запись, .txt, аудио), admin (комнаты), admin_system (настройки, пользователи, каталог AD, встречи, записи, состояние), health, ws; внутренние `/internal/v1` |
| `asr-service/app/` | провайдер GigaAM, Silero VAD, сегментатор, очередь инференса, конвейер трека (+запись PCM, флаги), LiveKit-воркер, менеджер, health |
| `frontend/src/` | вход, комнаты, комната (LiveKit, трансляция экрана, запись, транскрипция), история и протоколы, админка (10 вкладок) |
| `frontend/src/screenShare.ts` | профили трансляции экрана (чёткость / сбалансированный / плавность) |
| `scripts/lib/envlib.sh`, `scripts/lib/dockerlib.sh`, `tests/scripts/` | чистые функции (.env, DATA_ROOT, LDAP URI, RAM, сводка портов, распознавание своего nginx-site) и Docker-build подсистема (диагностика, пробная сборка, fallback BuildKit→legacy, отпечатки исходников для возобновления); 74 shell-теста (в CI) |
| `scripts/install.sh` | установка этапами (prerequisites→preflight→dirs→models→build→database→migrations→services→healthcheck→nginx→firewall), возобновление, nginx — последним |
| `scripts/setup.sh` | мастер установки (вопросы с валидацией, свободные порты, секреты, самопроверка .env, preflight, план, установка по «y») |
| `scripts/*.sh`, `deployment/` | preflight, install, deploy, rollback, backup, restore, status, logs, smoke-test, models; compose, образ LiveKit, nginx-шаблоны |
| `docs/INSTALL_AND_UPDATE.md`, `docs/AUDIT.md` | пошаговая инструкция развёртывания/обновления; аудит интерфейса и админки |
| `legacy/php/` | старый проект (локально, вне Git); безопасно удалять (см. §8) |

## 4. Технологии

Python 3.12 (образы), FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16, Redis 7, LiveKit Server, ldap3, livekit-api/livekit
SDK, smbprotocol, httpx, GigaAM (pinned commit), silero-vad, torch, React 18 + TypeScript + Vite, livekit-client.
Тесты: pytest (+asyncio, fakeredis, httpx.MockTransport), vitest.

## 5. Конфигурация

Параметры инфраструктуры — `.env` (`.env.example`). Бизнес-настройки (хранилище, обезличивание, LLM, инструкции,
профиль экрана, часовой пояс) — в админке, в БД; секреты зашифрованы `APP_MASTER_KEY`. Порты хоста обязательны, без
значений по умолчанию. Профили установки `shared-host` / `standalone`; данные — `DATA_ROOT` отдельно от checkout.

## 6. Реализовано (и как проверено)

| Область | Состояние |
| --- | --- |
| Вход AD, throttle, сессии, CSRF, роли, отключение пользователей | **102 теста backend проходят** (SQLite + fakeredis + подставной каталог) |
| Комнаты/ACL/пароль/вместимость, встречи, reaper, webhook, токены LiveKit, WebSocket | то же |
| Админ-API: комнаты, настройки (секреты не раскрываются), пользователи, встречи, поиск в AD, система, аудит | то же |
| Стенограмма: выгрузка в локальное хранилище (структура папок, шапка «Участвовали»), `.txt`, коллизии минут | то же |
| Краткий протокол: обезличивание (DocClean/generic, fail closed, чанки) → LLM (OpenAI/Anthropic/совместимые); LLM не получает имён; автосоздание | то же (HTTP-мокирование) |
| Кнопки начать/остановить запись (REST → ASR `config` → WS-индикатор) | backend покрыт тестами; ASR-часть — 23 теста |
| Запись аудио: PCM → WAV, структура папок, админ-скачивание, сроки хранения (текст/аудио раздельно) | тесты backend (PCM имитируется файлом) и ASR (PCM пишется конвейером) |
| SMB-хранилище | построение UNC-путей и ошибки проверены тестами; **реальный SMB-сервер не проверялся** |
| Миграции 0001–0002 | тест сверки схемы с моделями, цепочка, откат |
| ASR: `GigaAmProvider`, `SileroVad`, `RoomWorker` (LiveKit) | написано по исходникам библиотек, **не запускалось** (нет torch/модели/LiveKit) |
| Frontend (вход, комнаты, админка, история) | `tsc`, 6 тестов vitest, `vite build`; **прогнан в Chrome против локального стенда** (подставной AD, без LiveKit/ASR) — см. `docs/AUDIT.md`; живая комната, трансляция экрана, запись, реплики в реальном времени **не проверены** |
| Compose, Dockerfile, nginx-шаблоны, скрипты | первая реальная установка на Ubuntu 24.04 (shared-host) состоялась у владельца; найденные дефекты исправлены, покрыты shell-тестами (мастер, `.env`, DATA_ROOT, LDAP URI, RAM, models.sh); **чистая переустановка после исправлений не проверена** |

## 7. Известные ограничения

- Ничего не проверено на реальных AD, PostgreSQL, Redis, LiveKit, GigaAM, SMB-сервере, браузере и Docker — первый прогон на Linux обязателен (DEPLOYMENT.md, `--dry-run`).
- Группы пользователя фиксируются в сессии при входе.
- Присутствие: webhook LiveKit + сверка reaper'ом каждые 10 с через LiveKit API; при недоступном LiveKit встречи не завершаются автоматически.
- «Один показывающий» — мягкое правило клиента (сервер не блокирует публикацию).
- Отбрасывание сегментов при переполнении очереди ASR не восстанавливается (`dropped` в `/readyz` и «Система»).
- Per-IP лимит входа best-effort (`TRUSTED_PROXY_HOPS`); основной барьер — лимит по логину.
- Первая реальная установка (Ubuntu 24.04, shared-host) выявила 6 дефектов мастера/preflight/models — исправлены (HISTORY.md), повторная чистая установка на сервере ещё не выполнялась.
- Реальная несовместимость BuildKit на Ubuntu 24.04 (docker.io 29.1.3, containerd 2.2.1, overlayfs): сборка падает на экспорте образа (`mount callback failed … containerd-mount`, `failed to open writer … locked`) независимо от параллельности (гипотеза о параллельной сборке backend+ASR опровергнута); `DOCKER_BUILDKIT=0` работает. Установщик проверяет builder пробной сборкой и явно переключается на legacy; деструктивных действий над Docker не выполняет. Legacy builder в новых версиях Docker может исчезнуть.
- Контрольные суммы модели не проверяются (по решению владельца: файлы upstream могут меняться); проверяется только размер.
- Предположены и не подтверждены: тег `LIVEKIT_IMAGE_TAG` и путь `/livekit-server` в образе LiveKit; VP9 для экрана зависит от браузера (SDK использует резервный кодек).
- Выгруженные во внешнее хранилище файлы не удаляются по срокам хранения; нет контроля свободного места под записи.
- Репозиторий создан локально (`main`, CI в `.github/workflows/ci.yml`); публикация на GitHub выполняется владельцем (docs/INSTALL_AND_UPDATE.md §1). `legacy/php/` в репозиторий не входит.

## 8. Незавершённые задачи

1. Реальная проверка на Linux/Docker/AD/LiveKit/GigaAM/SMB/браузере; по результатам — исправления.
2. Публикация на GitHub, deploy key на сервере, первый релизный тег (`docs/INSTALL_AND_UPDATE.md`).
3. Удаление `legacy/php/` по решению владельца (там рабочие данные старого проекта: `var/`, в т.ч. SQLite и ключ).
4. Метрики Prometheus; E2E-тесты UI (Playwright); серверное принуждение «один показывающий»; контроль места под записи.
5. Переопределение профиля экрана на уровне комнаты; обновление групп AD без повторного входа.
