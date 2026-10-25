# Как настроить reverse proxy и HTTPS

> Относится к версии 0.39.4 · проверено 2026-10-25 · уровень проверки: 1–2 (шаблоны nginx, shell-тесты); настройка за внешним прокси проверена на подставных проверках, настоящий внешний прокси — частично (см. `PROJECT.md`) · тип страницы: практическое руководство

Результат: приложение открывается по HTTPS на вашем имени, звонки подключаются, WebSocket проходит. Для кого: администратор сети. Все порты — [[Справочник: порты и сеть|Справочник-порты-и-сеть]].

## Что нужно заранее

- DNS-имя (например, `meet.example.org`), указывающее на ваш reverse proxy (`shared-host`) или на сервер (`standalone`).
- Сертификат, выданный доверенным центром, **с полной цепочкой**.
- HTTPS обязателен: браузер даёт микрофон и экран только в защищённом контексте.

## Схемы публичного адреса

| Схема | `APP_PUBLIC_URL` и `LIVEKIT_PUBLIC_URL` | Сертификат | Звонки |
| --- | --- | --- | --- |
| Корпоративный домен за внешним прокси (рекомендуется) | `https://meet.example.org` и `wss://meet.example.org/livekit` | выдан доверенным центром, TLS завершает прокси | ничего на устройствах добавлять не нужно |
| Свой центр сертификации | то же имя | сертификат корпоративного центра | центр должен быть доверен на устройствах |
| Доступ по IP | `https://192.0.2.10` и `wss://192.0.2.10/livekit` | обычно самоподписанный (создаёт установщик) | **устройство должно доверять сертификату**, иначе звонки не подключатся; рекомендуется перейти на имя |

Свой сертификат на nginx установщика (`standalone`): задайте `NGINX_TLS_CERT` и `NGINX_TLS_KEY` в `.env` и выполните:

```bash
sudo scripts/install.sh --profile standalone --from nginx     # не запускалась
```

## Требования к любому reverse proxy

| Требование | Что будет, если нарушено |
| --- | --- |
| WebSocket Upgrade для `/livekit/` и `/api/v1/ws` | комната не подключается, нет реплик (проба даёт `400` или `426` вместо `101`) |
| Заголовки `Upgrade` и `Connection: upgrade` передаются, HTTP/1.1 к бэкенду, таймаут чтения не менее 1 часа | встречи рвутся через минуту |
| `Host` сохраняется, `X-Forwarded-For` добавляется, `X-Forwarded-Proto` передаётся как есть (`https`) | ошибки входа и CSRF, смешанное содержимое |
| Путь `/livekit/` передаётся без изменений | 404 на `/rtc/v1`, медленный вход |
| Для `/livekit/` и `/api/v1/ws`: без буферизации (`proxy_buffering off`, `proxy_request_buffering off`) и `tcp_nodelay on` | лишние десятки–сотни миллисекунд на сообщение |
| Медиа не проксируется: клиенты идут напрямую на `LIVEKIT_NODE_IP` по `LIVEKIT_TCP_PORT/tcp` и `LIVEKIT_UDP_PORT/udp` | страница открывается, но нет звука и видео |
| Прокси не требует входа для `/guest/*`, `/api/v1/guest/*`, `/api/v1/ws`, `/api/v1/meetings/*`, `/livekit/`, `/drawio/` | гость не сможет войти |

Пример для nginx: `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; proxy_read_timeout 3600s;`. Готовые `location` — в `deployment/nginx/*.tpl`. Apache — `mod_proxy_wstunnel`; Caddy и Traefik пропускают WebSocket автоматически; в Nginx Proxy Manager включите «Websockets Support».

## Проверить WebSocket

```bash
scripts/smoke-test.sh      # не запускалась; строки «LiveKit WebSocket /rtc»: код 101 — норма

curl -i -N --http1.1 -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
     -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' 'https://meet.example.org/livekit/rtc?access_token=ТОКЕН'    # не запускалась
```

Ожидаемый результат: `HTTP/1.1 101` (без токена — `401`). Маршрут `/rtc/v1` без параметров отвечает `400 join_request is required`: это норма; `404` — признак устаревшего LiveKit.

## Разрешить дополнительные адреса

Браузер присылает заголовок `Origin`. Сервер сверяет его со списком: `APP_PUBLIC_URL` плюс `ALLOWED_ORIGINS`. Страница, открытая по другому адресу (например, через IP или SSH-туннель), получает «Недопустимый Origin» (HTTP 403, WebSocket закрывается кодом 4403).

1. Найдите отклонённый адрес:

   ```bash
   ./scripts/logs.sh backend --tail 500 | grep "Origin отклонён"     # не запускалась
   ```

2. В `.env` задайте основной адрес и, при необходимости, дополнительные:

   ```bash
   APP_PUBLIC_URL=https://meet.example.org
   ALLOWED_ORIGINS=https://meet.example.org,https://192.0.2.10
   ```

3. Перезапустите backend: `./scripts/ctl.sh restart backend`.

Проверка Origin и CSRF остаются включёнными; список лишь расширяет разрешённые адреса. Подстановок `*` нет. Заголовки `X-Forwarded-Host` и `X-Forwarded-Proto` в проверке Origin не доверяются. Cookie привязаны к хосту: у каждого адреса вход свой; при `COOKIE_SECURE=true` каждый адрес должен открываться по HTTPS.

Адрес клиента определяется по `X-Forwarded-For` только от доверенных прокси: `TRUSTED_PROXY_CIDRS` (по умолчанию `127.0.0.0/8,::1/128`) и `TRUSTED_PROXY_HOPS` (по умолчанию 1; внешний прокси плюс nginx хоста — 2).

## Включить HSTS (необязательно)

HSTS в приложении и шаблонах **не включён намеренно**: если после включения сертификат окажется недействительным, пользователи не смогут обойти предупреждение. Включайте на узле, который завершает TLS и которым вы управляете, и только после проверки цепочки:

1. Проверьте цепочку: `openssl s_client -connect meet.example.org:443 -servername meet.example.org </dev/null | openssl x509 -noout -dates -issuer` (не запускалась).
2. Добавьте `add_header Strict-Transport-Security "max-age=300" always;` в блок `server` с `ssl`; повторите директиву в каждом `location`, где задаёте свои заголовки.
3. Повышайте срок поэтапно: 300 секунд → сутки → неделя → 15 552 000 (6 месяцев). `includeSubDomains` нужен, только если все поддомены отдают HTTPS. `preload` не используйте.
4. Откат: значение `max-age=0` на срок прежнего значения.

Полный чек-лист: `docs/HTTPS_AND_HSTS.md` в репозитории.

## Типичные ошибки

| Симптом | Причина |
| --- | --- |
| `ERR_CONNECTION_RESET` при входе в комнату через VPN | слишком большой MTU адаптера VPN (помогало `MTU=1250`); Windows: `netsh interface ipv4 set subinterface "<адаптер>" mtu=1250 store=persistent` |
| Звонок «подключается» бесконечно | закрыты `LIVEKIT_UDP_PORT/udp` и `LIVEKIT_TCP_PORT/tcp` или неверный `LIVEKIT_NODE_IP` |
| Ошибка WebSocket при открытой странице | устройство не доверяет сертификату, VPN-маршрут |
| «Недопустимый Origin» | адрес не в `APP_PUBLIC_URL` и `ALLOWED_ORIGINS` |

## Что дальше

- [[Справочник: порты и сеть|Справочник-порты-и-сеть]]
- [[Диагностика]]
- [[Безопасность и приватность|Безопасность-и-приватность]]
