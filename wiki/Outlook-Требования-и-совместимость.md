# Требования и совместимость Outlook и Exchange

*Для кого: администратор Peregovorka и администратор Exchange.*

## Зачем это нужно

Статья помогает до внедрения понять, подходит ли ваша почтовая система, и отделяет расчёт от проверки.

> **Главное.** На настоящем сервере Exchange, в Microsoft 365 и в настоящем Outlook интеграция не проверялась. Ни одну версию нельзя назвать подтверждённой. Таблицы ниже показывают, на что интеграция рассчитана по коду и документации Microsoft.

## Что нужно

| Требование | Зачем |
| --- | --- |
| Публичный адрес Peregovorka по HTTPS | Outlook не загружает надстройки по HTTP |
| Сертификат, которому доверяют рабочие станции | самоподписанный сертификат Outlook и Exchange не примут |
| Манифест отдаётся напрямую, без перенаправлений | Exchange 2019 за перенаправлениями не идёт |
| Доступ компьютеров с Outlook к адресу сервера | страницы, Office.js и программный интерфейс лежат на вашем сервере |
| Доступ компьютеров к CDN Microsoft | только если в образе нет локальной копии Office.js или выбран режим CDN |
| Адрес почты в каталоге | по нему определяется ящик сотрудника |
| Доступ сервера к Exchange по HTTPS | для сверки и календаря |
| Права на календари | [Как подключить календарь Exchange](Как-подключить-календарь-Exchange) |
| Учётная запись Peregovorka у организатора | единого входа нет |

## Совместимость

