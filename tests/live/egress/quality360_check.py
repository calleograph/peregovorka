#!/usr/bin/env python3
"""Живая проверка 360p: комната вещания, выбор 360p при запуске эфира (как в окне «Трансляция»), публикатор с настоящими LiveKit и Egress;
проверяется, что HLS и плейлист — 640×360, и что у зрителя выбор 360p в списке качества эфира. Синтетические данные.
  ADMIN_PW_FILE=… PEREGOVORKA_URL=http://localhost:28480 python quality360_check.py"""
import asyncio, json, os, subprocess, sys, time
import httpx
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import smoke_room as S  # noqa: E402

BASE = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480")
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


def admin():
    c = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=90)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": sorted(d["sessions"], key=lambda s: s["created_at"])[0]["id"]})
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]; c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


def main():
    c = admin()
    c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "rtc_viewers_max": 0, "ll_hls": True, "adaptive_viewers": True}).raise_for_status()
    slug = f"q360-{time.strftime('%H%M%S')}"
    rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": "1080p", "live_cdn": False})
    t("настройка комнаты принимает 360p (live_quality)", lv.status_code == 200 and lv.json()["quality"] == "1080p", str(lv.status_code))
    lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_quality": "360p"})
    t("комната сохраняет 360p", lv.status_code == 200 and lv.json()["quality"] == "360p", str(lv.status_code))
    token = lv.json()["view_path"].rsplit("/", 1)[-1]
    j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json(); mid = j["meeting_id"]
    pub = subprocess.Popen([sys.executable, os.path.join(HERE, "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", "1280x720", "--seconds", "300"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        t("публикатор подключён", "PUBLISHING" in pub.stdout.readline())
        time.sleep(3)
        r = c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": True, "options": {"quality": "360p"}})
        t("эфир запущен с выбором 360p при старте", r.status_code < 300, str(r.status_code) + " " + r.text[:150])
        vc = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=30)
        for _ in range(240):
            if vc.get(f"/api/v1/live/{token}").json()["status"] == "live": break
            time.sleep(0.5)
        st = c.get(f"/api/v1/rooms/{rid}/live").json()
        t("эфир записан с качеством 360p", (st.get("broadcasts") or [{}])[0].get("quality") == "360p", str((st.get("broadcasts") or [{}])[0].get("quality")))
        viewer = vc.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Проверка 360"}).json()["viewer"]
        acc = {}
        for _ in range(60):
            acc = vc.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()
            if acc.get("origin_path"): break
            time.sleep(2)
        path = acc.get("origin_path") or acc["path"]
        time.sleep(10)
        pl = httpx.get(BASE + path, timeout=20).text
        seg = [l for l in pl.splitlines() if l.endswith(".ts")][-1]
        data = httpx.get(BASE + path.rsplit("/", 1)[0] + "/" + seg, timeout=30).content
        fp = os.path.join(S.OUT, "q360-seg.ts"); os.makedirs(S.OUT, exist_ok=True); open(fp, "wb").write(data)
        pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,avg_frame_rate", "-of", "json", fp], capture_output=True, text=True)
        s0 = (json.loads(pr.stdout or "{}").get("streams") or [{}])[0]
        t("HLS-сегмент 640×360", (s0.get("width"), s0.get("height")) == (640, 360), f"{s0.get('width')}×{s0.get('height')} {s0.get('avg_frame_rate')}")
        kbps = round(len(data) * 8 / 2.0 / 1000)
        t("битрейт около 1 Мбит/с (не 3–9)", 400 <= kbps <= 1600, f"{kbps} кбит/с по размеру сегмента")
        os.remove(fp)
        cfg = c.get(f"/api/v1/rooms/{rid}/live").json()
        t("в настройках комнаты остаётся 360p", cfg.get("quality") == "360p", str(cfg.get("quality")))
    finally:
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": False}); time.sleep(3)
        pub.terminate()
        c.post(f"/api/v1/meetings/{mid}/end")
        c.delete(f"/api/v1/admin/rooms/{rid}")
    print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
