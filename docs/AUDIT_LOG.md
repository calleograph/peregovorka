# Журнал аудита

«Администрирование → Безопасность → Журнал аудита». Отвечает на вопрос «кто, что, когда и откуда сделал». Хранится отдельно от технического «Журнала событий» (тот — для диагностики, этот — для расследований); пароли, токены, ключи и содержимое документов в аудит не попадают.

## Что записывается

| Область | События (`action`) |
| --- | --- |
| Безопасность | `security.ip_access.settings`, `security.ip_access.confirm`, `security.ip_access.auto_revert`, `security.ip_access.console_reset`, `security.ip_access.entry_add/entry_update/entry_delete`, `security.ip.block`, `security.ip.unblock`, `security.waf.update`, `security.events.export` (события самого защитного слоя — в разделе «События и блокировки») |
| Вход | `auth.login`, `auth.login_failed`, `auth.login_denied` (пароль верен, но правила допуска не пустили), `auth.login_locked`, `auth.logout`, `auth.totp_failed`, `auth.backup_code_used`, `captcha.rejected` |
| 2FA | `auth.totp_enable`, `auth.totp_disable`, `auth.totp_backup_regenerate`, `auth.totp_reset` |
| Комнаты и встречи | `meeting.join`, `meeting.join_denied`, `meeting.leave` (пользователи и гости), создание, изменение и удаление комнат, завершение встречи, выдача и отзыв доступа |
| Материалы | `protocol.export`, `transcript.download`, `chat_file.download`, `board.download`, скачивание аудио, удаление протоколов и записей |
| Пользователи и каталог | `user.activate`/`user.deactivate`, `ldap.create`/`update`/`delete`/`legacy_import`, `ca.add`/`ca.delete` |
| Настройки | `settings.<группа>.update` (секреты в записях заменены), `config.export`, `config.import` |
| Резервные копии | `backup.create`, `backup.verify`, `backup.download`, `backup.key_create`, `backup.settings` |
| Сам аудит | `audit.export`, `audit.verify` |

## Поля записи

`timestamp` (UTC), `event_type`, `actor_id`, `actor_type` (user, guest, system, service), `actor_name`, `source_ip`, `user_agent`, `session_id` (необратимый отпечаток идентификатора сеанса: по нему связывают записи одного сеанса), `object_type`, `object_id`, `result` (success, denied, failure), `request_id` (связь с техническим журналом), `details`. Время в интерфейсе показывается в часовом поясе браузера, в базе и выгрузках — UTC.

## Фильтры, поиск, выгрузка

Фильтры: исполнитель, действие, результат, тип исполнителя и объекта, IP, период (с/по), произвольный текст. Выгрузка в CSV (с BOM для Excel, ячейки, начинающиеся с `= + - @`, экранируются) или JSON по текущим фильтрам, не более 100 000 записей. Сама выгрузка записывается в аудит.

## Целостность

Каждая запись подписана HMAC-SHA256 (ключ — производный от `APP_MASTER_KEY`). Кнопка «Проверить целостность» (`GET /admin/audit/verify`) находит **изменённые записи** и **пропуски в нумерации** (удалённые из середины журнала). Что она **не** видит: удаление самых свежих записей и подмену одновременно ключа и базы. Записи, созданные до появления подписи (версии до 0.20), считаются отдельно и не проверяются.

## Срок хранения

«Хранение и журналы → Хранение журнала → Хранить журнал аудита». `0` (по умолчанию) — не удалять никогда; иначе записи старше срока удаляются раз в час вместе с очисткой журнала событий. Удаляется только начало журнала, поэтому проверка целостности остаётся чистой.

## Права

Просмотр, выгрузка и проверка — только администраторы (проверка на сервере). Скачивание резервных копий из хранилища требует ещё и повторного ввода пароля.