Основа таблицы: матрица из раздела администрирования и [таблица наборов Mailbox API Microsoft](https://learn.microsoft.com/en-us/javascript/api/requirement-sets/outlook/outlook-api-requirement-sets). Набор Mailbox API определяет, какие вызовы доступны надстройке.

| Среда | Набор Mailbox API | Базовые функции | Передача при отправке | Сверка | Проверено на настоящей системе |
| --- | --- | --- | --- | --- | --- |
| Exchange 2016, классический Outlook для Windows | сервер до 1.5 | рассчитано | нет | EWS | нет |
| Exchange 2019, классический Outlook для Windows | сервер до 1.5 | рассчитано, основной сценарий | нет | EWS | нет |
| Exchange Subscription Edition, классический Outlook | сервер до 1.5 | рассчитано | нет | EWS | нет |
| Exchange 2016, 2019, SE, Outlook в браузере | сервер до 1.5 | рассчитано | нет | EWS | нет |
| Локальный Exchange, новый Outlook | по клиенту и серверу | зависит от клиента и политики | нет | EWS | нет |
| Microsoft 365, Outlook для Windows, Mac и в браузере | 1.12 и выше | рассчитано | рассчитано | Graph | нет |
| Мобильный Outlook | — | не поддержан манифестом | нет | — | нет |
| Exchange 2013 | — | не заявлен | нет | — | нет |

Страница `/outlook` и раздел администрирования показывают для Subscription Edition пометку «уточняйте у Microsoft». На сентябрь 2026 года таблица Microsoft указывает для него наборы до 1.5.

**Базовые функции:** создать конференцию из окна встречи, добавить ссылку в приглашение, открыть конференцию, войти, получить сведения о связанной конференции. Манифест требует Mailbox 1.3.

**Передача при отправке** требует Mailbox 1.12. На локальном Exchange настройка не применяется, манифест остаётся на 1.3.

Функции выше набора сервера зависят от клиента. Из-за этого сверка и серии из надстройки на локальном Exchange могут не работать: [Ограничение надстройки на локальном Exchange](Outlook-Синхронизация-и-повторяющиеся-встречи#ограничение-надстройки-на-локальном-exchange).

## Особенности клиентов

**Классический Outlook 2016 и 2019.** Такие сборки показывают надстройку во встроенном Internet Explorer 11. Страницы рассчитаны на это: есть заголовок совместимости и замены отсутствующих функций браузера. Проверено условие работы, а не сам движок.

Окно дополнительного шага входа открывает основное приложение, а его IE 11 не поддерживает. Учётные записи с кодом подтверждения, проверкой «не робот» или обязательной сменой пароля в таком клиенте войти не смогут. Вход без этих шагов идёт прямо в панели.

**Outlook в браузере на локальном Exchange.** При первом запуске браузер спрашивает, доверять ли домену Outlook: домен вашего сервера Exchange библиотека Office.js заранее не знает.

**Microsoft 365.** Выбирайте Graph. Матрица продукта допускает и EWS, но Microsoft [отключает EWS в Exchange Online](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/deprecation-of-ews-exchange-online): отключение начинается в октябре 2026 года, полное завершение запланировано на 2027 год. На локальный Exchange это не распространяется.

Сроки поддержки самих версий Exchange и Outlook смотрите у Microsoft.

## Требования к сверке и календарю

| | EWS | Microsoft Graph |
| --- | --- | --- |
| Где применяется | локальный Exchange | Microsoft 365 |
| Вход | Basic, NTLM, Negotiate, Kerberos | приложение Entra ID, секрет приложения |
| Чтение | `ApplicationImpersonation` или «Рецензент» | `Calendars.ReadBasic.All` |
| Запись | только `ApplicationImpersonation` | `Calendars.ReadWrite` |
| Адрес | HTTPS, один адрес или балансировщик | глобальные адреса Microsoft, национальные облака не поддержаны |
| Версия запросов EWS | заголовок `Exchange2013` | — |

## Что проверено, а что нет

| Что | Как проверено |
| --- | --- |
| Создание, перенос, отмена, вход, ошибки панели | автоматические тесты и настоящий браузер, настоящий сервер Peregovorka, заменённая библиотека Outlook |
| Манифест | официальный валидатор Microsoft |
| Сценарии PowerShell | разобраны парсером PowerShell, `Test-OutlookAddin.ps1` выполнен вне Exchange |
| Загрузка Office.js | настоящий браузер, настоящий образ web, заменённый узел Outlook |
| Запросы Graph и EWS | подставные ответы по документированным форматам |
| Вход NTLM в EWS | обмен с серверной частью библиотеки, а не с Exchange |
| Вход Kerberos | попытка получить билет без KDC, настоящий обмен не проверялся |
| Проверка сертификатов | настоящий TLS-сервер в тестах, цепочки смоделированы |
| SMTP с NTLM | подставной сервер с настоящим STARTTLS |

**Не проверялось:**

- установка надстройки и `Install-OutlookAddin.ps1` на настоящем Exchange;
- панель, окно входа и сохранение входа в настоящем Outlook;
- рассылка приглашений и отмен настоящим Exchange;
- права имперсонации, политики Graph, настоящий KDC и балансировщик;
- серии с исключениями и часовые пояса на настоящем сервере.

Отдельные ошибки поступали из среды с Exchange 2019: «Ошибка надстройки» в классическом Outlook, «Ошибка настройки» в Outlook в браузере, отказ входа SMTP на сервере с одним NTLM. По ним внесены исправления. Сообщение об ошибке не является проверкой: подтверждения, что после исправлений сценарий прошёл там целиком, нет.

## Официальные материалы Microsoft

- [Наборы требований Mailbox API](https://learn.microsoft.com/en-us/javascript/api/requirement-sets/outlook/outlook-api-requirement-sets)
- [Обзор надстроек Outlook](https://learn.microsoft.com/en-us/office/dev/add-ins/outlook/outlook-add-ins-overview)
- [Надстройки Outlook в Exchange Online](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/add-ins-for-outlook/add-ins-for-outlook)
- [Управление доступом пользователей к надстройкам](https://learn.microsoft.com/en-us/exchange/clients-and-mobile-in-exchange-online/add-ins-for-outlook/manage-user-access-to-add-ins)
- [Централизованное развёртывание в Microsoft 365](https://learn.microsoft.com/en-us/microsoft-365/admin/manage/manage-deployment-of-add-ins)
- [Командлеты New-App](https://learn.microsoft.com/en-us/powershell/module/exchange/new-app) и [Get-App](https://learn.microsoft.com/en-us/powershell/module/exchange/get-app): раздел «Applies to» показывает версии
- [Установка надстроек для Exchange 2013](https://learn.microsoft.com/ru-ru/exchange/install-or-remove-outlook-add-ins-exchange-2013-help#install-an-add-in-for-outlook). Это инструкция только для Exchange 2013, для новых версий проверяйте справку своей версии
- [Имперсонация и EWS](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/impersonation-and-ews-in-exchange) и [календари и EWS](https://learn.microsoft.com/en-us/exchange/client-developer/exchange-web-services/calendars-and-ews-in-exchange)
- [Ограничение доступа приложения к ящикам](https://learn.microsoft.com/en-us/graph/auth-limit-mailbox-access)
- [Номера сборок Exchange](https://learn.microsoft.com/en-us/exchange/new-features/build-numbers-and-release-dates)

## Что дальше

- [Как подключить надстройку Outlook](Как-подключить-надстройку-Outlook)
- [Как подключить календарь Exchange](Как-подключить-календарь-Exchange)
- [Централизованная установка надстройки](Outlook-Централизованная-установка-надстройки)
- [Что проверено](Границы-проверки)
