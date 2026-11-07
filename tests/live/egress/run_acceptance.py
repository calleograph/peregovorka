#!/usr/bin/env python3
"""Приёмка эфира на настоящем стенде: серия запусков подряд, независимость выходов (отказ площадки, отказ MediaMTX), повтор при нехватке процессора, уборка процессов и временных файлов.
  ADMIN_PW_FILE=… python run_acceptance.py [запусков=20]   Приёмник RTMP — контейнер pgrtmp. Данные синтетические."""
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent

BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
HDR = {"Origin": os.environ.get("ORIGIN", "http://localhost:28480")}
EG, MTX, RTMP = "pgdoc-livekit-egress-1", "pgdoc-mediamtx-1", "pgrtmp"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


def admin() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers=HDR, timeout=90)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][-1]["id"]})
    r.raise_for_status()
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def sh(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout.strip()


def eg_state() -> dict:
    out = sh("docker", "exec", EG, "sh", "-c", 'ps -eo comm | sort | uniq -c; echo TMP $(ls /home/egress/tmp 2>/dev/null | wc -l); echo KB $(du -sk /home/egress | cut -f1); echo SHM $(du -sk /dev/shm | cut -f1)')
    d = {"chrome": 0, "Xvfb": 0, "egress": 0}
    for ln in out.splitlines():
        p = ln.split()
        if len(p) == 2 and p[1] in d:
            d[p[1]] = int(p[0])
        elif p and p[0] in ("TMP", "KB", "SHM"):
            d[p[0]] = int(p[1])
    return d


class Room:
    def __init__(self, c, dest=False):
        self.c = c
        slug = f"a-{time.strftime('%H%M%S')}-{os.getpid() % 1000}"
        self.rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
        self.token = c.patch(f"/api/v1/rooms/{self.rid}/live", json={"live_enabled": True, "live_quality": "720p", "live_cdn": False}).json()["view_path"].rsplit("/", 1)[-1]
        self.slug = slug
        if dest:
            c.post(f"/api/v1/rooms/{self.rid}/live/destinations", json={"platform": "custom", "name": "Площадка", "server_url": f"rtmp://{RTMP}:1935/live", "key": slug}).raise_for_status()
        j = c.post(f"/api/v1/rooms/{self.rid}/join", json={}).json()
        self.mid = j["meeting_id"]
        self.pub = subprocess.Popen([sys.executable, str(HERE / "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", "1280x720", "--seconds", "7200"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert "PUBLISHING" in self.pub.stdout.readline()
        time.sleep(3)

    def status(self):
        return httpx.get(f"{BASE}/api/v1/live/{self.token}", headers=HDR, timeout=20).json()["status"]

    def go(self, limit=60) -> float:
        t0 = time.time()
        r = self.c.post(f"/api/v1/meetings/{self.mid}/live", json={"enabled": True})
        r.raise_for_status()
        while time.time() - t0 < limit:
            if self.status() == "live":
                return time.time() - t0
            time.sleep(0.25)
        return -1.0

    def stop(self):
        self.c.post(f"/api/v1/meetings/{self.mid}/live", json={"enabled": False})
        for _ in range(100):
            if self.status() != "live":
                break
            time.sleep(0.3)

    def close(self):
        self.pub.terminate()
        self.c.post(f"/api/v1/meetings/{self.mid}/end")


c = admin()
c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "rtc_viewers_max": 0}).raise_for_status()

# 1. серия запусков
room = Room(c)
times = []
for i in range(N):
    s = room.go()
    times.append(s)
    time.sleep(4)
    room.stop()
    time.sleep(3)
    if i % 5 == 4:
        print(f"  запусков {i + 1}: {eg_state()}", flush=True)
ok = [x for x in times if x > 0]
t(f"{N} запусков подряд", len(ok) == N, f"удачных {len(ok)}; среднее {statistics.mean(ok):.1f} с, макс {max(ok):.1f} с" if ok else "нет")
room.close()
time.sleep(12)
st = eg_state()
t("после эфиров нет браузеров и Xvfb", st["chrome"] == 0 and st["Xvfb"] == 0, str(st))
t("обработчики Egress завершились", st["egress"] <= 1, str(st))
t("временные файлы не копятся", st.get("TMP", 0) < 5 and st.get("KB", 0) < 200_000, str(st))

# 2. независимость выходов
room = Room(c, dest=True)
assert room.go() > 0
time.sleep(14)
sh("docker", "kill", RTMP)                                    # площадка пропала посреди эфира
time.sleep(10)
t("отказ площадки: эфир идёт", room.status() == "live")
vw = httpx.post(f"{BASE}/api/v1/live/{room.token}/viewer", headers=HDR, json={"display_name": "Проверка"}, timeout=20).json()["viewer"]
acc = httpx.post(f"{BASE}/api/v1/live/{room.token}/access", headers=HDR, json={"viewer": vw}, timeout=20).json()
h = httpx.get(BASE + (acc.get("origin_path") or acc["path"]), timeout=20)
t("отказ площадки: HLS идёт", h.status_code == 200 and ".ts" in h.text)
sh("docker", "start", RTMP)
sh("docker", "stop", MTX)                                     # служба низкой задержки упала
time.sleep(8)
acc2 = httpx.post(f"{BASE}/api/v1/live/{room.token}/access", headers=HDR, json={"viewer": vw}, timeout=20).json()
t("отказ MediaMTX: эфир идёт", room.status() == "live")
t("отказ MediaMTX: новые зрители получают обычный HLS", acc2["path"].startswith("/hls/"), acc2["path"][:12])
sh("docker", "start", MTX)
time.sleep(4)
t("после возврата MediaMTX эфир цел", room.status() == "live")
room.stop()
time.sleep(6)
rec = sh("docker", "exec", "pgdoc-postgres-1", "sh", "-c", f'psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "select status, size_bytes, coalesce(error,\'\') from recordings where meeting_id=\'{room.mid}\' order by created_at desc limit 1"')
t("запись MP4 закрыта, несмотря на отказы", rec.startswith("ready") and int(rec.split("|")[1]) > 100000, rec[:80])
room.close()

# 3. нехватка процессора при запуске: повтор сам доводит эфир
room = Room(c)
sh("docker", "update", "--cpus", "0.15", EG)
t0 = time.time()
c.post(f"/api/v1/meetings/{room.mid}/live", json={"enabled": True})
time.sleep(24)                                                 # браузер не успевает за 20 с
sh("docker", "update", "--cpus", "20", EG)
ok = False
while time.time() - t0 < 120:
    if room.status() == "live":
        ok = True
        break
    time.sleep(0.5)
ev = sh("docker", "exec", "pgdoc-postgres-1", "sh", "-c", 'psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "select code from live_diag_events where code like \'stage_%\' order by id desc limit 5"').split()
t("нехватка процессора: эфир пошёл сам", ok, f"за {time.time() - t0:.0f} с, этапы {ev}")
room.stop()
room.close()

print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
for f in FAIL:
    print("  ", f)
sys.exit(1 if FAIL else 0)
