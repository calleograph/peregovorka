import type { Field } from "./SettingsForm";

/**
 * Описания полей настроек: полное название, что поле делает и пример заполнения (примеры — условные, не относятся к реальному
 * развёртыванию). Поля сгруппированы по смыслу (section), чтобы при заполнении не путаться.
 */

const isSmb = (v: Record<string, unknown>) => v.mode === "smb";
const isLocal = (v: Record<string, unknown>) => v.mode === "local";

/** Общие поля «куда писать»: локальный каталог или сетевой ресурс SMB. Используется и для протоколов, и для записей. */
const targetFields = (kind: "protocols" | "audio"): Field[] => [
  { section: "Где хранить", name: "mode", label: "Тип хранилища", type: "select", options: [["local", "Локальный каталог на сервере"], ["smb", "Сетевой ресурс SMB (общая папка Windows/NAS)"]],
    help: kind === "audio"
      ? "Записи — большие файлы. Локальный каталог — это том приложения внутри сервера; для длительного хранения используйте SMB-ресурс."
      : "Куда складываются готовые документы встреч. Приложение подключается к SMB само, монтировать ресурс в систему не нужно." },
  { section: "Где хранить", name: "local_path", label: "Каталог внутри контейнера", type: "text", showIf: isLocal,
    placeholder: kind === "audio" ? "/data/exports/audio" : "/data/exports",
    help: "Абсолютный путь внутри контейнера приложения (он лежит в разделе DATA_ROOT на сервере). Не путь Windows и не адрес сети.",
    example: kind === "audio" ? "/data/exports/audio" : "/data/exports" },
  { section: "Сетевой ресурс SMB", name: "smb_server", label: "Сервер (имя или IP)", type: "text", showIf: isSmb, placeholder: "files.corp.local",
    help: "Только имя хоста или IP — без схемы (smb://) и без слэшей.", example: "files.corp.local или 192.0.2.20" },
  { section: "Сетевой ресурс SMB", name: "smb_share", label: "Общий ресурс (имя шары)", type: "text", showIf: isSmb, placeholder: kind === "audio" ? "meetings-audio" : "meetings",
    help: "Имя общей папки на сервере, без слэшей.", example: kind === "audio" ? "meetings-audio" : "meetings" },
  { section: "Сетевой ресурс SMB", name: "smb_base_path", label: "Подкаталог на ресурсе", type: "text", showIf: isSmb, placeholder: kind === "audio" ? "peregovorka/audio" : "peregovorka",
    help: "Необязательно. Внутри него создаётся структура «комната / дата и день недели / время начала». Без «..».", example: kind === "audio" ? "peregovorka/audio" : "peregovorka/protocols" },
  { section: "Сетевой ресурс SMB", name: "smb_domain", label: "Домен учётной записи", type: "text", showIf: isSmb, placeholder: "CORP", help: "Необязательно (NetBIOS-имя домена).", example: "CORP" },
  { section: "Сетевой ресурс SMB", name: "smb_username", label: "Учётная запись с правом записи", type: "text", showIf: isSmb, placeholder: "svc-peregovorka",
    help: "Сервисная учётная запись, у которой есть право создавать и записывать файлы на ресурсе.", example: "svc-peregovorka" },
  { section: "Сетевой ресурс SMB", name: "smb_password", label: "Пароль учётной записи", type: "secret", showIf: isSmb,
    help: "Хранится в зашифрованном виде и после сохранения не показывается. Пустое поле — оставить прежний пароль." },
];

export const storageFields: Field[] = [
  { section: "Выгрузка протоколов", name: "enabled", label: "Выгружать протоколы во внешнее хранилище", type: "bool",
    help: "Когда включено, после завершения встречи (и после формирования документа) файлы кладутся в хранилище по структуре «комната / дата, день недели / время начала»." },
  ...targetFields("protocols"),
  { section: "Что выгружать", name: "export_transcript", label: "Выгружать стенограмму (protocol.txt)", type: "bool", help: "Полный текст реплик с временем и именами участников." },
  { section: "Что выгружать", name: "export_summary", label: "Выгружать готовые протоколы и резюме (official-protocol.md, summary.md)", type: "bool",
    help: "Документы, сформированные моделью автоматически или участниками по кнопке «Сформировать протокол»." },
];

