"""Сценарий (см. README.md): реакции и эмодзи в чате КОМНАТЫ встречи (настоящий LiveKit, комната `sales`). Алиса и Борис входят во встречу,
Алиса пишет, Борис ставит реакцию из быстрой панели, Алиса видит её без перезагрузки и присоединяется нажатием на значок; эмодзи из панели
вставляется в поле ввода; действия у сообщения — иконки; снимки светлой и тёмной темы и узкой ширины."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

R = S.RUN_ID
MSG = f"[...document.querySelectorAll('.chat-msg')].find(m => m.innerText.includes('Реакции {R}'))"


async def open_chat(c: S.Client):
    await c.click_btn("Чат")
    return await c.wait_for("!!document.querySelector('.chat textarea')", 8)


async def main():
    A, B = S.Client("rc-a", "alice", "alice-pass", 9851), S.Client("rc-b", "bob", "bob-pass", 9852)
    try:
        await A.start(); await B.start()
        S.check("оба во встрече", await A.join() and await B.join())
        await asyncio.sleep(2)
        S.check("чат открыт у обоих", await open_chat(A) and await open_chat(B))
        await A.js("document.querySelector('.chat textarea').focus()")
        await A.send("Input.insertText", text=f"Реакции {R}")
        await A.send("Input.dispatchKeyEvent", type="rawKeyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        await A.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        S.check("сообщение дошло", await B.wait_for(f"!!{MSG}", 10))
        S.check("действия у сообщения — иконки (реакция, копировать), без текстовых подписей",
                await B.js(f"(() => {{ const a = {MSG}.querySelector('.chat-acts'); return !!a && a.querySelectorAll('button').length === 2 && a.innerText.trim() === ''; }})()"))
        await B.js(f"{MSG}.querySelector('[aria-label=Реакция]').click()")
        S.check("быстрая панель реакций", await B.wait_for("document.querySelectorAll('.react-bar button').length === 7", 4))
        await B.js("[...document.querySelectorAll('.react-bar button')].find(b => b.textContent === '👍').click()")
        S.check("реакция видна автору без перезагрузки", await A.wait_for(f"{MSG}?.querySelector('.msg-react')?.innerText.replace(/\\s/g, '') === '👍1'", 8))
        await A.js(f"{MSG}.querySelector('.msg-react').click()")
        S.check("присоединиться к реакции нажатием на значок — у обоих 2, у себя выделено",
                await B.wait_for(f"{MSG}?.querySelector('.msg-react')?.innerText.replace(/\\s/g, '') === '👍2'", 8)
                and await A.wait_for(f"{MSG}?.querySelector('.msg-react.mine') !== null", 4))
        await A.js("document.querySelector('.chat [aria-label=Эмодзи]').click()")
        await A.wait_for("!!document.querySelector('.emoji-pop')", 4)
        await A.js("[...document.querySelectorAll('.emoji-grid button')].find(b => b.textContent === '😀').click()")
        S.check("эмодзи вставляется в поле ввода чата комнаты", await A.wait_for("document.querySelector('.chat textarea').value.includes('😀')", 4))
        await A.js("document.querySelector('.chat [aria-label=Эмодзи]').click()")
        await asyncio.sleep(0.3)
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=1300, deviceScaleFactor=1, mobile=False)
        await asyncio.sleep(0.6)
        await A.js(f"{MSG}.scrollIntoView({{block: 'center'}})")
        await A.shot("light")
        geo = await A.js("""(() => { const b = document.querySelector('.chat-box'); const t = b.querySelector('textarea').getBoundingClientRect();
          const bs = [...b.querySelectorAll('button')].map(x => x.getBoundingClientRect()); return {h: bs.map(x => Math.round(x.height)), bottom: bs.map(x => Math.round(x.bottom)), ta: Math.round(t.bottom)}; })()""")
        S.check("кнопки поля ввода одного размера и на одной линии", len(set(geo["h"])) == 1 and len(set(geo["bottom"])) == 1, str(geo))
        await A.js("document.documentElement.setAttribute('data-theme', 'dark')")
        await asyncio.sleep(0.3)
        await A.shot("dark")
        await B.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
        await asyncio.sleep(0.8)
        await B.click_btn("Чат")
        await asyncio.sleep(0.8)
        await B.js(f"{MSG}?.scrollIntoView({{block: 'center'}})")
        await B.shot("mobile")
        for c in (A, B):
            errs = await c.js("window.__errs.filter(e => !/ResizeObserver|livekit|AbortError/i.test(e)).slice(0, 5)")
            S.check(f"{c.label}: ошибок страницы нет", not errs, str(errs))
    finally:
        for c in (A, B):
            await c.close()
    failed = [n for n, ok, _ in S.results if not ok]
    print(f"ИТОГО: {len(S.results) - len(failed)} из {len(S.results)}; не прошли: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
