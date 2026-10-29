"""Тесты scripts/check_download_links.py без сети: подставной opener вместо urllib. Запуск: python3 -I tests/scripts/check_download_links_test.py"""
import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_download_links as c  # noqa: E402


class Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def opener_for(releases, fail_first=0):
    calls = {"n": 0}

    def op(req, timeout=0):
        calls["n"] += 1
        if calls["n"] <= fail_first:
            raise urllib.error.URLError("сеть")
        tag = req.full_url.rsplit("/", 1)[-1]
        if tag not in releases:
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
        return Resp(json.dumps(releases[tag]).encode())
    op.calls = calls
    return op


OK = {"desktop-v1.3.1": {"draft": False, "assets": [{"name": "Peregovorka-Setup-1.3.1.exe", "size": 10, "state": "uploaded"}]},
      "android-v0.1.1": {"draft": False, "assets": [{"name": "Peregovorka-0.1.1-android-debugkey.apk", "size": 10, "state": "uploaded"}]}}


def test_all():
    assert c.check("o/r", "1.3.1", "0.1.1", opener_for(OK), pause=0) == []
    # выпуска Android нет
    p = c.check("o/r", "1.3.1", "0.1.2", opener_for(OK), pause=0)
    assert len(p) == 1 and "android-v0.1.2" in p[0] and "нет" in p[0]
    # нет установщика Windows с нужным именем
    bad = {**OK, "desktop-v1.3.1": {"draft": False, "assets": [{"name": "Peregovorka-1.3.1-x64.msi", "size": 5}]}}
    assert "нет нужного файла" in c.check("o/r", "1.3.1", "0.1.1", opener_for(bad), pause=0)[0]
    # черновик и пустой файл
    bad = {**OK, "android-v0.1.1": {"draft": True, "assets": [{"name": "a.apk", "size": 0}]}}
    p = c.check("o/r", "1.3.1", "0.1.1", opener_for(bad), pause=0)
    assert any("черновик" in x for x in p) and any("пустой" in x for x in p)
    # кратковременный сбой сети — повтор и успех
    op = opener_for(OK, fail_first=1)
    assert c.check("o/r", "1.3.1", "0.1.1", op, pause=0) == [] and op.calls["n"] == 3
    # GitHub недоступен все попытки — Unavailable, а не «всё хорошо» и не «файла нет»
    try:
        c.check("o/r", "1.3.1", "0.1.1", opener_for(OK, fail_first=99), pause=0)
        raise AssertionError("ожидалось Unavailable")
    except c.Unavailable:
        pass
    # версии читаются из исходников
    assert c.read_const(c.ROOT / "frontend/src/androidDist.ts", "ANDROID_VERSION")
    assert c.read_const(c.ROOT / "frontend/src/desktopDist.ts", "DESKTOP_VERSION")


if __name__ == "__main__":
    test_all()
    print("check_download_links: OK")