export const audioStorageFields: Field[] = [
  { section: "Выгрузка записей", name: "enabled", label: "Выгружать аудиозаписи во внешнее хранилище", type: "bool",
    help: "Если выключено, записи остаются на локальном томе приложения. Недоступность хранилища запись не теряет: файл остаётся локально, статус «ошибка выгрузки» виден администратору, выгрузка повторяется автоматически." },
  ...targetFields("audio"),
  { section: "Локальная копия", name: "keep_local_copy", label: "Оставлять локальную копию после успешной выгрузки", type: "bool",
    help: "Включено — файл остаётся и на сервере (быстрее скачивать, но занимает диск; удалится по сроку хранения). Выключено — после выгрузки локальный файл удаляется." },
];

export const anonFields: Field[] = [
  { section: "Сервис обезличивания", name: "enabled", label: "Обезличивание включено", type: "bool",
    help: "Без него документы не формируются: в языковую модель уходит только обезличенный текст (если сервис недоступен — отправки нет вообще)." },
  { section: "Сервис обезличивания", name: "profile", label: "Тип API", type: "select", options: [["docclean", "DocClean (api_text)"], ["generic", "Произвольный JSON API"]] },
  { section: "Сервис обезличивания", name: "base_url", label: "Адрес сервиса (Base URL)", type: "text", placeholder: "https://anon.corp.local",
    help: "Только https (http — лишь при явном разрешении ниже).", example: "https://anon.corp.local:8443" },
  { section: "Сервис обезличивания", name: "token", label: "Токен / ключ API", type: "secret", help: "Хранится зашифрованно. Пустое поле — оставить прежний." },
  { section: "Параметры DocClean", name: "docclean_mode", label: "Режим DocClean", type: "select", showIf: (v) => v.profile === "docclean",
    options: [["ai_ready", "ai_ready — подготовка текста для ИИ (рекомендуется)"], ["full", "full — максимальное обезличивание"], ["personal_corporate", "personal_corporate — персональные и корпоративные данные"], ["personal", "personal — только персональные данные"]] },
  { section: "Параметры DocClean", name: "docclean_groups", label: "Группы данных для обезличивания", type: "text", showIf: (v) => v.profile === "docclean", placeholder: "pdn, corporate, secrets",
    help: "Через запятую из: pdn (персональные данные), corporate (корпоративные), secrets (секреты), network (сетевые), other. Пусто — по умолчанию сервиса.", example: "pdn, corporate, secrets" },
  { section: "Параметры произвольного API", name: "endpoint", label: "Путь метода", type: "text", showIf: (v) => v.profile === "generic", placeholder: "/anonymize", example: "/api/v1/anonymize" },
  { section: "Параметры произвольного API", name: "request_field", label: "Поле запроса (JSON-путь)", type: "text", showIf: (v) => v.profile === "generic", placeholder: "text", help: "В какое поле тела запроса кладётся текст.", example: "text или payload.text" },
  { section: "Параметры произвольного API", name: "response_field", label: "Поле ответа (JSON-путь)", type: "text", showIf: (v) => v.profile === "generic", placeholder: "anonymized_text", help: "Откуда брать обезличенный текст в ответе.", example: "anonymized_text или result.text" },
  { section: "Параметры произвольного API", name: "status_field", label: "Поле статуса (необязательно)", type: "text", showIf: (v) => v.profile === "generic", example: "status" },
  { section: "Параметры произвольного API", name: "status_ok_value", label: "Значение «успех» поля статуса", type: "text", showIf: (v) => v.profile === "generic", example: "ok" },
  { section: "Параметры произвольного API", name: "auth_type", label: "Способ авторизации", type: "select", showIf: (v) => v.profile === "generic", options: [["bearer", "Bearer-токен"], ["header", "Свой заголовок"], ["basic", "Basic (логин/пароль)"], ["none", "Без авторизации"]] },
  { section: "Параметры произвольного API", name: "auth_header_name", label: "Имя заголовка", type: "text", showIf: (v) => v.profile === "generic" && v.auth_type === "header", placeholder: "X-API-Key", example: "X-API-Key" },
  { section: "Параметры произвольного API", name: "auth_username", label: "Пользователь (для Basic)", type: "text", showIf: (v) => v.profile === "generic" && v.auth_type === "basic" },
  { section: "Параметры произвольного API", name: "extra_body", label: "Дополнительные поля тела запроса (JSON-объект)", type: "textarea", showIf: (v) => v.profile === "generic", placeholder: '{"mode": "full"}', help: "Необязательно. Должен быть JSON-объектом.", example: '{"language": "ru"}' },
  { section: "Соединение", name: "connect_timeout", label: "Таймаут соединения", unit: "с", type: "number", min: 1, max: 60, help: "Сколько ждать установки соединения.", example: "5" },
  { section: "Соединение", name: "timeout", label: "Таймаут ответа", unit: "с", type: "number", min: 1, max: 600, help: "Сколько ждать обработки одного фрагмента.", example: "60" },
  { section: "Соединение", name: "max_chunk_chars", label: "Размер фрагмента", unit: "символов", type: "number", min: 500, max: 1000000, help: "Длинная стенограмма отправляется на обезличивание частями не больше этого размера.", example: "20000" },
  { section: "Безопасность соединения", name: "use_corporate_ca", label: "Проверять сертификат по корпоративному удостоверяющему центру (LDAP_CA_FILE)", type: "bool", help: "Включайте, если сервис использует внутренний сертификат." },
  { section: "Безопасность соединения", name: "allow_http", label: "Разрешить небезопасный http://", type: "bool", help: "Только для изолированных тестов: данные пойдут без шифрования." },
];

