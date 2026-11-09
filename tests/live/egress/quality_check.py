#!/usr/bin/env python3
"""Выбор качества зрителем и лестница «видео → низкое (360p) → только звук» в настоящем браузере (CDP Network.emulateNetworkConditions). Данные синтетические.
Проверяется: меню качества, ручной выбор, баннер «подождите», низкое качество по запросу, автоматический переход на низкое, а не на звук, затем на звук,
и что «Включить видео» после плохой связи держится (раньше счётчик остановок сбрасывался, и режим тут же откатывался на звук).
  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox PEREGOVORKA_URL=http://localhost:28480 python quality_check.py"""
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
PROBE = "(()=>{const e=document.querySelector('.live-stage video');return e?{t:e.currentTime,p:e.paused,w:e.videoWidth,h:e.videoHeight,m:e.dataset.mode||'video',buf:e.buffered.length?e.buffered.end(e.buffered.length-1)-e.currentTime:0}:null})()"


async def main():
    c = admin()
    c.put("/api/v1/admin/settings/live", json={"enabled": True, "max_concurrent": 3, "allow_private_destinations": True, "rtc_viewers_max": 0, "ll_hls": True, "adaptive_viewers": True}).raise_for_status()
    slug = f"q-{time.strftime('%H%M%S')}"
    rid = c.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "broadcast", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    lv = c.patch(f"/api/v1/rooms/{rid}/live", json={"live_enabled": True, "live_quality": "720p", "live_cdn": False}).json()
    token = lv["view_path"].rsplit("/", 1)[-1]
    j = c.post(f"/api/v1/rooms/{rid}/join", json={}).json()
    mid = j["meeting_id"]
    pub = subprocess.Popen([sys.executable, os.path.join(HERE, "latency_probe.py"), "publish", "--url", os.environ.get("LK_URL", "ws://127.0.0.1:27880"), "--token", j["token"], "--size", "1280x720", "--seconds", "900"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert "PUBLISHING" in pub.stdout.readline()
    await asyncio.sleep(3)
    v = Viewer("viewer", "alice", "alice-pass", 9852)

    async def wait(cond, secs, step=0.5):
        t0 = time.time()
        while time.time() - t0 < secs:
            r = await v.js(cond)
            if r:
                return time.time() - t0
            await asyncio.sleep(step)
        return None

    async def pick(label):
        """Выбирает пункт меню; пока идёт прежнее переключение, пункты закрыты — ждём до 12 с (так и должен вести себя зритель, которому сказали «подождите»)."""
        for _ in range(24):
            await v.js("(()=>{const m=document.querySelector('.live-menu');if(!m)document.querySelector('.live-q > button')?.click();})()")
            await asyncio.sleep(0.5)
            ok = await v.js(f"(()=>{{const b=[...document.querySelectorAll('.live-menu button')].find(x=>x.textContent.includes({json.dumps(label)}));if(b&&!b.disabled)b.click();return !!b&&!b.disabled}})()")
            if ok:
                return True
        return False

    mode_is = lambda m: f"(((document.querySelector('.live-stage video')||{{dataset:{{}}}}).dataset.mode||'video')==='{m}')"
    playing = "(()=>{const e=document.querySelector('.live-stage video');return !!e&&!e.paused&&e.readyState>=3})()"
    try:
        c.post(f"/api/v1/meetings/{mid}/live", json={"enabled": True}).raise_for_status()
        vc = httpx.Client(base_url=BASE, headers={"Origin": BASE}, timeout=30)
        for _ in range(240):
            if vc.get(f"/api/v1/live/{token}").json()["status"] == "live":
                break
            await asyncio.sleep(0.5)
        viewer = vc.post(f"/api/v1/live/{token}/viewer", json={"display_name": "Проверка"}).json()["viewer"]
        acc = {}
        for _ in range(60):
            acc = vc.post(f"/api/v1/live/{token}/access", json={"viewer": viewer}).json()
            if acc.get("audio_path"):
                break
            await asyncio.sleep(2)
        t("сервер предлагает дорожки (звук)", bool(acc.get("audio_path")))
        t("низкое качество не запущено, пока оно никому не нужно", "low_path" not in acc and not acc.get("low_wait"), str({k: v_ for k, v_ in acc.items() if k.startswith("low")}))
        ps = subprocess.run(["docker", "exec", "pgdoc-live-relay-1", "sh", "-c", "pgrep -fa 'libx264' | wc -l"], capture_output=True, text=True).stdout.strip()
        t("перекодирующего процесса нет", ps == "0", ps)
        # зритель просит низкое качество: процесс запускается
        w = vc.post(f"/api/v1/live/{token}/access", json={"viewer": viewer, "want_low": True}).json()
        t("запрос низкого качества принят (готовится или готово)", bool(w.get("low_wait") or w.get("low_path")), str({k: v_ for k, v_ in w.items() if k.startswith("low")}))
        ready = None
        for i in range(40):
            w = vc.post(f"/api/v1/live/{token}/access", json={"viewer": viewer, "want_low": True}).json()
            if w.get("low_path"):
                ready = i * 2.5
                break
            await asyncio.sleep(2.5)
        t("низкое качество появилось", ready is not None, f"через ≈{ready:.0f} с" if ready is not None else "нет")
        if ready is not None:
            lp = httpx.get(BASE + w["low_path"], timeout=20)
            t("плейлист низкого качества отдаётся", lp.status_code == 200 and "#EXTM3U" in lp.text, f"{lp.status_code}")
        await v.start()
        await v.goto(f"/live/{token}", 1.0)
        await v.js(f"sessionStorage.setItem('pg:live:viewer:{token}', {json.dumps(viewer)}); localStorage.removeItem('pg:live:quality'); location.reload()")
        await asyncio.sleep(4)
        await v.js("document.querySelector('.live-stage video')?.play?.()")
        await asyncio.sleep(12)
        t("хорошая связь: на старте режим не прыгает (остаёмся на видео)", await v.js(mode_is("video")), str(await v.js(DBG)))
        t("хорошая связь: идёт видео 720p", await wait(f"{playing} && document.querySelector('.live-stage video').videoHeight>420", 40) is not None)
        # --- меню качества
        await v.js("document.querySelector('.live-q > button')?.click()")
        await asyncio.sleep(0.5)
        items = await v.js("[...document.querySelectorAll('.live-menu [role=menuitemradio]')].map(x=>x.querySelector('.qn').textContent.trim())")
        t("меню качества: Авто, Высокое, Низкое, Только звук", bool(items) and len(items) == 4 and items[0].startswith("Авто") and "Низкое" in items[2], str(items))
        t("Авто отмечено выбранным", bool(await v.js("document.querySelector('.live-menu [aria-checked=true] .qn')?.textContent.startsWith('Авто')")))
        # --- ручное «Низкое»
        t("выбор «Низкое»", bool(await pick("Низкое")))
        banner = await wait("(()=>{const x=document.querySelector('.live-pill,.live-audio');return !!x&&/Включаем|Готовим|подождите/i.test(x.textContent)})()", 6)
        t("после выбора сразу показано «подождите»", banner is not None, f"{banner:.1f} с" if banner is not None else "")
        await v.js("document.querySelector('.live-q > button')?.click()")
        await asyncio.sleep(0.4)
        t("пока идёт переключение, пункты меню закрыты (кнопки не конфликтуют)", bool(await v.js("(()=>{const b=[...document.querySelectorAll('.live-menu button')];return b.length>0&&b.every(x=>x.disabled)})()")))
        await v.js("document.querySelector('.live-menu') && document.querySelector('.live-q > button')?.click()")
        low = await wait(f"{mode_is('low')} && {playing} && document.querySelector('.live-stage video').videoHeight>0 && document.querySelector('.live-stage video').videoHeight<=420", 90)
        t("низкое качество идёт (картинка ≤360p, время движется)", low is not None, f"через {low:.0f} с" if low is not None else str(await v.js(DBG)))
        await asyncio.sleep(25)
        t("ручной выбор не отменяется автоматикой", await v.js(mode_is("low")), str(await v.js(DBG)))
        # --- ручное «Высокое»
        t("выбор «Высокое»", bool(await pick("Высокое")))
        hi = await wait(f"{mode_is('video')} && {playing} && document.querySelector('.live-stage video').videoHeight>420", 60)
        t("обычное качество вернулось (720p)", hi is not None, f"через {hi:.0f} с" if hi is not None else str(await v.js(PROBE)))
        # --- Авто, связь 1100 кбит/с: низкое, не звук
        t("выбор «Авто»", bool(await pick("Авто")))
        await asyncio.sleep(3)
        await v.send("Network.enable")
        await v.send("Network.emulateNetworkConditions", offline=False, latency=120, downloadThroughput=1100 * 125, uploadThroughput=1100 * 125)
        down = await wait(f"{mode_is('low')} || {mode_is('audio')}", 150)
        t("связь 1100 кбит/с: автоматика перешла на ступень ниже", down is not None, f"через {down:.0f} с" if down is not None else str(await v.js(DBG)))
        # если процесс низкого качества к этому моменту остановлен (он живёт только по запросу), автоматика не ждёт сервер: сразу звук, а затем, когда низкое качество готово, поднимается к нему
        low_in = await wait(mode_is("low"), 100)
        t("…в итоге низкое качество (360p): либо сразу, либо через звук, когда процесс подготовлен", low_in is not None, str(await v.js(DBG)))
        await asyncio.sleep(20)
        p = await v.js(PROBE)
        t("на низком качестве картинка идёт", bool(p) and not p["p"] and p["w"] > 0 and p["h"] <= 420, str(p))
        # --- связь 250 кбит/с: звук
        await v.send("Network.emulateNetworkConditions", offline=False, latency=250, downloadThroughput=250 * 125, uploadThroughput=250 * 125)
        aud = await wait(mode_is("audio"), 150)
        t("связь 250 кбит/с: перешли на «только звук»", aud is not None, f"через {aud:.0f} с" if aud is not None else str(await v.js(DBG)))
        await asyncio.sleep(3)
        pa, pb = await v.js(PROBE), None
        await asyncio.sleep(10)
        pb = await v.js(PROBE)
        t("на звуке: время движется", bool(pa and pb) and pb["t"] - pa["t"] > 7 and not pb["p"], f"{pb['t'] - pa['t']:.1f} с за 10 с" if pa and pb else "")
        t("на экране звука понятное пояснение и кнопка", bool(await v.js("(()=>{const x=document.querySelector('.live-audio');return !!x&&/Связь с эфиром слабая/.test(x.textContent)&&!!x.querySelector('button')})()")))
        # --- связь вернулась; сразу жмём «Включить видео» (раньше счётчик остановок сбрасывался и режим откатывался на звук)
        await v.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        await asyncio.sleep(2)
        await v.js("document.querySelector('.live-audio button')?.click()")
        ban = await wait("(()=>{const x=document.querySelector('.live-audio,.live-pill');return !!x&&/Включаем видео/.test(x.textContent)})()", 5)
        t("«Включить видео»: сразу видно «Включаем видео… подождите»", ban is not None, f"{ban:.1f} с" if ban is not None else "")
        vid = await wait(f"{mode_is('video')} && {playing} && document.querySelector('.live-stage video').videoHeight>420 && !document.querySelector('.live-audio')", 60)
        t("видео включилось", vid is not None, f"через {vid:.0f} с" if vid is not None else str(await v.js(DBG)))
        bounced = False
        for _ in range(60):
            if not await v.js(mode_is("video")):
                bounced = True
                break
            await asyncio.sleep(1)
        t("видео держится 60 с, назад на звук не откатывается", not bounced)
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
