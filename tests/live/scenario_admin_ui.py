"""Сценарий (см. README.md): меню администрирования (группы по назначению), предупреждение о несохранённых изменениях в формах настроек, «Отменить изменения».
Не проверяет дизайн: смысл подсказок и порядок полей оценивает человек; автоматический обход всех разделов — audit_admin_ui.py."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

GROUPS = ["Обзор", "Встречи", "Комнаты и пользователи", "Интеграции", "Безопасность", "Хранение и журналы", "Система"]
NAV = "(t) => { const b = [...document.querySelectorAll('.admin-nav .nav-items button')].find(x => x.textContent.trim() === t); if (!b) return false; b.click(); return true; }"


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    try:
        await R.start()
        await R.goto("/admin?tab=sip_policy", 3.0)
        await R.wait_for("document.querySelectorAll('.admin-nav .nav-head').length >= 7", 15)
        got = await R.js("[...document.querySelectorAll('.admin-nav .nav-head')].map(b => b.textContent.trim())")
        S.check("группы меню администрирования соответствуют структуре", got == GROUPS, str(got))
        S.check("в «Интеграции» есть Microsoft Outlook, SIP-телефония и политика телефонии",
                await R.js("(() => { const g = [...document.querySelectorAll('.nav-group')].find(x => x.querySelector('.nav-head')?.textContent.trim() === 'Интеграции'); const t = g ? [...g.querySelectorAll('.nav-items button')].map(b => b.textContent.trim()) : []; return ['Microsoft Outlook', 'SIP-телефония', 'Политика телефонии'].every(x => t.includes(x)); })()"))
        S.check("в «Безопасность» собраны 2FA, CAPTCHA, шифрование и аудит",
                await R.js("(() => { const g = [...document.querySelectorAll('.nav-group')].find(x => x.querySelector('.nav-head')?.textContent.trim() === 'Безопасность'); const t = g ? [...g.querySelectorAll('.nav-items button')].map(b => b.textContent.trim()) : []; return t.length === 4 && t.some(x => x.startsWith('Двухфакторная')) && t.includes('CAPTCHA') && t.some(x => x.startsWith('Шифрование')) && t.some(x => x.startsWith('Журнал аудита')); })()"))
        await R.js("""(() => { const i = document.querySelector('.nav-search'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, 'телефон'); i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        S.check("поиск по меню находит SIP-телефонию и политику телефонии", await R.wait_for("(() => { const t = document.querySelector('.nav-found')?.innerText || ''; return t.includes('SIP-телефония') && t.includes('Политика телефонии'); })()", 8),
                await R.js("document.querySelector('.nav-found')?.innerText"))
        await R.js("""(() => { const i = document.querySelector('.nav-search'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, ''); i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        # несохранённые изменения
        await R.wait_for("[...document.querySelectorAll('form input')].some(i => i.type === 'number')", 15)
        S.check("до правок предупреждения нет", not await R.js("document.body.innerText.includes('Есть несохранённые изменения')"))
        await R.js("""(() => { const i = [...document.querySelectorAll('form input')].find(x => x.type === 'number'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          set.call(i, String(Number(i.value) + 1)); i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        S.check("после правки появилась отметка «Есть несохранённые изменения» и кнопка «Отменить изменения»",
                await R.wait_for("document.body.innerText.includes('Есть несохранённые изменения') && [...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Отменить изменения')", 8))
        await R.js("window.__asked = 0; window.__answer = false; window.confirm = (t) => { window.__asked++; return window.__answer; }")
        await R.js(f"({NAV})('Microsoft Outlook')")
        await asyncio.sleep(0.8)
        S.check("переход в другой раздел спросил подтверждение; на «нет» остались в форме",
                (await R.js("window.__asked")) == 1 and await R.js("document.body.innerText.includes('Политика телефонии: ограничения и журнал вызовов')"))
        await R.js("window.__answer = true")
        await R.js(f"({NAV})('Microsoft Outlook')")
        S.check("на «да» раздел переключился", await R.wait_for("document.body.innerText.includes('Состояние и проверки')", 15))
        await R.goto("/admin?tab=sip_policy", 2.5)
        await R.wait_for("[...document.querySelectorAll('form input')].some(i => i.type === 'number')", 15)
        before = await R.js("[...document.querySelectorAll('form input')].find(x => x.type === 'number').value")
        await R.js("""(() => { const i = [...document.querySelectorAll('form input')].find(x => x.type === 'number'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
          set.call(i, '77'); i.dispatchEvent(new Event('input', {bubbles: true})); })()""")
        await R.js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Отменить изменения').click()")
        S.check("«Отменить изменения» возвращает сохранённое значение и снимает отметку",
                await R.wait_for(f"[...document.querySelectorAll('form input')].find(x => x.type === 'number').value === '{before}' && !document.body.innerText.includes('Есть несохранённые изменения')", 10))
        await R.js("window.__asked = 0; window.confirm = (t) => { window.__asked++; return false; }")
        await R.js(f"({NAV})('Microsoft Outlook')")
        S.check("без правок переход происходит без вопроса", await R.wait_for("document.body.innerText.includes('Состояние и проверки')", 15) and (await R.js("window.__asked")) == 0)
        await R.shot("admin-menu-1366")
        S.check("ошибок страницы нет", not (await R.state())["errs"], str((await R.state())["errs"][:2]))
    finally:
        await R.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
