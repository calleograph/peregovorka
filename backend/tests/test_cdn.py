"""Свои узлы CDN: подпись ссылок (проверка открытым ключом, как на узле), выбор узла, выдача ссылки зрителю и запасной путь, файл ключей источника,
оценка сервера, администрирование (валидация, отпечаток, права). SSH и сам узел здесь подменены — живая проверка: tests/live/cdn/."""
from __future__ import annotations

import base64
import os
import stat
import uuid
from collections import Counter

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from app.services import cdn as C
from .conftest import login, put_settings
from .test_live import access, call, get_room, lv, patch, room_live  # noqa: F401 — фикстура lv

MASTER = "test-master-key-for-cdn"


def verify(path: str, keys: dict[str, str]) -> bool:
    """Проверка, как на узле (pgcdn.js): ES256 строки pgcdn1|<доступ>|<срок> ключом kid."""
    _, _, acc, exp, tok, _name = path.split("/")
    kid, sig = tok.split(".")
    if kid not in keys:
        return False
    raw = base64.urlsafe_b64decode(sig + "==")
    pub = serialization.load_der_public_key(base64.b64decode(keys[kid]))
    try:
        pub.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"pgcdn1|{acc}|{exp}".encode(), ec.ECDSA(hashes.SHA256()))
        return True
    except InvalidSignature:
        return False


def test_node_link_is_verified_by_the_public_key_and_rotation_keeps_one_previous_generation():
    acc = "0123456789abcdef01234567"
    p1, exp = C.sign_path(MASTER, 1, acc, 900, now=1_000_000)
    assert exp % 300 == 0 and exp > 1_000_900 and len(p1.split("/")[4]) == 8 + 1 + 86
    assert p1 == C.sign_path(MASTER, 1, acc, 900, now=1_000_010)[0] or True        # ECDSA случайна: путь не обязан совпадать, срок — да
    assert C.sign_path(MASTER, 1, acc, 900, now=1_000_010)[1] == exp, "все зрители окна получают один срок — общий кеш подписи на узле"
    assert verify(p1, C.public_keys(MASTER, 1))
    assert not verify(p1.replace(acc, "f" * 24), C.public_keys(MASTER, 1)), "подпись привязана к эфиру"
    assert not verify(p1, C.public_keys("other-master", 1)), "ключ выводится из главного ключа сервера"
    assert verify(p1, C.public_keys(MASTER, 2)), "после смены ключа старые ссылки ещё открываются (предыдущее поколение)"
    assert not verify(p1, C.public_keys(MASTER, 3)), "после двойной смены — нет"
    assert len(C.public_keys(MASTER, 1, ahead=True)) == 2 and verify(C.sign_path(MASTER, 2, acc, 60)[0], C.public_keys(MASTER, 1, ahead=True)), \
        "смена ключа: узлы получают следующий ключ раньше, чем им начинают подписывать"


def _node(name, weight=100, **kw):
    n = C.CdnNode(id=uuid.uuid5(uuid.NAMESPACE_DNS, name), name=name, public_name=name, weight=weight, enabled=True, state="ready", health="up",
                  ssh_host=name, ssh_user="root")
    for k, v in kw.items():
        setattr(n, k, v)
    return n


def test_viewers_stick_to_their_node_spread_by_weight_and_only_a_failed_nodes_viewers_move():
    a, b, c = _node("a.example.org"), _node("b.example.org"), _node("c.example.org", weight=200)
    viewers = [f"v{i}" for i in range(4000)]
    first = {v: C.pick([a, b, c], v).public_name for v in viewers}
    assert first == {v: C.pick([c, b, a], v).public_name for v in viewers}, "порядок узлов не влияет"
    share = Counter(first.values())
    assert 0.4 < share["c.example.org"] / 4000 < 0.6 and 0.2 < share["a.example.org"] / 4000 < 0.3, share
    b.health = "down"
    moved = {v: C.pick([a, b, c], v).public_name for v in viewers}
    assert all(moved[v] == first[v] for v in viewers if first[v] != "b.example.org"), "зрители исправных узлов не переезжают"
    assert "b.example.org" not in moved.values()
    assert C.pick([a, b, c], "v1", avoid=first["v1"]).public_name != first["v1"], "плеер просит обойти узел — другой узел"
    a.enabled, c.state = False, "error"
    assert C.pick([a, b, c], "v1") is None, "исправных узлов нет"
    assert C.pick([_node("x.example.org")], "v1", avoid="x.example.org").public_name == "x.example.org", "единственный узел не обходится"


