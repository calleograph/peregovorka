#!/usr/bin/env python3
"""Кнопка «Звук» (общее отключение звука встречи у себя) в комнате, тестовый экземпляр. Чужой звук подставляется элементом audio с data-identity:
настоящего второго участника этот сценарий не поднимает.
  ADMIN_PW_FILE=… BROWSER_PATH=… BROWSER_FLAGS=--no-sandbox DISPLAY=:98 PEREGOVORKA_URL=http://localhost:28480 python sound_mute_check.py"""
import asyncio, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_chain as B
import smoke_room as S

PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


BTN = "[...document.querySelectorAll('.rbar button')].find(b => /^Звук/.test(b.getAttribute('aria-label')||''))"
STATE = "(() => { const b = " + BTN + "; return b ? {label: b.getAttribute('aria-label'), pressed: b.getAttribute('aria-pressed'), cls: b.className, mic: document.querySelector('.rbar button[aria-label^=\"Микрофон\"]')?.getAttribute('aria-pressed')} : null })()"
AUD = "[...document.querySelectorAll('audio[data-identity]')].map(a => a.muted)"


async def main():
    api = B.admin()
    slug = f"snd-{time.strftime('%H%M%S')}"
    rid = api.post("/api/v1/admin/rooms", json={"name": slug, "slug": slug, "room_type": "regular", "transcription_enabled": False, "acl": [], "moderators": []}).json()["id"]
    S.ROOM = f"/rooms/{slug}"
    p = B.Presenter("snd", "admin", "-", 9892)
    p.cookie = api.headers["Cookie"].split("=", 1)[1]
    try:
        await p.start()
        await p.goto(S.ROOM, 2.5)
        entered = False
        for _ in range(60):
            if await p.js("!!document.querySelector('.rbar')"):
                entered = True; break
            await p.js("[...document.querySelectorAll('button')].find(b => /^(Присоединиться|Войти в переговорку)/.test(b.textContent.trim()))?.click()")
            await p.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            await asyncio.sleep(1)
        t("вошёл в комнату", entered)
        await asyncio.sleep(3)
        s0 = await p.js(STATE)
        t("кнопка «Звук» есть в панели, звук включён", bool(s0) and s0["label"] == "Звук" and s0["pressed"] == "true", str(s0))
        order = await p.js("[...document.querySelectorAll('.rbar-main .rbtn')].map(b => b.getAttribute('aria-label'))")
        t("она стоит сразу после микрофона", order[1] == "Звук" and order[0].startswith("Микрофон"), str(order[:3]))
        await p.js("(() => { const box = document.querySelector('.hidden-audio'); for (const id of ['u-other1','u-other2']) { const a = document.createElement('audio'); a.dataset.identity = id; box.appendChild(a); } })()")
        await p.js("(" + BTN + ").click()")
        await asyncio.sleep(0.5)
        s1 = await p.js(STATE)
        t("после нажатия: «Звук выкл.», красная, не нажата", s1["label"] == "Звук выкл." and s1["pressed"] == "false" and "off" in s1["cls"], str(s1))
        t("микрофон при этом не тронут", s1["mic"] == s0["mic"], f"{s0['mic']} → {s1['mic']}")
        a1 = await p.js(AUD)
        t("все звуковые элементы заглушены", len(a1) >= 2 and all(a1), str(a1))
        await p.shot("sound-off")
        await p.js("(" + BTN + ").click()")
        await asyncio.sleep(0.5)
        s2 = await p.js(STATE)
        a2 = await p.js(AUD)
        t("повторное нажатие возвращает звук", s2["label"] == "Звук" and s2["pressed"] == "true" and not any(a2), f"{s2['label']} {a2}")
    finally:
        try: await p.close()
        except Exception: pass
        api.delete(f"/api/v1/admin/rooms/{rid}")
    print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
    sys.exit(1 if FAIL else 0)

asyncio.run(main())
