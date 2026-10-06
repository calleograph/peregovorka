# DEPLOYMENT.md — развёртывание и эксплуатация

Репозиторий без изменений разворачивается в любой организации: всё
специфичное задаётся в `.env` (см. `.env.example`, каждая переменная описана).
Все команды выполняются из корня Git checkout. Любой скрипт работает **только
с текущим экземпляром** (`COMPOSE_PROJECT_NAME`).

> Статус проверки: скрипты и Compose написаны и синтаксически проверены,
> но **ещё не запускались на реальном Ubuntu с Docker** (в среде разработки
> Docker нет). Первый реальный прогон обязан идти через `--dry-run`.
> Актуальный статус — `PROJECT.md`, раздел «Известные ограничения».

## 1. Профили установки

| | `shared-host` | `standalone` |
| --- | --- | --- |
| Сервер | уже работает, есть другие приложения (пример: Ubuntu 24.04 + Moodle на Apache :80 за внешним reverse proxy; системный nginx для доп. проектов) | новый выделенный Ubuntu Server |
| Docker/Compose | внешняя зависимость; нет — установка **останавливается** с диагностикой | может быть установлен `install.sh` (`apt install`, репозиторий Docker) |
| nginx | отдельный новый site-файл; `nginx -t` до reload; существующие файлы (в т.ч. `sites-available/projects`) **не редактируются** | отдельный site-файл; nginx может быть установлен |
| Файрвол | не меняется | меняется только с `--configure-firewall` |
| apt upgrade / dist-upgrade / autoremove / reboot | никогда | никогда |
| docker system/volume prune, чужие контейнеры | никогда | никогда |
| Apache / PHP / PHP-FPM / Moodle | не затрагиваются | — |

## 2. Порты

Все порты задаются в `.env` без значений по умолчанию в Compose и проверяются
`preflight.sh`. Автоматического «подбора свободного порта» нет.

| Переменная | Протокол | Где слушает | Назначение |
| --- | --- | --- | --- |
| `WEB_PORT` (+`WEB_BIND_ADDR`, по умолчанию `127.0.0.1`) | TCP | контейнер `web` | вход для host-nginx / reverse proxy |
| `NGINX_LISTEN_PORT` | TCP | host nginx | порт нашего site-файла (куда ходит внешний proxy) |
| `LIVEKIT_HTTP_PORT` | TCP | **только 127.0.0.1** | API/WebSocket LiveKit (для диагностики/proxy) |
| `LIVEKIT_TCP_PORT` | TCP | `LIVEKIT_BIND_ADDR` | ICE/TCP (запасной канал, если UDP закрыт) |
| `LIVEKIT_UDP_PORT` | UDP | `LIVEKIT_BIND_ADDR` | ICE/UDP, **один mux-порт** вместо диапазона |
| — | — | — | PostgreSQL/Redis **не публикуются** (только внутренняя сеть проекта) |

Клиентам LAN должны быть доступны `LIVEKIT_TCP_PORT/tcp` и `LIVEKIT_UDP_PORT/udp`
хоста **напрямую** (не через HTTP-прокси) по адресу `LIVEKIT_NODE_IP`.
`use_external_ip` отключён: внешние STUN/облачные сервисы не используются.

## 3. DNS, TLS и reverse proxy

- Заведите DNS-имя (`NGINX_SERVER_NAME`, например `meet.<домен>`), указывающее
  на внешний reverse proxy (shared-host) или на сервер (standalone).
- **HTTPS обязателен**: браузер выдаёт доступ к микрофону только в secure
  context. `APP_PUBLIC_URL` и `LIVEKIT_PUBLIC_URL` (`wss://…/livekit`) — с
  публичным именем.
- shared-host: внешний proxy терминирует TLS и передаёт трафик на
  `NGINX_LISTEN_PORT` хоста с заголовком `X-Forwarded-Proto: https`; он должен
  пропускать WebSocket (`Upgrade`) для `/api/v1/ws` и `/livekit/`.
  Наш site-файл генерируется из `deployment/nginx/site.http.conf.tpl`.
- standalone с TLS на этом nginx: укажите `NGINX_TLS_CERT`/`NGINX_TLS_KEY`
  (пути на хосте) — используется `site.tls.conf.tpl`.
- `/internal/` из интернета/LAN не доступен (404 на host-nginx и в `web`).

## 4. Active Directory

1. **LDAPS** (636) на контроллерах домена должен быть включён с
   сертификатом, выданным доверенным CA.
2. Экспортируйте цепочку CA в PEM (`ad-ca.pem`; корневой + промежуточные) и
   положите на хост, например `/etc/peregovorka/ad-ca.pem`; путь — в
   `LDAP_CA_FILE`. Файл монтируется в контейнер read-only. Проверка
   сертификата (цепочка и имя хоста) обязательна; обходов нет. В
   `LDAP_URIS` указывайте **DNS-имена**, совпадающие с SAN сертификата.
