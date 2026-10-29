# Справочник: публичный API

Базовый адрес: `https://<сервер>/api/public/v1`. Машиночитаемое описание OpenAPI 3.1: `/api/public/v1/openapi.json`. Справочная страница без ключа и без скриптов: `/api/public/v1/docs`. Версия одна. Управление — **Администрирование → Интеграции → Публичный API**; по умолчанию API выключен.

## Методы

| Метод и путь | Право | Назначение |
| --- | --- | --- |
| `GET /me` | любое | кто я: интеграция, права, область комнат, срок ключа |
| `GET /rooms`, `GET /rooms/{room_id}` | `rooms:read` | список и карточка комнат |
| `GET /meetings`, `GET /meetings/{meeting_id}` | `meetings:read` | список встреч (новые первыми) и карточка |
| `GET /meetings/{meeting_id}/participants` | `meetings:read` | участники: имя, должность, подразделение, без e-mail и телефона |
| `POST /meetings/{meeting_id}/end` | `meetings:end` | завершить идущую встречу для всех (в аудите — `api:<интеграция>`) |
| `GET /meetings/{meeting_id}/transcript` | `transcripts:read` | стенограмма: JSON с курсором или текст (`format=txt`, `md`, `vtt`, `srt`) |
| `GET /meetings/{meeting_id}/documents`, `GET …/documents/{document_id}` | `protocols:read`, `summaries:read` | протоколы, резюме, сохранённые стенограммы; JSON или `md`, `txt`, `html` |
| `GET /meetings/{meeting_id}/map` | `maps:read` | карта разговора |
| `GET /meetings/{meeting_id}/recordings` | `recordings:read` | сведения об аудиозаписях без скачивания |
| `GET /meetings/{meeting_id}/messages` | `messages:read` | сообщения чата встречи |
| `POST /meetings/{meeting_id}/documents` | `protocols:generate` или `summaries:generate` | поставить задачу формирования (`kind`: `protocol` или `summary`, необязательная `instruction`); `202 Accepted` |
| `POST /meetings/{meeting_id}/map` | `maps:generate` | поставить задачу построения карты; `202` |
| `GET /jobs`, `GET /jobs/{job_id}` | `jobs:read` | свои задачи и состояние |
| `POST /jobs/{job_id}/cancel` | `jobs:read` | отменить **ожидающую** задачу |
| `POST /meetings/{meeting_id}/recordings/{recording_id}/download-url` | `recordings:download` | короткоживущая ссылка на запись (5 минут) |
| `GET /downloads/{token}` | без ключа | скачать запись по ссылке |

Изменение и удаление встреч, протоколов и записей через API не предусмотрено. Подписки на события создаёт только администратор в интерфейсе.

## Права

| Право | Что разрешает |
| --- | --- |
| `rooms:read` | список и карточки комнат |
| `meetings:read` | встречи и участники |
| `meetings:end` | завершить встречу |
| `transcripts:read` | стенограмма |
| `protocols:read`, `summaries:read`, `maps:read` | чтение документов |
| `recordings:read` | сведения о записях |
| `messages:read` | чат встречи |
| `protocols:generate`, `summaries:generate`, `maps:generate` | запуск формирования (нагружает языковую модель) |
| `jobs:read` | состояние и отмена **своих** задач |
| `recordings:download` | ссылки на скачивание записей |

Выдавайте минимум. Чужая комната и любой вложенный объект отвечают `404 not_found`, как несуществующие.

## Идентификаторы

`rom_…` комната, `mtg_…` встреча, `pro_…` документ, `map_…` карта, `rec_…` запись, `usr_…` и `gst_…` участник, `msg_…` сообщение, `job_…` задача, `evt_…` событие. Идентификатор другого типа или «голый» UUID не принимается (404).

## Ошибки

Форма всегда `{"error": {"code": …, "message": …, "request_id": …}}`; в каждом ответе есть заголовок `X-Request-ID`.

