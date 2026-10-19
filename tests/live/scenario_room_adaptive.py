"""Сценарий (см. README.md): адаптивность комнаты и статусы шапки. На размерах 1920×1080, 1366×768, 1280×720, 1024×768, 800×600 и 390×844:
нет горизонтальной прокрутки, шапка без «Подключено» и «Транскрибация идёт», есть индикатор сигнала, доска закрывается компактным крестиком, который виден и не перекрыт;
панель чата на телефоне закрывается; «Авто» раскладывает как «Рядом» (колонка справа). Два участника, настоящий LiveKit."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/arch"
SIZES = [(1920, 1080, False), (1366, 768, False), (1280, 720, False), (1024, 768, False), (800, 600, False), (390, 844, True)]
HEAD = """(()=>{const h=document.querySelector('.room-head');const t=h?h.innerText:'';const r=e=>{const x=e.getBoundingClientRect();return {l:Math.round(x.left),r:Math.round(x.right),t:Math.round(x.top),b:Math.round(x.bottom)}};
 return {text:t,sig:!!document.querySelector('.room-head .sig'),sigR:document.querySelector('.room-head .sig')&&r(document.querySelector('.room-head .sig')),sw:document.documentElement.scrollWidth,iw:innerWidth,
  headOver:h?[...h.children].filter(c=>{const x=c.getBoundingClientRect();return x.width>0&&(x.right>innerWidth+1||x.left<-1)}).length:-1}})()"""
CLOSE = """(()=>{const b=document.querySelector('.board-close');if(!b)return null;const x=b.getBoundingClientRect();const cx=x.left+x.width/2,cy=x.top+x.height/2;const top=document.elementFromPoint(cx,cy);
 return {x:Math.round(x.left),y:Math.round(x.top),w:Math.round(x.width),h:Math.round(x.height),inView:x.left>=0&&x.top>=0&&x.right<=innerWidth&&x.bottom<=innerHeight,free:!!top&&(top===b||b.contains(top)),cover:top&&(top.className+' '+top.tagName+' '+(top.closest('[class]')?.className||'')).slice(0,120),iw:innerWidth,ih:innerHeight}})()"""


async def vp(c, w, h, mobile):
    await c.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=mobile)
    await asyncio.sleep(1.3)


async def main():
    A = S.Client("A", "root", "root-pass", 9731)
    B = S.Client("B", "alice", "alice-pass", 9732)
    errs = {}
    try:
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.st-cell.type-camera').length>=2", 40) for c in (A, B)])
        await A.wait_for("!!document.querySelector('.room-head .sig')", 40)
        for w, h, mob in SIZES:
            await vp(A, w, h, mob)
            g = await A.js(HEAD)
            tag = f"{w}×{h}"
            S.check(f"{tag}: в шапке нет «Подключено» и «Транскрибация идёт»", "Подключено" not in g["text"] and "Транскрибация идёт" not in g["text"] and "Связь хорошая" not in g["text"], g["text"][:120])
            S.check(f"{tag}: индикатор сигнала на месте и внутри окна", g["sig"] and g["sigR"]["r"] <= g["iw"], str(g["sigR"]))
            S.check(f"{tag}: нет горизонтальной прокрутки, элементы шапки в пределах окна", g["sw"] <= g["iw"] + 1 and g["headOver"] == 0, f"{g['sw']}/{g['iw']}, за краем {g['headOver']}")
            # доска: открыть → крестик виден и не перекрыт → закрыть
            if mob:
                await A.js("(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.getAttribute('aria-label')||'')==='Доска');b&&b.click()})()")
            else:
                await A.click_btn("Доска")
            opened = await A.wait_for("!!document.querySelector('.st-board.on .board-close')", 40)
            S.check(f"{tag}: доска открывается", opened)
            if opened:
                await asyncio.sleep(1.0)
                if (w, h) in ((800, 600), (390, 844)): await A.shot(f"adaptive-board-{w}")
                c = await A.js(CLOSE)
                S.check(f"{tag}: крестик закрытия доски виден, компактный и не перекрыт", c and c["inView"] and c["free"] and c["w"] <= 40 and c["h"] <= 40, str(c))
                S.check(f"{tag}: доска не создаёт горизонтальной прокрутки", await A.js("document.documentElement.scrollWidth <= innerWidth + 1"))
                if (w, h) == (1366, 768):
                    await A.shot("adaptive-board-1366")
                    S.check("«Авто»: при открытой доске остальные участники — колонкой справа (режим «Рядом»)", await A.js("document.querySelector('.st-view')?.classList.contains('mode-side')"), str(await A.js("document.querySelector('.st-view')?.className")))
                await A.js("document.querySelector('.board-close').click()")
                S.check(f"{tag}: крестик закрывает доску", await A.wait_for("!document.querySelector('.st-board.on')", 10))
            if w <= 1000:
                S.check(f"{tag}: узкое окно — панель чата по умолчанию свёрнута", await A.js("!!document.querySelector('.transcript.collapsed')"))
                await A.js("(()=>{const b=[...document.querySelectorAll('button')].find(x=>(x.getAttribute('aria-label')||'')==='Чат');b&&b.click()})()")
                await asyncio.sleep(1.0)
                p = await A.js("""(()=>{const e=document.querySelector('.transcript:not(.collapsed)');if(!e)return null;const x=e.getBoundingClientRect();const bar=document.querySelector('.controls.rbar').getBoundingClientRect();
                  const c=e.querySelector('button[aria-label="Свернуть панель"]');const y=c&&c.getBoundingClientRect();const btns=[...document.querySelectorAll('.rbar .rbtn')];
                  const free=btns.filter(b=>{const r=b.getBoundingClientRect();const t=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return t&&(t===b||b.contains(t))}).length;
                  return {w:Math.round(x.width),iw:innerWidth,panelBottom:Math.round(x.bottom),barTop:Math.round(bar.top),btns:btns.length,free,close:y&&{in:y.left>=0&&y.right<=innerWidth&&y.top>=0&&y.bottom<=innerHeight}}})()""")
                S.check(f"{tag}: чат открыт, кнопки управления встречей не перекрыты (панель заканчивается над ними)", p and p["panelBottom"] <= p["barTop"] + 1 and p["free"] == p["btns"] and p["btns"] > 0, str(p))
                S.check(f"{tag}: кнопка закрытия панели чата доступна и панель в пределах окна", p and p["close"] and p["close"]["in"] and p["w"] <= p["iw"], str(p))
                if p and p["close"]:
                    await A.js("document.querySelector('.transcript button[aria-label=\"Свернуть панель\"]').click()"); await asyncio.sleep(0.6)
                    S.check(f"{tag}: панель чата закрывается", await A.js("!!document.querySelector('.transcript.collapsed')"))
                await A.shot(f"adaptive-chat-{w}")
            if (w, h) == (1280, 720):
                await A.shot("adaptive-1280")
        errs = {c.label: (await c.state())["errs"] for c in (A, B)}
        S.check("ошибок страницы нет", not any(errs.values()), json.dumps(errs, ensure_ascii=False)[:300])
    finally:
        for c in (A, B):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

asyncio.run(main())