3. Создайте сервисную учётку **только на чтение** (`LDAP_BIND_DN`).
4. Создайте AD-группу администраторов и укажите её DN в `LDAP_ADMIN_GROUP_DN`.
   Администраторы определяются **только** членством (включая вложенные группы).
5. Необязательно: `LDAP_ACCESS_GROUP_DN` — общий «вход разрешён» для всех.
   Доступ к конкретным комнатам задаётся ACL комнаты в админке.
6. Защита от блокировок: приложение прекращает обращаться в AD по логину
   после `LOGIN_MAX_FAILURES_PER_USER` ошибок (по умолчанию 3) на
   `LOGIN_LOCKOUT_SECONDS`. Значение должно быть **меньше** порога блокировки
   AD (Account lockout threshold).

## 5. Данные (`DATA_ROOT`) и секреты

- Код (Git checkout) и данные хранятся раздельно. `DATA_ROOT` содержит:
  `postgres/`, `redis/`, `models/gigaam/`, `recordings/`, `exports/`,
  `backups/`, `state/` (журнал деплоев).
- Секреты — только в `.env` (права `600`, владелец — пользователь деплоя), в
  Git не попадает. `APP_MASTER_KEY` шифрует секреты в БД; **храните его копию
  отдельно** — без него настройки с паролями (например, SMB-хранилища)
  невосстановимы.
- Сертификаты/CA монтируются read-only.

## 6. Модель ASR

Модель в Git не хранится. Из корня репозитория:

```bash
scripts/models.sh                       # скачать (нужен интернет)
scripts/models.sh --from-dir /mnt/usb/gigaam   # закрытая сеть: готовый каталог
```

Нужны файлы `v3_e2e_rnnt.ckpt` и `v3_e2e_rnnt_tokenizer.model`. Повторный
запуск при совпадении md5 ничего не скачивает. ASR-сервис **не скачивает**
модель сам: без файла он остаётся not-ready.

## 6.1 Настройка после установки (админка → вкладки)

| Вкладка | Что задать |
| --- | --- |
| Переговорки | название, технический идентификатор, пароль (необязательно), число участников, транскрибация/запись аудио/камера/экран, сроки хранения текста и аудио (пусто — бессрочно, `0` — после обработки), доступ (группы/пользователи AD — поиск по каталогу прямо в форме), инструкции протокола комнаты |
| Хранилище | куда выгружать протоколы: локальный каталог (`/data/exports` = `$DATA_ROOT/exports`) или **SMB**: сервер, общий ресурс, подкаталог, домен/учётка с правом записи, пароль. Кнопка «Проверить подключение» делает пробную запись. Структура: `<комната>/<дата, день недели>/<время>/protocol.txt` |
| Обезличивание | адрес и токен сервиса (DocClean или произвольный JSON API), режим и группы данных, таймауты. Без включённого обезличивания краткий протокол не создаётся |
| LLM и протокол | тип API (OpenAI/Anthropic/совместимый), адрес, модель, ключ; общие инструкции; автосоздание по завершении встречи |
| Экран | профиль трансляции (чёткость / сбалансированный / плавность), звук экрана, «один показывающий» |
| Пользователи | отключение/включение учёток (мгновенно прекращает сессию); часовой пояс для имён папок |

Секреты (пароль SMB, токен обезличивателя, ключ LLM) хранятся в БД **зашифрованными** ключом `APP_MASTER_KEY` и не показываются
после сохранения. Для HTTPS-адресов обезличивателя/LLM используется тот же корпоративный CA (`LDAP_CA_FILE`) при включённой опции.
Контейнеры backend и asr работают от uid **10001**; `install.sh` выдаёт этому uid только `$DATA_ROOT/recordings` и `$DATA_ROOT/exports`.
Выгруженные во внешнее хранилище файлы приложение по срокам хранения **не удаляет**.

## 7. Установка на существующий общий сервер (shared-host)

```bash
git clone <repo-url> /var/www/projects/peregovorka && cd /var/www/projects/peregovorka
git checkout <release-tag-or-sha>                 # воспроизводимая версия
chmod +x scripts/*.sh                              # если права не сохранились при копировании
cp .env.example .env && chmod 600 .env && $EDITOR .env
#   INSTALL_PROFILE=shared-host, уникальный COMPOSE_PROJECT_NAME, свободные порты,
#   NGINX_SITE_NAME и NGINX_SERVER_NAME (уникальные)
scripts/models.sh                                 # или --from-dir
scripts/preflight.sh                              # только чтение; исправьте все FAIL
scripts/install.sh --profile shared-host --dry-run   # прочитайте ПЛАН
scripts/install.sh --profile shared-host
scripts/smoke-test.sh
```

