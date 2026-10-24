"""Сценарий (см. README.md): корпоративные чаты в настоящем браузере. Перед запуском: стенд (devserver_live.py) и подставное распознавание
`curl -X POST $STAND/__dev/fake_asr` (на стенде нет GigaAM). Пользователи: alice (два устройства) и bob.

Проверяется: кнопка «Чаты» и окно поверх страницы; поиск сотрудника в каталоге и личный чат; доставка без перезагрузки, счётчик и уведомление;
второе устройство той же учётной записи получает сообщения и синхронизирует прочитанность; отметка «прочитано»; групповой чат; папки, закрепление,
архив и очистка из контекстного меню (только у себя); длинная история — подгрузка вверх и поиск с переходом; вставка скриншота (Ctrl+V) и файл,
просмотр изображения; диктовка через ASR сервера и «Остановить и отправить», освобождение микрофона; обрыв связи — пропущенное догружается без
дублей; чат во время видеовстречи (звонок не прерывается); тёмная тема и 390 px. Не проверяется: настоящий GigaAM, AD, несколько экземпляров backend
(см. backend/tests и docs/CHATS.md)."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

HOOKS = r"""
window.__wss = []; window.__streams = [];
const _WS = window.WebSocket;
window.WebSocket = function (...a) { const w = new _WS(...a); window.__wss.push(w); return w; };
window.WebSocket.prototype = _WS.prototype; Object.assign(window.WebSocket, { OPEN: 1, CLOSED: 3, CONNECTING: 0, CLOSING: 2 });
const _gum = navigator.mediaDevices && navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
if (_gum) navigator.mediaDevices.getUserMedia = async (c) => { const s = await _gum(c); window.__streams.push(s); return s; };
"""
R = S.RUN_ID
API = "/api/v1/chats"


async def start(c: S.Client):
    await c.start()
    await c.send("Page.addScriptToEvaluateOnNewDocument", source=HOOKS)
    await c.goto("/", 2.5)


def js_api(method, path, body=None):
    b = "undefined" if body is None else repr(body).replace("True", "true").replace("False", "false").replace("None", "null")
    return f"""(async () => {{ const me = await (await fetch('/api/v1/auth/me')).json();
      const r = await fetch('{path}', {{method: '{method}', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}}, body: {b} === undefined ? undefined : JSON.stringify({b})}});
      return {{status: r.status, body: await r.json().catch(() => null)}}; }})()"""


async def open_chats(c: S.Client):
    await c.js("document.querySelector('.chat-nav')?.click()")
    return await c.wait_for("!!document.querySelector('.chat-win')", 10)


async def type_and_send(c: S.Client, text: str):
    await c.js("document.querySelector('.composer textarea').focus()")
    await c.send("Input.insertText", text=text)
    await c.send("Input.dispatchKeyEvent", type="keyDown", key="Enter", code="Enter", windowsVirtualKeyCode=13)
    await c.send("Input.dispatchKeyEvent", type="keyUp", key="Enter", code="Enter", windowsVirtualKeyCode=13)


async def main():
    A, A2, B = S.Client("chat-a", "alice", "alice-pass", 9831), S.Client("chat-a2", "alice", "alice-pass", 9832), S.Client("chat-b", "bob", "bob-pass", 9833)
    try:
        import httpx                                                       # стенд: 300 сообщений истории не должны упереться в предел частоты
        with httpx.Client(base_url=os.environ.get("STAND_API", "http://127.0.0.1:8000"), timeout=10) as h:
            h.post("/api/v1/auth/login", json={"login": "root", "password": "root-pass"}).raise_for_status()
            h.headers["X-CSRF-Token"] = h.get("/api/v1/auth/me").json()["csrf_token"]
            h.put("/api/v1/admin/settings/chats", json={"messages_per_minute": 600}).raise_for_status()
            h.post("/api/v1/auth/logout")
        for c in (A, A2, B):
            await start(c)
        S.check("кнопка «Чаты» в верхнем меню", await A.wait_for("!!document.querySelector('.chat-nav')", 10))
        S.check("окно чатов открывается поверх страницы", await open_chats(A) and await A.js("location.pathname") == "/")
        # ---- личный чат через поиск сотрудников
        await A.js("document.querySelector('[aria-label=\"Новый чат\"]').click()")
        await A.wait_for("!!document.querySelector('[aria-label=\"Поиск сотрудников\"]')", 5)
        await A.js("document.querySelector('[aria-label=\"Поиск сотрудников\"]').focus()")
        await A.send("Input.insertText", text="Борис")
        ok = await A.wait_for("[...document.querySelectorAll('.people-row')].some(r => r.innerText.includes('Борис Мартынов'))", 8)
        S.check("поиск сотрудника по имени (каталог)", ok)
        await A.js("[...document.querySelectorAll('.people-row')].find(r => r.innerText.includes('Борис Мартынов')).click()")
        S.check("личная переписка открыта", await A.wait_for("!!document.querySelector('.composer textarea') && document.querySelector('.conv-who b')?.textContent === 'Борис Мартынов'", 8))
        await type_and_send(A, f"Привет {R}! **важно** https://example.org/x")
        S.check("сообщение видно у отправителя сразу", await A.wait_for(f"document.querySelector('.msgs').innerText.includes('Привет {R}')", 5))
        # ---- доставка без перезагрузки, счётчик, уведомление; второе устройство
        S.check("у получателя растёт счётчик без перезагрузки", await B.wait_for("document.querySelector('.chat-badge')?.textContent === '1'", 10))
        S.check("уведомление о новом сообщении в другом разделе", await B.wait_for(f"[...document.querySelectorAll('.chat-toast')].some(t => t.innerText.includes('Привет {R}'))", 6))
        S.check("второе устройство той же учётной записи получает переписку", await open_chats(A2) and
                await A2.wait_for(f"[...document.querySelectorAll('.chat-row')].some(r => r.innerText.includes('Привет {R}'))", 10))
        await open_chats(B)
        await B.wait_for("!!document.querySelector('.chat-row')", 8)
        await B.js("document.querySelector('.chat-row').click()")
        mine = f"[...document.querySelectorAll('.msg-text')].find(t => t.innerText.includes('Привет {R}'))"
        S.check("получатель видит сообщение с разметкой и ссылкой", await B.wait_for(f"!!{mine}?.querySelector('strong') && {mine}?.querySelector('a')?.href === 'https://example.org/x'", 8))
        S.check("после прочтения счётчик сброшен", await B.wait_for("document.querySelector('.chat-badge')?.hidden === true", 8))
        S.check("отправитель видит «прочитано»", await A.wait_for("!!document.querySelector('.msg.mine [aria-label=\"прочитано\"]')", 8))
        await A.js("document.querySelector('[aria-label=\"Закрыть чаты\"]').click()")         # Алиса ушла в другой раздел
        await type_and_send(B, f"Ответ {R}")
        S.check("ответ: уведомление и счётчик у отправителя без перезагрузки", await A.wait_for(f"[...document.querySelectorAll('.chat-toast')].some(t => t.innerText.includes('Ответ {R}')) && document.querySelector('.chat-badge')?.textContent === '1'", 8))
        S.check("на втором устройстве — непрочитанное", await A2.wait_for("[...document.querySelectorAll('.chat-row-n')].some(n => !n.hidden && n.textContent === '1')", 8))
        await open_chats(A)
        await A.js("[...document.querySelectorAll('.chat-row')].find(r => r.innerText.includes('Борис Мартынов')).click()")
        S.check("ответ виден в переписке", await A.wait_for(f"document.querySelector('.msgs')?.innerText.includes('Ответ {R}')", 8))
        S.check("прочитано на одном устройстве — сброшено на другом", await A2.wait_for("![...document.querySelectorAll('.chat-row-n')].some(n => !n.hidden)", 10))
        cid = await A.js(f"(async () => (await (await fetch('{API}')).json()).items.find(i => i.kind === 'direct').id)()")
        # ---- групповой чат
        await A.js("document.querySelector('[aria-label=\"Новый чат\"]').click()")
        await A.wait_for("!!document.querySelector('.seg')", 5)
        await A.js("[...document.querySelectorAll('.seg button')].find(b => b.textContent === 'Групповой').click()")
        await A.js("document.querySelector('.modal input[placeholder^=\"Например\"]').focus()")
        await A.send("Input.insertText", text=f"Группа {R}")
        await A.js("document.querySelector('[aria-label=\"Поиск сотрудников\"]').focus()")
        await A.send("Input.insertText", text="Борис")
        await A.wait_for("[...document.querySelectorAll('.people-row')].some(r => r.innerText.includes('Борис Мартынов'))", 8)
        await A.js("[...document.querySelectorAll('.people-row')].find(r => r.innerText.includes('Борис Мартынов')).click()")
        await A.js("[...document.querySelectorAll('.modal button')].find(b => b.textContent.startsWith('Создать группу')).click()")
        S.check("групповой чат создан и открыт", await A.wait_for(f"document.querySelector('.conv-who b')?.textContent === 'Группа {R}'", 8))
        S.check("участник видит новую группу без перезагрузки", await B.wait_for(f"[...document.querySelectorAll('.chat-row')].some(r => r.innerText.includes('Группа {R}'))", 10))
        # ---- папки и контекстное меню (личные настройки)
        r = await A.js(js_api("POST", f"{API}/folders/mine", {"name": f"Работа {R}"}))
        await A.wait_for(f"[...document.querySelectorAll('.chat-tab')].some(t => t.textContent === 'Работа {R}')", 6)
        row = "[...document.querySelectorAll('.chat-row')].find(r => r.innerText.includes('Борис Мартынов'))"
        await A.js(f"{row}.dispatchEvent(new MouseEvent('contextmenu', {{bubbles: true, clientX: 200, clientY: 220}}))")
        S.check("контекстное меню по правой кнопке", await A.wait_for("!!document.querySelector('.ctx-menu')", 4))
        await A.js("[...document.querySelectorAll('.ctx-item')].find(b => b.textContent.includes('Переместить в папку')).click()")
        await A.wait_for("!!document.querySelector('.ctx-sub')", 3)
        await A.js(f"[...document.querySelectorAll('.ctx-sub .ctx-item')].find(b => b.textContent.includes('Работа {R}')).click()")
        await A.js(f"[...document.querySelectorAll('.chat-tab')].find(t => t.textContent === 'Работа {R}').click()")
        S.check("переписка перемещена в папку", await A.wait_for(f"document.querySelectorAll('.chat-row').length === 1 && {row} !== undefined", 6))
        folder_b = await B.js(f"(async () => (await (await fetch('{API}')).json()).items.find(i => i.kind === 'direct').folder_id)()")
        S.check("у собеседника папка не появилась", folder_b is None, str(folder_b))
        await A.js("[...document.querySelectorAll('.chat-tab')].find(t => t.textContent === 'Все').click()")
        await A.js(f"{row}.dispatchEvent(new MouseEvent('contextmenu', {{bubbles: true, clientX: 200, clientY: 220}}))")
        await A.js("[...document.querySelectorAll('.ctx-item')].find(b => b.textContent.trim() === 'Закрепить').click()")
        S.check("закреплённая — первой в списке", await A.wait_for("document.querySelector('.chat-row')?.innerText.includes('Борис Мартынов') && !!document.querySelector('.chat-row .chat-row-ico')", 6))
        await A.js(f"{row}.dispatchEvent(new MouseEvent('contextmenu', {{bubbles: true, clientX: 200, clientY: 220}}))")
        await A.js("[...document.querySelectorAll('.ctx-item')].find(b => b.textContent.trim() === 'Архивировать').click()")
        archived = await A.wait_for(f"!{row}", 6)
        await A.js("[...document.querySelectorAll('.chat-tab')].find(t => t.textContent === 'Архив').click()")
        S.check("архивирование и вкладка «Архив»", archived and await A.wait_for(f"!!{row}", 6))
        await A.js(f"{row}.dispatchEvent(new MouseEvent('contextmenu', {{bubbles: true, clientX: 200, clientY: 220}}))")
        await A.js("[...document.querySelectorAll('.ctx-item')].find(b => b.textContent.trim() === 'Вернуть из архива').click()")
        await A.js("[...document.querySelectorAll('.chat-tab')].find(t => t.textContent === 'Все').click()")
        S.check("возврат из архива", await A.wait_for(f"!!{row}", 6))
        # ---- длинная история: 300 сообщений, подгрузка вверх и поиск
        await B.js(f"""(async () => {{ const me = await (await fetch('/api/v1/auth/me')).json();
          for (let i = 0; i < 300; i++) await fetch('{API}/{cid}/messages', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}},
            body: JSON.stringify({{text: (i % 100 === 7 ? 'квартальный отчёт ' : 'обычная строка ') + i + ' {R}', client_id: 'hist{R}x' + i}})}}); return true; }})()""")
        # второе устройство открывает переписку впервые: с сервера приходит только последняя страница, старое — по прокрутке вверх
        await A2.js(f"[...document.querySelectorAll('.chat-row')].find(r => r.innerText.includes('Борис Мартынов')).click()")
        await A2.wait_for(f"document.querySelector('.msgs')?.innerText.includes('строка 299 {R}')", 15)
        n0 = await A2.js("document.querySelectorAll('.msg').length")
        await A2.js("document.querySelector('.msgs').scrollTop = 0; document.querySelector('.msgs').dispatchEvent(new Event('scroll'))")
        ok = await A2.wait_for(f"document.querySelectorAll('.msg').length > {n0}", 8)
        await asyncio.sleep(0.3)
        top = await A2.js("document.querySelector('.msgs').scrollTop")
        S.check("длинная история подгружается вверх постранично, позиция чтения сохраняется", n0 < 300 and ok and top > 50, f"загружено {n0} из 300+, scrollTop={top}")
        await A.js(f"{row}.click()")
        await A.wait_for(f"document.querySelector('.msgs').innerText.includes('строка 299 {R}')", 15)
        await A.js("document.querySelector('[aria-label=\"Поиск по сообщениям\"]').click()")
        await A.wait_for("!!document.querySelector('.conv-search input')", 4)
        await A.js("document.querySelector('.conv-search input').focus()")
        await A.send("Input.insertText", text=f"квартальный {R}")
        ok = await A.wait_for("document.querySelector('.conv-search-count')?.textContent === '1 из 3'", 10)
        S.check("поиск по истории: число совпадений", ok, await A.js("document.querySelector('.conv-search-count')?.textContent"))
        await A.js("[...document.querySelectorAll('.conv-search-hits button')][2].click()")
        S.check("переход к найденному сообщению в истории и подсветка", await A.wait_for(f"!!document.querySelector('.msg.flash') && document.querySelector('.msg.flash').innerText.includes('квартальный отчёт 7 {R}') && !!document.querySelector('.msg-text mark')", 10))
        await A.js("document.querySelector('[aria-label=\"Закрыть поиск\"]').click()")
        # ---- вложения: вставка скриншота и файл
        await A.js("""(async () => { const cv = Object.assign(document.createElement('canvas'), {width: 120, height: 80}); const g = cv.getContext('2d'); g.fillStyle = '#2457d6'; g.fillRect(0, 0, 120, 80);
          const blob = await new Promise(r => cv.toBlob(r, 'image/png')); const dt = new DataTransfer(); dt.items.add(new File([blob], '', {type: 'image/png'}));
          const ta = document.querySelector('.composer textarea'); ta.focus(); ta.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); return true; })()""")
        S.check("скриншот из буфера обмена прикреплён", await A.wait_for("[...document.querySelectorAll('.composer-file')].some(f => !f.innerText.includes('загрузка'))", 8))
        await A.js("document.querySelector('.composer-send').click()")
        S.check("получатель видит миниатюру изображения", await B.wait_for("!!document.querySelector('.msg-thumb img')", 12))
        await B.js("document.querySelector('.msg-thumb').click()")
        S.check("изображение открывается в просмотрщике", await B.wait_for("!!document.querySelector('.iv-backdrop')", 6))
        await B.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        await B.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        pdf = os.path.join(S.OUT, f"doc-{R}.pdf")
        open(pdf, "wb").write(b"%PDF-1.4\n% test\n")
        doc = await A.send("DOM.getDocument")
        node = await A.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=".composer input[type=file]")
        await A.send("DOM.setFileInputFiles", nodeId=node["nodeId"], files=[pdf])
        await A.wait_for("[...document.querySelectorAll('.composer-file')].some(f => f.innerText.includes('.pdf') && !f.innerText.includes('загрузка'))", 8)
        await A.js("document.querySelector('.composer-send').click()")
        S.check("документ: название, размер и тип у получателя", await B.wait_for(f"[...document.querySelectorAll('.msg-file')].some(f => f.innerText.includes('doc-{R}.pdf') && f.innerText.includes('PDF'))", 12))
        href = await B.js(f"[...document.querySelectorAll('.msg-file')].find(f => f.innerText.includes('doc-{R}.pdf')).getAttribute('href')")
        st = await B.js(f"fetch('{href}').then(r => r.status)")
        S.check("скачивание вложения участником — через backend", st == 200, str(st))
        # ---- диктовка
        await A.js("document.querySelector('.composer textarea').focus()")
        await A.send("Input.insertText", text="Вручную: ")
        await A.js("document.querySelector('[aria-label=\"Диктовка\"]').click()")
        S.check("состояние «Идёт диктовка»", await A.wait_for("document.querySelector('.dictation')?.innerText.includes('Идёт диктовка')", 10))
        S.check("распознанный текст появляется в поле, ручной текст сохранён", await A.wait_for("document.querySelector('.composer textarea').value.startsWith('Вручную:') && /фраза 1/i.test(document.querySelector('.composer textarea').value)", 12),
                await A.js("document.querySelector('.composer textarea').value"))
        await A.js("[...document.querySelectorAll('.dictation button')].find(b => b.textContent.includes('Остановить и отправить')).click()")
        S.check("«Остановить и отправить» отправляет окончательный текст", await B.wait_for("[...document.querySelectorAll('.msg-text')].some(t => t.innerText.startsWith('Вручную:') && /фраза/i.test(t.innerText))", 15))
        S.check("микрофон освобождён после диктовки", await A.wait_for("window.__streams.length > 0 && window.__streams.every(s => s.getTracks().every(t => t.readyState === 'ended'))", 5))
        # ---- обрыв связи: пропущенное догружается без дублей
        await A2.js("document.querySelector('.msgs').scrollTop = 1e6")
        await A2.send("Network.emulateNetworkConditions", offline=True, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        await A2.js("window.__wss.filter(w => w.url.endsWith('/api/v1/ws')).forEach(w => w.close())")
        await asyncio.sleep(1.0)
        for k in (1, 2):
            await B.js(js_api("POST", f"{API}/{cid}/messages", {"text": f"пока нет связи {k} {R}", "client_id": f"off{R}x{k}"}))
        await asyncio.sleep(1.0)
        await A2.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        ok = await A2.wait_for(f"document.querySelector('.msgs').innerText.includes('пока нет связи 2 {R}')", 25)
        dup = await A2.js(f"[...document.querySelectorAll('.msg-text')].filter(t => t.innerText.includes('пока нет связи 1 {R}')).length")
        S.check("после обрыва связи пропущенные сообщения догружены без дублей", ok and dup == 1, f"повторов: {dup}")
        # ---- очистка и удаление только у себя
        await A.js(f"{row}.dispatchEvent(new MouseEvent('contextmenu', {{bubbles: true, clientX: 200, clientY: 220}}))")
        await A.js("[...document.querySelectorAll('.ctx-item')].find(b => b.textContent.trim() === 'Очистить историю').click()")
        await A.wait_for("!!document.querySelector('.modal')", 4)
        await A.js("[...document.querySelectorAll('.modal button')].find(b => b.textContent === 'Очистить').click()")
        cleared = await A.wait_for("document.querySelectorAll('.msg').length === 0", 8)
        b_has = await B.js(f"(async () => (await (await fetch('{API}/{cid}/messages')).json()).messages.length)()")
        S.check("очистка истории — только у себя (с подтверждением)", cleared and b_has > 0, f"у собеседника сообщений: {b_has}")
        # ---- темы и узкий экран
        await B.js("document.documentElement.setAttribute('data-theme', 'dark')")
        await asyncio.sleep(0.3)
        await B.shot("dark")
        bg = await B.js("getComputedStyle(document.querySelector('.chat-win')).backgroundColor")
        S.check("тёмная тема: окно в цветах темы", bg not in ("rgb(255, 255, 255)", ""), bg)
        await B.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
        await asyncio.sleep(0.6)
        await B.shot("mobile-conv")
        one = await B.js("getComputedStyle(document.querySelector('.chat-side')).display === 'none' && getComputedStyle(document.querySelector('.chat-main')).display !== 'none'")
        await B.js("document.querySelector('.conv-back').click()")
        await asyncio.sleep(0.4)
        back = await B.js("getComputedStyle(document.querySelector('.chat-side')).display !== 'none' && getComputedStyle(document.querySelector('.chat-main')).display === 'none'")
        sw = await B.js("document.documentElement.scrollWidth")
        await B.shot("mobile-list")
        S.check("390 px: переход между списком и перепиской, без горизонтальной прокрутки", one and back and sw <= 390, f"scrollWidth={sw}")
        await B.js("document.documentElement.setAttribute('data-theme', 'light')")
        await A.shot("desktop")
        # ---- чат во время видеовстречи
        await A.js("document.querySelector('[aria-label=\"Закрыть чаты\"]').click()")
        joined = await A.join()
        await asyncio.sleep(4)
        s0 = await A.js(S.STATS)
        await open_chats(A)
        await A.js(f"{row}.click()")
        await A.wait_for("!!document.querySelector('.composer textarea')", 6)
        await type_and_send(A, f"Пишу из встречи {R}")
        await A.js("document.querySelector('[aria-label=\"Диктовка\"]').click()")
        await A.wait_for("document.querySelector('.dictation')?.innerText.includes('Идёт диктовка')", 10)
        note = await A.js("document.querySelector('.dictation')?.innerText.includes('Вы во встрече')")
        await asyncio.sleep(2)
        await A.js("[...document.querySelectorAll('.dictation button')].find(b => b.textContent.includes('Стоп')).click()")
        await asyncio.sleep(3)
        s1 = await A.js(S.STATS)
        got = await B.wait_for(f"(async () => (await (await fetch('{API}/search/all?q=' + encodeURIComponent('Пишу из встречи {R}'))).json()).messages.length === 1)()", 10)
        S.check("чат во время встречи: сообщение доставлено, звук встречи продолжает уходить, подсказка про микрофон встречи",
                joined and got and note and s1["aout"] > s0["aout"], f"вошли={joined}, доставлено={got}, подсказка={note}, исходящие аудиопакеты {s0['aout']} → {s1['aout']}")
        for c in (A, A2, B):
            errs = await c.js("window.__errs.filter(e => !/ResizeObserver|livekit|AbortError/i.test(e)).slice(0, 5)")
            S.check(f"{c.label}: ошибок страницы нет", not errs, str(errs))
    finally:
        for c in (A, A2, B):
            await c.close()
    failed = [n for n, ok, _ in S.results if not ok]
    print(f"ИТОГО: {len(S.results) - len(failed)} из {len(S.results)}; не прошли: {failed}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
