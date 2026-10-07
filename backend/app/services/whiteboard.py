"""Общая доска встречи: проверка и описание схем draw.io (mxfile XML).

Сам редактор (draw.io) работает в браузере; backend хранит схему как XML (её можно открыть и продолжить редактировать), ничего не
рисует и не «понимает» схему глубже, чем нужно для двух вещей:
  1. безопасно принять XML от клиента (размер, запрет DOCTYPE/ENTITY, корректный корень);
  2. получить текстовое описание схемы для LLM (блоки, подписи, связи) и счётчик элементов («доска использовалась»).
"""
from __future__ import annotations

import base64
import html
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass

MAX_XML_CHARS = 3_000_000
MAX_PATCH_CHARS = 400_000
MAX_DECOMPRESSED = 8_000_000
MAX_DESCRIPTION_CHARS = 20_000

EMPTY_XML = ('<mxfile><diagram id="p1" name="Страница 1"><mxGraphModel><root><mxCell id="0"/>'
             '<mxCell id="1" parent="0"/></root></mxGraphModel></diagram></mxfile>')

_FORBIDDEN = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
_BR = re.compile(r"<\s*(br|/p|/div|/li)\s*/?>", re.IGNORECASE)
_WS = re.compile(r"[ \t\r\f\v]+")


class WhiteboardError(ValueError):
    pass


def _decode_diagram(text: str) -> str | None:
    """Сжатое содержимое <diagram>: base64 → raw deflate → URL-кодированный XML. None — не получилось."""
    try:
        raw = base64.b64decode(text.strip(), validate=False)
        d = zlib.decompressobj(-15)
        out = d.decompress(raw, MAX_DECOMPRESSED)
        if d.unconsumed_tail:
            return None  # распаковка превысила предел — «бомба»
        return urllib.parse.unquote(out.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def parse(xml: str) -> ET.Element:
    """Разбор XML схемы с защитой: без DOCTYPE/ENTITY (нет расширения сущностей), ограничение размера, корень mxfile/mxGraphModel."""
    if not isinstance(xml, str) or not xml.strip():
        raise WhiteboardError("Пустая схема")
    if len(xml) > MAX_XML_CHARS:
        raise WhiteboardError("Схема слишком велика")
    if _FORBIDDEN.search(xml):
        raise WhiteboardError("Недопустимое содержимое схемы")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        raise WhiteboardError("Схема не является корректным XML") from None
    if root.tag not in ("mxfile", "mxGraphModel"):
        raise WhiteboardError("Это не схема draw.io")
    return root


def validate(xml: str) -> str:
    parse(xml)
    return xml


def validate_patch(patch: object) -> object:
    """Патч diffSync — JSON-структура draw.io; принимаем только объект/массив ограниченного размера (содержимое не интерпретируем)."""
    import json  # noqa: PLC0415

    if not isinstance(patch, (dict, list)):
        raise WhiteboardError("Некорректный патч")
    if len(json.dumps(patch, ensure_ascii=False)) > MAX_PATCH_CHARS:
        raise WhiteboardError("Патч слишком велик")
    return patch


def _plain(value: str | None) -> str:
    """Подпись фигуры: draw.io хранит HTML (html=1) — переводим в обычный текст, сохраняя переносы."""
    if not value:
        return ""
    text = _BR.sub("\n", value)
    text = html.unescape(_TAG.sub("", text))
    lines = [_WS.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


@dataclass
class Description:
    text: str
    shapes: int      # блоков + связей


def _pages(root: ET.Element) -> list[tuple[str, ET.Element]]:
    if root.tag == "mxGraphModel":
        return [("Схема", root)]
    out: list[tuple[str, ET.Element]] = []
    for d in root.findall("diagram"):
        name = d.get("name") or "Страница"
        model = d.find("mxGraphModel")
        if model is None and (d.text or "").strip():
            decoded = _decode_diagram(d.text or "")
            if decoded and not _FORBIDDEN.search(decoded):
                try:
                    model = ET.fromstring(decoded)
                except ET.ParseError:
                    model = None
        if model is not None and model.tag == "mxGraphModel":
            out.append((name, model))
    return out


def describe(xml: str) -> Description:
    """Текстовое описание схемы для LLM: блоки с подписями и связи «от → к». Рисунки без текста перечисляются как «фигура без подписи»."""
    root = parse(xml)
    chunks: list[str] = []
    total = 0
    for page_name, model in _pages(root):
        cells = {c.get("id"): c for c in model.iter("mxCell") if c.get("id") is not None}
        vertices, edges = [], []
        for cid, c in cells.items():
            if cid in ("0", "1"):
                continue
            if c.get("edge") == "1":
                edges.append(c)
            elif c.get("vertex") == "1":
                vertices.append(c)

        def label(cid: str | None) -> str:
            c = cells.get(cid or "")
            txt = _plain(c.get("value")) if c is not None else ""
            return f"«{txt.replace(chr(10), ' / ')}»" if txt else "(фигура без подписи)"

        total += len(vertices) + len(edges)
        lines = [f"Страница «{page_name}»: блоков {len(vertices)}, связей {len(edges)}."]
        texts = [_plain(v.get("value")) for v in vertices]
        for t in texts:
            if t:
                lines.append("- блок: " + t.replace("\n", " / "))
        unlabeled = sum(1 for t in texts if not t)
        if unlabeled:
            lines.append(f"- фигур без подписи: {unlabeled}")
        for e in edges:
            cap = _plain(e.get("value"))
            lines.append(f"- связь: {label(e.get('source'))} → {label(e.get('target'))}" + (f" (подпись: {cap.replace(chr(10), ' / ')})" if cap else ""))
        chunks.append("\n".join(lines))
    text = "\n\n".join(chunks)
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[:MAX_DESCRIPTION_CHARS] + "\n… (описание схемы сокращено)"
    return Description(text, total)
