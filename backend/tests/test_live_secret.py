"""Секрет подписи HLS: backend пишет его атомарно с правами 0640 (группа 10001 — общая с nginx), не меняя значения; диагностика по этапам и по тому,
что сообщает работающий nginx (поколение загруженного секрета), а не по времени запуска контейнера web."""
from __future__ import annotations

import hashlib
import os
import time
from types import SimpleNamespace

import pytest

from app.services import media_checks as mc
from app.services.live import SECRET_MODE, LiveBroadcaster, hls_secret, nginx_secret_conf


def _live(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_DIR", str(tmp_path / "live"))
    s = SimpleNamespace(data_dir=str(tmp_path), app_master_key="k" * 32, livekit_api_secret="s" * 40)
    lb = LiveBroadcaster.__new__(LiveBroadcaster)
    lb._s = s  # noqa: SLF001
    return lb, s


def test_secret_is_written_atomically_with_0640_and_the_value_is_kept(tmp_path, monkeypatch):
    lb, s = _live(tmp_path, monkeypatch)
    assert lb.prepare() is None
    conf = tmp_path / "live" / "nginx" / "secret.conf"
    assert conf.read_text() == nginx_secret_conf(hls_secret(s)) and (conf.stat().st_mode & 0o777) == SECRET_MODE, "посторонние локальные пользователи не читают"
    conf.chmod(0o644)                                                     # так писали прежние версии
    ino = conf.stat().st_ino
    assert lb.prepare() is None
    assert (conf.stat().st_mode & 0o777) == 0o640 and conf.stat().st_ino != ino, "права исправлены атомарной заменой"
    assert conf.read_text() == nginx_secret_conf(hls_secret(s)), "значение секрета не меняется"
    assert not [p for p in conf.parent.iterdir() if p.name != "secret.conf"], "временных файлов не остаётся"


@pytest.mark.skipif(os.geteuid() == 0, reason="root пишет куда угодно")
def test_unwritable_directory_is_reported_with_the_reason(tmp_path, monkeypatch):
    lb, _ = _live(tmp_path, monkeypatch)
    nd = tmp_path / "live" / "nginx"
    nd.mkdir(parents=True)
    nd.chmod(0o555)                                                       # как каталог, созданный Docker от root
    try:
        why = lb.prepare()
        assert why and "не записан" in why
    finally:
        nd.chmod(0o755)


def _run(lb, settings, state, monkeypatch):
    async def fake(url):
        return state
    monkeypatch.setattr(mc, "web_hls_state", fake)
    import asyncio
    return {c["id"]: c for c in asyncio.run(mc.secret_checks(lb, settings))}


def test_diagnostics_use_the_generation_nginx_loaded_not_the_container_start_time(tmp_path, monkeypatch):
    lb, s = _live(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.live.SECRET_GID", os.getgid())      # тесты не root: «правильная» группа — своя
    lb.prepare()
    gen = hashlib.sha256(nginx_secret_conf(hls_secret(s)).encode()).hexdigest()[:12]
    ok = _run(lb, s, ("ok", gen), monkeypatch)
    assert [ok[i]["status"] for i in ("secret_dir", "secret_write", "secret_file", "secret_mode", "secret_read", "secret_test", "secret_applied")] == ["ok"] * 7
    conf = tmp_path / "live" / "nginx" / "secret.conf"
    os.utime(conf, (time.time() - 600, time.time() - 600))
    old = _run(lb, s, ("ok", "0123456789ab"), monkeypatch)
    assert old["secret_applied"]["status"] == "error" and old["secret_applied"]["action"]["op"] == "web_restart", "nginx работает со старым секретом"
    bad = _run(lb, s, ("test_failed", gen), monkeypatch)
    assert bad["secret_test"]["status"] == "error" and bad["secret_applied"]["status"] == "skipped"
    unreadable = _run(lb, s, ("unreadable", ""), monkeypatch)
    assert unreadable["secret_read"]["status"] == "error" and unreadable["secret_read"]["action"]["repair"] == "data_dirs"
    nobody = _run(lb, s, None, monkeypatch)
    assert nobody["secret_applied"]["status"] == "info", "web не ответил — честно «проверить нельзя», а не ошибка и не «всё хорошо»"


def test_wrong_group_or_mode_is_an_error_with_the_repair_action(tmp_path, monkeypatch):
    lb, s = _live(tmp_path, monkeypatch)
    lb.prepare()
    monkeypatch.setattr("app.services.live.SECRET_GID", os.getgid() + 12345)          # группа не та, а backend её назначить не может
    r = _run(lb, s, ("ok", "x"), monkeypatch)
    assert r["secret_mode"]["status"] == "error" and r["secret_mode"]["action"]["repair"] == "data_dirs"
