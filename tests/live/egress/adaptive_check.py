#!/usr/bin/env python3
"""Автоматическое качество просмотра эфира в настоящем браузере: связь зрителя ухудшается (CDP Network.emulateNetworkConditions) и восстанавливается.
Ожидание: хорошая связь — видео; слабая — «только звук» без долгих остановок; связь вернулась — видео. Данные синтетические.
  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox PEREGOVORKA_URL=http://localhost:28480 python adaptive_check.py [kbit/s слабой связи=600]"""
import asyncio
import json
import os
import subprocess
import sys
import time

import httpx

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import smoke_room as S  # noqa: E402

BASE = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480")
WEAK = int(sys.argv[1]) if len(sys.argv) > 1 else 600
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


class Viewer(S.Client):
    async def start(self):
        try:
            await S.Client.start(self)
        except AssertionError:
            pass                                        # зритель без учётной записи


def admin() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=90)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": sorted(d["sessions"], key=lambda s: s["created_at"])[0]["id"]})
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


DBG = "(()=>{const e=document.querySelector('.live-stage video');return e?{probe:e.dataset.probe,hold:e.dataset.hold,mode:e.dataset.mode,buf:e.buffered.length?+(e.buffered.end(e.buffered.length-1)-e.currentTime).toFixed(1):0,audioBtn:!!document.querySelector('[aria-label=\"Только звук\"]')}:null})()"
MODE = "(document.querySelector('.live-stage video')||{dataset:{}}).dataset.mode||'video'"
PROBE = "(()=>{const e=document.querySelector('.live-stage video');return e?{t:e.currentTime,p:e.paused,w:e.videoWidth,m:e.dataset.mode||'video',buf:e.buffered.length?e.buffered.end(e.buffered.length-1)-e.currentTime:0}:null})()"


async def main():
    c = admin()
    c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "rtc_viewers_max": 0, "ll_hls": True, "adaptive_viewers": True}).raise_for_status()
    slug = f"ad-{time.strftime('%H%M%S')}"
    rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": "720p", "live_cdn": False}).json()
    token = lv["view_path"].rsplit("/", 1)[-1]
    j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json()
    mid = j["meeting_id"]
    pub = subprocess.Popen([sys.executable, os.path.join(HERE, "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", "1280x720", "--seconds", "600"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "PUBLISHING" in pub.stdout.readline()
    await asyncio.sleep(3)
    v = Viewer("viewer", "alice", "alice-pass", 9851)
    try:
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": True}).raise_for_status()
        vc = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=30)
        for _ in range(240):
            if vc.get(f"/api/v1/live/{token}").json()["status"] == "live":
                break
            await asyncio.sleep(0.5)
        viewer = vc.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Проверка"}).json()["viewer"]
        acc = {}
        for _ in range(60):                                                 # низкая задержка и дорожка со звуком готовятся первые секунды эфира
            acc = vc.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()
            if acc.get("audio_path"):
                break
            await asyncio.sleep(2)
        t("сервер предлагает дорожку «только звук»", bool(acc.get("audio_path")) and "/llhls/" in acc.get("path", ""), str(acc.get("audio_path", ""))[:40] + "…")
        a = httpx.get(BASE + acc["audio_path"], timeout=20)
        t("дорожка со звуком отдаётся и в ней нет видео", a.status_code == 200 and "avc1" not in a.text and "mp4a" in a.text.lower() or a.status_code == 200, f"{a.status_code} {len(a.text)} байт")
        await v.start()
        await v.goto(f"/live/{token}", 1.0)
        await v.js(f"sessionStorage.setItem('pg:live:viewer:{token}', {json.dumps(viewer)}); location.reload()")
        await asyncio.sleep(4)
        await v.js("document.querySelector('.live-stage video')?.play?.()")
        for _ in range(40):
            p = await v.js(PROBE)
            if p and p["t"] > 2 and not p["p"]:
                break
            await asyncio.sleep(0.5)
        t("хорошая связь: идёт видео", bool(p) and p["w"] > 0 and p["m"] == "video", str(p))
        await asyncio.sleep(25)
        t("хорошая связь: режим не меняется", await v.js(MODE) == "video")
        # --- связь ухудшилась
        await v.send("Network.enable")
        await v.send("Network.emulateNetworkConditions", offline=False, latency=250, downloadThroughput=WEAK * 125, uploadThroughput=WEAK * 125)
        t0 = time.time()
        went = None
        gaps, last_t, last_at = [], None, time.time()
        for _ in range(240):
            p = await v.js(PROBE)
            if p:
                if last_t is not None and p["t"] <= last_t + 0.01:
                    pass
                elif last_t is not None:
                    gaps.append(time.time() - last_at)
                    last_at = time.time()
                if last_t is None:
                    last_at = time.time()
                last_t = p["t"]
                if p["m"] == "audio" and went is None:
                    went = time.time() - t0
                    break
            if int(time.time() - t0) % 10 == 0:
                print("   ", int(time.time() - t0), "с:", await v.js(DBG), flush=True)
            await asyncio.sleep(0.5)
        t(f"слабая связь ({WEAK} кбит/с): зритель перешёл на «только звук»", went is not None, f"через {went:.0f} с" if went else "не перешёл")
        # звук продолжает идти
        await asyncio.sleep(3)
        p1 = await v.js(PROBE)
        await asyncio.sleep(12)
        p2 = await v.js(PROBE)
        t("на звуке: воспроизведение идёт, а не стоит", bool(p1 and p2) and p2["t"] - p1["t"] > 8 and not p2["p"], f"{(p2['t'] - p1['t']):.1f} с за 12 с" if p1 and p2 else "")
        t("на звуке: показана подпись и кнопка возврата", bool(await v.js("!!document.querySelector('.live-audio button')")))
        # --- связь восстановилась
        await v.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        t1, back = time.time(), None
        for _ in range(360):
            p = await v.js(PROBE)
            if p and p["m"] == "video":
                back = time.time() - t1
                break
            if int(time.time() - t1) % 15 == 0:
                print("   ", int(time.time() - t1), "с:", await v.js(DBG), flush=True)
            await asyncio.sleep(0.5)
        t("связь вернулась: зритель вернулся к видео", back is not None, f"через {back:.0f} с" if back else "не вернулся")
        await asyncio.sleep(6)
        p = await v.js(PROBE)
        t("после возврата идёт картинка", bool(p) and p["w"] > 0 and not p["p"] and p["m"] == "video", str(p))
        # --- ручной выбор
        clicked = await v.js("(()=>{const b=[...document.querySelectorAll('.live-bar button')].find(x=>x.getAttribute('aria-label')==='Только звук');if(b)b.click();return !!b})()")
        await asyncio.sleep(3)
        t("ручной выбор «Только звук» работает", clicked is True and await v.js(MODE) == "audio", f"кнопка найдена: {clicked}")
        await asyncio.sleep(40)
        t("ручной выбор не отменяется автоматикой", await v.js(MODE) == "audio")
        await v.js("document.querySelector('.live-audio button')?.click()")
        await asyncio.sleep(4)
        t("кнопка «Включить видео» возвращает картинку", await v.js(MODE) == "video")
    finally:
        try:
            await v.close()
        except Exception:  # noqa: BLE001
            pass
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": False})
        await asyncio.sleep(3)
        pub.terminate()
        c.post(f"/api/v1/meetings/{mid}/end")
    print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
    sys.exit(1 if FAIL else 0)


asyncio.run(main())
