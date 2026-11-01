"""Текст из файла для лаборатории обезличивания: TXT/MD/CSV/LOG/JSON, DOCX, ODT, PDF (текстовый слой), HTML. Всё в памяти: файл на диск не пишется.

Сканированные страницы PDF (изображение без текстового слоя) распознаванием (OCR) пока не обрабатываются. Документ с такой страницей отклоняется целиком:
её содержимое не проверено, поэтому «обезличенный» результат по остальным страницам выглядел бы чистым, не будучи им.
"""
from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".xml", ".yml", ".yaml", ".ini", ".conf", ".env"}
SUPPORTED = sorted(TEXT_EXT | {".docx", ".odt", ".pdf", ".html", ".htm"})
MAX_ZIP_RATIO = 100              # защита от «zip-бомб» в DOCX/ODT: распакованное не больше чем в 100 раз
MAX_UNZIPPED = 50 * 1024 * 1024


class ExtractError(ValueError):
    pass


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _zip_member(data: bytes, name: str) -> bytes:
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        info = z.getinfo(name)
    except (zipfile.BadZipFile, KeyError):
        raise ExtractError("файл повреждён или это не документ этого формата") from None
    if info.file_size > MAX_UNZIPPED or (info.compress_size and info.file_size / info.compress_size > MAX_ZIP_RATIO):
        raise ExtractError("документ распаковывается в подозрительно большой объём — обработка остановлена")
    return z.read(name)


def _xml_text(xml: bytes, para_tags: tuple[str, ...]) -> str:
    try:
        root = ElementTree.fromstring(xml)                      # стандартный разборщик Python не загружает внешние сущности
    except ElementTree.ParseError:
        raise ExtractError("не удалось разобрать содержимое документа") from None
    out: list[str] = []
    for el in root.iter():
        if el.tag.rsplit("}", 1)[-1] in para_tags:
            out.append("".join(el.itertext()))
    return "\n".join(out)


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def extract(name: str, data: bytes) -> tuple[str, dict]:
    """(текст, сведения: формат, страниц, примечание). ExtractError — понятная причина отказа."""
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext in TEXT_EXT:
        return _decode(data), {"format": ext[1:].upper()}
    if ext == ".docx":
        return _xml_text(_zip_member(data, "word/document.xml"), ("p",)), {"format": "DOCX"}
    if ext == ".odt":
        return _xml_text(_zip_member(data, "content.xml"), ("p", "h")), {"format": "ODT"}
    if ext in (".html", ".htm"):
        p = _Html()
        p.feed(_decode(data))
        return re.sub(r"\n{3,}", "\n\n", "".join(p.parts)).strip(), {"format": "HTML"}
    if ext == ".pdf":
        try:
            from pypdf import PdfReader  # noqa: PLC0415

            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ExtractError("PDF защищён паролем")
            pages, scans = [], []
            for i, page in enumerate(reader.pages, 1):
                t = page.extract_text() or ""
                pages.append(t)
                if len(t.strip()) < 20:
                    try:
                        has_image = len(page.images) > 0
                    except Exception:  # noqa: BLE001
                        has_image = True                    # не удалось проверить — считаем непроверенной
                    if has_image:
                        scans.append(i)
        except ExtractError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ExtractError(f"PDF не читается ({type(exc).__name__})") from None
        if scans:
            listed = ", ".join(map(str, scans[:10])) + ("…" if len(scans) > 10 else "")
            raise ExtractError(f"Страницы без текстового слоя (сканы): {listed} из {len(pages)}. Их содержимое не проверено, поэтому результат не выдаётся. "
                               "Распознавание сканов (OCR) пока не выполняется — проверьте документ в текстовом виде.")
        return "\n\n".join(pages).strip(), {"format": "PDF", "pages": len(pages)}
    raise ExtractError("Формат не поддерживается. Поддерживаются: " + ", ".join(SUPPORTED))
