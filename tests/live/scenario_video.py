"""Сценарий (см. README.md): видеозапись встречи в браузере. Стенд запущен с LIVE_FAKE_EGRESS=1: вместо LiveKit Egress при остановке ffmpeg создаёт настоящий короткий MP4 —
проверяются ПРИЛОЖЕНИЕ вокруг записи (права, меню «Аудиозапись/Видеозапись», индикатор у всех, уведомления, фрагменты, список материалов, потоковая отдача с Range),
но НЕ сама служба Egress и не качество картинки."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

MENU = """(async()=>{document.querySelector('button[aria-label="Ещё"]').click();await new Promise(r=>setTimeout(r,300));
 const t=[...document.querySelectorAll('.ctx-menu .ctx-item')].map(x=>x.textContent.trim());window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));return t})()"""
CLICK = """(async(label)=>{document.querySelector('button[aria-label="Ещё"]').click();await new Promise(r=>setTimeout(r,300));
 const b=[...document.querySelectorAll('.ctx-menu .ctx-item')].find(x=>x.textContent.includes(label));if(!b){window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));return false}b.click();return true})"""
BADGES = "[...document.querySelectorAll('.rec-badge')].map(b=>b.textContent.trim())"


async def api(c, method, path, body=None):
    js = f"(async()=>{{const me=await (await fetch('/api/v1/auth/me')).json();const r=await fetch('/api/v1{path}',{{method:'{method}',headers:{{'content-type':'application/json','X-CSRF-Token':me.csrf_token||''}},body:{json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});let j=null;try{{j=await r.json()}}catch(e){{}}return {{s:r.status,j}}}})()"
    return await c.js(js)


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9631)
    B = S.Client("bob", "bob", "bob-pass", 9632)
    R = S.Client("root", "root", "root-pass", 9633)
    try:
        await A.start(); await B.start(); await R.start()
        r = await api(R, "PUT", "/admin/settings/video", {"enabled": True, "min_free_gb": 1, "max_concurrent": 3})
        S.check("администратор включил видеозапись", r["s"] == 200 and r["j"]["enabled"] is True, str(r)[:150])
        rooms = await api(R, "GET", "/admin/rooms")
        room = next(x for x in rooms["j"] if x["slug"] == "sales")
        r = await api(R, "PATCH", f"/rooms/{room['id']}/manage", {"video_mode": "manual", "video_quality": "720p", "record_audio": True})
        S.check("режим комнаты «вручную» сохранён", r["s"] == 200 and r["j"]["video_mode"] == "manual" and r["j"]["video_enabled"] is True, str(r)[:150])
        await A.goto("/rooms/sales", 2.0); S.check("Алиса (руководитель) вошла", await A.join(navigate=False))
        await B.goto("/rooms/sales", 2.0); S.check("Борис вошёл", await B.join(navigate=False))
        await asyncio.sleep(3)
        ma, mb = await A.js(MENU), await B.js(MENU)
        S.check("у руководителя в меню «Видеозапись: выключена» и отдельная «Аудиозапись»", any("Видеозапись: выключена" in t for t in ma) and any(t.startswith("Аудиозапись") for t in ma), str(ma)[:200])
        S.check("у обычного участника кнопки видеозаписи нет", not any("Видеозапись" in t for t in mb), str(mb)[:200])
        S.check("до запуска индикатора нет", not any("ВИДЕО" in b for b in await A.js(BADGES)) and not any("ВИДЕО" in b for b in await B.js(BADGES)))

        S.check("Алиса: «Видеозапись: выключена» → запуск", await A.js(f"({CLICK})('Видеозапись: выключена')"))
        S.check("индикатор «ВИДЕОЗАПИСЬ» виден у руководителя и у участника", await A.wait_for(f"{BADGES}.some(b=>b.includes('ВИДЕОЗАПИСЬ'))", 15) and await B.wait_for(f"{BADGES}.some(b=>b.includes('ВИДЕОЗАПИСЬ'))", 15))
        S.check("у участника уведомление о начале видеозаписи", await B.wait_for("document.body.innerText.includes('Началась видеозапись встречи')", 10))
        S.check("меню руководителя: «Видеозапись: включена»", any("Видеозапись: включена" in t for t in await A.js(MENU)))
        S.check("индикатор аудиозаписи не появился (запись звука независима)", not any("АУДИОЗАПИСЬ" in b for b in await B.js(BADGES)), str(await B.js(BADGES)))
        S.check("аудиозапись включается отдельно: оба индикатора, видео не затронуто", await A.js(f"({CLICK})('Аудиозапись: выключена')")
                and await B.wait_for(f"{BADGES}.some(b=>b.includes('АУДИОЗАПИСЬ')) && {BADGES}.some(b=>b.includes('ВИДЕОЗАПИСЬ'))", 15), str(await B.js(BADGES)))
        S.check("аудиозапись выключается, видеозапись продолжается", await A.js(f"({CLICK})('Аудиозапись: включена')")
                and await B.wait_for(f"!{BADGES}.some(b=>b.includes('АУДИОЗАПИСЬ')) && {BADGES}.some(b=>b.includes('ВИДЕОЗАПИСЬ'))", 15), str(await B.js(BADGES)))
        await A.shot("video-on"); await B.shot("video-on")
        r = await api(B, "POST", "/meetings/00000000-0000-0000-0000-000000000000/video-recording", {"enabled": False})
        S.check("чужая/несуществующая встреча — 404", r["s"] == 404, str(r)[:100])

        S.check("Алиса останавливает видеозапись", await A.js(f"({CLICK})('Видеозапись: включена')"))
        S.check("индикатор исчез у всех", await A.wait_for(f"!{BADGES}.some(b=>b.includes('ВИДЕОЗАПИСЬ'))", 15) and await B.wait_for(f"!{BADGES}.some(b=>b.includes('ВИДЕОЗАПИСЬ'))", 15))
        S.check("уведомление об остановке", await B.wait_for("document.body.innerText.includes('Видеозапись встречи остановлена')", 10))
        await asyncio.sleep(3)
        st = await api(A, "GET", f"/rooms/{room['id']}/manage")
        mid = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>x.room_name==='Продажи')||{}).id})()")
        media = await api(A, "GET", f"/meetings/{mid}/media")
        vids = [m for m in media["j"]["mixes"] if m["has_video"]]
        S.check("в материалах встречи появился видеофайл: ready, MP4, размер и длительность", len(vids) == 1 and vids[0]["status"] == "ready" and vids[0]["mime"] == "video/mp4" and vids[0]["size_bytes"] > 1000 and vids[0]["duration_s"] == 6, str(vids)[:300])
        rid = vids[0]["id"]
        rng = await A.js(f"fetch('/api/v1/meetings/{mid}/media/{rid}/stream',{{headers:{{Range:'bytes=0-99'}}}}).then(async r=>({{s:r.status,ct:r.headers.get('content-type'),cr:r.headers.get('content-range'),n:(await r.arrayBuffer()).byteLength}}))")
        S.check("отдача с Range (перемотка): 206, video/mp4, 100 байт", rng["s"] == 206 and rng["ct"] == "video/mp4" and rng["n"] == 100, str(rng))
        ok = await A.js(f"""(async()=>{{const r=await fetch('/api/v1/meetings/{mid}/media/{rid}/stream');const b=await r.blob();const v=document.createElement('video');v.muted=true;v.src=URL.createObjectURL(b);
          return await new Promise(res=>{{v.onloadedmetadata=()=>res({{w:v.videoWidth,h:v.videoHeight,d:Math.round(v.duration)}});v.onerror=()=>res('error');setTimeout(()=>res('timeout'),10000)}})}})()""")
        S.check("браузер декодирует файл: 1280×720, ≈ 6 с", isinstance(ok, dict) and ok["w"] == 1280 and ok["h"] == 720 and ok["d"] == 6, str(ok))

        S.check("повторный запуск даёт второй фрагмент, первый сохранён", (await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": True}))["s"] == 200
                and (await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": False}))["s"] == 200)
        await asyncio.sleep(3)
        media = await api(A, "GET", f"/meetings/{mid}/media")
        S.check("теперь два видеофрагмента, оба ready", len([m for m in media["j"]["mixes"] if m["has_video"] and m["status"] == "ready"]) == 2, str([(m["status"], m["has_video"]) for m in media["j"]["mixes"]]))
        r = await api(B, "POST", f"/meetings/{mid}/video-recording", {"enabled": True})
        S.check("участник без прав управления получает 403", r["s"] == 403, str(r)[:100])
        r = await api(R, "GET", "/admin/video/status")
        S.check("статус для администратора: готовых файлов 2, ошибок 0", r["s"] == 200 and r["j"]["files"]["ready"] == 2 and r["j"]["files"]["failed"] == 0 and r["j"]["running"] == 0, str(r)[:250])
        await R.goto("/admin", 2.0)
    finally:
        for c in (A, B, R):
            try: await c.close()
            except Exception: pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