| HTTP | `code` | Когда |
| --- | --- | --- |
| 400 | `invalid_cursor` | курсор повреждён или от другой выборки |
| 400 | `invalid_idempotency_key` | `Idempotency-Key`: 1–64 символа `A-Za-z0-9_.:-` |
| 401 | `unauthorized`, `invalid_api_key` | нет ключа или ключ неверен |
| 401 | `key_revoked`, `key_expired` | ключ отозван или истёк (сообщается только при верном секрете) |
| 403 | `client_disabled`, `ip_not_allowed`, `insufficient_scope` | интеграция отключена, адрес не разрешён, не хватает права |
| 404 | `not_found` | нет объекта или он вне области комнат |
| 409 | `not_ready` | документ ещё формируется |
| 409 | `meeting_not_ended`, `llm_not_configured`, `anonymizer_not_configured`, `no_materials` | задачу поставить нельзя |
| 409 | `idempotency_conflict`, `idempotency_in_progress` | тот же ключ для другого запроса или первый запрос ещё идёт |
| 409 | `not_cancellable` | отменить можно только ожидающую задачу |
| 410 | `link_expired`, `file_missing` | срок ссылки истёк или файл удалён из хранилища |
| 413 | `too_large` | стенограмма слишком велика для текста (более 20 000 реплик) |
| 422 | `validation_error` | неверные параметры (`error.fields`) |
| 429 | `rate_limited`, `too_many_jobs` | лимит или пять незавершённых задач |
| 503 | `api_disabled` | API выключен |
| 500 | `internal_error` | сообщите администратору `request_id` |

## Лимиты и постраничный вывод

- Окно — одна минута, отдельно на каждую интеграцию и класс запросов (`read`, `write`, `ai`, `download`); значения задаёт администратор. Заголовки: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`; при `429` — `Retry-After`.
- `limit` (по умолчанию 50, максимум задаёт администратор) и `after` (курсор). Курсор привязан к позиции в выборке, поэтому новые встречи не дают повторов и пропусков.

## Фоновые задачи

Состояния: `queued → processing → completed | failed | cancelled`. Очередь: по умолчанию одна одновременная задача (языковая модель не должна мешать звонкам); не более 5 незавершённых на интеграцию. Условия проверяются при постановке.

## Идемпотентность

`Idempotency-Key` (1–64 символа) действует в пределах интеграции 24 часа (настройка). Повтор того же запроса возвращает прежний ответ с заголовком `Idempotent-Replay: true`; вторая задача не создаётся. Тот же ключ с другим телом — `409 idempotency_conflict`.

## События (webhooks)

События: `meeting.started`, `meeting.ended`, `document.ready`, `document.failed`, `job.completed`, `job.failed`, `webhook.test`. В событии только идентификаторы.

| Заголовок | Значение |
| --- | --- |
| `X-Peregovorka-Event`, `X-Peregovorka-Event-Id`, `X-Peregovorka-Delivery` | тип, идентификатор события, идентификатор доставки |
| `X-Peregovorka-Timestamp` | секунды Unix |
| `X-Peregovorka-Signature` | `v1=<hex>` — HMAC-SHA256 от `"<timestamp>.<тело>"`; при замене секрета — две подписи через запятую |
| `X-Peregovorka-Attempt` | номер попытки |

Ответ `2xx` — принято; редиректы не выполняются. Повторы: задержка около 10 секунд, 20, 40 … до часа (разброс ±20 %), до 8 попыток. После 5 неудач подряд получатель «деградирует», после 20 — «отключён»; ответ `410 Gone` отключает сразу. Замена секрета без простоя: 24 часа действуют два секрета.

**Защита адресов (SSRF):** только `https`; без логина, пароля и фрагмента; все IP имени проверяются при сохранении и при каждой отправке; редиректы и системные прокси отключены; loopback, частные сети, CGNAT, link-local, multicast и метаданные облака запрещены (для on-prem есть явный список разрешённых узлов).

## Безопасность

Ключ хранится только как SHA-256; сравнение за постоянное время; журнал обращений хранит метод, шаблон пути, статус, время, адрес и `request_id` (без заголовков и тел); CORS не включён; ответы `Cache-Control: no-store`. Подробности — [Безопасность и приватность](Безопасность-и-приватность).

## Что дальше

- [Как использовать публичный API](Как-использовать-публичный-API)
- [Безопасность и приватность](Безопасность-и-приватность)
- [Справочник: журнал аудита и события](Справочник-журнал-аудита-и-события)
