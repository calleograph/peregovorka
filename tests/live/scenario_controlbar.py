"""Сценарий (см. README.md): панель управления встречей не «прыгает». Два браузера (участник и администратор) входят в комнату, затем меняются состояния
(рука, микрофон, камера, показ экрана, запись, транскрибация) — после каждого изменения измеряется положение каждой кнопки: по горизонтали и по высоте панели смещений быть не должно.
Проверяется также принадлежность кнопок группам (основные — единым блоком; «Завершить для всех» и «Выйти» — отдельной подгруппой) и подвал страницы переговорок.
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

ROOM = "/rooms/mkt"
GEOM = """(()=>{const bar=document.querySelector('.rbar');if(!bar)return null;const r=b=>{const x=b.getBoundingClientRect();return [Math.round(x.left),Math.round(x.top),Math.round(x.width)]};
 const out={};for(const b of bar.querySelectorAll('.rbtn')){out[b.getAttribute('aria-label')||b.title]=r(b)}
 return {h:Math.round(bar.getBoundingClientRect().height),w:Math.round(bar.getBoundingClientRect().width),cells:[...bar.querySelectorAll('.rbtn-wrap')].map(c=>Math.round(c.getBoundingClientRect().width)),
   main:[...bar.querySelectorAll('.rbar-main .rbtn')].map(b=>b.getAttribute('aria-label')),end:[...bar.querySelectorAll('.rbar-end .rbtn')].map(b=>b.getAttribute('aria-label')),
   hasMain:!!bar.querySelector('.rbar-main'),hasEnd:!!bar.querySelector('.rbar-end'),pos:out}})()"""


MORE_JS = """(async(label)=>{document.querySelector('button[aria-label="Ещё"]').click();await new Promise(r=>setTimeout(r,250));
 const b=[...document.querySelectorAll('.ctx-menu .ctx-item')].find(x=>x.textContent.includes(label));if(!b){window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}));return false}b.click();return true})"""


async def more(c, label):
    """Действия «Ещё»: запись, транскрибация и др. вынесены из панели в меню (0.12.0)."""
    return await c.js(f"({MORE_JS})({json.dumps(label, ensure_ascii=False)})")


def key_of(label: str) -> str:
    """Кнопка меняет подпись при смене состояния: сравниваем по порядку, а не по названию."""
    return label


async def geom(c):
    return await c.js(GEOM)


def order(g):
    """Список (подпись, x, y) в порядке следования кнопок."""
    return [(k, v[0], v[1]) for k, v in g["pos"].items()]


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9561)
    R = S.Client("root", "root", "root-pass", 9562)
    try:
        await A.start(); await R.start()
        await A.goto(ROOM, 2.0); S.check("участник вошёл", await A.join(navigate=False))
        await R.goto(ROOM, 2.0); S.check("администратор вошёл", await R.join(navigate=False))
        await asyncio.sleep(3)
        for who, c in (("участник", A), ("администратор", R)):
            g0 = await geom(c)
            S.check(f"{who}: панель найдена, основные и завершающие кнопки — отдельными группами", bool(g0 and g0["hasMain"] and g0["hasEnd"]), str(g0 and (g0["main"], g0["end"]))[:140])
            S.check(f"{who}: «Выйти» в завершающей подгруппе, не среди основных", "Выйти" in g0["end"] and "Выйти" not in g0["main"])
            S.check(f"{who}: все ячейки одной ширины (ритм сетки)", len(set(g0["cells"])) == 1, str(set(g0["cells"])))
        ga = await geom(A)
        gr = await geom(R)
        S.check("у администратора «Выключить у всех» — в меню «Ещё», панель не растёт", "Ещё" in gr["main"] and "Выключить у всех" not in gr["end"], str(gr["main"])[:200])
        S.check("«Завершить для всех» у администратора — в завершающей подгруппе вместе с «Выйти»", "Завершить для всех" in gr["end"])
        await R.shot("bar-admin-default"); await A.shot("bar-participant-default")

        async def stable(c, name, act, who):
            before = await geom(c)
            await act()
            await asyncio.sleep(1.2)
            after = await geom(c)
            pos_b, pos_a = order(before), order(after)
            same_count = len(pos_b) == len(pos_a)
            xs = [(b[0], b[1], a[1]) for b, a in zip(pos_b, pos_a) if b[1] != a[1] or b[2] != a[2]]
            S.check(f"{who}: {name} — кнопки не сдвинулись по горизонтали/вертикали, высота панели та же", same_count and not xs and before["h"] == after["h"] and before["w"] == after["w"],
                    f"было h={before['h']} стало h={after['h']}; сдвинулись: {xs[:3]}")
            return after

        # поднять руку — раньше «Завершить для всех» и «Выйти» смещались
        for who, c in (("участник", A), ("администратор", R)):
            await stable(c, "«Поднять руку»", lambda c=c: c.click_btn("Поднять руку"), who)
            await stable(c, "«Опустить руку»", lambda c=c: c.click_btn("Опустить руку"), who)
        await R.shot("bar-admin-hand")
        await stable(A, "микрофон выкл → вкл", lambda: A.click_btn("Микрофон выкл.") or A.click_btn("Микрофон"), "участник")
        await stable(A, "камера", lambda: A.click_btn("Камера выкл.") or A.click_btn("Камера"), "участник")
        await stable(R, "запись: начать", lambda: more(R, "Аудиозапись: выключена"), "администратор")
        await R.shot("bar-admin-recording")
        await stable(R, "запись: остановить", lambda: more(R, "Аудиозапись: включена"), "администратор")
        await stable(R, "транскрибация: остановить", lambda: more(R, "Остановить транскрибацию"), "администратор")
        await stable(R, "транскрибация: возобновить", lambda: more(R, "Возобновить транскрибацию"), "администратор")
        # показ экрана: на стенде вместо экрана подставляется холст (см. README) — кнопка меняет подпись и «со звуком» скрывается
        await stable(A, "показ экрана: начать", lambda: A.click_btn("Показать экран"), "участник")
        await A.shot("bar-participant-screen")
        await stable(A, "показ экрана: остановить", lambda: A.click_btn("Остановить показ"), "участник")
        # «Завершить для всех»: подтверждение — окном, панель не меняется
        before = await geom(R)
        await R.click_btn("Завершить для всех")
        await asyncio.sleep(0.8)
        dlg = await R.js("!!document.querySelector('[role=dialog]') && document.querySelector('[role=dialog]').innerText.includes('Завершить встречу для всех')")
        after = await geom(R)
        S.check("подтверждение «Завершить для всех» — отдельным окном, панель управления не изменилась", bool(dlg) and before["h"] == after["h"] and order(before) == order(after))
        await R.shot("bar-admin-confirm-end")
        await R.js("[...document.querySelectorAll('[role=dialog] button')].find(b=>b.textContent.includes('Отмена'))?.click()")
        # узкое окно: ячейки по-прежнему равной ширины
        await R.send("Emulation.setDeviceMetricsOverride", width=820, height=900, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(1)
        gn = await geom(R)
        S.check("узкое окно (820 px): ячейки равной ширины, горизонтальной прокрутки нет", len(set(gn["cells"])) == 1 and await R.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), str(set(gn["cells"])))
        await R.shot("bar-admin-narrow")
        await R.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)

        # подвал страницы переговорок: название и версия слева; справа проект и ссылки на приложения для Windows и Android
        await A.goto("/", 2.5)
        FOOT = """(()=>{const f=document.querySelector('.app-foot');if(!f)return null;const sp=f.querySelectorAll('.app-foot-in > span');const m=document.querySelector('main').getBoundingClientRect();
          const a=[...f.querySelectorAll('a')].map(x=>({t:x.textContent.trim(),h:x.getAttribute('href')}));
          return {text:f.innerText,firstLeft:Math.round(sp[0].getBoundingClientRect().left),mainLeft:Math.round(m.left),lastRight:Math.round(sp[sp.length-1].getBoundingClientRect().right),links:a,
                  scroll:document.documentElement.scrollWidth>window.innerWidth+1}})()"""
        foot = await A.js(FOOT)
        S.check("подвал: слева название и версия, справа «Peregovorka (open source)» и «Что нового»", bool(foot) and "Peregovorka (open source)" in foot["text"] and "Что нового" in foot["text"] and foot["firstLeft"] <= foot["mainLeft"] + 24, str(foot)[:300])
        win = [x for x in (foot or {}).get("links", []) if x["t"].startswith("Приложение для Windows")]
        andr = [x for x in (foot or {}).get("links", []) if x["t"].startswith("Приложение для Android")]
        S.check("подвал: ссылка на установщик Windows с версией (файл .exe выпуска desktop-v…)", len(win) == 1 and "/releases/download/desktop-v" in win[0]["h"] and win[0]["h"].endswith(".exe"), str(win))
        S.check("подвал: рядом ссылка на приложение для Android — страница выпуска android-v… с версией", len(andr) == 1 and "/releases/tag/android-v" in andr[0]["h"] and andr[0]["t"].split()[-1] in andr[0]["h"], str(andr))
        await A.shot("rooms-footer")
        await A.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True); await asyncio.sleep(1.2)
        await A.js("window.scrollTo(0, document.body.scrollHeight)"); await asyncio.sleep(0.5)
        fm = await A.js(FOOT)
        S.check("подвал на 390 px: обе ссылки на месте, горизонтальной прокрутки нет", bool(fm) and not fm["scroll"] and sum(x["t"].startswith("Приложение для") for x in fm["links"]) == 2, str(fm)[:200] if fm else "нет подвала")
        await A.shot("rooms-footer-390")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)
        print("ошибки консоли:", (await A.state())["errs"][:2], (await R.state())["errs"][:2])
    finally:
        await A.close(); await R.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
