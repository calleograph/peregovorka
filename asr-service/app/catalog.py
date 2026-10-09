"""Каталог ASR-моделей: что можно выбрать в админке и каким runtime это запускается.

Расширяемость без переделки интерфейса: модель описывается данными (id, название, runtime, файлы). Новый вариант (Q8_0, ONNX, другая
семья) добавляется либо записью в DEFAULT_CATALOG, либо файлом `catalog.json` рядом с весами (каталог ASR_MODEL_DIR) — админка
покажет его автоматически. Новый runtime регистрируется в `runtimes.py` (одна функция-фабрика); интерфейс и менеджер не меняются.
Формат catalog.json — список объектов с полями ModelSpec, например:
  [{"id": "gigaam-v3-e2e-rnnt-q8_0", "title": "GigaAM v3 e2e RNNT — Q8_0", "runtime": "gguf", "quant": "Q8_0",
    "files": ["gigaam-v3-e2e-rnnt-Q8_0.gguf"]}]
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("asr.catalog")

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
FULL_ID = "gigaam-v3-e2e-rnnt-full"
Q5_ID = "gigaam-v3-e2e-rnnt-q5_k_m"


@dataclass(frozen=True)
class ModelSpec:
    id: str
    title: str
    runtime: str                      # pytorch | gguf | (onnx и др. — регистрируются в runtimes.py)
    files: tuple[str, ...]            # имена файлов относительно ASR_MODEL_DIR
    family: str = "gigaam"
    device: str = "cpu"
    quant: str = ""                   # "" (полная точность) | Q5_K_M | Q8_0 …
    description: str = ""
    params: dict = field(default_factory=dict)   # параметры runtime (например model_name для PyTorch)

    def public(self) -> dict:
        return {"id": self.id, "title": self.title, "runtime": self.runtime, "family": self.family, "device": self.device,
                "quant": self.quant or "full", "description": self.description, "files": list(self.files)}


def default_catalog(model_name: str = "v3_e2e_rnnt", *, include_quantized: bool = False) -> list[ModelSpec]:
    """Штатная модель распознавания — только полная GigaAM v3 e2e RNNT. Квантованная Q5_K_M снята с вооружения (хуже распознаёт и практической пользы не дала):
    она не входит в каталог, не скачивается и не предлагается; код runtime GGUF оставлен для возможных будущих моделей (catalog.json)."""
    specs = [
        ModelSpec(
            id=FULL_ID, title="GigaAM v3 e2e RNNT — Full", runtime="pytorch", quant="",
            files=(f"{model_name}.ckpt", f"{model_name}_tokenizer.model"), params={"model_name": model_name},
            description="Полная точность, PyTorch. Штатная модель распознавания речи.",
        ),
    ]
    if include_quantized:
        specs.append(ModelSpec(
            id=Q5_ID, title="GigaAM v3 e2e RNNT — Q5_K_M", runtime="gguf", quant="Q5_K_M",
            files=("gigaam-v3-e2e-rnnt-Q5_K_M.gguf",),
            description="Компактная квантованная модель (GGUF, движок transcribe.cpp встроен в образ ASR) для снижения нагрузки на CPU и ускорения распознавания.",
        ))
    return specs


def load_catalog(model_dir: str | Path, model_name: str = "v3_e2e_rnnt") -> list[ModelSpec]:
    """Встроенный каталог + необязательный catalog.json из каталога моделей. Некорректные записи пропускаются с предупреждением."""
    specs = {s.id: s for s in default_catalog(model_name)}
    extra = Path(model_dir) / "catalog.json"
    if extra.is_file():
        try:
            raw = json.loads(extra.read_text(encoding="utf-8"))
            for item in raw if isinstance(raw, list) else []:
                try:
                    sid = str(item["id"]).strip().lower()
                    if not ID_RE.match(sid):
                        raise ValueError("id: латиница, цифры, . _ -")
                    files = tuple(str(f) for f in item["files"])
                    if not files or any("/" in f or "\\" in f or f.startswith(".") for f in files):
                        raise ValueError("files: имена файлов без путей")
                    specs[sid] = ModelSpec(
                        id=sid, title=str(item.get("title") or sid), runtime=str(item["runtime"]), files=files,
                        family=str(item.get("family", "gigaam")), device=str(item.get("device", "cpu")), quant=str(item.get("quant", "")),
                        description=str(item.get("description", "")), params=dict(item.get("params", {})))
                except (KeyError, ValueError, TypeError) as exc:
                    log.warning("Запись catalog.json пропущена", extra={"error": str(exc)[:200]})
        except (OSError, ValueError) as exc:
            log.warning("catalog.json не прочитан", extra={"error": str(exc)[:200]})
    return list(specs.values())


@dataclass(frozen=True)
class FileStatus:
    present: bool
    missing: tuple[str, ...]
    size_bytes: int


def file_status(spec: ModelSpec, model_dir: str | Path) -> FileStatus:
    base = Path(model_dir)
    missing, size = [], 0
    for f in spec.files:
        p = base / f
        if p.is_file():
            size += p.stat().st_size
        else:
            missing.append(f)
    return FileStatus(not missing, tuple(missing), size)