def test_assessment_names_blockers_and_the_limiting_resource():
    facts = {"os_id": "ubuntu", "os_version": "24.04", "os_name": "Ubuntu 24.04", "arch": "amd64", "systemd": True, "sudo": True, "cpus": 8, "mem_bytes": 16 * 2**30,
             "cache_fs_avail": 200 * 2**30, "nics": [{"name": "eth0", "speed_mbit": 1000}], "disks": {"blockdevices": []}, "ports_busy": {"80": 0, "443": 0}}
    a = C.assess(facts, 5000)
    assert a["ok"] and a["viewers"] == a["by_link"] == int(1000 * 0.8 / ((5000 + 128) * 1.06 / 1000)), "канал 1 Гбит/с — предел ≈150 зрителей 1080p"
    bad = C.assess({**facts, "os_id": "centos", "os_version": "9", "sudo": False, "ports_busy": {"443": 2}, "cache_fs_avail": 5 * 2**30, "distro_nginx": True}, 5000)
    assert not bad["ok"] and len(bad["blockers"]) == 5
    virt = C.assess({**facts, "nics": [{"name": "eth0", "speed_mbit": -1}]}, 9000)
    assert virt["by_link"] is None and any("провайдер" in n["text"] for n in virt["notes"])
    r = C.recommend(1500, 5000)
    assert r["egress_gbps"] == round(1500 * (5128 * 1.06 / 1000) * 1.3 / 1000, 2) and r["cpu_cores"] >= 5 and r["ram_gb"] >= 4


@pytest.fixture
def nodes(lv, monkeypatch):
    """Два узла в базе (исправные), свой главный ключ."""
    from app.models import CdnNode

    lv.app_obj.state.settings.app_master_key = lv.app_obj.state.settings.app_master_key or base64.b64encode(os.urandom(32)).decode()

    async def add(db):
        cdn = lv.app_obj.state.cdn
        out = []
        for name in ("cdn1.example.org", "cdn2.example.org"):
            n = CdnNode(id=uuid.uuid4(), name=name, public_name=name, state="ready", health="up", ssh_host=name, ssh_user="root", tls_mode="acme")
            cdn.new_secrets(n)
            db.add(n)
            out.append(n)
        await db.commit()
        return [str(n.id) for n in out]
    ids = call(lv, add)
    lv.app_obj.state.cdn.invalidate()
    return ids


def _set_health(lv, name, health):
    from sqlalchemy import update

    from app.models import CdnNode

    async def go(db):
        await db.execute(update(CdnNode).where(CdnNode.public_name == name).values(health=health))
        await db.commit()
    call(lv, go)
    lv.app_obj.state.cdn.invalidate()


def test_viewer_gets_a_node_link_with_an_origin_fallback_and_falls_back_to_origin_when_all_nodes_are_down(lv, nodes):
    room = room_live(lv)
    token = get_room(lv, room["id"]).live_token
    login(lv, "carol")
    assert patch(lv, room, live_cdn=True).json()["detail"]["code"] == "cdn_off"
    put_settings(lv, "live", cdn_nodes_enabled=True)
    assert patch(lv, room, live_cdn=True).json()["cdn"] is True, "свои узлы — тоже CDN для комнаты"
    a = access(lv, token).json()
    assert a["node"] in ("cdn1.example.org", "cdn2.example.org") and a["base"] == f"https://{a['node']}"
    keys = C.public_keys(C.master_of(lv.app_obj.state.settings), 1)
    assert verify(a["path"], keys) and a["origin_path"].startswith("/hls/") and len(a["origin_path"].split("/")[4]) == 22, "запасной путь — подпись источника (nginx secure_link)"
    other = access(lv, token, avoid=a["node"]).json()
    assert other["node"] != a["node"], "плеер не получил поток с узла — сервер даёт другой"
    stream = patch(lv, room, live_title="t").json()["stream_path"]
    assert stream.startswith("https://cdn") and verify(stream.split(".org", 1)[1], keys), "ссылка на поток для внешних плееров — на узел"
    _set_health(lv, "cdn1.example.org", "down")
    _set_health(lv, "cdn2.example.org", "down")
    b = access(lv, token).json()
    assert b["base"] == "" and "node" not in b and b["path"].startswith("/hls/"), "все узлы недоступны — прямо с этого сервера"
    put_settings(lv, "live", cdn_fallback_origin=False)
    r = access(lv, token)
    assert r.status_code == 503 and r.json()["detail"]["code"] == "cdn_unavailable", "резерв запрещён администратором — понятный отказ, плеер повторит"
    _set_health(lv, "cdn2.example.org", "up")
    c = access(lv, token).json()
    assert c["node"] == "cdn2.example.org" and "origin_path" not in c, "без резерва запасной путь не выдаётся"
    patch(lv, room, live_cdn=False)
    assert access(lv, token).json()["base"] == "", "комната без CDN — прежняя прямая раздача"


def test_origin_keys_file_lists_installing_and_ready_nodes_only_with_restricted_permissions(lv, nodes):
    from sqlalchemy import update

    from app.models import CdnNode

    cdn = lv.app_obj.state.cdn

    async def go(db):
        await db.execute(update(CdnNode).where(CdnNode.public_name == "cdn2.example.org").values(state="checked"))
        await db.commit()
        return await cdn.write_origin_keys(db)
    assert call(lv, go) is None
    p = cdn.keys_map_path()
    text = p.read_text()
    assert text.count("\n") == 1 and text.startswith('"') and text.rstrip().endswith('";'), text
    assert stat.S_IMODE(p.stat().st_mode) == 0o640, "файл ключей — как секрет подписи: не для посторонних"


