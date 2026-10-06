# Peregovorka — локальная голосовая переговорка с транскрибацией

Корпоративная веб-система переговорок: вход доменной учёткой Active Directory,
голос/видео/экран через локальный LiveKit, автоматическая транскрибация
**каждого участника отдельно** (GigaAM v3 E2E RNNT + Silero VAD), протокол встречи
по времени и участникам. Всё работает внутри локальной сети, без облаков.

- Архитектура и принятые решения — [ARCHITECTURE.md](ARCHITECTURE.md)
- **Пошаговое развёртывание и обновление через GitHub — [docs/INSTALL_AND_UPDATE.md](docs/INSTALL_AND_UPDATE.md)**
- Порты, AD, данные, диагностика — [DEPLOYMENT.md](DEPLOYMENT.md); аудит интерфейса — [docs/AUDIT.md](docs/AUDIT.md)
- **Фактическое состояние проекта — [PROJECT.md](PROJECT.md)**, журнал изменений — [HISTORY.md](HISTORY.md)
- Все переменные конфигурации — [.env.example](.env.example)
- Контракт backend ⇄ ASR — [docs/ASR_CONTRACT.md](docs/ASR_CONTRACT.md); миграции и бэкапы — [docs/MIGRATIONS.md](docs/MIGRATIONS.md)

## Состав репозитория

| Каталог | Содержимое |
| --- | --- |
| `backend/` | FastAPI: AD-вход, сессии, комнаты/ACL, встречи, LiveKit-токены, WebSocket, админ-API, аудит, хранилище/SMB, протоколы (обезличивание → LLM), записи, сроки хранения; `migrations/` (Alembic), `tests/` |
| `asr-service/` | ASR: провайдеры (GigaAM), VAD-сегментация, очередь инференса, LiveKit-воркер, health; `tests/` |
| `frontend/` | React + TypeScript (Vite): вход, комнаты, комната (трансляция экрана, запись, транскрипция), история и протоколы, админка (комнаты, хранилище/SMB, обезличивание, LLM, экран, пользователи, записи, аудит, система) |
| `deployment/` | `compose.yml`, образ LiveKit, шаблоны nginx |
| `scripts/` | `preflight`, `install`, `deploy`, `rollback`, `backup`, `restore`, `status`, `logs`, `smoke-test`, `models` |
| `docs/` | эксплуатационная документация; `docs/legacy/` — архив старого проекта |
| `legacy/php/` | прежний PHP-проект (AI-чат) — источник для переноса, будет удалён |

## Быстрый старт (коротко)

```bash
cp .env.example .env && chmod 600 .env && $EDITOR .env
scripts/models.sh                  # веса GigaAM в $DATA_ROOT (или --from-dir для закрытой сети)
scripts/preflight.sh               # только проверка, ничего не меняет
scripts/install.sh --profile shared-host --dry-run   # план; затем без --dry-run
scripts/smoke-test.sh
```

Подробно — в [DEPLOYMENT.md](DEPLOYMENT.md). Для shared-host читайте раздел 7 до запуска.

## Разработка и тесты

```bash
# backend (Python 3.12+)
cd backend && pip install -r requirements-dev.txt && pytest
# ASR (тесты не требуют torch/GigaAM — используются подставные провайдер и VAD)
cd asr-service && pip install -r requirements-dev.txt && pytest
# frontend
cd frontend && npm ci && npm test && npm run build
```

LDAP в unit-тестах подменяется (`FakeDirectory`), реальный AD в тестах не используется.
