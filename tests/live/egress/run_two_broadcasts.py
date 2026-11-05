#!/usr/bin/env python3
"""Два эфира подряд в одной комнате на настоящем Egress: новый эфир не должен совпасть с прошлым ни по адресу, ни по именам сегментов (кеш CDN хранит их по этим именам),
а подпись прошлого эфира и подпись «ожидания» не открывают защищённый следующий эфир.
BASE, ORIGIN, ADMIN_PW_FILE, LK_URL — как в ../cdn/broadcast.py."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
HDR = {"Origin": os.environ.get("ORIGIN", "http://localhost:28480")}
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


def admin() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers=HDR, timeout=60)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][-1]["id"]})
    r.raise_for_status()
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


c = admin()
v = httpx.Client(base_url=BASE, headers=HDR, timeout=30)
c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3}).raise_for_status()
slug = f"two-{time.strftime('%H%M%S')}"
rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": "720p", "live_cdn": False}).json()
token = lv["view_path"].rsplit("/", 1)[-1]
j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json()
mid = j["meeting_id"]
pub = subprocess.Popen([sys.executable, str(HERE / "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", "1280x720", "--seconds", "600"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
assert "PUBLISHING" in pub.stdout.readline()
time.sleep(4)
viewer = v.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Проверка"}).json()["viewer"]


def status():
    return v.get(f"/api/v1/live/{token}").json()


def go(on=True):
    r = c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": on})
    r.raise_for_status()
    want = ("live",) if on else ("ended", "waiting")
    for _ in range(240):
        if status()["status"] in want:
            return
        time.sleep(0.5)
    raise TimeoutError(status())


def segments(path):
    txt = ""
    for _ in range(60):
        r = v.get(path)
        txt = r.text
        names = re.findall(r"seg_[^\s]+\.ts", txt)
        if len(names) >= 2:
            return names
        time.sleep(1)
    raise TimeoutError(txt[:300])


try:
    go(True)
    a = v.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()
    first = segments(a["path"])
    print("сегменты первого эфира:", first[:3], flush=True)
    t("nginx отдаёт сегмент с новым именем", v.get(a["path"].rsplit("/", 1)[0] + "/" + first[0]).status_code == 200)
    go(False)
    t("пока эфира нет, подпись не выдаётся", v.post(f"/api/v1/live/{token}/access", json={"viewer": viewer, "long": True}).status_code == 409)
    r = c.put(f"/api/v1/rooms/{rid}/live/password", json={"password": "zritel-2026-x"})
    t("пароль задан", r.status_code == 200, r.text[:100])
    t("подпись по-прежнему не выдаётся (обход через ожидание закрыт)", v.post(f"/api/v1/live/{token}/access", json={"viewer": viewer, "long": True}).status_code == 409)
    go(True)
    t("без пароля новый эфир закрыт", v.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()["detail"]["code"] == "password_required")
    t("прошлая подпись новый эфир не открывает", v.get(a["path"]).status_code in (403, 404))
    b = v.post(f"/api/v1/live/{token}/access", json={"viewer": viewer, "password": "zritel-2026-x"}).json()
    second = segments(b["path"])
    print("сегменты второго эфира:", second[:3], flush=True)
    t("адрес нового эфира другой", a["path"].split("/")[2] != b["path"].split("/")[2])
    t("имена сегментов не совпадают с прошлым эфиром", not set(first) & set(second), f"{first[0]} / {second[0]}")
    go(False)
finally:
    pub.terminate()
    c.post(f"/api/v1/meetings/{mid}/end")
print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
sys.exit(1 if FAIL else 0)
