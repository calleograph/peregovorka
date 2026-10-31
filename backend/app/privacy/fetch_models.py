"""Получение моделей распознавания имён (сборка образа, CI, стенд разработчика): из колеса natasha 1.6.0 с PyPI берутся только два файла —
векторы navec и модель NER slovnet (MIT). Адрес, размер и SHA-256 закреплены; сам пакет natasha не устанавливается (он тянет pymorphy2,
несовместимый с Python 3.11+). Повторный запуск при совпадении сумм ничего не скачивает.

    python -m app.privacy.fetch_models /opt/peregovorka/nlp
"""
from __future__ import annotations

import hashlib
import io
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

WHEEL_URL = "https://files.pythonhosted.org/packages/32/9c/bb9d33c13564bcc939bb727087ef51b16ed3b49cc3b8fdec07c87b02f1de/natasha-1.6.0-py3-none-any.whl"
WHEEL_SHA256 = "e12b130d05a0b4d2eb6442092ed5b5769a68a311c17f2095b1aa739fb8d6dc78"
FILES = {
    "navec_news_v1_1B_250K_300d_100q.tar": ("natasha/data/emb/navec_news_v1_1B_250K_300d_100q.tar", "f07270833d78523edc5781538d67038e95b43975e4a7ae757c693b687f9cbfca"),
    "slovnet_ner_news_v1.tar": ("natasha/data/model/slovnet_ner_news_v1.tar", "16c3343a6572ddf0e2b2fc1923113de15a7f5ca5bde90e780dd590418665603e"),
}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def ready(target: Path) -> bool:
    return all((target / name).is_file() and _sha(target / name) == sha for name, (_, sha) in FILES.items())


def _download() -> bytes:
    for attempt in range(5):                                     # сборка образа переживает кратковременный сбой сети, как pip (PIP_RETRIES)
        try:
            return urllib.request.urlopen(WHEEL_URL, timeout=120).read()     # noqa: S310 — закреплённый адрес PyPI
        except OSError:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError


def fetch(target: Path, wheel: Path | None = None) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if ready(target):
        return
    data = wheel.read_bytes() if wheel else _download()
    if hashlib.sha256(data).hexdigest() != WHEEL_SHA256:
        raise SystemExit("natasha-1.6.0: контрольная сумма колеса не совпала — файл не принят")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name, (member, sha) in FILES.items():
            blob = z.read(member)
            if hashlib.sha256(blob).hexdigest() != sha:
                raise SystemExit(f"{name}: контрольная сумма не совпала")
            tmp = target / (name + ".part")
            tmp.write_bytes(blob)
            tmp.replace(target / name)


if __name__ == "__main__":
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "/opt/peregovorka/nlp")
    fetch(dest, Path(sys.argv[2]) if len(sys.argv) > 2 else None)
    print(f"модели распознавания имён: {dest} — готово")
