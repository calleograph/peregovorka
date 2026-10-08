"""Глобальные настройки из админки (таблица app_settings).

Группы типизированы pydantic-моделями; секретные поля (пароль SMB, токен обезличивателя, API-ключ LLM)
хранятся зашифрованными (SecretBox, AES-256-GCM, ключ APP_MASTER_KEY) и наружу не отдаются —
только флаг «значение задано». Пустая строка в PUT очищает секрет, отсутствие поля — не меняет.
"""
from __future__ import annotations

import json
import ntpath
import posixpath
import re
from typing import Any, ClassVar, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import AppSetting, utcnow
from ..security.secretbox import SecretBox, SecretBoxError


class SettingsError(Exception):
    pass


class _Group(BaseModel):
    SECRETS: ClassVar[tuple[str, ...]] = ()
    model_config = {"extra": "forbid"}

    @classmethod
    def merge_hint(cls, merged: dict, patch: dict) -> None:
        """Поправка слияния «текущие значения + patch» до проверки (для групп с выводимыми полями). По умолчанию ничего не делает."""


class _StorageTarget(_Group):
    """Куда писать файлы: локальный каталог (внутри смонтированного тома) или сетевой ресурс SMB."""

    SECRETS: ClassVar[tuple[str, ...]] = ("smb_password",)
    enabled: bool = False
    profile_id: str = ""            # профиль хранилища (services/filestore.py); пусто — старый вариант: адрес ниже, прямо в этой группе
    mode: Literal["local", "smb"] = "local"
    local_path: str = "/data/exports"
    smb_server: str = ""
    smb_share: str = ""
    smb_base_path: str = ""
    smb_username: str = ""
    smb_domain: str = ""
    smb_password: str = ""

    @field_validator("smb_server")
    @classmethod
    def _server(cls, v: str) -> str:
        if v and not re.fullmatch(r"[A-Za-z0-9._-]{1,253}", v):
            raise ValueError("smb_server: имя хоста или IP без схемы и слэшей")
        return v

    @field_validator("smb_share")
    @classmethod
    def _share(cls, v: str) -> str:
        if v and not re.fullmatch(r"[^\\/:*?\"<>|]{1,80}", v):
            raise ValueError("smb_share: имя ресурса без слэшей")
        return v

    @field_validator("smb_base_path")
    @classmethod
    def _base(cls, v: str) -> str:
        v = v.strip().strip("/\\")
        if ".." in re.split(r"[\\/]", v):
            raise ValueError("smb_base_path: «..» запрещено")
        return v

    @field_validator("local_path")
    @classmethod
    def _local(cls, v: str) -> str:
        v = v.strip()
        if not (posixpath.isabs(v) or ntpath.isabs(v)) or ".." in re.split(r"[\\/]", v):
            raise ValueError("local_path: абсолютный путь без «..»")
        return v.rstrip("/\\") or v

    @field_validator("profile_id")
    @classmethod
    def _profile(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", v):
            raise ValueError("profile_id: идентификатор хранилища")
        return v.lower()

    @model_validator(mode="after")
    def _smb_required(self):
        if self.enabled and not self.profile_id and self.mode == "smb" and not (self.smb_server and self.smb_share):
            raise ValueError("Для SMB укажите сервер и общий ресурс")
        return self


class StorageSettings(_StorageTarget):
    """Хранилище ПРОТОКОЛОВ (текст)."""

    export_transcript: bool = True
    export_summary: bool = True


class AudioStorageSettings(_StorageTarget):
    """Хранилище ЗАПИСЕЙ аудио. Если выключено — записи остаются на локальном томе приложения.
    При недоступности хранилища запись не теряется: остаётся локально и выгружается повторно."""

    local_path: str = "/data/exports/audio"
    keep_local_copy: bool = True


FORBIDDEN_EXTENSIONS = frozenset({"exe", "bat", "cmd", "com", "scr", "msi", "dll", "ps1", "vbs", "js", "jar", "sh", "html", "htm", "svg", "xhtml", "php", "lnk"})
DEFAULT_ATTACHMENT_TYPES = "png,jpg,jpeg,gif,webp,pdf,txt,md,csv,docx,xlsx,pptx,odt,ods,odp,zip,drawio"


class ChatFilesSettings(_Group):
    """Вложения чата. Файлы лежат в выбранном хранилище (подпапка Chat/files) или, если оно не выбрано, на локальном диске приложения;
    в базе — только метаданные. Исполняемые и «активные» типы (exe, js, html, svg …) запрещены всегда."""

    enabled: bool = True
    profile_id: str = ""
    max_size_mb: int = Field(default=25, ge=1, le=100)
    max_files_per_message: int = Field(default=5, ge=1, le=20)
    allowed_extensions: str = DEFAULT_ATTACHMENT_TYPES   # через запятую, без точек

    @field_validator("profile_id")
    @classmethod
    def _profile(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not re.fullmatch(r"[0-9a-fA-F-]{32,36}", v):
            raise ValueError("profile_id: идентификатор хранилища")
        return v.lower()

    @field_validator("allowed_extensions")
    @classmethod
    def _ext(cls, v: str) -> str:
        items = [e.strip().lower().lstrip(".") for e in re.split(r"[,\s;]+", v or "") if e.strip()]
        for e in items:
            if not re.fullmatch(r"[a-z0-9]{1,10}", e):
                raise ValueError(f"allowed_extensions: «{e}» — расширение латиницей и цифрами")
        bad = sorted(set(items) & FORBIDDEN_EXTENSIONS)
        if bad:
            raise ValueError("Эти типы запрещены по соображениям безопасности: " + ", ".join(bad))
        return ",".join(dict.fromkeys(items))

    def extensions(self) -> set[str]:
        return {e for e in self.allowed_extensions.split(",") if e}


class AnonymizerSettings(_Group):
    """API обезличивания (как в прежнем проекте): DocClean либо произвольный JSON API. Fail closed."""

    SECRETS: ClassVar[tuple[str, ...]] = ("token",)
    enabled: bool = False
    profile: Literal["docclean", "generic"] = "docclean"
    base_url: str = ""
    docclean_mode: Literal["ai_ready", "full", "personal_corporate", "personal"] = "ai_ready"
    docclean_groups: str = ""  # через запятую из: pdn, corporate, secrets, network, other
    connect_timeout: int = Field(default=5, ge=1, le=60)
    timeout: int = Field(default=60, ge=1, le=600)
    max_chunk_chars: int = Field(default=20000, ge=500, le=1_000_000)
    use_corporate_ca: bool = True
    allow_http: bool = False
    token: str = ""
    # generic
    endpoint: str = "/anonymize"
    request_field: str = "text"
    response_field: str = "anonymized_text"
    status_field: str = ""
    status_ok_value: str = ""
    auth_type: Literal["none", "bearer", "header", "basic"] = "bearer"
    auth_header_name: str = "X-API-Key"
    auth_username: str = ""
    extra_body: str = ""

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("base_url: http(s)://хост[:порт][/путь]")
        return v.rstrip("/")

    @field_validator("docclean_groups")
    @classmethod
    def _groups(cls, v: str) -> str:
        allowed = {"pdn", "corporate", "secrets", "network", "other"}
        items = [x.strip() for x in v.split(",") if x.strip()]
        bad = [x for x in items if x not in allowed]
        if bad:
            raise ValueError(f"Недопустимые группы: {', '.join(bad)}")
        return ",".join(items)

    @field_validator("extra_body")
    @classmethod
    def _extra(cls, v: str) -> str:
        if v.strip():
            try:
                if not isinstance(json.loads(v), dict):
                    raise ValueError
            except ValueError:
                raise ValueError("extra_body: JSON-объект") from None
        return v

    @model_validator(mode="after")
    def _check(self):
        if self.enabled:
            if not self.base_url:
                raise ValueError("Укажите base_url сервиса обезличивания")
            if self.base_url.startswith("http://") and not self.allow_http:
                raise ValueError("Разрешён только https:// (http — лишь при явном «allow_http»)")
        return self


class LlmSettings(_Group):
    """Внешняя/внутренняя LLM для краткого протокола. Данные уходят ТОЛЬКО после обезличивания."""

    SECRETS: ClassVar[tuple[str, ...]] = ("api_key",)
    enabled: bool = False
    # Режим: local — встроенная локальная модель (Qwen3 0.6B, данные не покидают сервер); external — внешняя/внутренняя API-модель по полям ниже;
    # off — выключена. None (настройки до 0.5.0): external, если включена (enabled), иначе off.
    provider: Literal["local", "external", "off"] | None = None
    local_model: str = "qwen3-0.6b-q4_k_m"
    type: Literal["openai", "anthropic", "openai_compatible"] = "openai_compatible"
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    max_tokens: int = Field(default=4000, ge=64, le=64000)
    temperature: float = Field(default=0.2, ge=0, le=2)
    timeout: int = Field(default=180, ge=5, le=1800)
    use_corporate_ca: bool = True
    allow_http: bool = False
    routing_provider: str = ""  # для шлюзов вида polza.ai: provider.only

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if v and not re.match(r"^https?://[^\s/]+", v):
            raise ValueError("base_url: http(s)://хост[:порт][/путь]")
        return v.rstrip("/")

    @classmethod
    def merge_hint(cls, merged: dict, patch: dict) -> None:
        # клиент прежней версии меняет только флаг «включена» — режим выводится из него заново, а не берётся из ранее выведенного значения
        if "enabled" in patch and "provider" not in patch:
            merged["provider"] = None

    @property
    def effective_provider(self) -> str:
        return self.provider or ("external" if self.enabled else "off")

    @model_validator(mode="after")
    def _check(self):
        # режим — единственный источник правды; флаг enabled выводится из него (старые настройки без режима: включена → внешняя, иначе выключена)
        if self.provider is None:
            self.provider = "external" if self.enabled else "off"
        self.enabled = self.provider != "off"
        if self.provider in ("local", "off"):
            return self                      # внешние поля не обязательны
        if self.enabled or self.provider == "external":
            if not self.model:
                raise ValueError("Укажите модель")
            if self.type == "openai_compatible" and not self.base_url:
                raise ValueError("Для OpenAI-совместимого API укажите base_url")
            if self.base_url.startswith("http://") and not self.allow_http:
                raise ValueError("Разрешён только https:// (http — лишь при явном «allow_http»)")
        return self


class JournalSettings(_StorageTarget):
    """Журнал событий (входы, подключения, ошибки устройств и сети, действия). Хранится `retention_days` дней и очищается автоматически.
    `enabled` + `mode`/путь — ДУБЛИРОВАНИЕ во внешнее хранилище (каталог или SMB): так журнал не занимает место на сервере.
    `keep_local=False` — в базе сервера события не хранятся вовсе (смотреть можно только во внешних файлах); оба выключены — журнал не ведётся."""

    local_path: str = "/data/exports/logs"
    retention_days: int = Field(default=30, ge=1, le=3650)
    keep_local: bool = True
    min_level: Literal["debug", "info", "warn", "error"] = "info"
    external_flush_seconds: int = Field(default=60, ge=5, le=3600)


DEFAULT_PROTOCOL_INSTRUCTION = (
    "Сформировать официальный протокол совещания. Выделить тему, участников, обсуждавшиеся вопросы, принятые решения, "
    "поручения, ответственных и сроки. Не придумывать отсутствующие сведения."
)
DEFAULT_SUMMARY_INSTRUCTION = (
    "Кратко (не более 10 строк) изложить суть встречи: о чём говорили, ключевые решения и поручения. Не придумывать отсутствующие сведения."
)


class ProtocolSettings(_Group):
    instructions: str = Field(default=DEFAULT_PROTOCOL_INSTRUCTION, max_length=20000)
    summary_instructions: str = Field(default=DEFAULT_SUMMARY_INSTRUCTION, max_length=20000)
    auto_generate: bool = False
    auto_summary: bool = False
    max_input_chars: int = Field(default=60000, ge=2000, le=1_000_000)


class ScreenSettings(_Group):
    """Профиль демонстрации экрана по умолчанию для всех комнат."""

    profile: Literal["sharp", "balanced", "motion"] = "sharp"  # sharp: текст/слайды; motion: видео/анимация
    share_audio: bool = False
    one_sharer_at_a_time: bool = False


class AsrModelSettings(_Group):
    """Какая модель распознавания активна. Пусто — модель по умолчанию из .env (ASR_MODEL_ID). Список моделей ведёт сам ASR (каталог)."""

    active_model: str = ""
    # Параметры VAD (деление речи на реплики). Пусто — значение из .env (ASR_VAD_*). Применяются к НОВЫМ трекам без перезапуска ASR.
    vad_threshold: float | None = Field(default=None, gt=0, lt=1)
    vad_end_silence_ms: int | None = Field(default=None, ge=100, le=5000)
    vad_min_speech_ms: int | None = Field(default=None, ge=32, le=5000)
    vad_pad_ms: int | None = Field(default=None, ge=0, le=1000)
    vad_max_segment_seconds: float | None = Field(default=None, ge=3.0, le=25.0)

    @field_validator("active_model")
    @classmethod
    def _id(cls, v: str) -> str:
        v = v.strip().lower()
        if v and not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", v):
            raise ValueError("active_model: идентификатор модели из каталога (латиница, цифры, . _ -)")
        return v


class GeneralSettings(_Group):
    timezone: str = "UTC"  # для имён папок протоколов и подписей времени
    post_meeting_access_minutes: int = Field(default=120, ge=1, le=1440)  # сколько участник, оставшийся на странице завершённой встречи, сохраняет доступ
    default_text_retention_days: int | None = Field(default=None, ge=0, le=36500)
    default_audio_retention_days: int | None = Field(default=None, ge=0, le=36500)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Неизвестный часовой пояс (пример: Europe/Moscow)") from None
        return v


def _clean_dn_list(v: list[str], what: str) -> list[str]:
    out: list[str] = []
    for item in v or []:
        item = str(item).strip()
        if not item:
            continue
        if len(item) > 600 or "=" not in item:
            raise ValueError(f"{what}: нужен DN группы (например, CN=Admins,OU=Groups,DC=example,DC=local)")
        if item.lower() not in [x.lower() for x in out]:
            out.append(item)
    if len(out) > 50:
        raise ValueError(f"{what}: не больше 50 групп")
    return out


class AccessSettings(_Group):
    """Кто входит в систему из каталога: группы администраторов и (необязательно) группы, которым разрешён вход. Локальный администратор от этого не зависит."""

    admin_groups: list[str] = Field(default_factory=list)
    user_groups: list[str] = Field(default_factory=list)   # пусто — вход разрешён всем доменным пользователям подключений

    @field_validator("admin_groups")
    @classmethod
    def _admin(cls, v: list[str]) -> list[str]:
        return _clean_dn_list(v, "Группы администраторов")

    @field_validator("user_groups")
    @classmethod
    def _users(cls, v: list[str]) -> list[str]:
        return _clean_dn_list(v, "Группы доступа")


class SetupSettings(_Group):
    """Мастер первоначальной настройки: после завершения или пропуска больше не показывается автоматически."""

    completed: bool = False
    skipped: list[str] = Field(default_factory=list)


class MailPolicySettings(_Group):
    """Глобальные правила отправки материалов встреч (SMTP-реквизиты — в профилях почты). Руководитель комнаты выбирает только «что и кому»."""

    max_attachment_mb: int = Field(default=10, ge=1, le=50)       # крупнее — вместо вложения ссылка на материал в приложении
    attach_format: Literal["docx", "pdf", "txt", "md"] = "docx"
    allowed_domains: str = ""                                      # через запятую; пусто — любые адреса
    max_attempts: int = Field(default=4, ge=1, le=10)
    retry_minutes: int = Field(default=5, ge=1, le=240)            # задержка до первого повтора (дальше удваивается)
    keep_days: int = Field(default=90, ge=1, le=3650)              # сколько хранить записи об отправке
    subject_prefix: str = Field(default="", max_length=60)

    @field_validator("allowed_domains")
    @classmethod
    def _domains(cls, v: str) -> str:
        items = [d.strip().lower().lstrip("@") for d in re.split(r"[,\s;]+", v or "") if d.strip()]
        for d in items:
            if not re.fullmatch(r"[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?", d):
                raise ValueError(f"Домен «{d}»: латиница, цифры, точка и дефис (например, example.local)")
        return ",".join(dict.fromkeys(items))

    def domains(self) -> list[str]:
        return [d for d in self.allowed_domains.split(",") if d]


class StorageSyncSettings(_Group):
    """Периодическая сверка метаданных базы с реальным содержимым хранилищ (в фоне, пакетами)."""

    enabled: bool = True
    interval_hours: int = Field(default=12, ge=1, le=168)
    batch_size: int = Field(default=500, ge=50, le=5000)         # объектов за один проход перед паузой
    guard_percent: int = Field(default=95, ge=50, le=100)        # если «пропало» не меньше стольких процентов (и не менее 10 файлов) — похоже на сбой, не применять без подтверждения


GROUPS: dict[str, type[_Group]] = {
    "storage": StorageSettings,
    "audio_storage": AudioStorageSettings,
    "chat_files": ChatFilesSettings,
    "anonymizer": AnonymizerSettings,
    "llm": LlmSettings,
    "protocol": ProtocolSettings,
    "screen": ScreenSettings,
    "general": GeneralSettings,
    "asr": AsrModelSettings,
    "journal": JournalSettings,
    "access": AccessSettings,
    "setup": SetupSettings,
    "mail_policy": MailPolicySettings,
    "storage_sync": StorageSyncSettings,
}


class SettingsService:
    def __init__(self, secretbox: SecretBox | None):
        self._box = secretbox

    @property
    def box(self) -> SecretBox | None:
        return self._box

    # ----------------------------------------------------------------- низкий уровень
    async def _rows(self, db: AsyncSession, group: str) -> dict[str, AppSetting]:
        rows = (await db.execute(select(AppSetting).where(AppSetting.key.like(f"{group}.%")))).scalars().all()
        return {r.key[len(group) + 1:]: r for r in rows}

    def _decode(self, row: AppSetting) -> Any:
        if row.is_secret:
            if not row.value:
                return ""
            if self._box is None:
                raise SettingsError("APP_MASTER_KEY не задан — секреты недоступны")
            try:
                return self._box.decrypt(row.value, aad=row.key.encode())
            except SecretBoxError as exc:
                raise SettingsError(f"Не удалось расшифровать {row.key}: {exc}") from None
        return json.loads(row.value)

    # --------------------------------------------------------------------- чтение
    async def get(self, db: AsyncSession, group: str) -> _Group:
        """Группа целиком, СЕКРЕТЫ В ОТКРЫТОМ ВИДЕ — только для серверного использования."""
        model = GROUPS[group]
        rows = await self._rows(db, group)
        data = {}
        for name, row in rows.items():
            if name in model.model_fields:
                try:
                    data[name] = self._decode(row)
                except SettingsError:
                    if name in model.SECRETS:
                        data[name] = ""
                    else:
                        raise
        try:
            return model(**data)
        except ValueError:
            # сохранённые значения устарели относительно схемы — берём допустимые по одному
            ok = {}
            for k, v in data.items():
                try:
                    model(**{k: v})
                    ok[k] = v
                except ValueError:
                    continue
            return model.model_construct(**{**model().model_dump(), **ok})

    async def public(self, db: AsyncSession, group: str) -> dict:
        """Для админки/UI: секреты заменены на {name}_set: bool."""
        model = GROUPS[group]
        obj = await self.get(db, group)
        out = obj.model_dump()
        for name in model.SECRETS:
            out[f"{name}_set"] = bool(out.pop(name, ""))
        return out

    # --------------------------------------------------------------------- запись
    async def preview(self, db: AsyncSession, group: str, patch: dict) -> _Group:
        """Результат применения patch с валидацией — БЕЗ сохранения."""
        model = GROUPS[group]
        unknown = [k for k in patch if k not in model.model_fields]
        if unknown:
            raise SettingsError(f"Неизвестные поля: {', '.join(unknown)}")
        merged = (await self.get(db, group)).model_dump()
        for k, v in patch.items():
            if k in model.SECRETS and v is None:
                continue  # None = «не менять»
            merged[k] = v
        model.merge_hint(merged, patch)
        try:
            return model(**merged)
        except ValueError as exc:
            raise SettingsError(_first_error(exc)) from None

    async def update(self, db: AsyncSession, group: str, patch: dict, *, actor: str) -> list[str]:
        """Применяет частичное обновление, валидируя группу целиком. Возвращает имена изменённых полей."""
        model = GROUPS[group]
        new = await self.preview(db, group, patch)
        rows = await self._rows(db, group)
        changed: list[str] = []
        for name, raw in patch.items():
            if name in model.SECRETS and raw is None:
                continue
            val = getattr(new, name)
            key = f"{group}.{name}"
            is_secret = name in model.SECRETS
            if is_secret and val and self._box is None:
                raise SettingsError("APP_MASTER_KEY не задан — секреты сохранить нельзя")
            row = rows.get(name)
            if is_secret:
                stored = self._box.encrypt(val, aad=key.encode()) if val else ""
                same = row is not None and val == self._safe_secret(row)
            else:
                stored = json.dumps(val, ensure_ascii=False)
                same = row is not None and row.value == stored
            if same:
                continue
            if row is None:
                db.add(AppSetting(key=key, value=stored, is_secret=is_secret, updated_by=actor))
            else:
                row.value, row.updated_by, row.updated_at = stored, actor, utcnow()
            changed.append(name)
        await db.commit()
        return changed

    def _safe_secret(self, row: AppSetting) -> str:
        try:
            return self._decode(row) if row.value else ""
        except SettingsError:
            return "\0"


def _first_error(exc: ValueError) -> str:
    errs = getattr(exc, "errors", None)
    if callable(errs):
        e = errs()[0]
        msg = str(e.get("msg", "")).removeprefix("Value error, ")
        loc = ".".join(str(x) for x in e.get("loc", ()))
        return msg if (not loc or msg.startswith(f"{loc}:")) else f"{loc}: {msg}"
    return str(exc)
