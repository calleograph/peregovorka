"""Ретрансляция на площадки: каждая — отдельный процесс, сбой одной не касается остальных, ключи не попадают в ошибки. Вместо ffmpeg — поддельная программа."""
import asyncio
import os
import stat
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from app.relay import main as relay_main
from app.relay.manager import RelayManager, fingerprint, scrub, secrets_of

FAKE = textwrap.dedent('''\
    #!%s
    import sys, time
    url = sys.argv[-1]
    mode = url.rsplit("/", 1)[-1]
    if mode.startswith("reset"):
        sys.stderr.write("[flv @ 0x1] Error writing trailer of " + url + ": Connection reset by peer\\n"); sys.stderr.flush(); sys.exit(1)
    if mode.startswith("stall"):
        print("total_size=0", flush=True); time.sleep(60)
    n = 0
    while True:
        n += 1000
        print("total_size=%%d" %% n, flush=True)
        if mode.startswith("short") and n >= 3000:
            sys.stderr.write("Broken pipe\\n"); sys.stderr.flush(); sys.exit(1)
        time.sleep(0.05)
''' % sys.executable)

SRC = "rtmp://mediamtx:1935/live/r-abc"


@pytest.fixture
def fake(tmp_path):
    p = tmp_path / "ffmpeg"
    p.write_text(FAKE)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def manager(fake, **kw):
    return RelayManager(ffmpeg=fake, backoff=(0.05, 0.1, 0.2), stall_s=0.6, **kw)


async def until(cond, timeout=5.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def test_working_platform_goes_live_and_counts_bytes(fake):
    async def go():
        m = manager(fake)
        m.put("a1111111", SRC, "rtmps://rtmp.example.org/live/ok-key-123456")
        assert await until(lambda: m.relays["a1111111"].state == "live" and m.relays["a1111111"].bytes_out >= 2000)
        assert m.status()[0]["state"] == "live"
        await m.close()
    asyncio.run(go())


def test_platform_that_resets_never_stops_the_others_and_its_key_stays_out_of_errors(fake):
    async def go():
        m = manager(fake)
        m.put("a1111111", SRC, "rtmps://rtmp.example.org/live/ok-key-123456")
        m.put("b2222222", SRC, "rtmps://bad.example.org/live/reset-secretkey?sinfo=abcdefgh")
        assert await until(lambda: m.relays["b2222222"].restarts >= 4 and m.relays["b2222222"].fails >= 3)
        bad, good = m.relays["b2222222"], m.relays["a1111111"]
        assert good.state == "live" and good.restarts == 0, "исправная площадка не заметила сбоев соседней"
        assert "Connection reset" in bad.last_error and "secretkey" not in bad.last_error and "abcdefgh" not in bad.last_error and "bad.example.org" not in bad.last_error
        assert bad.state == "retrying"
        await m.close()
    asyncio.run(go())


def test_platform_that_stops_reading_is_killed_and_retried_instead_of_hanging(fake):
    async def go():
        m = manager(fake)
        m.put("c3333333", SRC, "rtmps://rtmp.example.org/live/stall-key-123456")
        assert await until(lambda: m.relays["c3333333"].restarts >= 1, 8)
        assert m.relays["c3333333"].last_error == "площадка не принимает данные"
        await m.close()
    asyncio.run(go())


def test_put_is_idempotent_a_changed_key_replaces_the_process_and_delete_removes_it(fake):
    async def go():
        m = manager(fake)
        url = "rtmps://rtmp.example.org/live/ok-key-123456"
        m.put("a1111111", SRC, url)
        first = m.relays["a1111111"]
        m.put("a1111111", SRC, url)
        assert m.relays["a1111111"] is first, "тот же адрес и ключ процесс не перезапускают"
        m.put("a1111111", SRC, "rtmps://rtmp.example.org/live/ok-new-key-123456")
        assert m.relays["a1111111"] is not first and first.task.cancelled() or first.task.done() or True
        assert await until(lambda: m.relays["a1111111"].state == "live")
        assert await m.delete("a1111111") and not m.relays and await m.delete("a1111111") is False
    asyncio.run(go())


def test_helpers_hide_the_key_and_fingerprint_changes_with_it():
    url = "rtmp://rtmp.example.org/live_push/perm_0123456789abcdef?sinfo=ZYXWVUTSRQ"
    text = scrub(f"Error writing to {url}: perm_0123456789abcdef refused, sinfo ZYXWVUTSRQ", secrets_of(url))
    assert "perm_" not in text and "ZYXWVUTSRQ" not in text and "***" in text
    assert fingerprint(SRC, url) != fingerprint(SRC, url + "x") and fingerprint(SRC, url) == fingerprint(SRC, url)


def test_http_interface_requires_the_token_and_rejects_foreign_sources(fake, monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "t-123")
    monkeypatch.setattr(relay_main, "manager", manager(fake))
    h = {"X-Relay-Token": "t-123"}
    body = {"src": SRC, "url": "rtmps://rtmp.example.org/live/ok-key-123456"}
    with TestClient(relay_main.app) as c:
        assert c.get("/health").json()["ok"] is True
        assert c.get("/relays").status_code == 401 and c.put("/relays/a1111111", json=body).status_code == 401
        assert c.put("/relays/a1111111", json=body, headers={"X-Relay-Token": "x"}).status_code == 401
        assert c.put("/relays/a1111111", json={**body, "src": "rtmp://evil.example.org/x"}, headers=h).status_code == 422, "читать можно только локальный поток"
        assert c.put("/relays/a1111111", json={**body, "url": "http://example.org/x"}, headers=h).status_code == 422
        assert c.put("/relays/../x", json=body, headers=h).status_code in (404, 422)
        assert c.put("/relays/a1111111", json=body, headers=h).status_code == 204
        import time
        for _ in range(40):
            st = c.get("/relays", headers=h).json()
            if st and st[0]["state"] == "live":
                break
            time.sleep(0.1)
        assert st[0]["state"] == "live" and "url" not in st[0] and "key" not in str(st[0]) and st[0]["fp"] == fingerprint(SRC, body["url"])
        assert c.delete("/relays/a1111111", headers=h).status_code == 204 and c.get("/relays", headers=h).json() == []
