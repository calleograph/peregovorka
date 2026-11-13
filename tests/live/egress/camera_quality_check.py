#!/usr/bin/env python3
"""Качество камеры по умолчанию в комнате (тестовый экземпляр): миграция, значение новой комнаты, сохранение руководителем, конфиг клиента и применение при входе в браузере.
  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox DISPLAY=:98 PEREGOVORKA_URL=http://localhost:28480 python camera_quality_check.py"""
import asyncio, json, os, sys, time
import httpx
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_chain as B
import smoke_room as S

BASE = B.BASE
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


async def main():
    api = B.admin()
    rooms = api.get("/api/v1/admin/rooms").json()
    rooms = rooms if isinstance(rooms, list) else rooms.get("items", [])
    t("в старых комнатах после миграции — «min»", bool(rooms) and all(r.get("camera_quality") == "min" for r in rooms[:20]), f"проверено {min(20, len(rooms))} из {len(rooms)}: {sorted({r.get('camera_quality') for r in rooms[:20]})}")
    slug = f"cq-{time.strftime('%H%M%S')}"
    r = api.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "regular", "transcription_enabled": False, "acl": [], "moderators": []})
    rid = r.json()["id"]
    t("новая комната: «min» по умолчанию", r.json().get("camera_quality") == "min", str(r.json().get("camera_quality")))
    j = api.post(f"/api/v1/rooms/{rid}/join", json={}).json()
    t("конфиг клиента отдаёт camera_quality=min", j["client"].get("camera_quality") == "min", str(j["client"].get("camera_quality")))
    bad = api.patch(f"/api/v1/rooms/{rid}/manage", json={"camera_quality": "ultra"})
    t("неизвестное значение отклоняется", bad.status_code == 422, str(bad.status_code))
    ok = api.patch(f"/api/v1/rooms/{rid}/manage", json={"camera_quality": "medium"})
    t("руководитель сохраняет «medium»", ok.status_code == 200 and ok.json().get("camera_quality") == "medium", f"{ok.status_code} {ok.json().get('camera_quality') if ok.status_code == 200 else ok.text[:120]}")
    j = api.post(f"/api/v1/rooms/{rid}/join", json={}).json()
    t("вход после сохранения получает «medium»", j["client"].get("camera_quality") == "medium")
    api.patch(f"/api/v1/rooms/{rid}/manage", json={"camera_quality": "min"})
    # браузер: при входе выбрано «Минимальное» в меню «Качество видео»
    S.ROOM = f"/rooms/{slug}"
    p = B.Presenter("cq", "admin", "-", 9891)
    p.cookie = api.headers["Cookie"].split("=", 1)[1]
    try:
        await p.start()
        await p.goto(S.ROOM, 2.5)
        entered = False
        for _ in range(60):
            if await p.js("[...document.querySelectorAll('button')].some(b => (b.getAttribute('aria-label')||b.textContent).includes('Показать экран'))"):
                entered = True; break
            await p.js("[...document.querySelectorAll('button')].find(b => /^(Присоединиться|Войти в переговорку)/.test(b.textContent.trim()))?.click()")
            await p.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            await asyncio.sleep(1)
        t("вошёл в комнату в браузере", entered)
        await asyncio.sleep(3)
        await p.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label')||b.textContent.trim()) === 'Ещё')?.click()")
        await asyncio.sleep(0.8)
        await p.js("[...document.querySelectorAll('[role=menuitem],button')].find(b => b.textContent.trim().startsWith('Качество видео'))?.click()")
        await asyncio.sleep(0.8)
        checked = await p.js("[...document.querySelectorAll('[role=menuitemradio][aria-checked=true],[role=menuitem][aria-checked=true],[aria-checked=true]')].map(x => x.textContent.trim()).filter(Boolean)")
        t("в меню «Качество видео» выбрано «Минимальное» (а не «Автоматически»)", any("Минимальное" in x for x in checked) and not any(x.startswith("Автоматически") for x in checked), str(checked))
        await p.shot("camera-quality-menu")
    finally:
        try: await p.close()
        except Exception: pass
        api.delete(f"/api/v1/admin/rooms/{rid}")
    print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