### 7.1 Что создаётся на хосте (shared-host)

Файлы и каталоги:

| Путь | Что | Когда |
| --- | --- | --- |
| `$DATA_ROOT/{postgres,redis,models/gigaam,recordings,exports,backups,state}` | каталоги данных | install |
| `$NGINX_SITES_AVAILABLE/$NGINX_SITE_NAME` | **один** site-файл с маркером `managed-by: peregovorka:<проект>` | install, если `NGINX_MANAGE=yes` |
| `$NGINX_SITES_ENABLED/$NGINX_SITE_NAME` | симлинк на него | install |
| `$DATA_ROOT/state/deploy-history.log` | журнал деплоев | deploy |
| `$DATA_ROOT/backups/*.dump` | резервные копии | backup |
| Docker: образы `<проект>-{backend,asr,web,livekit}:<тег>`, сеть `<проект>_default`, контейнеры `<проект>-<сервис>-1` | ресурсы compose-проекта | install/deploy |

Команды, которые выполняет установщик (полный список печатает `--dry-run`):
`docker compose -p <проект> build | up -d postgres redis | run --rm --no-deps backend alembic upgrade head | up -d`,
`mkdir -p`, запись/симлинк site-файла, `nginx -t`, при успехе `systemctl reload nginx`
(или `nginx -s reload`). При ошибке `nginx -t` собственные изменения
откатываются, reload **не выполняется**.

Не выполняется: `apt upgrade/dist-upgrade/autoremove`, `reboot`,
`docker system prune`, `docker volume prune`, перезапуск/остановка чужих
контейнеров, `docker compose down` чужих проектов, изменение Apache, PHP,
PHP-FPM, Moodle, `sites-available/projects`, системного файрвола.

## 8. Установка на новый чистый сервер (standalone)

```bash
sudo apt-get update && sudo apt-get install -y git      # единственное ручное требование
sudo git clone <repo-url> /opt/peregovorka && cd /opt/peregovorka
sudo chown -R $USER: /opt/peregovorka && git checkout <tag>
cp .env.example .env && chmod 600 .env && $EDITOR .env   # INSTALL_PROFILE=standalone
scripts/models.sh
scripts/install.sh --profile standalone --dry-run
scripts/install.sh --profile standalone --configure-firewall   # ufw — только по явному флагу
scripts/smoke-test.sh
```

`install.sh` в standalone может выполнить `apt-get install` для Docker и
nginx (без `upgrade`), но не делает `dist-upgrade`/`reboot`.

## 9. Обновление

```bash
scripts/deploy.sh --dry-run          # что будет сделано
scripts/deploy.sh                    # fast-forward по upstream-ветке
scripts/deploy.sh --ref v1.2.0       # либо конкретный тег/SHA (воспроизводимо)
```

Deploy отказывает при локальных изменениях в рабочей копии и при не-fast-forward.
Если между версиями изменились миграции — автоматически делается backup БД
(`--no-backup` — только осознанно). Версия и commit видны в
`GET /api/v1/version` и в админке.

## 10. Backup, restore, rollback

- `scripts/backup.sh [--with-env] [--with-recordings] [--keep N]` — `pg_dump -Fc`.
- `scripts/restore.sh <dump>` — после подтверждения (ввод имени проекта),
  с автоматическим страховочным дампом `pre-restore`.
- `scripts/rollback.sh [--to SHA]` — возвращает **код и образы** (пересборки нет).
  БД не откатывается: при наличии миграций между версиями выводится
  предупреждение и требуется подтверждение. Правила — `docs/MIGRATIONS.md`.

## 11. Диагностика

```bash
scripts/status.sh              # состояние и health всех сервисов проекта
scripts/logs.sh backend -f     # журналы (postgres redis livekit backend asr web)
scripts/preflight.sh --phase post
scripts/smoke-test.sh
```

| Симптом | Куда смотреть |
| --- | --- |
| Не входит доменная учётка | `logs.sh backend` (коды `invalid_credentials`/`account_locked`/`tls_error`), `preflight.sh` (проверка сертификата LDAPS) |
| Нет звука / «подключение» зависает | доступность `LIVEKIT_UDP_PORT/udp` и `LIVEKIT_TCP_PORT/tcp` с клиента; `LIVEKIT_NODE_IP`; `logs.sh livekit` |
| Нет микрофона в браузере | страница открыта не по HTTPS |
| Текст не появляется | `curl .../api/v1/health/ready` (поле `asr`), `logs.sh asr`, `scripts/models.sh` |
| Порт занят | `preflight.sh` называет порт; измените его в `.env` |

## 12. Правила для разработчика (Git)

Основная ветка — стабильная; релизы помечаются тегами. Сервер обновляется
только через `deploy.sh` (Git), без ручного копирования. Схема БД меняется
только миграциями Alembic (`backend/migrations/versions`).
