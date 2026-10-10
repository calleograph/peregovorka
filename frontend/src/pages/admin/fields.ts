import type { Field } from "./SettingsForm";

/**
 * Описания полей настроек: полное название, что поле делает и пример заполнения (примеры — условные, не относятся к реальному
 * развёртыванию). Поля сгруппированы по смыслу (section), чтобы при заполнении не путаться.
 */

// Прежний способ (адрес прямо в разделе) остаётся для уже настроенных установок и показывается, пока хранилище не выбрано; новые выбирают его из раздела «Хранилища».
const legacy = (v: Record<string, unknown>) => !v.profile_id && !!v.enabled;
const isSmb = (v: Record<string, unknown>) => legacy(v) && v.mode === "smb";
const isLocal = (v: Record<string, unknown>) => legacy(v) && v.mode === "local";

/** Выбор хранилища: варианты подставляет SettingsForm из раздела «Хранилища». */
export const profileField = (section: string, help: string): Field => ({
  section, name: "profile_id", label: "Хранилище", type: "select", options: [["", "Не выбрано"]], help });

/** Общие поля «куда писать»: локальный каталог или сетевой ресурс SMB. Используется и для протоколов, и для записей. */
const targetFields = (kind: "protocols" | "audio"): Field[] => [
  profileField("Где хранить", kind === "audio"
    ? "Выберите хранилище, созданное в разделе «Хранилища»: записи лягут в его подпапку Audio/. Адрес и пароль здесь вводить не нужно."
    : "Выберите хранилище из раздела «Хранилища»: стенограммы лягут в Transcripts/, протоколы и резюме — в Protocols/, переписка — в Chat/, схемы доски — в Boards/."),
  { section: "Прежний способ: свой адрес в этом разделе", name: "mode", label: "Тип хранилища", type: "select", showIf: legacy, options: [["local", "Локальный каталог на сервере"], ["smb", "Сетевой ресурс SMB (общая папка Windows/NAS)"]],
    help: kind === "audio"
      ? "Записи — большие файлы. Локальный каталог — это том приложения внутри сервера; для длительного хранения используйте SMB-ресурс."
      : "Куда складываются готовые документы встреч. Приложение подключается к SMB само, монтировать ресурс в систему не нужно." },
  { section: "Прежний способ: свой адрес в этом разделе", name: "local_path", label: "Каталог внутри контейнера", type: "text", showIf: isLocal,
    placeholder: kind === "audio" ? "/data/exports/audio" : "/data/exports",
    help: "Абсолютный путь внутри контейнера приложения (он лежит в разделе DATA_ROOT на сервере). Не путь Windows и не адрес сети.",
    example: kind === "audio" ? "/data/exports/audio" : "/data/exports" },
  { section: "Прежний способ: свой адрес в этом разделе", name: "smb_server", label: "Сервер (имя или IP)", type: "text", showIf: isSmb, placeholder: "files.corp.local",
    help: "Только имя хоста или IP — без схемы (smb://) и без слэшей.", example: "files.corp.local или 192.0.2.20" },
  { section: "Прежний способ: свой адрес в этом разделе", name: "smb_share", label: "Общий ресурс (имя шары)", type: "text", showIf: isSmb, placeholder: kind === "audio" ? "meetings-audio" : "meetings",
    help: "Имя общей папки на сервере, без слэшей.", example: kind === "audio" ? "meetings-audio" : "meetings" },
  { section: "Прежний способ: свой адрес в этом разделе", name: "smb_base_path", label: "Подкаталог на ресурсе", type: "text", showIf: isSmb, placeholder: kind === "audio" ? "peregovorka/audio" : "peregovorka",
    help: "Необязательно. Внутри него создаётся структура «комната / дата и день недели / время начала». Без «..».", example: kind === "audio" ? "peregovorka/audio" : "peregovorka/protocols" },
  { section: "Прежний способ: свой адрес в этом разделе", name: "smb_domain", label: "Домен учётной записи", type: "text", showIf: isSmb, placeholder: "CORP", help: "Необязательно (NetBIOS-имя домена).", example: "CORP" },
  { section: "Прежний способ: свой адрес в этом разделе", name: "smb_username", label: "Учётная запись с правом записи", type: "text", showIf: isSmb, placeholder: "svc-peregovorka",
    help: "Сервисная учётная запись, у которой есть право создавать и записывать файлы на ресурсе.", example: "svc-peregovorka" },
  { section: "Прежний способ: свой адрес в этом разделе", name: "smb_password", label: "Пароль учётной записи", type: "secret", showIf: isSmb,
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

/** Вложения чата: файлы лежат в выбранном хранилище (подпапка Chat/files) или, если оно не выбрано, на локальном диске сервера приложения. */
export const chatFilesFields: Field[] = [
  { section: "Вложения", name: "enabled", label: "Разрешить вложения в чате", type: "bool",
    help: "Участники могут прикреплять файлы кнопкой, перетаскиванием и вставкой скриншота (Ctrl+V). В базе хранятся только сведения о файле, сам файл — в хранилище." },
  profileField("Где хранить", "Выберите хранилище из раздела «Хранилища» — файлы лягут в подпапку Chat/files/ (в том числе на SMB). Не выбрано — файлы остаются на локальном диске сервера приложения. Если хранилище недоступно, пользователь увидит понятную ошибку, а событие попадёт в журнал: файл не теряется молча."),
  { section: "Ограничения", name: "max_size_mb", label: "Максимальный размер одного файла", unit: "МБ", type: "number", min: 1, max: 100,
    help: "Больше загрузить нельзя. Предел 100 МБ задан веб-сервером (nginx).", example: "25" },
  { section: "Ограничения", name: "max_files_per_message", label: "Файлов в одном сообщении", type: "number", min: 1, max: 20, example: "5" },
  { section: "Ограничения", name: "allowed_extensions", label: "Разрешённые типы файлов (расширения через запятую)", type: "text",
    help: "Тип проверяется и по содержимому: картинка под видом документа не пройдёт. Исполняемые и «активные» типы (exe, bat, js, html, svg и т. п.) запрещены всегда.",
    example: "png, jpg, gif, webp, pdf, txt, docx, xlsx, pptx, zip" },
];

export const anonFields: Field[] = [
  { section: "Сервис обезличивания", name: "enabled", label: "Обезличивание включено", type: "bool",
    help: "Включено: в языковую модель уходит только обезличенный текст, а если сервис недоступен — протокол не создаётся (данные не отправляются). Выключено: протоколы и резюме создаются как обычно, но текст уходит в модель без обезличивания. Отдельной переговорке можно задать своё (в её настройках)." },
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

const TYPE_OPTIONS: [string, string][] = [
  ["openai_compatible", "OpenAI-совместимый (шлюзы, локальные серверы: адрес /chat/completions)"],
  ["openai", "OpenAI"],
  ["anthropic", "Anthropic (Claude)"],
  ["custom", "Свой OpenAI-подобный сервис (любой адрес, свои заголовки и возможности)"],
];
const needsUrl = (v: { type?: unknown }) => v.type === "openai_compatible" || v.type === "custom";

/** Какая модель что делает по умолчанию в системе (протокол, резюме). Подключение к внешним API — отдельно (профили). */
export const llmModeFields: Field[] = [
  { section: "Какая модель что делает по умолчанию", name: "provider", label: "Протокол: чем формировать", type: "select",
    options: [["local", "Локальная: Qwen3 1.7B Q4_K_M (встроенная, данные не покидают сервер)"], ["external", "Внешняя LLM (подключение — «Внешние API» ниже)"], ["off", "Отключено"]],
    help: "Цепочка: распознавание речи (GigaAM) → стенограмма → языковая модель → протокол или резюме. Локальная модель работает на этом сервере без выхода в интернет и подходит для простых задач: краткое резюме, решения, задачи, ответственные, протокол по проверенным пунктам. Для длинных и сложных встреч надёжнее внешняя модель." },
  { section: "Какая модель что делает по умолчанию", name: "summary_provider", label: "Резюме: чем формировать", type: "select",
    options: [["same", "Так же, как протокол"], ["local", "Локальная: Qwen3 1.7B Q4_K_M"], ["external", "Внешняя LLM (подключение — «Внешние API» ниже)"], ["off", "Резюме не формировать"]],
    help: "Протокол и резюме — разные задачи, поэтому модель выбирается для каждой отдельно. Это значение действует, пока в переговорке, встрече или при формировании документа не выбрано другое." },
  { section: "Какая модель что делает по умолчанию", name: "map_provider", label: "Карта разговора: чем формировать", type: "select",
    options: [["same", "Так же, как протокол"], ["local", "Локальная: Qwen3 1.7B Q4_K_M"], ["external", "Внешняя LLM (подключение — «Внешние API» ниже)"], ["off", "Карты не формировать"]],
    help: "Карта разговора — отдельное назначение: по частям стенограммы выделяются темы, их порядок и длительность. Локальная модель справляется с этим на сервере без видеокарты за несколько минут; другую можно выбрать здесь позже." },
  { section: "Какая модель что делает по умолчанию", name: "on_missing", label: "Если выбранная для переговорки модель недоступна (удалена, отключена)", type: "select",
    options: [["system", "Использовать системную модель по умолчанию"], ["unavailable", "Не формировать: показать «модель недоступна»"]],
    help: "Касается переговорок и встреч, где выбрана конкретная модель. Разовый выбор модели при формировании документа молча не заменяется." },
];

/** Подключение к одному внешнему API: тип, адрес, ключ, заголовки, возможности, лимиты. Одно и то же для «основного» API и для каждого профиля. */
export const llmApiFields: Field[] = [
  { section: "Подключение", name: "type", label: "Тип API", type: "select", options: TYPE_OPTIONS,
    help: "От типа зависят формат запроса и набор полей. «Свой OpenAI-подобный сервис» — для нестандартных шлюзов: все возможности включаются вручную." },
  { section: "Подключение", name: "base_url", label: "Адрес API (Base URL)", type: "text", showIf: needsUrl, placeholder: "https://llm.corp.local/v1",
    help: "Для OpenAI и Anthropic адрес не нужен. Для совместимых и своих сервисов обязателен.", example: "https://llm.corp.local/v1 или https://api.example.com/v1" },
  { section: "Подключение", name: "model", label: "Идентификатор модели", type: "text", placeholder: "gpt-4o-mini", help: "Название модели у выбранного провайдера.", example: "gpt-4o-mini, claude-sonnet-5-5" },
  { section: "Подключение", name: "api_key", label: "Ключ API", type: "secret", help: "Хранится зашифрованно и после сохранения не показывается. Пустое поле — оставить прежний." },
  { section: "Подключение", name: "extra_headers", label: "Дополнительные заголовки запроса", type: "headers", showIf: (v) => v.type === "custom" || v.type === "openai_compatible",
    help: "Нужны некоторым шлюзам (идентификатор организации, ключ маршрутизации). Секретные значения шифруются и в интерфейс не возвращаются." },
  { section: "Подключение", name: "routing_provider", label: "Фиксировать провайдера маршрута (для шлюзов)", type: "text", showIf: (v) => v.type === "openai_compatible",
    help: "Необязательно: имя провайдера, через которого шлюз должен направлять запросы.", example: "azure" },
  { section: "Параметры генерации", name: "max_tokens_summary", label: "Максимальная длина ответа для резюме", unit: "токенов", type: "number", min: 64, max: 200000, nullable: true, placeholder: "2000",
    help: "Резюме короткое: обычно хватает 1 500–2 500 токенов. Пусто — 2 000." },
  { section: "Параметры генерации", name: "max_tokens_protocol", label: "Максимальная длина ответа для протокола", unit: "токенов", type: "number", min: 64, max: 200000, nullable: true, placeholder: "7000",
    help: "Часовой протокол длиннее резюме — нужен запас (обычно 6 000–8 000). Пусто — 7 000. Если указано окно контекста, предел уменьшается автоматически." },
  { section: "Параметры генерации", name: "context_window", label: "Окно контекста модели", unit: "токенов", type: "number", min: 0, max: 4000000,
    help: "Вход и ответ вместе. 0 — неизвестно. Если задано, длина ответа не может превысить доступное; фактический предел виден в окне «Сформировать» и в журнале документа." },
  { section: "Параметры генерации", name: "temperature", label: "Температура", type: "number", min: 0, max: 2, step: 0.1,
    help: "Для протоколов и резюме — 0: ответ воспроизводим и без «творчества»." },
  { section: "Параметры генерации", name: "timeout", label: "Таймаут ответа", unit: "с", type: "number", min: 5, max: 1800, help: "Длинная встреча обрабатывается дольше.", example: "180" },
  { section: "Возможности провайдера", name: "send_temperature", label: "Передавать параметр temperature", type: "bool",
    help: "Снимите, если провайдер отвергает этот параметр или принимает только узкий диапазон (если провайдер отклонит запрос из-за temperature, сервис один раз повторит запрос без него)." },
  { section: "Возможности провайдера", name: "supports_system", label: "Поддерживает системное сообщение", type: "bool", help: "Иначе инструкция вставляется в начало сообщения пользователя." },
  { section: "Возможности провайдера", name: "supports_json", label: "Поддерживает строгий JSON (response_format)", type: "bool", help: "Нужен для структурного извлечения; если не поддерживается — снимите." },
  { section: "Возможности провайдера", name: "supports_streaming", label: "Поддерживает потоковый ответ", type: "bool", help: "Справочно: сервис пока получает ответ целиком." },
  { section: "Безопасность соединения", name: "use_corporate_ca", label: "Проверять сертификат по корпоративному удостоверяющему центру", type: "bool" },
  { section: "Безопасность соединения", name: "allow_http", label: "Разрешить небезопасный http://", type: "bool", help: "Только для изолированных тестов." },
];

export const protocolFields: Field[] = [
  { section: "Инструкции по умолчанию", name: "instructions", label: "Инструкция для полного протокола (по умолчанию)", type: "textarea", rows: 5,
    help: "Показывается пользователю в окне «Сформировать протокол» — он может её изменить. Дополнения конкретной переговорки добавляются к ней.",
    example: "Сформировать официальный протокол совещания. Выделить тему, участников, вопросы, решения, поручения, ответственных и сроки. Не придумывать отсутствующие сведения." },
  { section: "Инструкции по умолчанию", name: "summary_instructions", label: "Инструкция для краткого резюме (по умолчанию)", type: "textarea", rows: 4,
    example: "Кратко (не более 10 строк) изложить суть встречи: о чём говорили, решения и поручения." },
  { section: "Формат документа", name: "external_mode", label: "Внешняя модель формирует документ", type: "select",
    options: [["free", "Свободно: Markdown по инструкции пользователя"], ["structured", "Структурно: JSON по схеме, таблицы и оформление собирает система"]],
    help: "Структурный режим даёт одинаковый аккуратный документ (итог, обсуждение, решения, задачи, открытые вопросы с источниками) у любой модели; свободный — полагается на инструкцию и вёрстку самой модели. Локальная Qwen3 всегда работает структурно." },
  { section: "Автоматическое формирование", name: "auto_generate", label: "Формировать полный протокол автоматически после завершения встречи", type: "bool", help: "Берётся инструкция по умолчанию. Участники всё равно могут сформировать свой вариант." },
  { section: "Автоматическое формирование", name: "auto_summary", label: "Формировать краткое резюме автоматически после завершения встречи", type: "bool" },
  { section: "Автоматическое формирование", name: "auto_map", label: "Формировать карту разговора автоматически после завершения встречи", type: "bool", help: "По умолчанию выключено: карта строится несколько минут и нагружает сервер без видеокарты. Каждая переговорка может включить или выключить это у себя." },
  { section: "Ограничения", name: "max_input_chars", label: "Максимум стенограммы за один запрос к модели", unit: "символов", type: "number", min: 2000, max: 1000000, help: "Длиннее — стенограмма обрабатывается по частям.", example: "60000" },
];

export const screenFields: Field[] = [
  { section: "Качество показа экрана", name: "profile", label: "Профиль показа экрана", type: "select",
    options: [["sharp", "Чёткость — текст, слайды, код (1080p, 15 к/с)"], ["balanced", "Сбалансированный (1080p, 20 к/с)"], ["motion", "Плавность — видео, анимация (1080p, 30 к/с)"]],
    help: "Один клик у участника — «Показать экран». Профиль действует во всех комнатах, где показ разрешён. Если у зрителей экран «ступенчатый» — выберите «Чёткость»; если рвётся видео — «Плавность»." },
  { section: "Поведение", name: "share_audio", label: "Разрешить передавать звук вкладки/системы вместе с экраном", type: "bool", help: "Участник сможет отметить «со звуком». Звук экрана в транскрибацию не попадает." },
  { section: "Поведение", name: "one_sharer_at_a_time", label: "Только один показывающий одновременно", type: "bool", help: "Пока кто-то показывает экран, остальным кнопка недоступна." },
];

export const privacyFields: Field[] = [
  { section: "Уведомление о cookie", name: "cookie_text", label: "Текст уведомления на странице входа", type: "textarea", rows: 3, help: "Короткий текст под кнопками «Понятно» и «Подробнее». Принятие запоминается в браузере." },
  { section: "Страница «Обработка данных»", name: "operator", label: "Оператор / организация", type: "text" },
  { section: "Страница «Обработка данных»", name: "purpose", label: "Назначение системы", type: "textarea", rows: 3 },
  { section: "Страница «Обработка данных»", name: "data_types", label: "Какие типы информации обрабатываются", type: "textarea", rows: 4 },
  { section: "Страница «Обработка данных»", name: "cookies", label: "Использование технических cookie", type: "textarea", rows: 3 },
  { section: "Страница «Обработка данных»", name: "retention", label: "Сроки хранения", type: "textarea", rows: 3 },
  { section: "Страница «Обработка данных»", name: "contact", label: "Контакт для вопросов", type: "textarea", rows: 2 },
  { section: "Страница «Обработка данных»", name: "policy_url", label: "Ссылка на внутреннюю политику (положение)", type: "text", placeholder: "https://intranet.example.local/policy" },
];

export const generalFields: Field[] = [
  { section: "Время", name: "timezone", label: "Часовой пояс", type: "text", placeholder: "Europe/Moscow", help: "Название IANA. Используется в именах папок хранилища и подписях времени документов.", example: "Europe/Moscow" },
  { section: "Доступ к завершённым встречам", name: "post_meeting_access_minutes", label: "Сколько минут участник сохраняет доступ после завершения встречи", unit: "минут", type: "number", min: 1, max: 1440,
    help: "Участник, оставшийся на странице завершённой встречи, может формировать протоколы; после ухода со страницы доступ закрывается. Это ограничение по времени на случай, если страница остаётся открытой.", example: "120" },
  { section: "Сроки хранения по умолчанию", name: "default_text_retention_days", label: "Хранить текст (стенограммы, протоколы)", unit: "дней", type: "number", nullable: true, min: 0, max: 36500,
    help: "Для новых переговорок. Пусто — бессрочно; 0 — не хранить после обработки. Для каждой комнаты можно задать своё значение.", example: "365" },
  { section: "Сроки хранения по умолчанию", name: "default_audio_retention_days", label: "Хранить аудиозаписи", unit: "дней", type: "number", nullable: true, min: 0, max: 36500,
    help: "Для новых переговорок. Пусто — бессрочно; 0 — удалять сразу после обработки.", example: "30" },
  { section: "Временные переговорки", name: "temp_rooms_enabled", label: "Разрешить пользователям создавать временные переговорки", type: "bool",
    help: "Кнопка «Создать временную переговорку» в списке комнат. Создавать могут только вошедшие в систему пользователи; гостям недоступно. Комната существует, пока идёт встреча, затем закрывается; её материалы остаются в «Истории»." },
  { section: "Временные переговорки", name: "temp_room_max_per_user", label: "Активных временных комнат у одного пользователя", type: "number", min: 1, max: 50, example: "2" },
  { section: "Временные переговорки", name: "temp_room_max_total", label: "Активных временных комнат во всей системе", type: "number", min: 1, max: 1000, example: "30" },
  { section: "Временные переговорки", name: "temp_room_grace_minutes", label: "Сколько ждать возврата, когда все вышли", unit: "минут", type: "number", min: 1, max: 240,
    help: "Если за это время никто не вернулся, комната закрывается. Вернулся вовремя — отсчёт отменяется.", example: "5" },
  { section: "Временные переговорки", name: "temp_room_idle_minutes", label: "Закрывать комнату, в которую никто не вошёл, через", unit: "минут", type: "number", min: 5, max: 1440, example: "30" },
  { section: "Временные переговорки", name: "temp_room_max_hours", label: "Предельная длительность встречи во временной комнате", unit: "часов", type: "number", min: 1, max: 168,
    help: "Защита от зависших встреч: по истечении встреча завершается принудительно, комната закрывается.", example: "12" },
  { section: "Временные переговорки", name: "temp_room_allow_guest_link", label: "Разрешить владельцу временной комнаты выпускать гостевую ссылку", type: "bool",
    help: "По умолчанию выключено. Гость входит только в ту комнату, ссылку на которую получил, и не может создавать комнаты." },
];

/** Параметры VAD (деление речи на реплики): меняются на лету, применяются к новым трекам. Пусто — значение из .env (ASR_VAD_*). */
export const vadFields: Field[] = [
  { section: "Деление речи на реплики (VAD)", name: "vad_end_silence_ms", label: "Пауза, после которой реплика считается законченной", unit: "мс", type: "number", nullable: true, min: 100, max: 5000,
    help: "Меньше — реплики появляются быстрее (меньше задержка), но длинные фразы с паузами дробятся. Основной параметр задержки транскрипции.", example: "400 (интерактивно) … 700 (по умолчанию)" },
  { section: "Деление речи на реплики (VAD)", name: "vad_min_speech_ms", label: "Минимальная длина речи", unit: "мс", type: "number", nullable: true, min: 32, max: 5000,
    help: "Короче — отбрасывается как шум. Слишком большое значение «съедает» короткие реплики («да», «нет»).", example: "200" },
  { section: "Деление речи на реплики (VAD)", name: "vad_pad_ms", label: "Запас вокруг речи (до и после)", unit: "мс", type: "number", nullable: true, min: 0, max: 1000,
    help: "Защищает от обрезания начала и концов слов; больше — безопаснее для слов, но чуть дольше задержка.", example: "120" },
  { section: "Деление речи на реплики (VAD)", name: "vad_max_segment_seconds", label: "Максимальная длина одной реплики", unit: "с", type: "number", nullable: true, min: 3, max: 25, step: 1,
    help: "Длинная непрерывная речь режется на части не длиннее этого значения (GigaAM — не более 25 с). Меньше — ниже задержка на длинной речи, но возможны разрывы фраз.", example: "12" },
  { section: "Деление речи на реплики (VAD)", name: "vad_threshold", label: "Порог вероятности речи", type: "number", nullable: true, min: 0.05, max: 0.95, step: 0.05,
    help: "Выше — строже (меньше ложных срабатываний на шум, но тихая речь может теряться); ниже — чувствительнее.", example: "0.5" },
];

/** Журнал событий: срок хранения, место хранения (в базе сервера и/или во внешнем хранилище), минимальный уровень. */
export const journalFields: Field[] = [
  { section: "Срок хранения", name: "retention_days", label: "Хранить записи журнала", unit: "дней", type: "number", min: 1, max: 3650,
    help: "Записи старше этого срока удаляются автоматически (проверка раз в час), свежие остаются. Это же правило действует для внешнего хранилища (удаляются каталоги старых дней).", example: "30" },
  { section: "Что и где хранить", name: "keep_local", label: "Хранить журнал в базе сервера (его можно смотреть и фильтровать в админке)", type: "bool",
    help: "Выключите, если не хотите занимать место на сервере: тогда события пишутся только во внешнее хранилище (ниже), а в админке видны лишь прежние записи. Если выключены оба варианта, журнал не ведётся." },
  { section: "Что и где хранить", name: "min_level", label: "Какие события записывать", type: "select",
    options: [["debug", "Все, включая отладочные (много записей)"], ["info", "Сведения, предупреждения и ошибки (рекомендуется)"], ["warn", "Только предупреждения и ошибки"], ["error", "Только ошибки"]],
    help: "Меньше событий — меньше места. Для разбора проблем с подключением нужны «сведения» и выше." },
  { section: "Внешнее хранилище журнала", name: "enabled", label: "Дублировать журнал во внешнее хранилище", type: "bool",
    help: "События пакетами записываются файлами NDJSON по дням: журнал/ГГГГ-ММ-ДД/ЧЧММСС-xxxx.ndjson. Так сервер не засоряется, а история хранится на отдельном диске или сетевом ресурсе." },
  { ...profileField("Внешнее хранилище журнала", "Журнал лягут в подпапку Logs/ выбранного хранилища (раздел «Хранилища»)."), showIf: (v) => !!v.enabled },
  { section: "Внешнее хранилище журнала", name: "mode", label: "Тип хранилища (прежний способ)", type: "select", showIf: (v) => !!v.enabled && !v.profile_id, options: [["local", "Каталог (внешний диск, примонтированный в контейнер)"], ["smb", "Сетевой ресурс SMB (общая папка Windows/NAS)"]] },
  { section: "Внешнее хранилище журнала", name: "local_path", label: "Каталог внутри контейнера", type: "text", showIf: (v) => !!v.enabled && !v.profile_id && v.mode === "local", placeholder: "/data/exports/logs",
    help: "Абсолютный путь внутри контейнера приложения (он лежит в DATA_ROOT на сервере; чтобы писать на другой диск, примонтируйте его в DATA_ROOT).", example: "/data/exports/logs" },
  { section: "Внешнее хранилище журнала", name: "smb_server", label: "Сервер (имя или IP)", type: "text", showIf: (v) => !!v.enabled && !v.profile_id && v.mode === "smb", placeholder: "files.corp.local", example: "files.corp.local" },
  { section: "Внешнее хранилище журнала", name: "smb_share", label: "Общий ресурс (имя шары)", type: "text", showIf: (v) => !!v.enabled && !v.profile_id && v.mode === "smb", placeholder: "logs", example: "peregovorka-logs" },
  { section: "Внешнее хранилище журнала", name: "smb_base_path", label: "Подкаталог на ресурсе", type: "text", showIf: (v) => !!v.enabled && !v.profile_id && v.mode === "smb", placeholder: "peregovorka", example: "peregovorka" },
  { section: "Внешнее хранилище журнала", name: "smb_domain", label: "Домен учётной записи", type: "text", showIf: (v) => !!v.enabled && !v.profile_id && v.mode === "smb", placeholder: "CORP" },
  { section: "Внешнее хранилище журнала", name: "smb_username", label: "Учётная запись с правом записи", type: "text", showIf: (v) => !!v.enabled && !v.profile_id && v.mode === "smb", placeholder: "svc-peregovorka" },
  { section: "Внешнее хранилище журнала", name: "smb_password", label: "Пароль учётной записи", type: "secret", showIf: (v) => !!v.enabled && !v.profile_id && v.mode === "smb", help: "Хранится зашифрованно. Пустое поле — оставить прежний." },
  { section: "Внешнее хранилище журнала", name: "external_flush_seconds", label: "Как часто выгружать накопленное", unit: "с", type: "number", min: 5, max: 3600, showIf: (v) => !!v.enabled,
    help: "Чаще — свежее журнал во внешнем хранилище, но больше мелких файлов.", example: "60" },
];

/** Расписание сверки хранилищ. */
export const syncFields: Field[] = [
  { section: "Автоматическая сверка", name: "enabled", label: "Сверять хранилища автоматически", type: "bool", help: "Фоновая проверка, что файлы, о которых знает база, действительно лежат в хранилище." },
  { section: "Автоматическая сверка", name: "interval_hours", label: "Как часто", unit: "часов", type: "number", min: 1, max: 168, help: "От нескольких часов до суток и более. Чаще — быстрее обнаруживается ручное удаление, но больше обращений к хранилищу.", example: "12" },
  { section: "Параметры", name: "batch_size", label: "Размер пакета", unit: "объектов", type: "number", min: 50, max: 5000, help: "Сколько встреч проверять за один проход перед паузой (чтобы не нагружать сетевое хранилище).", example: "500" },
  { section: "Параметры", name: "guard_percent", label: "Порог защиты от сбоя хранилища", unit: "%", type: "number", min: 50, max: 100,
    help: "Если в выборке пропало не меньше стольких процентов файлов (и не менее 10 штук), это похоже на сбой (не смонтирован том, подменён ресурс), а не на ручное удаление: изменения не применяются, пока администратор не подтвердит.", example: "95" },
];

/** Правила рассылки материалов (SMTP-реквизиты — в профилях почты; «что и кому» выбирает руководитель комнаты). */
export const mailPolicyFields: Field[] = [
  { section: "Вложения", name: "attach_format", label: "Формат документов во вложении", type: "select", options: [["docx", "DOCX (Word)"], ["pdf", "PDF"], ["html", "HTML (веб-страница)"], ["txt", "Простой текст"], ["md", "Markdown"]] },
  { section: "Вложения", name: "max_attachment_mb", label: "Максимальный размер вложений в письме", unit: "МБ", type: "number", min: 1, max: 50,
    help: "Если документы вместе больше — они не вкладываются, а в письме даётся ссылка на страницу встречи (открывается после входа). Запись аудио по почте не отправляется.", example: "10" },
  { section: "Очередь и повторы", name: "max_attempts", label: "Попыток отправки", type: "number", min: 1, max: 10, help: "При временной ошибке сервера (нет связи, занят) письмо отправляется повторно.", example: "4" },
  { section: "Очередь и повторы", name: "retry_minutes", label: "Пауза до первого повтора", unit: "минут", type: "number", min: 1, max: 240, help: "Каждая следующая пауза вдвое длиннее.", example: "5" },
  { section: "Очередь и повторы", name: "keep_days", label: "Хранить журнал отправки", unit: "дней", type: "number", min: 1, max: 3650, example: "90" },
  { section: "Письмо", name: "subject_prefix", label: "Префикс темы письма", type: "text", placeholder: "[Peregovorka]", help: "Необязательно: помогает настроить фильтры в почтовых программах." },
  { section: "Дополнительно (расширенные настройки)", name: "allowed_domains", label: "Разрешённые домены получателей", type: "text", placeholder: "example.local, partner.example",
    help: "Через запятую. Пусто — отправка на любые адреса. Если задано, письма на адреса других доменов (например, внешние) не отправляются — ни автоматически, ни вручную.", example: "example.local" },
];

/** «Параметры задач» (вкладка «Параметры генерации»): потолок длины ответа по каждой задаче; итог = min(потолок задачи, предел подключения, свободный контекст). */
export const llmTaskFields: Field[] = [
  { section: "Потолок длины ответа по задачам", name: "limit_protocol", label: "Протокол", unit: "токенов", type: "number", min: 64, max: 200000, nullable: true, placeholder: "без потолка задачи",
    help: "Пусто — действует предел подключения (по умолчанию 7 000)." },
  { section: "Потолок длины ответа по задачам", name: "limit_summary", label: "Резюме", unit: "токенов", type: "number", min: 64, max: 200000, nullable: true, placeholder: "без потолка задачи",
    help: "Резюме короткое: обычно хватает 1 500–2 500 токенов." },
  { section: "Потолок длины ответа по задачам", name: "limit_map", label: "Карта разговора", unit: "токенов", type: "number", min: 64, max: 200000, nullable: true, placeholder: "без потолка задачи",
    help: "Ответ на один фрагмент стенограммы — несколько тем; обычно достаточно 700–1 000." },
];
export const structuredFields: Field[] = protocolFields.filter((f) => f.name === "external_mode");

const SOURCE_HELP = "Источники по порядку через запятую: ad — каталог, bitrix — портал Bitrix24, local — то, что человек указал сам. Берётся первый источник, у которого поле заполнено.";
/** Bitrix24 — дополнительный источник профиля. Вход и комната от портала не зависят: при его недоступности остаются прежние данные. */
export const bitrixFields: Field[] = [
  { section: "Подключение", name: "enabled", label: "Брать данные профиля из Bitrix24", type: "bool",
    help: "Каталог (AD) остаётся основой входа. Bitrix24 только дополняет карточку: должность, подразделение, телефон, фото. Если портал недоступен, вход и встречи работают как обычно." },
  { section: "Подключение", name: "portal_url", label: "Адрес портала", type: "text", placeholder: "https://portal.example.com", example: "https://portal.example.com" },
  { section: "Подключение", name: "webhook_url", label: "Входящий webhook", type: "secret",
    help: "Полный адрес вида https://портал/rest/<номер>/<секрет>/. В Bitrix24 создаётся в «Разработчикам → Другое → Входящий вебхук». Нужны права «Пользователи (user_brief или user_basic)» и, для названий подразделений, «Подразделения (department)». Секрет хранится зашифрованно и не показывается. Только чтение." },
  { section: "Что подтягивать", name: "use_title", label: "Должность", type: "bool" },
  { section: "Что подтягивать", name: "use_department", label: "Подразделение", type: "bool" },
  { section: "Что подтягивать", name: "use_phone", label: "Рабочий телефон", type: "bool" },
  { section: "Что подтягивать", name: "use_photos", label: "Фото профиля", type: "bool",
    help: "Фото скачивается один раз и хранится у нас (в формате WebP, до 5 МБ на входе). Фото, загруженное самим человеком, портал не заменяет, пока «local» стоит в приоритете раньше «bitrix»." },
  { section: "Приоритеты полей", name: "priority_display_name", label: "ФИО", type: "text", help: SOURCE_HELP, example: "ad, bitrix, local" },
  { section: "Приоритеты полей", name: "priority_email", label: "E-mail", type: "text", example: "ad, bitrix, local" },
  { section: "Приоритеты полей", name: "priority_title", label: "Должность", type: "text", example: "bitrix, ad, local" },
  { section: "Приоритеты полей", name: "priority_department", label: "Подразделение", type: "text", example: "bitrix, ad, local" },
  { section: "Приоритеты полей", name: "priority_phone", label: "Телефон", type: "text", example: "bitrix, ad, local" },
  { section: "Приоритеты полей", name: "priority_avatar", label: "Фото", type: "text", example: "local, bitrix, ad" },
  { section: "Режим работы", name: "cache_hours", label: "Обновлять данные человека не чаще", unit: "часов", type: "number", min: 1, max: 720,
    help: "Данные обновляются в фоне при входе, не чаще этого срока. После сбоя портала повтор — не раньше чем через 10 минут.", example: "24" },
  { section: "Режим работы", name: "timeout", label: "Таймаут запроса к порталу", unit: "с", type: "number", min: 1, max: 30, example: "5" },
  { section: "Безопасность соединения", name: "verify_tls", label: "Проверять сертификат портала", type: "bool" },
  { section: "Безопасность соединения", name: "use_corporate_ca", label: "Доверять корпоративному удостоверяющему центру (LDAP_CA_FILE)", type: "bool",
    help: "Включайте, если портал использует внутренний сертификат." },
  { section: "Безопасность соединения", name: "allow_http", label: "Разрешить небезопасный http://", type: "bool", help: "Только для изолированных тестов: секрет webhook пойдёт без шифрования." },
];
