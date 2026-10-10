"""Реестр конфигурации: ЧТО входит в резервную копию настроек и КАК переносится. Единственное место, где это решается.

Любой параметр, которым пользуется приложение, обязан быть здесь описан — иначе тест покрытия (`tests/test_config_registry.py`) падает. Это защита от «тихой потери»: добавили настройку или
столбец, забыли про перенос — тест не пройдёт, пока политика не выбрана осознанно.

Четыре слоя:
  * ТАБЛИЦЫ БД (`TABLES`): каждая таблица — «переносится» (с политикой для каждого столбца) или «не переносится» (с причиной: данные встреч, журналы, сессии, пользователи …);
  * ГРУППЫ НАСТРОЕК (`GROUPS_POLICY`): настройки из «Администрирования» (таблица `app_settings`), секреты определяются самими группами (`SECRETS`);
  * ФАЙЛЫ (`FILES`): изображения оформления;
  * ОКРУЖЕНИЕ (`ENV`): параметры `.env` — принадлежат конкретной установке и не переносятся (с причиной по каждому).

Политики столбцов: data — переносится как есть; secret — зашифрованное значение (расшифровывается ключом исходного сервера, в архиве хранится внутри шифрованного контейнера,
при импорте шифруется ключом нового сервера); hash — хэш пароля/ключа, переносится как хэш (исходного секрета в системе нет, но по хэшу он продолжает проверяться);
reset — состояние времени выполнения (счётчики, идентификаторы объектов старого LiveKit): не переносится, получает значение по умолчанию; user_ref — ссылка на пользователя
этого сервера: не переносится (на новом сервере пользователи другие), остаётся только текстовое имя.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

SCHEMA_VERSION = 1                 # версия формата содержимого архива; меняется вместе с правилами преобразования старых архивов (`TRANSFORMS`)
FORMAT_VERSION = 1                 # версия контейнера (шифрование, структура заголовка)

DATA, SECRET, HASH, RESET, USER_REF = "data", "secret", "hash", "reset", "user_ref"
POLICIES = (DATA, SECRET, HASH, RESET, USER_REF)

# виды параметров, привязанных к конкретному серверу: по ним в предпросмотре показываются предупреждения
BOUND_HOST, BOUND_URL, BOUND_PATH, BOUND_SMB, BOUND_PORT, BOUND_CERT, BOUND_LIVEKIT, BOUND_ADDRESS = "host", "url", "path", "smb", "port", "cert", "livekit", "address"
BOUND_TITLE = {
    BOUND_HOST: "имя или адрес сервера", BOUND_URL: "адрес (URL)", BOUND_PATH: "путь в файловой системе", BOUND_SMB: "сетевая папка SMB", BOUND_PORT: "порт",
    BOUND_CERT: "сертификат (проверьте соответствие имени нового сервера)", BOUND_LIVEKIT: "объект LiveKit старого сервера", BOUND_ADDRESS: "адрес/сеть",
}


@dataclass(frozen=True)
class TablePolicy:
    export: bool
    reason: str = ""                                         # для не переносимых — почему; для переносимых — пояснение
    title: str = ""                                          # как называется в предпросмотре
    columns: dict[str, str] = field(default_factory=dict)    # столбец → политика (для переносимых таблиц покрыть нужно ВСЕ столбцы)
    bound: dict[str, str] = field(default_factory=dict)      # столбец → вид привязки к серверу (BOUND_*)
    aad: Callable[[dict], bytes] | None = None               # как формируется AAD шифрования секрета (по строке)
    where: str = ""                                          # человекочитаемое условие отбора строк (для документации); отбор — в exporter.FILTERS
    order: int = 100                                         # порядок вставки (родители раньше детей)
    hashes: tuple[str, ...] = ()                             # (столбец) — показывать в предпросмотре как «хранится только хэш»


def _aad(prefix: str) -> Callable[[dict], bytes]:
    return lambda row: f"{prefix}{row['id']}".encode()


def _cols(policy_by_col: dict[str, list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for pol, names in policy_by_col.items():
        for n in names:
            out[n] = pol
    return out


_TS = ["created_at", "updated_at"]

TABLES: dict[str, TablePolicy] = {
    # ------------------------------------------------------------------------------ переносятся
    "ca_certificates": TablePolicy(True, "доверенные корпоративные сертификаты (публичные части)", "Сертификаты УЦ", order=10,
        columns=_cols({DATA: ["id", "label", "subject", "issuer", "serial", "sha256", "not_before", "not_after", "is_ca", "self_signed", "pem", "added_by", "created_at"]})),
    "api_profiles": TablePolicy(True, "профили внешних API (языковые модели, обезличивание и др.), секреты и секретные заголовки", "Профили внешних API", order=10, aad=_aad("api_profile."),
        columns=_cols({DATA: ["id", "kind", "name", "config", "is_default", *_TS], SECRET: ["secret_enc"]}), bound={"config": BOUND_URL}),
    "ldap_profiles": TablePolicy(True, "подключения к каталогу (LDAP/AD), пароль учётной записи для чтения", "Подключения к каталогу", order=10, aad=_aad("ldap_profile."),
        columns=_cols({DATA: ["id", "name", "enabled", "position", "host", "port", "protocol", "base_dn", "upn_suffix", "netbios_domain", "timeout_s", "bind_dn", "login_attribute",
                              "display_name_attribute", "email_attribute", "use_for_users", "use_for_admins", *_TS], SECRET: ["secret_enc"]}), bound={"host": BOUND_HOST, "port": BOUND_PORT}),
    "mail_profiles": TablePolicy(True, "подключения к почтовому серверу (SMTP), пароль", "Почта (SMTP)", order=10, aad=_aad("mail_profile."),
        columns=_cols({DATA: ["id", "name", "host", "port", "security", "auth_type", "username", "from_address", "from_name", "timeout_s", "verify_cert", "is_active", *_TS], SECRET: ["secret_enc"]}),
        bound={"host": BOUND_HOST, "port": BOUND_PORT}),
    "mail_templates": TablePolicy(True, "шаблоны писем", "Шаблоны писем", order=10,
        columns=_cols({DATA: ["id", "name", "subject", "body", "signature", "materials", "is_default", *_TS]})),
    "sip_profiles": TablePolicy(True, "SIP-подключения, пароль; идентификаторы транков LiveKit старого сервера не переносятся — после импорта подключение нужно синхронизировать", "SIP-телефония", order=10, aad=_aad("sip_profile."),
        columns=_cols({DATA: ["id", "name", "enabled", "is_default", "direction", "host", "port", "transport", "username", "realm", "caller_id", "allowed_numbers", "inbound_numbers",
                              "allowed_addresses", "codecs", "media_encryption", "ring_timeout_s", "created_at", "updated_at"], SECRET: ["secret_enc"],
                       RESET: ["lk_outbound_trunk_id", "lk_inbound_trunk_id", "last_check", "last_check_at"]}),
        bound={"host": BOUND_HOST, "port": BOUND_PORT, "allowed_addresses": BOUND_ADDRESS, "lk_outbound_trunk_id": BOUND_LIVEKIT, "lk_inbound_trunk_id": BOUND_LIVEKIT}),
    "storage_profiles": TablePolicy(True, "файловые хранилища (SMB / папка), пароль", "Файловые хранилища", order=10, aad=_aad("storage_profile."),
        columns=_cols({DATA: ["id", "name", "kind", "config", *_TS], SECRET: ["secret_enc"]}), bound={"config": BOUND_SMB}),
    "protocol_templates": TablePolicy(True, "общие шаблоны и инструкции протоколов (личные шаблоны пользователей не переносятся)", "Шаблоны протоколов", order=10, where="scope != 'user'",
        columns=_cols({DATA: ["id", "name", "kind", "instruction", "scope", *_TS], USER_REF: ["owner_user_id"]})),
    "legal_documents": TablePolicy(True, "документы организации (черновик и опубликованная редакция)", "Документы организации", order=10,
        columns=_cols({DATA: ["kind", "title", "draft_md", "content_md", "published_title", "version", "published", "require_consent", "published_at", "updated_at", "updated_by"]})),
    "legal_revisions": TablePolicy(True, "история редакций документов (подтверждения пользователей не переносятся)", "Редакции документов", order=11,
        columns=_cols({DATA: ["id", "kind", "version", "title", "content_md", "published_at", "published_by", "unpublished_at"]})),
    "rooms": TablePolicy(True, "постоянные переговорки: настройки, роли, запись и транскрибация, инструкции; временные не переносятся; пароль — как хэш", "Переговорки", order=20,
        where="lifetime = 'permanent'", hashes=("password_hash",),
        columns=_cols({DATA: ["id", "slug", "name", "description", "is_enabled", "max_participants", "transcription_enabled", "record_audio", "camera_allowed", "screen_share_allowed",
                              "text_retention_days", "audio_retention_days", "protocol_instructions", "history_access", "anonymize_mode", "llm_profile_id", "anonymizer_profile_id",
                              "mute_on_join", "guest_access_enabled", "guest_token", "room_type", "auto_record", "recording_mode", "board_allowed", "board_access", "mail_delivery",
                              "llm_mode", "llm_local_model", "llm_summary_mode", "llm_summary_profile_id", "llm_summary_local_model", "sip_mode", "sip_profile_id", "sip_extension",
                              "sip_allow_inbound", "sip_allow_outbound", "sip_contacts", "welcome_message", "auto_map_mode", "slug_history", "lifetime", "lifecycle",
                              "created_by_name", "created_at", "updated_at"],
                       HASH: ["password_hash"], RESET: ["sip_dispatch_rule_id", "closed_at", "auto_close_at"], USER_REF: ["created_by_user_id"]}), bound={"sip_dispatch_rule_id": BOUND_LIVEKIT}),
    "room_acl": TablePolicy(True, "кому доступна переговорка (группы и пользователи каталога)", "Доступ к переговоркам", order=21,
        columns=_cols({DATA: ["id", "room_id", "subject_type", "subject_ref", "display_name"]})),
    "room_moderators": TablePolicy(True, "руководители переговорок", "Руководители переговорок", order=21,
        columns=_cols({DATA: ["id", "room_id", "subject_type", "subject_ref", "display_name"]})),
    "api_clients": TablePolicy(True, "интеграции публичного API: права, комнаты, разрешённые адреса", "Интеграции API", order=30,
        columns=_cols({DATA: ["id", "name", "description", "enabled", "scopes", "rooms", "ip_allowlist", "created_by", "created_at", "updated_at"]}), bound={"ip_allowlist": BOUND_ADDRESS}),
    "api_keys": TablePolicy(True, "ключи интеграций: в системе хранится только хэш, поэтому переносится хэш (ключ продолжает работать); сам ключ восстановить нельзя", "Ключи API", order=31,
        hashes=("secret_hash",),
        columns=_cols({DATA: ["id", "client_id", "key_id", "last4", "label", "expires_at", "revoked_at", "created_at"], HASH: ["secret_hash"], RESET: ["last_used_at", "last_used_ip"]})),
    "webhook_endpoints": TablePolicy(True, "получатели событий (webhooks), секрет подписи", "События (webhooks)", order=30, aad=_aad("webhook:"),
        columns=_cols({DATA: ["id", "name", "url", "enabled", "events", "rooms", "created_by", "created_at", "updated_at"], SECRET: ["secret_enc", "previous_secret_enc"],
                       RESET: ["status", "previous_until", "consecutive_failures", "last_success_at", "last_failure_at", "last_error", "disabled_reason"]}), bound={"url": BOUND_URL}),
    # ------------------------------------------------------------------------------ настройки: отдельный раздел
    "app_settings": TablePolicy(False, "переносится отдельным разделом «настройки» (по группам, см. GROUPS_POLICY)"),
    # ------------------------------------------------------------------------------ не переносятся
    "users": TablePolicy(False, "пользователи принадлежат конкретному серверу и каталогу; локальный администратор нового сервера сохраняется и после импорта"),
    "guest_participants": TablePolicy(False, "данные встреч"), "meetings": TablePolicy(False, "данные встреч"), "meeting_participants": TablePolicy(False, "данные встреч"),
    "meeting_grants": TablePolicy(False, "данные встреч"), "meeting_chat_messages": TablePolicy(False, "переписка встреч"), "meeting_chat_attachments": TablePolicy(False, "вложения чата"),
    "meeting_whiteboards": TablePolicy(False, "схемы встреч"), "transcript_segments": TablePolicy(False, "стенограммы"), "protocols": TablePolicy(False, "протоколы и резюме встреч"),
    "conversation_maps": TablePolicy(False, "карты разговора"), "recordings": TablePolicy(False, "записи встреч"), "recording_waveforms": TablePolicy(False, "волновые формы записей: производные данные, строятся заново по запросу"),
    "storage_transfers": TablePolicy(False, "задания переноса записей между хранилищами: состояние выполнения"), "storage_transfer_items": TablePolicy(False, "задания переноса записей между хранилищами: состояние выполнения"),
    "storage_sync_runs": TablePolicy(False, "отчёты сверки хранилища"),
    "audit_log": TablePolicy(False, "журнал аудита"), "event_log": TablePolicy(False, "журнал событий"),
    "mail_messages": TablePolicy(False, "очередь и история отправленных писем"), "legal_consents": TablePolicy(False, "подтверждения документов пользователями"),
    "api_idempotency": TablePolicy(False, "служебные данные публичного API"), "api_jobs": TablePolicy(False, "задачи публичного API"), "api_request_log": TablePolicy(False, "журнал запросов публичного API"),
    "webhook_deliveries": TablePolicy(False, "история доставки событий"),
}

# столбцы app_settings: переносятся не по таблице, а по группам
APP_SETTINGS_COLUMNS = ("key", "value", "is_secret", "updated_at", "updated_by")


@dataclass(frozen=True)
class GroupPolicy:
    export: bool = True
    reason: str = ""
    title: str = ""
    skip: dict[str, str] = field(default_factory=dict)       # поле → причина, почему не переносится (состояние выполнения, привязка к установке)
    bound: dict[str, str] = field(default_factory=dict)      # поле → вид привязки к серверу


GROUPS_POLICY: dict[str, GroupPolicy] = {
    "api": GroupPolicy(title="Публичный API и события", bound={"webhook_allow_hosts": BOUND_ADDRESS}),
    "bitrix24": GroupPolicy(title="Bitrix24", bound={"portal_url": BOUND_URL, "webhook_url": BOUND_URL}),
    "privacy": GroupPolicy(title="Конфиденциальность и сроки хранения", bound={"policy_url": BOUND_URL}),
    "site": GroupPolicy(title="Оформление и документы организации", bound={"org_url": BOUND_URL, "support_url": BOUND_URL, "portal_url": BOUND_URL}),
    "storage": GroupPolicy(title="Хранилище протоколов и материалов", bound={"local_path": BOUND_PATH, "smb_server": BOUND_SMB, "smb_share": BOUND_SMB, "smb_base_path": BOUND_SMB}),
    "audio_storage": GroupPolicy(title="Хранилище записей", bound={"local_path": BOUND_PATH, "smb_server": BOUND_SMB, "smb_share": BOUND_SMB, "smb_base_path": BOUND_SMB}),
    "chat_files": GroupPolicy(title="Вложения чата"),
    "anonymizer": GroupPolicy(title="Обезличивание", bound={"base_url": BOUND_URL}),
    "llm": GroupPolicy(title="Языковая модель", bound={"base_url": BOUND_URL}),
    "protocol": GroupPolicy(title="Протоколы и резюме (инструкции)"),
    "screen": GroupPolicy(title="Показ экрана"),
    "general": GroupPolicy(title="Общие настройки"),
    "asr": GroupPolicy(title="Распознавание речи (модели)"),
    "journal": GroupPolicy(title="Журнал событий", bound={"local_path": BOUND_PATH, "smb_server": BOUND_SMB, "smb_share": BOUND_SMB, "smb_base_path": BOUND_SMB}),
    "access": GroupPolicy(title="Доступ к системе (группы каталога)"),
    "setup": GroupPolicy(title="Мастер первой настройки"),
    "mail_policy": GroupPolicy(title="Политика отправки писем"),
    "storage_sync": GroupPolicy(title="Сверка хранилища"),
    "autoupdate": GroupPolicy(title="Автообновление", reason="расписание обновлений относится к серверу, но безопасно переносится как настройка"),
}

# файлы данных, которые входят в копию (имя в архиве → пояснение)
FILES: dict[str, str] = {
    "branding/logo": "логотип", "branding/logo_compact": "компактный логотип", "branding/favicon": "значок вкладки",
}

# параметры окружения (.env) — относятся к конкретной установке, не переносятся
ENV_REASON_DEFAULT = "параметр установки (.env): задаётся при развёртывании сервера и не переносится"
ENV: dict[str, str] = {
    "app_version": "версия определяется собранным образом", "app_git_commit": "версия определяется собранным образом", "app_built_at": "версия определяется собранным образом",
    "app_public_url": "внешний адрес этой установки", "log_level": ENV_REASON_DEFAULT, "log_format": ENV_REASON_DEFAULT, "docs_enabled": ENV_REASON_DEFAULT,
    "data_dir": "каталог данных этой установки", "meeting_mix_enabled": ENV_REASON_DEFAULT, "trusted_proxy_hops": "топология сети этой установки", "trusted_proxy_cidrs": "топология сети этой установки",
    "database_url": "подключение к базе этой установки", "postgres_host": "подключение к базе этой установки", "postgres_port": "подключение к базе этой установки", "postgres_db": "подключение к базе этой установки",
    "postgres_user": "подключение к базе этой установки", "postgres_password": "секрет этой установки (не копируется)",
    "redis_url": "подключение к Redis этой установки", "redis_host": "подключение к Redis этой установки", "redis_port": "подключение к Redis этой установки", "redis_password": "секрет этой установки (не копируется)",
    "livekit_internal_url": "адрес LiveKit этой установки", "livekit_public_url": "адрес LiveKit этой установки", "livekit_api_key": "ключ LiveKit этой установки (не копируется)",
    "livekit_api_secret": "секрет LiveKit этой установки (не копируется)", "livekit_token_ttl_seconds": ENV_REASON_DEFAULT, "livekit_server_version": "определяется установленным LiveKit",
    "asr_internal_url": "адрес службы распознавания этой установки", "local_llm_url": "адрес локальной модели этой установки", "local_llm_models_dir": "каталог моделей этой установки",
    "local_llm_enabled": ENV_REASON_DEFAULT, "local_llm_model_file": ENV_REASON_DEFAULT, "local_llm_model_alias": ENV_REASON_DEFAULT, "local_llm_model_bytes": ENV_REASON_DEFAULT, "local_llm_model_sha256": ENV_REASON_DEFAULT,
    "sip_enabled": ENV_REASON_DEFAULT, "sip_signaling_port": "сеть этой установки", "sip_rtp_start": "сеть этой установки", "sip_rtp_end": "сеть этой установки", "sip_media_ip": "сеть этой установки",
    "sip_allowed_cidrs": "сеть этой установки", "sip_health_url": "адрес службы этой установки", "livekit_node_ip": "сеть этой установки", "livekit_rtc_tcp_port": "сеть этой установки", "livekit_rtc_udp_port": "сеть этой установки",
    "ldap_uris": "прежние параметры каталога из .env: при первом запуске автоматически становятся подключением в базе, которое переносится", "ldap_base_dn": "см. ldap_uris",
    "ldap_bind_dn": "см. ldap_uris", "ldap_bind_password": "см. ldap_uris (секрет не копируется из .env)", "ldap_ca_file": "файл сертификатов этой установки; сертификаты из базы переносятся",
    "ldap_login_attribute": "см. ldap_uris", "ldap_display_name_attribute": "см. ldap_uris", "ldap_admin_group_dn": "см. ldap_uris; группы администраторов переносятся в «Доступ к системе»",
    "ldap_access_group_dn": "см. ldap_uris", "ldap_timeout_seconds": "см. ldap_uris",
    "cookie_secure": ENV_REASON_DEFAULT, "cookie_name": ENV_REASON_DEFAULT, "session_idle_timeout_seconds": ENV_REASON_DEFAULT, "session_absolute_timeout_seconds": ENV_REASON_DEFAULT,
    "login_max_failures_per_user": ENV_REASON_DEFAULT, "login_max_failures_per_ip": ENV_REASON_DEFAULT, "login_failure_window_seconds": ENV_REASON_DEFAULT, "login_lockout_seconds": ENV_REASON_DEFAULT,
    "app_master_key": "ключ шифрования этой установки: НЕ переносится — секреты в архиве перешифровываются ключом нового сервера", "internal_api_token": "секрет этой установки (не копируется)",
    "meeting_end_grace_seconds": ENV_REASON_DEFAULT, "default_text_retention_days": ENV_REASON_DEFAULT, "default_audio_retention_days": ENV_REASON_DEFAULT, "segment_consumer_block_ms": ENV_REASON_DEFAULT,
    "room_password_max_failures": ENV_REASON_DEFAULT, "room_password_failure_window_seconds": ENV_REASON_DEFAULT,
}
# переменные окружения вне Settings, которые читает приложение
ENV_OTHER: dict[str, str] = {"FFMPEG_BIN": "путь к ffmpeg на этом сервере", "PDF_FONT_DIR": "каталог шрифтов на этом сервере"}


def exported_tables() -> list[str]:
    return sorted((n for n, p in TABLES.items() if p.export), key=lambda n: (TABLES[n].order, n))


# --------------------------------------------------------------------------------- преобразование старых архивов
# {версия, с которой переходим: функция(payload) → payload следующей версии}. Пока схема одна; при её изменении здесь описывается переход, а тест проверяет цепочку до текущей.
TRANSFORMS: dict[int, Callable[[dict], dict]] = {}


def upgrade_payload(payload: dict, from_version: int) -> dict:
    v = from_version
    while v < SCHEMA_VERSION:
        step = TRANSFORMS.get(v)
        if step is None:
            raise ValueError(f"Нет правила преобразования архива версии {v} в {v + 1}")
        payload = step(payload)
        v += 1
    return payload


# --------------------------------------------------------------------------------- постоянные тома (compose: ${DATA_ROOT}/<имя>)
# Что лежит на постоянных томах и как это учитывается в копии конфигурации. Тест сверяет список с deployment/compose.yml: новый том без политики — ошибка.
VOLUMES: dict[str, str] = {
    "postgres": "база данных: настройки и таблицы переносятся логически (по реестру), а не копированием файлов",
    "redis": "служебные данные и сессии (временные): не переносятся",
    "ca": "собранный набор сертификатов (производный файл): пересобирается из сертификатов базы при импорте и при запуске",
    "branding": "изображения оформления: переносятся (см. FILES)",
    "avatars": "фото профилей пользователей: не переносятся (пользователи принадлежат серверу)",
    "chat-files": "вложения чата встреч: данные встреч, не переносятся",
    "recordings": "записи встреч: данные встреч, не переносятся (для них — перенос между хранилищами)",
    "exports": "локальная папка-хранилище материалов: данные, не переносятся; настройки хранилища переносятся",
    "updater": "состояние помощника обновления этого сервера: не переносится",
    "models": "модели распознавания и языковые модели: файлы устанавливаются на сервере отдельно; выбор моделей (настройки) переносится",
}

# --------------------------------------------------------------------------------- файл окружения .env (все ключи .env.example)
# Ключи .env описывают конкретный сервер и не переносятся, но влияют на работу: поэтому они сгруппированы по причинам, а отличия исходного и нового сервера показываются в предпросмотре импорта.
ENV_FILE_PREFIX: dict[str, str] = {
    "COMPOSE_": "параметры развёртывания (compose)", "INSTALL_": "параметры установки", "APP_": "адреса и ключи этой установки", "DATA_": "каталог данных этой установки", "IMAGE_": "версия образов этой установки",
    "LOG_": "журналирование этой установки", "WEB_": "сетевые порты этой установки", "LIVEKIT_": "параметры LiveKit этой установки (ключи, адреса, порты)", "POSTGRES_": "подключение к базе этой установки",
    "REDIS_": "подключение к Redis этой установки", "DOCS_": "служебная документация API этой установки", "INTERNAL_": "служебный секрет этой установки", "LDAP_": "прежние параметры каталога: при первом запуске становятся подключением в базе, которое переносится",
    "COOKIE_": "параметры сессии этой установки", "TRUSTED_": "топология сети этой установки", "SESSION_": "параметры сессии этой установки", "LOGIN_": "защита входа этой установки",
    "DEFAULT_": "значения по умолчанию установки (сроки хранения задаются в настройках и переносятся)", "MEETING_": "параметры встреч этой установки", "ASR_": "службы распознавания этой установки (выбор модели — в настройках, переносится)",
    "GIGAAM_": "источник модели распознавания этой установки", "LLM_": "локальная языковая модель этой установки", "SIP_": "сеть SIP этой установки (подключения — в настройках, переносятся)", "NGINX_": "веб-сервер этой установки",
    "BACKUP_": "каталог резервных копий этого сервера", "MIN_": "пороги проверки ресурсов этого сервера",
}


def env_file_reason(key: str) -> str | None:
    for p, why in ENV_FILE_PREFIX.items():
        if key.startswith(p):
            return why
    return None


_SECRET_HINTS = ("PASSWORD", "SECRET", "TOKEN", "MASTER_KEY", "API_KEY", "DATABASE_URL", "REDIS_URL", "_KEY")


def is_secret_env(name: str) -> bool:
    n = name.upper()
    return any(h in n for h in _SECRET_HINTS)
