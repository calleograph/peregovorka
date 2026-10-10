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
        S.check("у администратора есть «Выключить у всех» ВНУТРИ основного блока (не особняком)", "Выключить у всех" in gr["main"] and "Выключить у всех" not in gr["end"], str(gr["main"])[:200])
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
        await stable(R, "запись: начать", lambda: R.click_btn("Начать запись"), "администратор")
        await R.shot("bar-admin-recording")
        await stable(R, "запись: остановить", lambda: R.click_btn("Остановить запись"), "администратор")
        await stable(R, "транскрибация: остановить", lambda: R.click_btn("Остановить транскрибацию"), "администратор")
        await stable(R, "транскрибация: возобновить", lambda: R.click_btn("Возобновить транскрибацию"), "администратор")
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

        # подвал страницы переговорок
        await A.goto("/", 2.5)
        foot = await A.js("""(()=>{const f=document.querySelector('.app-foot');if(!f)return null;const x=f.getBoundingClientRect();const m=document.querySelector('main').getBoundingClientRect();
          const first=f.querySelector('.app-foot-in > span').getBoundingClientRect();const last=f.querySelector('.app-foot-in > a').getBoundingClientRect();
          return {text:f.innerText,firstLeft:Math.round(first.left),mainLeft:Math.round(m.left),lastRight:Math.round(last.right),w:window.innerWidth,bottomGap:Math.round(window.innerHeight-x.bottom)}})()""")
        S.check("подвал: слева название, версия и ссылка на проект", bool(foot) and "Peregovorka" in foot["text"] and "Проект на GitHub" in foot["text"] and foot["firstLeft"] <= foot["mainLeft"] + 24, str(foot))
        S.check("подвал: ссылка «Что нового» справа, подпись проекта не прижата к правому краю", foot["lastRight"] > foot["firstLeft"] + 300, str(foot))
        await A.shot("rooms-footer")
        print("ошибки консоли:", (await A.state())["errs"][:2], (await R.state())["errs"][:2])
    finally:
        await A.close(); await R.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
