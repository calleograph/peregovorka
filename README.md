# Peregovorka — локальная голосовая переговорка с транскрибацией

Корпоративная веб-система переговорок для локальной сети. Никаких облачных сервисов для входа, звонков и распознавания речи.

**Что умеет**
- Вход доменной учёткой Active Directory (LDAPS), права на комнаты по группам AD, пароль на комнату.
- Голос, видео и **быстрая трансляция экрана** одним кликом (LiveKit; профили «чёткость» для слайдов/текста и «плавность» для видео).
- **Транскрибация каждого участника отдельно** (GigaAM v3 E2E RNNT + Silero VAD): в реальном времени видно, кто и что сказал.
- Запись беседы по кнопке или автоматически, запись аудио по участникам, сроки хранения текста и аудио.
- Протокол встречи по времени и участникам; выгрузка в локальный каталог или на SMB-ресурс (`комната / дата, день недели / время`).
- Краткий протокол (решения, поручения) через LLM — только после обезличивания текста по API.
- Админка: комнаты, встречи, хранилище, обезличивание, LLM, экран, пользователи, записи, журнал аудита, состояние системы.
- Развёртывание в Docker рядом с другими проектами сервера без вмешательства в них; обновление через Git.

## Установка одной командой (сервер Ubuntu 24.04 с Docker)

```bash
git clone --depth 1 https://github.com/leonheard/peregovorka.git /var/www/projects/peregovorka   && cd /var/www/projects/peregovorka && scripts/setup.sh --profile shared-host
```
Мастер задаёт вопросы, подбирает свободные порты, создаёт `.env` с секретами, проверяет сервер (`preflight`), показывает план и
**применяет его только после вашего подтверждения**. Для чистого сервера — `--profile standalone`. Подробности и обновление — в
[docs/INSTALL_AND_UPDATE.md](docs/INSTALL_AND_UPDATE.md) (обновление: `./scripts/check-updates.sh` → `./scripts/update.sh`).

## Документация

- Архитектура и принятые решения — [ARCHITECTURE.md](ARCHITECTURE.md)
- **Пошаговое развёртывание и обновление через GitHub — [docs/INSTALL_AND_UPDATE.md](docs/INSTALL_AND_UPDATE.md)**
- Порты, AD, данные, диагностика — [DEPLOYMENT.md](DEPLOYMENT.md); аудит интерфейса — [docs/AUDIT.md](docs/AUDIT.md)
- **Фактическое состояние проекта — [PROJECT.md](PROJECT.md)**, журнал изменений — [HISTORY.md](HISTORY.md)
- Все переменные конфигурации — [.env.example](.env.example)
- Версии LiveKit/SDK и политика обновлений — [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md); приёмочный тест — [docs/ACCEPTANCE_TEST.md](docs/ACCEPTANCE_TEST.md)
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

## Ручная установка (коротко)

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
