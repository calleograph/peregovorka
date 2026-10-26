# Требования и совместимость Outlook и Exchange

> Относится к версии 0.41.0 · проверено 2026-10-26 · уровень проверки: 3 на подставных Graph и EWS и в браузере; **настоящие Exchange, Microsoft 365 и Outlook не проверялись** · тип страницы: справочник

Результат: вы проверили, подходит ли ваша почтовая система, и знаете, какие официальные материалы Microsoft читать. Для кого: администратор.

## Что нужно

| Требование | Зачем |
| --- | --- |
| Публичный адрес Peregovorka **по HTTPS** с сертификатом, которому доверяют рабочие станции | Outlook не загружает надстройки по HTTP; самоподписанный сертификат не подойдёт без установки доверия |
| Доступ клиентов Outlook к адресу сервера | страницы надстройки, Office.js и API работают с вашего же сервера |
| Адрес почты (`mail`) у сотрудника в каталоге | по нему определяется ящик для календаря |
| Для календаря: серверная сверка (Graph или EWS) и права на ящики | см. [[Настройка доступа к календарям|Как-подключить-календарь-Exchange]] |

## Совместимость

| Почтовая система | Надстройка | Календарь | Примечание |
| --- | --- | --- | --- |
| Exchange Server 2016 / 2019 | Mailbox API до **1.5** (разрешено манифестом 1.3); нужна работа добавления в приглашение | EWS, имперсонация (ApplicationImpersonation) для записи; «Рецензент» — только чтение | передача изменений при отправке (Mailbox 1.12) недоступна, вместо неё серверная сверка |
| Exchange Server Subscription Edition | как 2019; уточните набор требований в документации Microsoft | EWS | не проверялось |
| Microsoft 365 / Exchange Online | Mailbox 1.12 и выше | Microsoft Graph, право приложения `Calendars.ReadBasic.All` (чтение) и `Calendars.ReadWrite` (запись), ограничение политикой Application Access Policy | — |
| Exchange Server 2013 | **не поддерживается** | — | на странице Microsoft об установке надстроек для 2013 описан только общий принцип |

Классический Outlook 2016/2019 для Windows показывает надстройку во встроенном Internet Explorer 11; Peregovorka написана с учётом этого. Outlook в браузере (OWA) на локальном Exchange требует подтверждения доверия к домену Outlook при первом запуске панели ([[Диагностика типичных ошибок|Outlook-Диагностика-типичных-ошибок]]).

## Официальные материалы Microsoft

- [Установка и удаление надстроек Outlook для организации — справка по **Exchange 2013**](https://learn.microsoft.com/ru-ru/exchange/install-or-remove-outlook-add-ins-exchange-2013-help#install-an-add-in-for-outlook) (русский язык). **Это инструкция для Exchange Server 2013, а не универсальное руководство для всех выпусков Exchange**; для Exchange Server 2016 и 2019 принцип (Центр администрирования Exchange → «Организация» → «Надстройки», либо `New-App`) аналогичен, но названия и шаги проверяйте по справке своей версии.
- [Надстройки для Outlook в Exchange Online](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/add-ins-for-outlook/add-ins-for-outlook) — Microsoft 365 (английский язык).
- [Управление доступом пользователей к надстройкам (Exchange Online)](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/add-ins-for-outlook/manage-user-access-to-add-ins).
- [Централизованное развёртывание надстроек в Microsoft 365](https://learn.microsoft.com/en-us/microsoft-365/admin/manage/manage-deployment-of-add-ins).
- [Командлет New-App](https://learn.microsoft.com/en-us/powershell/module/exchange/new-app) и [Get-App](https://learn.microsoft.com/en-us/powershell/module/exchange/get-app): раздел «Applies to» страницы показывает, для каких версий он доступен.
- [Обзор надстроек Outlook](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/outlook-add-ins-overview) и [наборы требований Mailbox API](https://learn.microsoft.com/en-us/office/dev/add-ins/reference/requirement-sets/outlook-api-requirement-sets) — какая версия Exchange поддерживает какой набор.
- [Имперсонация и EWS](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/impersonation-and-ews-in-exchange) и [календари и EWS](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/calendars-and-ews-in-exchange) — локальный Exchange.
- [Ограничение доступа приложения к ящикам (Application Access Policy / RBAC для приложений)](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access) и [calendarView в Graph](https://learn.microsoft.com/en-us/graph/api/user-list-calendarview) — Microsoft 365.
- [Номера сборок и даты выпуска Exchange](https://learn.microsoft.com/en-us/exchange/new-features/build-numbers-and-release-dates) — как узнать свою сборку.

Команды из руководства для Exchange 2013 **не переносите** на новые версии без проверки в справке Microsoft для вашей версии: поведение командлетов и название разделов консоли отличается.

## Что дальше

- [[Установка надстройки Outlook|Как-подключить-надстройку-Outlook]]
- [[Централизованная установка надстройки администратором Exchange|Outlook-Централизованная-установка-надстройки]]
- [[Диагностика типичных ошибок|Outlook-Диагностика-типичных-ошибок]]
