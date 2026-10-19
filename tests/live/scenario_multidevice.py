"""Сценарий (см. README.md): одна учётная запись на нескольких устройствах и в одной встрече.

Проверяется в настоящем Chromium с настоящим LiveKit: компьютер и «телефон» (подмена User-Agent) одной учётной записи входят в одну комнату и не вытесняют друг друга; у устройств разные подписи;
показ экрана с компьютера виден и телефону; выход телефона не выводит компьютер из встречи; перезагрузка страницы компьютера не создаёт лишних подключений; при пяти сеансах шестой вход через
интерфейс предлагает завершить один из них и не трогает остальные; «Активные устройства» в личном кабинете. Не проверяется: настоящий телефон (используется подмена UA и мышь вместо касаний).
"""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/sales"
S.INIT += """
navigator.mediaDevices.getDisplayMedia = async function () {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d'); let n = 0;
  setInterval(() => { g.fillStyle = '#134'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '60px sans-serif'; g.fillText('ЭКРАН ' + (n++), 100, 300); }, 100);
  return c.captureStream(10);
};
"""
PHONE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
TITLES = "[...document.querySelectorAll('.tile')].map(t => t.getAttribute('title') || '')"
LOGIN = "(async () => { const r = await fetch('/api/v1/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({login: %r, password: %r})}); return r.status; })()"


async def tiles(c):
    return await c.js(TITLES)


async def main():
    PC = S.Client("pc", "alice", "alice-pass", 9495)
    PH = S.Client("phone", "alice", "alice-pass", 9496)
    BOB = S.Client("bob", "bob", "bob-pass", 9497)
    D3 = S.Client("sixth", "bob", "bob-pass", 9498)
    try:
        for c in (PC, PH, BOB):
            await c.start()
        # «телефон»: другой User-Agent и новый вход (отдельная сессия), сессия компьютера при этом остаётся
        await PH.send("Network.setUserAgentOverride", userAgent=PHONE)
        S.check("телефон: отдельный вход под той же учётной записью", await PH.js(LOGIN % ("alice", "alice-pass")) == 200)
        S.check("компьютер: сессия жива после входа с телефона", await PC.js("fetch('/api/v1/auth/me').then(r => r.status)") == 200)
        S.check("компьютер вошёл в комнату", await PC.join())
        await PC.wait_for("document.querySelectorAll('.tile').length>=1", 30)
        S.check("телефон вошёл в ту же комнату", await PH.join())
        S.check("Боб вошёл в комнату", await BOB.join())
        ok = await BOB.wait_for("document.querySelectorAll('.tile').length>=3", 40)
        t = await tiles(BOB)
        S.check("у Боба три плитки: два устройства Алисы и он сам", ok, str(t))
        S.check("устройства Алисы различимы (у телефона пометка)", any("(телефон)" in x for x in t) and sum("Алиса" in x for x in t) == 2, str(t))
        await asyncio.sleep(3)
        S.check("компьютер не отключён вторым устройством (нет ошибки о дубликате)", not await PC.js("document.body.innerText.includes('уже вошла другая вкладка') || document.body.innerText.includes('с другого устройства')"))
        S.check("и компьютер, и телефон остаются в комнате", len(await tiles(PC)) >= 3 and len(await tiles(PH)) >= 3, f"{await tiles(PC)} / {await tiles(PH)}")
        # показ экрана с компьютера виден телефону и Бобу
        S.check("компьютер: «Показать экран»", await PC.click_btn("Показать экран"))
        S.check("Боб видит показ экрана", await BOB.wait_for("!!document.querySelector('.screen-stage, [class*=stage] video, .stage video')", 25))
        S.check("телефон (второе устройство Алисы) видит показ экрана с компьютера", await PH.wait_for("!!document.querySelector('.screen-stage, [class*=stage] video, .stage video')", 25))
        await BOB.shot("multidevice-stage")
        S.check("компьютер: остановил показ", await PC.click_btn("Остановить показ"))
        # телефон уходит — компьютер остаётся
        S.check("телефон: «Выйти»", await PH.click_btn("Выйти"))
        await asyncio.sleep(4)
        t = await tiles(BOB)
        S.check("после выхода телефона у Боба остались компьютер Алисы и он сам", len(t) == 2 and not any("(телефон)" in x for x in t), str(t))
        S.check("компьютер остался во встрече", len(await tiles(PC)) >= 2)
        # перезагрузка страницы компьютера (обрыв сети) — тот же участник, лишних подключений нет
        await PC.send("Page.reload"); await asyncio.sleep(3)
        S.check("компьютер снова вошёл после перезагрузки", await PC.join(navigate=False) or await PC.join())
        await BOB.wait_for("document.querySelectorAll('.tile').length>=2", 30); await asyncio.sleep(4)
        t = await tiles(BOB)
        S.check("после повторного подключения у Боба по-прежнему две плитки (дубля нет)", len(t) == 2, str(t))
        # личный кабинет: «Активные устройства»
        await PC.goto("/profile", 2.5)
        S.check("личный кабинет: список «Активные устройства»", await PC.wait_for("document.body.innerText.includes('Активные устройства') && document.querySelectorAll('.sess-list li').length >= 2", 20))
        rows = await PC.js("[...document.querySelectorAll('.sess-list li')].map(x => x.innerText)")
        S.check("в списке есть телефон и эта вкладка отмечена", any("телефон" in r for r in rows) and any("это устройство" in r for r in rows), str(rows))
        await PC.shot("multidevice-profile")
        # шестой вход через интерфейс (Боб): пять сеансов, шестой предлагает завершить один
        await D3.start()
        for _ in range(4):
            await D3.js(LOGIN % ("bob", "bob-pass"))
        await D3.send("Network.clearBrowserCookies")
        await D3.goto("/", 2.5)
        await D3.wait_for("!!document.querySelector('input[autocomplete=username]')", 20)
        await D3.js("""(() => { const set = (el, v) => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true})); };
                      set(document.querySelector('input[autocomplete=username]'), 'bob'); set(document.querySelector('input[autocomplete=current-password]'), 'bob-pass'); })()""")
        await asyncio.sleep(0.5)
        await D3.js("document.querySelector('form.px-form button.cta').click()")
        S.check("шестой вход: окно «Слишком много сеансов» со списком из пяти", await D3.wait_for("document.querySelectorAll('.sess-list li').length === 5", 20))
        await D3.shot("multidevice-limit")
        alive = await BOB.js("fetch('/api/v1/auth/me').then(r => r.status)")
        S.check("сеансы Боба не завершены автоматически (его вкладка в комнате жива)", alive == 200 and len(await tiles(BOB)) >= 1)
        await D3.js("document.querySelector('.sess-list li button').click()")
        S.check("после выбора сеанса вход выполнен", await D3.wait_for("!document.querySelector('input[autocomplete=username]') && !!document.querySelector('header, .topbar')", 25))
        left = await D3.js("fetch('/api/v1/auth/sessions').then(r => r.json()).then(j => j.items.length)")
        S.check("после входа сеансов снова пять (один завершён, новый открыт)", left == 5, str(left))
        errs = [(await c.state())["errs"] for c in (PC, PH, BOB)]
        S.check("ошибок страниц нет", not any(errs), str(errs)[:300])
    finally:
        for c in (PC, PH, BOB, D3):
            if hasattr(c, "proc"):
                await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