export const llmFields: Field[] = [
  { section: "Модель", name: "enabled", label: "Языковая модель (LLM) включена", type: "bool", help: "Формирует протоколы и резюме по обезличенной стенограмме." },
  { section: "Модель", name: "type", label: "Тип API", type: "select", options: [["openai_compatible", "OpenAI-совместимый (шлюзы, локальные сервера)"], ["openai", "OpenAI"], ["anthropic", "Anthropic"]] },
  { section: "Модель", name: "base_url", label: "Адрес API (Base URL)", type: "text", placeholder: "https://llm.corp.local/v1",
    help: "Для OpenAI и Anthropic можно оставить пустым. Для совместимых API обязателен.", example: "https://llm.corp.local/v1 или https://api.polza.ai/api/v1" },
  { section: "Модель", name: "model", label: "Идентификатор модели", type: "text", placeholder: "gpt-4o-mini", help: "Название модели у выбранного провайдера.", example: "gpt-4o-mini, claude-sonnet-5-5" },
  { section: "Модель", name: "api_key", label: "Ключ API", type: "secret", help: "Хранится зашифрованно. Пустое поле — оставить прежний." },
  { section: "Модель", name: "routing_provider", label: "Фиксировать провайдера маршрута (для шлюзов)", type: "text", showIf: (v) => v.type === "openai_compatible", help: "Необязательно: имя провайдера, через которого шлюз должен направлять запросы.", example: "azure" },
  { section: "Параметры генерации", name: "max_tokens", label: "Максимальная длина ответа", unit: "токенов", type: "number", min: 64, max: 64000, help: "Ограничивает размер протокола.", example: "4000" },
  { section: "Параметры генерации", name: "temperature", label: "Температура (креативность)", type: "number", min: 0, max: 2, step: 0.1, help: "Для протоколов лучше низкая: меньше выдумок.", example: "0.2" },
  { section: "Параметры генерации", name: "timeout", label: "Таймаут ответа", unit: "с", type: "number", min: 5, max: 1800, help: "Длинная встреча обрабатывается дольше.", example: "180" },
  { section: "Безопасность соединения", name: "use_corporate_ca", label: "Проверять сертификат по корпоративному удостоверяющему центру", type: "bool" },
  { section: "Безопасность соединения", name: "allow_http", label: "Разрешить небезопасный http://", type: "bool", help: "Только для изолированных тестов." },
];