def test_cdn_admin_requires_admin_validates_input_and_confirms_the_host_key(lv, nodes, monkeypatch):
    login(lv, "carol")
    assert lv.get("/api/v1/admin/cdn").status_code in (401, 403)
    login(lv, "root")
    o = lv.get("/api/v1/admin/cdn").json()
    assert len(o["nodes"]) == 2 and o["mode"] == "origin" and o["profiles"] and all("pull_key" not in str(n) for n in o["nodes"]), "секреты узла наружу не отдаются"
    import asyncssh

    from app.services import cdn_ssh

    key = asyncssh.generate_private_key("ssh-ed25519")
    pub = key.export_public_key("openssh").decode().strip()

    async def fake_host_key(host, port):
        return pub, key.get_fingerprint("sha256")
    monkeypatch.setattr(cdn_ssh, "host_key", fake_host_key)
    hk = lv.post("/api/v1/admin/cdn/hostkey", json={"ssh_host": "203.0.113.10", "ssh_port": 22}).json()
    assert hk["fingerprint"].startswith("SHA256:")
    base = {"name": "Узел", "public_name": "cdn3.example.org", "ssh_host": "203.0.113.10", "ssh_user": "admin", "host_key": hk["host_key"], "fingerprint": hk["fingerprint"],
            "password": "x", "tls_mode": "acme"}
    assert lv.post("/api/v1/admin/cdn/hostkey", json={"ssh_host": "cdn-node1", "ssh_port": 22}).status_code == 200, "внутреннее имя сервера без точки"
    for bad in ({"public_name": "not a name"}, {"public_name": "single-label"}, {"ssh_host": "a b"}, {"ssh_user": "Root;rm"}, {"tls_mode": "plain"}, {"ssh_port": 0}):
        assert lv.post("/api/v1/admin/cdn/nodes", json={**base, **bad}).status_code == 422, bad
    assert lv.post("/api/v1/admin/cdn/nodes", json={**base, "fingerprint": "SHA256:other"}).json()["detail"]["code"] == "fingerprint_mismatch"
    assert lv.post("/api/v1/admin/cdn/nodes", json={**base, "public_name": "cdn1.example.org"}).status_code == 409, "имя уже занято"

    async def fake_connect(*a, **k):
        raise cdn_ssh.SshError("auth_failed", "Сервер отклонил логин, пароль или ключ.")
    monkeypatch.setattr(cdn_ssh, "connect", fake_connect)
    r = lv.post("/api/v1/admin/cdn/nodes", json=base)
    assert r.status_code == 502 and r.json()["detail"]["code"] == "auth_failed"
    manual = lv.post("/api/v1/admin/cdn/nodes", json={**base, "manual": True, "password": None})
    assert manual.status_code == 201 and manual.json()["ssh"]["user"] == "pgcdn"
    conf = lv.get(f"/api/v1/admin/cdn/nodes/{manual.json()['id']}/node.json").json()
    assert conf["mgmt_pubkey"].startswith("ssh-ed25519 ") and len(conf["pull_key"]) >= 32 and len(conf["keys"]) == 1 and "mgmt_key" not in conf
    assert lv.post(f"/api/v1/admin/cdn/nodes/{manual.json()['id']}/install", json={}).status_code == 409, "узел ручной установки не ставится по SSH"
    assert lv.get("/api/v1/admin/cdn/package.tar.gz").headers["content-type"] == "application/gzip"
    assert lv.get("/api/v1/admin/cdn/calc?viewers=0").status_code == 400


def test_install_interrupted_by_a_restart_does_not_leave_the_node_installing_forever(lv, nodes):
    from sqlalchemy import select, update

    from app.models import CdnNode

    async def go(db):
        await db.execute(update(CdnNode).where(CdnNode.public_name == "cdn2.example.org").values(state="installing"))
        await db.commit()
    call(lv, go)
    state = lv.app_obj.state
    call(lv, lambda db: state.cdn.poll_all(state.session_maker))

    async def read(db):
        return (await db.execute(select(CdnNode.state, CdnNode.last_error).where(CdnNode.public_name == "cdn2.example.org"))).one()
    st, why = call(lv, read)
    assert st == "error" and "Повторный запуск" in why


def test_node_certificate_is_checked_also_against_the_ca_catalog_unless_the_node_is_self_signed(lv, tmp_path):
    import ssl
    from types import SimpleNamespace as NS

    cdn = lv.app_obj.state.cdn
    assert cdn.verify(NS(tls_mode="selfsigned")) is False, "стенд"
    assert isinstance(cdn.verify(NS(tls_mode="acme"), ca_bundle=str(tmp_path / "нет-файла")), ssl.SSLContext), "каталога нет — системные корни"
