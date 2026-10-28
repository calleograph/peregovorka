"""Сбои и автозапуск на настоящем Egress: (A) падение Egress посреди записи, (C) режим «автоматически» — старт при входе и остановка пустой комнаты, (D) удаление записи."""
import asyncio, json, os, subprocess, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
os.environ.setdefault("PEREGOVORKA_URL", "http://127.0.0.1:5184")
import smoke_room as S

async def api(c, method, path, body=None):
    js = (f"(async()=>{{const me=await (await fetch('/api/v1/auth/me')).json();const r=await fetch('/api/v1{path}',{{method:'{method}',headers:{{'content-type':'application/json','X-CSRF-Token':me.csrf_token||''}},body:{json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});let j=null;try{{j=await r.json()}}catch(e){{}}return {{s:r.status,j}}}})()")
    return await c.js(js)

async def vids(c, mid):
    m = await api(c, "GET", f"/meetings/{mid}/media")
    return [x for x in m["j"]["mixes"] if x["has_video"]]

async def wait_status(c, mid, want, timeout, n=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = await vids(c, mid)
        if v and (n is None or len(v) >= n) and v[-1]["status"] in want:
            return v[-1], time.time() - t0
        await asyncio.sleep(2)
    return (await vids(c, mid) or [None])[-1], time.time() - t0

async def main():
    A = S.Client("alice", "alice", "alice-pass", 9741); B = S.Client("bob", "bob", "bob-pass", 9742); R = S.Client("root", "root", "root-pass", 9743)
    try:
        await A.start(); await B.start(); await R.start()
        await api(R, "PUT", "/admin/settings/video", {"enabled": True, "min_free_gb": 1, "max_concurrent": 2})
        room = next(x for x in (await api(R, "GET", "/admin/rooms"))["j"] if x["slug"] == "sales")
        await api(R, "PATCH", f"/rooms/{room['id']}/manage", {"video_mode": "auto", "video_quality": "720p"})
        # --- C. автозапуск
        await A.goto("/rooms/sales", 2.0); S.check("Алиса вошла", await A.join(navigate=False))
        mid = None
        for _ in range(20):
            mid = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>x.room_name==='Продажи'&&!x.ended_at)||{}).id})()")
            if mid: break
            await asyncio.sleep(1)
        ok = False
        for _ in range(30):
            st = (await api(A, "GET", f"/meetings/{mid}/video-recording"))["j"]
            if st["recording"]: ok = True; break
            await asyncio.sleep(1)
        S.check("режим «автоматически»: запись началась сама после входа первого участника", ok, str(st)[:200])
        await B.goto("/rooms/sales", 2.0); S.check("Борис вошёл", await B.join(navigate=False))
        S.check("у Бориса виден индикатор «ВИДЕОЗАПИСЬ»", await B.wait_for("[...document.querySelectorAll('.rec-badge')].some(b=>b.textContent.includes('ВИДЕОЗАПИСЬ'))", 20))
        await asyncio.sleep(12)
        # --- A. падение Egress посреди записи
        t_kill = time.time()
        subprocess.run(["docker", "kill", "pgtest-egress"], capture_output=True)
        v, dt = await wait_status(A, mid, ("ready", "failed"), 180)
        S.check("после падения Egress запись не «вечно идущая»: закрыта сверкой", bool(v) and v["status"] in ("ready", "failed"), f"{v and v['status']} за {time.time()-t_kill:.0f} с; {v and v.get('error')}")
        print("ПАДЕНИЕ:", json.dumps({k: (v or {}).get(k) for k in ("status", "error", "size_bytes", "duration_s")}, ensure_ascii=False), f"{time.time()-t_kill:.0f} с до итога")
        st = (await api(A, "GET", f"/meetings/{mid}/video-recording"))["j"]
        S.check("индикатор записи погас", not st["recording"], str(st)[:160])
        # --- возврат службы: автозапуск при новом входе
        subprocess.run(["docker", "start", "pgtest-egress"], capture_output=True)
        await asyncio.sleep(10)
        await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": True})
        v2, _ = await wait_status(A, mid, ("processing",), 30, n=2)
        S.check("после возврата службы запись снова стартует (второй фрагмент)", bool(v2) and v2["status"] == "processing" and len(await vids(A, mid)) == 2)
        await asyncio.sleep(12)
        # --- пустая комната: оба уходят
        for c in (A, B):
            await c.js("[...document.querySelectorAll('button')].filter(b=>(b.getAttribute('aria-label')||'')==='Выйти').forEach(b=>b.click())")
            await asyncio.sleep(1)
            await c.js("[...document.querySelectorAll('button')].filter(b=>/Выйти|Покинуть|Выход/.test(b.textContent)).forEach(b=>b.click())")
        t0 = time.time()
        v3, dt = await wait_status(R, mid, ("ready", "failed"), 120, n=2)
        S.check("пустая комната: запись закрыта сама и файл готов", bool(v3) and v3["status"] == "ready", f"{v3 and v3['status']} за {time.time()-t0:.0f} с; {v3 and v3.get('error')}")
        # --- D. удаление записи
        if v3:
            d = await api(R, "DELETE", f"/meetings/{mid}/recordings/{v3['id']}")
            S.check("удаление записи администратором", d["s"] in (200, 204), str(d))
            left = subprocess.run(["find", os.path.expanduser("~/egress-test/rec"), "-name", "video-2.mp4"], capture_output=True, text=True).stdout.strip()
            S.check("файл удалён с диска", left == "", left)
    finally:
        for c in (A, B, R):
            try: await c.close()
            except Exception: pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
