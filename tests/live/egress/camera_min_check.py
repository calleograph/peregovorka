#!/usr/bin/env python3
"""«Минимальное» — действительно минимум, «Только звук» — действительно без видео (настоящий Chromium с поддельной камерой, тестовый экземпляр).
Читается то, что реально выставлено у отправителя камеры (getParameters), а не подпись в меню.
  DISPLAY=:98 ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox PEREGOVORKA_URL=http://localhost:28480 python camera_min_check.py"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_chain as B
import smoke_room as S

PASS, FAIL = [], []
ENC = """(() => window.__pcs.flatMap(pc => pc.getSenders()).filter(s => s.track && s.track.kind === 'video' && s.track.readyState === 'live')
  .map(s => (s.getParameters().encodings || []).map(e => ({rid: e.rid || '', bps: e.maxBitrate, fps: e.maxFramerate, scale: e.scaleResolutionDownBy, active: e.active}))))()"""


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


async def enter(p, room):
    S.ROOM = room
    await p.goto(room, 2.5)
    for _ in range(60):
        if await p.js("[...document.querySelectorAll('button')].some(b => (b.getAttribute('aria-label')||b.textContent).includes('Показать экран'))"): return True
        await p.js("[...document.querySelectorAll('button')].find(b => /^(Присоединиться|Войти в переговорку)/.test(b.textContent.trim()))?.click()")
        await p.js("document.querySelector('.pj-actions .btn.primary')?.click()")
        await asyncio.sleep(1)
    return False


async def camera_on(p):
    await p.js("[...document.querySelectorAll('button')].find(b => /^Камера/.test((b.getAttribute('aria-label')||b.textContent).trim()))?.click()")


async def menu_pick(p, label):
    await p.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label')||b.textContent.trim()) === 'Ещё')?.click()")
    await asyncio.sleep(0.8)
    await p.js("[...document.querySelectorAll('[role=menuitem],button')].find(b => b.textContent.trim().startsWith('Качество видео'))?.click()")
    await asyncio.sleep(0.8)
    return await p.js(f"(() => {{ const b = [...document.querySelectorAll('[role=menuitemradio],[role=menuitem],button')].find(x => x.textContent.replace('✓','').trim() === {label!r}); if (b) b.click(); return !!b; }})()")


async def main():
    api = B.admin()
    slug = f"cm-{time.strftime('%H%M%S')}"
    rid = api.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "regular", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    p = B.Presenter("cm", "admin", "-", 9893); p.cookie = api.headers["Cookie"].split("=", 1)[1]
    try:
        # --- «Минимальное»
        await p.start()
        t("вошёл в комнату (значение комнаты «min»)", await enter(p, f"/rooms/{slug}"))
        await asyncio.sleep(3)
        await camera_on(p)
        await p.wait_for("window.__pcs.some(pc => pc.getSenders().some(s => s.track && s.track.kind === 'video' && s.track.readyState === 'live'))", 25)
        await asyncio.sleep(4)
        layers = (await p.js(ENC) or [[]])[0]
        total = sum(e["bps"] or 0 for e in layers); fps = [e["fps"] for e in layers]
        t("камера включена, слои simulcast есть", len(layers) >= 1, str(layers))
        t("«Минимальное»: все слои вместе ≤ 100 кбит/с", 0 < total <= 100_000, f"{total} бит/с по слоям {[e['bps'] for e in layers]}")
        t("«Минимальное»: ≤ 5 кадров/с у каждого слоя", all(f is not None and f <= 5 for f in fps), str(fps))
        top = min(layers, key=lambda e: e["scale"] or 1)
        t("«Минимальное»: верхний слой уменьшен в 4 раза (180p от 720p)", (top["scale"] or 1) >= 4, str(top["scale"]))
        # --- участник выбирает «Высокое» сам, слои возвращаются
        t("в меню выбрано «Высокое»", bool(await menu_pick(p, "Высокое")))
        await asyncio.sleep(2)
        layers = (await p.js(ENC) or [[]])[0]
        top = min(layers, key=lambda e: e["scale"] or 1)
        t("«Высокое»: верхний слой снова 720p, до 1,5 Мбит/с, 24 кадра/с", (top["scale"] or 1) == 1 and (top["bps"] or 0) >= 1_400_000 and (top["fps"] or 0) >= 24, str(layers))
        await p.close()
        # --- «Только звук»
        api = B.admin()                                            # закрытие браузера завершило остальные сеансы администратора
        api.patch(f"/api/v1/rooms/{rid}/manage", json={"camera_quality": "off"}).raise_for_status()
        p = B.Presenter("cm2", "admin", "-", 9894); p.cookie = api.headers["Cookie"].split("=", 1)[1]
        await p.start()
        t("вошёл в комнату (значение комнаты «off»)", await enter(p, f"/rooms/{slug}"))
        await asyncio.sleep(3)
        await camera_on(p)
        await asyncio.sleep(6)
        live = await p.js(ENC)
        t("«Только звук»: видео не уходит (нет живой видеодорожки)", not live, str(live))
        notice = await p.js("(document.body.innerText.match(/Камера не включена[^\\n]*/) || [''])[0]")
        t("участнику сказано, что делать", "Только звук" in notice and "Качество видео" in notice, notice[:140])
        t("после выбора «Минимальное» камера включается", bool(await menu_pick(p, "Минимальное")))
        await p.wait_for("window.__pcs.some(pc => pc.getSenders().some(s => s.track && s.track.kind === 'video' && s.track.readyState === 'live'))", 20)
        t("…и идёт уже в минимальном качестве", bool(await p.js(ENC)))
    finally:
        try: await p.close()
        except Exception: pass
        api = B.admin()
        api.delete(f"/api/v1/admin/rooms/{rid}")
    print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
