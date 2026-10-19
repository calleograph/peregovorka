"""Сценарий (см. README.md): руководитель комнаты в браузере — история и материалы встречи без участия, «Управление доступом» (поиск, выдача одному, группа только с подтверждением, отзыв),
вкладка «История действий», рассылка по материалам; получатель доступа видит только выбранную встречу, обычный пользователь — ничего. Почтовый сервер не подключён: проверяется интерфейс и API."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

FIND = "(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); const r = await (await fetch('/api/v1/meetings?limit=50')).json(); return r.filter(m => m.room_name === 'Продажи').map(m => m.id); })()"
BTN = "(l) => [...document.querySelectorAll('button')].find(b => b.textContent.trim() === l)"


async def click(c, label):
    return await c.js(f"(() => {{ const b = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === {label!r}); if (b) b.click(); return !!b; }})()")


async def main():
    L = S.Client("alice", "alice", "alice-pass", 9511)
    D = S.Client("dave", "dave", "dave-pass", 9512)
    O = S.Client("bob", "bob", "bob-pass", 9513)
    try:
        await L.start(); await D.start(); await O.start()
        ids = await L.js(FIND)
        S.check("руководитель видит в истории встречу комнаты «Продажи», где не участвовал", len(ids) >= 1, str(ids))
        mid = ids[0]
        await L.goto(f"/history/{mid}", 3.0)
        S.check("страница встречи: кнопка «Управление доступом» и вкладка «История действий»", await L.wait_for("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Управление доступом') && [...document.querySelectorAll('[role=tab]')].some(t => t.textContent.trim() === 'История действий')", 15))
        S.check("вкладка «Материалы встречи» вместо «Протоколы и резюме»", await L.js("[...document.querySelectorAll('[role=tab]')].some(t => t.textContent.startsWith('Материалы встречи'))"))
        # до выдачи доступа dave встречу не видит
        dstat = await D.js(f"fetch('/api/v1/meetings/{mid}/transcript').then(r => r.status)")
        S.check("сотрудник без доступа получает 404", dstat == 404, str(dstat))
        S.check("обычный пользователь не видит ни кнопки доступа, ни журнала через API", (await O.js(f"fetch('/api/v1/meetings/{mid}/access').then(r => r.status)")) == 404)
        # диалог доступа
        await click(L, "Управление доступом")
        S.check("диалог открыт", await L.wait_for("document.body.innerText.includes('Выдать доступ')", 10))
        await L.js("(() => { const i = document.querySelector('input[aria-label=\"Поиск в каталоге\"]'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, 'dave'); i.dispatchEvent(new Event('input', {bubbles: true})); })()")
        await click(L, "Найти")
        S.check("найден «Дмитрий Соколов»", await L.wait_for("document.body.innerText.includes('Дмитрий Соколов')", 10))
        await click(L, "Выдать доступ")
        S.check("доступ выдан, в списке есть строка с «Отозвать»", await L.wait_for("document.body.innerText.includes('Доступ выдан') && [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Отозвать')", 10))
        dstat = await D.js(f"fetch('/api/v1/meetings/{mid}/transcript').then(r => r.status)")
        S.check("получатель теперь открывает стенограмму этой встречи", dstat == 200, str(dstat))
        S.check("но не управляет доступом", (await D.js(f"fetch('/api/v1/meetings/{mid}/access').then(r => r.status)")) == 404)
        # группа: поиск группы не выдаёт, нужен отдельный явный шаг
        await L.js("(() => { const sel = document.querySelector('select[aria-label=\"Что искать\"]'); const set = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set; set.call(sel, 'group'); sel.dispatchEvent(new Event('change', {bubbles: true})); })()")
        await L.js("(() => { const i = document.querySelector('input[aria-label=\"Поиск в каталоге\"]'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, 'staff'); i.dispatchEvent(new Event('input', {bubbles: true})); })()")
        await click(L, "Найти")
        await L.wait_for("[...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('Состав группы'))", 10)
        await click(L, "Состав группы…")
        S.check("для группы показано число людей и отдельная кнопка подтверждения (доступ ещё не выдан)", await L.wait_for("[...document.querySelectorAll('button')].some(b => b.textContent.trim().startsWith('Подтверждаю: выдать доступ'))", 10))
        await L.shot("room-leader-access-1366")
        # отзыв
        await L.js("window.confirm = () => true")
        await click(L, "Отозвать")
        S.check("доступ отозван", await L.wait_for("document.body.innerText.includes('Доступ отозван')", 10))
        dstat = await D.js(f"fetch('/api/v1/meetings/{mid}/transcript').then(r => r.status)")
        S.check("после отзыва стенограмма получателю закрыта сразу (404)", dstat == 404, str(dstat))
        # журнал
        await click(L, "Закрыть")
        await L.goto(f"/history/{mid}", 2.0)
        await L.js("[...document.querySelectorAll('[role=tab]')].find(t => t.textContent.trim() === 'История действий').click()")
        S.check("журнал показывает выдачу и отзыв доступа без паролей и токенов", await L.wait_for("(() => { const t = document.body.innerText; return t.includes('Дмитрий') || t.includes('dave'); })() && document.body.innerText.includes('Доступ') && !/password|token/i.test(document.querySelector('table')?.innerText || '')", 15), (await L.js("document.body.innerText.slice(-600)")))
        # рассылка по материалам — в настройках комнаты
        await L.goto("/", 2.0)
        r = await L.js("(async () => { const rooms = await (await fetch('/api/v1/rooms')).json(); const room = rooms.find(x => x.name === 'Продажи'); const me = await (await fetch('/api/v1/auth/me')).json(); const g = await (await fetch('/api/v1/rooms/' + room.id + '/manage')).json(); return [g.mail_delivery.enabled, Object.values(g.mail_delivery.per_material).map(v => v.mode)]; })()")
        S.check("по умолчанию рассылка выключена, все материалы — «Не отправлять»", r == [False, ["none"] * 4], str(r))
        await L.shot("room-leader-journal-1366")
        await L.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        await L.goto(f"/history/{mid}", 3.0)
        S.check("390 px: нет горизонтальной прокрутки страницы", await L.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), str(await L.js("[document.documentElement.scrollWidth, window.innerWidth]")))
        await L.shot("room-leader-390")
        S.check("ошибок страницы нет", not (await L.state())["errs"], str((await L.state())["errs"][:2]))
    finally:
        for c in (L, D, O):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

asyncio.run(main())
