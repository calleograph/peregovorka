"""Настоящий эфир для испытаний CDN: комната-трансляция, синтетический докладчик (../egress/latency_probe.py), Egress, HLS. Узлы CDN — включены в комнате.
  BASE=… ORIGIN=… ADMIN_PW_FILE=… LK_URL=ws://127.0.0.1:27880 python broadcast.py 1080p STOP_FILE  → печатает строку JSON {"token": …}; пока нет STOP_FILE, эфир идёт."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
HDR = {"Origin": os.environ.get("ORIGIN", "http://localhost:28480")}
quality, stop = sys.argv[1], sys.argv[2]
SIZE = {"720p": "1280x720", "1080p": "1920x1080", "1440p": "2560x1440"}[quality]


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
c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3}).raise_for_status()
slug = f"cdn-{quality}-{time.strftime('%H%M%S')}"
rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": quality, "live_cdn": os.environ.get("CDN", "1") == "1"}).json()
token = lv["view_path"].rsplit("/", 1)[-1]
j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json()
pub = subprocess.Popen([sys.executable, str(HERE.parent / "egress" / "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"],
                        "--size", SIZE, "--seconds", "7200"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
assert "PUBLISHING" in pub.stdout.readline()
time.sleep(4)
c.post(f"/api/v1/meetings/{j['meeting_id']}/live", json={"enabled": True}).raise_for_status()
v = httpx.Client(base_url=BASE, headers=HDR, timeout=30)
for _ in range(120):
    if v.get(f"/api/v1/live/{token}").json()["status"] == "live":
        break
    time.sleep(0.5)
print(json.dumps({"token": token, "room": rid, "meeting": j["meeting_id"]}), flush=True)
while not os.path.exists(stop):
    time.sleep(2)
c = admin()
c.post(f"/api/v1/meetings/{j['meeting_id']}/live", json={"enabled": False})
pub.terminate()
c.post(f"/api/v1/meetings/{j['meeting_id']}/end")
c.delete(f"/api/v1/admin/rooms/{rid}")
