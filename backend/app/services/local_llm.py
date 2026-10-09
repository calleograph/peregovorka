"""Локальная языковая модель: каталог моделей, состояние файла и runtime, преобразование настройки «Локальная» в обычный OpenAI-совместимый вызов.

Цепочка: GigaAM → стенограмма → локальная Qwen3 → протокол/резюме. Модель работает в контейнере `llm-local` (llama.cpp, CPU) и отдаёт OpenAI-совместимый
API (`/v1/chat/completions`) ТОЛЬКО во внутренней сети compose без выхода наружу — данные встречи не покидают сервер. Остальная система не знает про runtime:
`LocalLlm.effective()` превращает выбор «Локальная» в обычную `LlmSettings` (openai_compatible + внутренний адрес), поэтому позже можно добавить другую
локальную модель (например, Gemma 3 4B) записью в `LOCAL_MODELS` и заменой файла в `${DATA_ROOT}/models/llm` — без изменения API и протоколов.

Файл модели хранится вне образа (`${DATA_ROOT}/models/llm`, монтируется в backend только для чтения): загружает его установщик/обновление
(scripts/lib/llmlib.sh) или кнопка в админке (через помощника обновлений). Перед использованием проверяются размер и SHA-256 (результат кэшируется по размеру и времени файла).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import Settings
from ..integrations.llm import LlmClient
from .settings import LlmSettings

log = logging.getLogger("app.llm.local")

# Небольшая локальная модель: на длинных встречах ошибается (путает ответственных, сроки), поэтому интерфейс просит проверять результат.
MODEL_NOTE = ("Qwen3 1.7B — небольшая локальная модель: данные не покидают сервер, документ собирается по проверенным пунктам с источниками (протокол — кодом, резюме — по этим пунктам). "
              "Это всё ещё небольшая модель: на длинных встречах возможны пропуски и неточности, важные пункты нужно сверять с записью.")


@dataclass(frozen=True)
class LocalModel:
    id: str                    # внутренний идентификатор (он же alias в llama.cpp и поле model в запросах)
    title: str
    runtime: str               # llama.cpp / GGUF
    file: str
    size_bytes: int
    sha256: str
    context_tokens: int        # размер контекста, с которым запускается runtime
    light: bool                # небольшая модель: показывать предупреждение на длинных входах
    max_input_chars: int       # безопасный размер одного фрагмента входа (символов); больше — разбивается на фрагменты
    warn_input_chars: int      # с какого размера стенограммы показывать предупреждение о качестве
    max_output_tokens: int
    tasks: tuple[str, ...] = field(default=("краткое резюме", "решения", "задачи и ответственные", "протокол по проверенным пунктам"))
    source: str = ""
    note: str = ""
    hidden: bool = False       # снята с вооружения: не скачивается, не показывается и не предлагается; сохранённый выбор молча заменяется основной моделью


QWEN3_17B = LocalModel(
    id="qwen3-1.7b-q4_k_m", title="Qwen3 1.7B Q4_K_M", runtime="llama.cpp (GGUF, CPU)", file="Qwen3-1.7B-Q4_K_M.gguf",
    size_bytes=1_282_439_584, sha256="72c5c3cb38fa32d5256e2fe30d03e7a64c6c79e668ad84057e3bd66e250b24fb", context_tokens=8192, light=True,
    max_input_chars=6_000, warn_input_chars=30_000, max_output_tokens=1500,
    source="huggingface.co/bartowski/Qwen_Qwen3-1.7B-GGUF (Qwen_Qwen3-1.7B-Q4_K_M.gguf)", note=MODEL_NOTE)

# Прежняя Qwen3 0.6B: на протоколах и длинных резюме оказалась слишком слабой (HISTORY.md). Запись оставлена только для старых сохранённых настроек.
QWEN3_06B = LocalModel(
    id="qwen3-0.6b-q4_k_m", title="Qwen3 0.6B Q4_K_M (снята с вооружения)", runtime="llama.cpp (GGUF, CPU)", file="Qwen3-0.6B-Q4_K_M.gguf",
    size_bytes=484_220_320, sha256="9acfc1e001311f34b4252001b626f2e466d592a42065f66571bff3790d4e1b14", context_tokens=8192, light=True,
    max_input_chars=6_000, warn_input_chars=15_000, max_output_tokens=1200, hidden=True,
    source="huggingface.co/bartowski/Qwen_Qwen3-0.6B-GGUF (Qwen_Qwen3-0.6B-Q4_K_M.gguf)", note="Снята с вооружения: слишком слабая для протоколов.")

# Реестр локальных моделей. Новая модель (более крупная) добавляется записью сюда + файл .gguf в каталоге моделей и параметры LLM_MODEL_* в .env:
# API, протоколы и интерфейс от этого не зависят.
LOCAL_MODELS: dict[str, LocalModel] = {QWEN3_17B.id: QWEN3_17B, QWEN3_06B.id: QWEN3_06B}
DEFAULT_LOCAL_MODEL = QWEN3_17B.id
VISIBLE_MODELS = {k: v for k, v in LOCAL_MODELS.items() if not v.hidden}


class LocalLlm:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self._s = settings
        self._transport = transport
        self._hash_cache: dict[str, tuple[int, int, str]] = {}   # путь → (размер, mtime, sha256)

    # ------------------------------------------------------------------ модель
    @property
    def url(self) -> str:
        return self._s.local_llm_url.rstrip("/")

    def url_for(self, m: LocalModel) -> str:
        return self.url

    def model_enabled(self, m: LocalModel) -> bool:
        """Локальная модель включена установкой (LLM_LOCAL_ENABLED)."""
        return self.enabled()

    def enabled(self) -> bool:
        return str(self._s.local_llm_enabled).strip().lower() in ("yes", "true", "1", "on")

    def model(self, model_id: str | None = None) -> LocalModel:
        """Описание модели; для модели по умолчанию имя файла, размер и хеш берутся из окружения (переопределение в .env)."""
        base = LOCAL_MODELS.get(model_id or DEFAULT_LOCAL_MODEL)
        if base is None or base.hidden:         # снятая с вооружения (0.6B) или неизвестная модель в старых настройках → основная
            base = LOCAL_MODELS[DEFAULT_LOCAL_MODEL]
        if base.id != DEFAULT_LOCAL_MODEL:
            return base
        s = self._s
        from dataclasses import replace  # noqa: PLC0415

        return replace(base, file=s.local_llm_model_file or base.file, size_bytes=s.local_llm_model_bytes or base.size_bytes,
                       sha256=s.local_llm_model_sha256 or base.sha256, id=s.local_llm_model_alias or base.id)

    def path(self, m: LocalModel) -> Path:
        return Path(self._s.local_llm_models_dir) / m.file

    # ------------------------------------------------------------ файл модели
    def file_state(self, m: LocalModel) -> dict:
        """Наличие, размер и SHA-256 файла (блокирующе; вызывать в потоке). state: ok | missing | partial | bad_size | bad_hash."""
        p = self.path(m)
        info = {"file": m.file, "size_bytes": None, "expected_bytes": m.size_bytes, "sha256_state": "unchecked", "state": "missing"}
        try:
            st = p.stat()
        except OSError:
            info["state"] = "partial" if Path(str(p) + ".part").exists() else "missing"
            return info
        info["size_bytes"] = st.st_size
        if m.size_bytes and st.st_size != m.size_bytes:
            info["state"] = "bad_size"
            return info
        if not m.sha256 or m.sha256.lower() == "skip":
            info["state"], info["sha256_state"] = "ok", "skipped"
            return info
        key = str(p)
        cached = self._hash_cache.get(key)
        if cached and cached[0] == st.st_size and cached[1] == st.st_mtime_ns:
            got = cached[2]
        else:
            h = hashlib.sha256()
            try:
                with p.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
                        h.update(chunk)
            except OSError:
                info["state"] = "missing"
                return info
            got = h.hexdigest()
            self._hash_cache[key] = (st.st_size, st.st_mtime_ns, got)
        info["sha256_state"] = "ok" if got.lower() == m.sha256.lower() else "mismatch"
        info["state"] = "ok" if info["sha256_state"] == "ok" else "bad_hash"
        return info

    # ------------------------------------------------------------------ runtime
    async def runtime(self, m: LocalModel | None = None) -> dict:
        """Отвечает ли llama.cpp во внутренней сети (короткий запрос /health)."""
        base = self.url_for(m) if m is not None else self.url
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=2.0), transport=self._transport) as c:
                r = await c.get(f"{base}/health")
            if r.status_code == 200:
                return {"reachable": True, "ready": True, "detail": "модель загружена, сервер готов"}
            if r.status_code == 503:
                return {"reachable": True, "ready": False, "detail": "модель загружается в память"}
            return {"reachable": True, "ready": False, "detail": f"ответ HTTP {r.status_code}"}
        except httpx.HTTPError as exc:
            return {"reachable": False, "ready": False, "detail": f"контейнер {(m.service.split(':')[0] if m is not None and m.service else 'llm-local')} недоступен ({type(exc).__name__})"}

    async def models_overview(self) -> list[dict]:
        """Все модели реестра: файл, включена ли на сервере, отвечает ли runtime — для раздела «Языковая модель» (без путей и секретов)."""
        async def one(mid: str, m: LocalModel) -> dict:
            mm = self.model(mid)
            fs = await asyncio.to_thread(self.file_state, mm)
            on = self.model_enabled(mm)
            rt = await self.runtime(mm) if (on and fs["state"] == "ok") else {"reachable": False, "ready": False, "detail": "не запущена" if on else "не включена на сервере"}
            return {"id": mm.id, "title": mm.title, "light": mm.light, "enabled_on_server": on, "size_bytes": mm.size_bytes,
                    "file_state": fs["state"], "runtime": rt, "ready": on and fs["state"] == "ok" and rt["ready"], "note": mm.note}

        return list(await asyncio.gather(*[one(k, v) for k, v in VISIBLE_MODELS.items()]))

    async def status(self, cfg: LlmSettings) -> dict:
        m = self.model(cfg.local_model)
        fs = await asyncio.to_thread(self.file_state, m)
        rt = await self.runtime(m) if fs["state"] == "ok" else {"reachable": False, "ready": False, "detail": "модель не загружена"}
        provider = cfg.effective_provider
        return {
            "enabled_by_install": self.enabled(), "provider": provider,
            "model": {"id": m.id, "title": m.title, "runtime": m.runtime, "light": m.light, "context_tokens": m.context_tokens, "tasks": list(m.tasks),
                      "note": m.note, "source": m.source, "warn_input_chars": m.warn_input_chars},
            "file": fs, "runtime": rt,
            "ready": fs["state"] == "ok" and rt["ready"],
            "endpoint": "внутренний (контейнер llm-local, OpenAI-совместимый /v1), наружу не опубликован; сеть без выхода в интернет",
            "available_models": [{"id": k, "title": v.title, "light": v.light} for k, v in VISIBLE_MODELS.items()],
            "models": await self.models_overview(),
        }

    # ----------------------------------------------------- настройка → вызов
    def effective(self, cfg: LlmSettings) -> tuple[LlmSettings, bool]:
        """(настройки для вызова, локальная ли модель). «Локальная» превращается в обычное OpenAI-совместимое подключение к внутреннему адресу."""
        provider = cfg.effective_provider
        if provider == "off":
            return cfg.model_copy(update={"enabled": False}), False
        if provider != "local":
            return cfg.model_copy(update={"enabled": True}) if cfg.provider == "external" else cfg, False
        m = self.model(cfg.local_model)
        return cfg.model_copy(update={
            "enabled": True, "type": "openai_compatible", "base_url": f"{self.url_for(m)}/v1", "model": m.id, "api_key": "",
            "max_tokens": m.max_output_tokens, "max_tokens_summary": min(cfg.max_tokens_summary or m.max_output_tokens, m.max_output_tokens),
            "max_tokens_protocol": min(cfg.max_tokens_protocol or m.max_output_tokens, m.max_output_tokens), "context_window": m.context_tokens,
            "temperature": min(cfg.temperature, 0.3), "send_temperature": True, "supports_system": True, "supports_json": True,
            "extra_headers": {}, "secret_headers": "", "secret_header_names": [],
            "timeout": max(cfg.timeout, 600), "use_corporate_ca": False, "allow_http": True, "routing_provider": ""}), True

    def client(self, cfg: LlmSettings, *, ca_file: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
               purpose: str = "protocol") -> LlmClient:
        eff, local = self.effective(cfg)
        return LlmClient(eff, ca_file=ca_file, transport=(self._transport or transport) if local else transport, local=local, purpose=purpose)

    def limits(self, cfg: LlmSettings) -> LocalModel | None:
        """Описание модели, если выбрана локальная (для размера фрагментов и предупреждений), иначе None."""
        return self.model(cfg.local_model) if cfg.effective_provider == "local" else None