export const protocolFields: Field[] = [
  { section: "Инструкции по умолчанию", name: "instructions", label: "Инструкция для полного протокола (по умолчанию)", type: "textarea", rows: 5,
    help: "Показывается пользователю в окне «Сформировать протокол» — он может её изменить. Дополнения конкретной переговорки добавляются к ней.",
    example: "Сформировать официальный протокол совещания. Выделить тему, участников, вопросы, решения, поручения, ответственных и сроки. Не придумывать отсутствующие сведения." },
  { section: "Инструкции по умолчанию", name: "summary_instructions", label: "Инструкция для краткого резюме (по умолчанию)", type: "textarea", rows: 4,
    example: "Кратко (не более 10 строк) изложить суть встречи: о чём говорили, решения и поручения." },
  { section: "Автоматическое формирование", name: "auto_generate", label: "Формировать полный протокол автоматически после завершения встречи", type: "bool", help: "Берётся инструкция по умолчанию. Участники всё равно могут сформировать свой вариант." },
  { section: "Автоматическое формирование", name: "auto_summary", label: "Формировать краткое резюме автоматически после завершения встречи", type: "bool" },
  { section: "Ограничения", name: "max_input_chars", label: "Максимум стенограммы за один запрос к модели", unit: "символов", type: "number", min: 2000, max: 1000000, help: "Длиннее — стенограмма обрабатывается по частям.", example: "60000" },
];

export const screenFields: Field[] = [
  { section: "Качество показа экрана", name: "profile", label: "Профиль показа экрана", type: "select",
    options: [["sharp", "Чёткость — текст, слайды, код (1080p, 15 к/с)"], ["balanced", "Сбалансированный (1080p, 20 к/с)"], ["motion", "Плавность — видео, анимация (1080p, 30 к/с)"]],
    help: "Один клик у участника — «Показать экран». Профиль действует во всех комнатах, где показ разрешён. Если у зрителей экран «ступенчатый» — выберите «Чёткость»; если рвётся видео — «Плавность»." },
  { section: "Поведение", name: "share_audio", label: "Разрешить передавать звук вкладки/системы вместе с экраном", type: "bool", help: "Участник сможет отметить «со звуком». Звук экрана в транскрибацию не попадает." },
  { section: "Поведение", name: "one_sharer_at_a_time", label: "Только один показывающий одновременно", type: "bool", help: "Пока кто-то показывает экран, остальным кнопка недоступна." },
];

export const generalFields: Field[] = [
  { section: "Время", name: "timezone", label: "Часовой пояс", type: "text", placeholder: "Europe/Moscow", help: "Название IANA. Используется в именах папок хранилища и подписях времени документов.", example: "Europe/Moscow" },
  { section: "Доступ к завершённым встречам", name: "post_meeting_access_minutes", label: "Сколько минут участник сохраняет доступ после завершения встречи", unit: "минут", type: "number", min: 1, max: 1440,
    help: "Участник, оставшийся на странице завершённой встречи, может формировать протоколы; после ухода со страницы доступ закрывается. Это ограничение по времени на случай, если страница остаётся открытой.", example: "120" },
  { section: "Сроки хранения по умолчанию", name: "default_text_retention_days", label: "Хранить текст (стенограммы, протоколы)", unit: "дней", type: "number", nullable: true, min: 0, max: 36500,
    help: "Для новых переговорок. Пусто — бессрочно; 0 — не хранить после обработки. Для каждой комнаты можно задать своё значение.", example: "365" },
  { section: "Сроки хранения по умолчанию", name: "default_audio_retention_days", label: "Хранить аудиозаписи", unit: "дней", type: "number", nullable: true, min: 0, max: 36500,
    help: "Для новых переговорок. Пусто — бессрочно; 0 — удалять сразу после обработки.", example: "30" },
];
