"""Сценарий (см. README.md): экран перед входом — слева крупный предпросмотр камеры 16:9, справа настройки, окно по центру, без прокрутки на 1366×768 и 1920×1080, на телефоне — предпросмотр сверху."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/hr"
GEO = """(()=>{const r=e=>{if(!e)return null;const x=e.getBoundingClientRect();return {x:Math.round(x.left),y:Math.round(x.top),w:Math.round(x.width),h:Math.round(x.height)}};
 const sel=[...document.querySelectorAll('.pc-card select')].map(s=>({h:Math.round(s.getBoundingClientRect().height),fs:getComputedStyle(s).fontSize}));
 return {iw:innerWidth,ih:innerHeight,sh:document.documentElement.scrollHeight,card:r(document.querySelector('.pj-card')),prev:r(document.querySelector('.pj-video')),video:r(document.querySelector('.pj-video-el')),vis:!!document.querySelector('.pj-video-el')&&!document.querySelector('.pj-video-el').hidden,
  oldvideo:!!document.querySelector('.precheck-video'),camcard:r([...document.querySelectorAll('.pc-card')].find(c=>c.innerText.includes('Камера')&&c.querySelector('select'))),sel,hw:r(document.querySelector('.pj-hw')),
  ph:document.querySelector('.pj-video-ph')?.innerText||'',cta:r(document.querySelector('.cta'))}})()"""

async def main():
    A = S.Client("A", "alice", "alice-pass", 9651)
    try:
        await A.start(); await A.goto(S.ROOM, 2.5)
        await A.wait_for("!!document.querySelector('.pj-video')", 20)
        g = await A.js(GEO)
        S.check("слева заглушка «камера выключена», а не пустой прямоугольник", "Камера" in g["ph"] and g["prev"] is not None, g["ph"][:80])
        await A.js("[...document.querySelectorAll('.precheck button')].filter(b=>/Разрешить доступ/.test(b.textContent)).forEach(b=>b.click())"); await asyncio.sleep(1)
        S.check("камера включается кнопкой «Проверить камеру»", await A.js("(()=>{const b=[...document.querySelectorAll('.precheck button')].find(b=>/Проверить камеру/.test(b.textContent));if(!b)return false;b.click();return true})()"))
        S.check("в предпросмотре идёт видео", await A.wait_for("(()=>{const v=document.querySelector('.pj-video-el');return !!v&&!v.hidden&&v.videoWidth>0})()", 15))
        for w, h in ((1366, 768), (1920, 1080)):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False); await asyncio.sleep(1)
            g = await A.js(GEO)
            ratio = g["prev"]["w"] / g["prev"]["h"]
            S.check(f"{w}×{h}: предпросмотр слева, пропорция 16:9, не меньше 420px в ширину", g["prev"]["x"] < g["hw"]["x"] and abs(ratio - 16 / 9) < 0.03 and g["prev"]["w"] >= 420, f"{g['prev']} {ratio:.2f}")
            S.check(f"{w}×{h}: маленького предпросмотра в карточке камеры нет", not g["oldvideo"])
            S.check(f"{w}×{h}: окно по центру по вертикали, страница не прокручивается", abs((g["card"]["y"] - 57) - (g["ih"] - (g["card"]["y"] + g["card"]["h"]))) < 90 and g["sh"] <= g["ih"] + 1, f"card={g['card']} sh={g['sh']}")
            S.check(f"{w}×{h}: списки устройств одной высоты и 13px", len({s['h'] for s in g['sel']}) == 1 and all(s['fs'] == '13px' for s in g['sel']), str(g["sel"]))
            S.check(f"{w}×{h}: кнопка входа на виду", g["cta"] is not None and g["cta"]["y"] + g["cta"]["h"] <= g["ih"])
            await A.shot(f"prejoin-{w}")
        await A.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True); await asyncio.sleep(1.2)
        g = await A.js(GEO)
        S.check("телефон: предпросмотр сверху, настройки ниже, нет горизонтальной прокрутки", g["prev"]["y"] < g["hw"]["y"] and await A.js("document.documentElement.scrollWidth<=innerWidth+1"), str(g["prev"]))
        await A.shot("prejoin-mobile")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)
        S.check("вход в переговорку работает", await A.join(navigate=False) and await A.wait_for("document.querySelectorAll('.st-cell').length>=1", 30))
        errs = [e for e in (await A.state())["errs"] if "favicon" not in e]
        S.check("ошибок страницы нет", not errs, str(errs)[:200])
    finally:
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
