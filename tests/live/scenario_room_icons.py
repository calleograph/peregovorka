"""Сценарий (см. README.md): значки комнат в браузере. Администратор создаёт комнату со значком из библиотеки (применяется после создания), заменяет его своим изображением через окно кадрирования,
значок виден в списке комнат, на экране входа, в заголовке комнаты и в истории; сброс возвращает цветной квадрат с буквами; на 390 px нет горизонтальной прокрутки."""
import asyncio, io, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from PIL import Image

NAME = "Значковая комната"
SLUG = "icon-room"


def photo() -> str:
    p = os.path.join(tempfile.mkdtemp(prefix="icon-"), "photo.png")
    im = Image.new("RGB", (1600, 900), (30, 120, 220))
    for x in range(0, 1600, 40):
        for y in range(0, 900, 40):
            if (x // 40 + y // 40) % 2: im.paste((250, 200, 20), (x, y, x + 40, y + 40))
    im.save(p); return p


async def click_text(c, label, scope="document"):
    return await c.js(f"(() => {{ const b = [...{scope}.querySelectorAll('button')].find(x => x.textContent.trim() === {label!r}); if (!b) return false; b.click(); return true; }})()")


async def set_input(c, selector, value):
    await c.js(f"(() => {{ const i = document.querySelector({selector!r}); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(i, {value!r}); i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")


async def main():
    A = S.Client("A", "root", "root-pass", 9771)
    try:
        await A.start()
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await A.js("sessionStorage.setItem('adminTab', 'rooms')")
        await A.goto("/admin", 3.0)
        S.check("раздел «Комнаты» открыт", await A.wait_for("[...document.querySelectorAll('button')].some(b => b.textContent.includes('Создать комнату'))", 15))
        await click_text(A, "＋ Создать комнату")
        await A.wait_for("document.querySelector('.room-form')", 5)
        S.check("на вкладке «Основное» есть блок «Значок комнаты» со стандартным значком", await A.js("(() => { const f = [...document.querySelectorAll('fieldset')].find(x => x.querySelector('legend')?.textContent === 'Значок комнаты'); return !!f && !!f.querySelector('.room-icon.std') && ['Выбрать из библиотеки', 'Загрузить свой', 'Сбросить на стандартный'].every(t => [...f.querySelectorAll('button')].some(b => b.textContent.trim() === t)); })()"))
        await set_input(A, ".room-form input[placeholder*='Север']", NAME)
        await set_input(A, ".room-form input[placeholder='meeting-room-1']", SLUG)
        # --- библиотека: поиск, категории, предпросмотр, применение
        await click_text(A, "Выбрать из библиотеки")
        S.check("библиотека открыта: плитки значков, категории, поиск", await A.wait_for("document.querySelectorAll('.ricon-tile').length >= 100 && document.querySelectorAll('.ricon-cats button').length === 9", 10), str(await A.js("[document.querySelectorAll('.ricon-tile').length, document.querySelectorAll('.ricon-cats button').length]")))
        await set_input(A, "input[aria-label='Поиск значка']", "сервер")
        await asyncio.sleep(0.4)
        S.check("поиск «сервер» оставляет подходящие значки", await A.js("(() => { const t = [...document.querySelectorAll('.ricon-tile')]; return t.length >= 1 && t.length < 10 && t.some(x => x.title === 'Сервер'); })()"))
        await set_input(A, "input[aria-label='Поиск значка']", "")
        await click_text(A, "Безопасность", "document.querySelector('.modal')")
        await asyncio.sleep(0.3)
        S.check("фильтр категории «Безопасность» показывает только её значки", await A.js("(() => { const t = [...document.querySelectorAll('.ricon-tile')]; return t.length >= 6 && t.length <= 12 && t.some(x => x.title === 'Щит') && !t.some(x => x.title === 'Сервер'); })()"))
        await A.js("[...document.querySelectorAll('.ricon-tile')].find(x => x.title === 'Щит').click()")
        S.check("предпросмотр выбранного значка", await A.js("document.querySelectorAll('.modal .room-icon.lib').length >= 2"))
        await A.shot("room-icons-library-1366")
        await click_text(A, "Применить")
        await asyncio.sleep(0.5)
        S.check("в форме создания показан выбранный значок «из библиотеки»", await A.js("(() => { const f = [...document.querySelectorAll('fieldset')].find(x => x.querySelector('legend')?.textContent === 'Значок комнаты'); return !!f.querySelector('.room-icon.lib') && f.textContent.includes('будет применён после создания'); })()"))
        await click_text(A, "Сохранить", "document.querySelector('.room-form')")
        S.check("комната создана", await A.wait_for(f"[...document.querySelectorAll('td')].some(td => td.textContent.includes({NAME!r}))", 15))
        S.check("в списке администрирования у комнаты значок из библиотеки", await A.js(f"[...document.querySelectorAll('tr')].some(r => r.textContent.includes({NAME!r}) && r.querySelector('.room-icon.lib'))"))
        # --- список комнат: карточка
        await A.goto("/", 3.0)
        S.check("карточка комнаты показывает значок из библиотеки", await A.wait_for(f"[...document.querySelectorAll('.room-card')].some(c => c.textContent.includes({NAME!r}) && c.querySelector('.room-icon.lib'))", 15))
        # --- замена на своё изображение через кадрирование (настройки комнаты руководителя: «Основное»)
        await A.js("sessionStorage.setItem('adminTab', 'rooms')")
        await A.goto("/admin", 3.0)
        await A.js(f"[...document.querySelectorAll('tr')].find(r => r.textContent.includes({NAME!r})).querySelector('button').click()")
        await A.wait_for("document.querySelector('.room-form')", 8)
        await A.js("document.querySelector('input[aria-label=\"Файл значка\"]').scrollIntoView()")
        doc = await A.send("DOM.getDocument", depth=1)
        node = await A.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="input[aria-label='Файл значка']")
        await A.send("DOM.setFileInputFiles", files=[photo()], nodeId=node["nodeId"])
        S.check("после выбора файла открылось окно кадрирования с предпросмотром", await A.wait_for("!!document.querySelector('.crop-box canvas') && !!document.querySelector('.crop-preview canvas') && !!document.querySelector('.crop-ring.rounded')", 10))
        await A.js("(() => { const r = document.querySelector('.crop-zoom input'); const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set; set.call(r, '1.6'); r.dispatchEvent(new Event('input', {bubbles: true})); })()")
        await A.shot("room-icons-cropper-1366")
        await click_text(A, "Сохранить", "document.querySelector('.modal')")
        await asyncio.sleep(2); await A.shot("room-icons-after-upload")
        S.check("свой значок сохранён: в форме показано «Своё изображение»", await A.wait_for("(() => { const f = [...document.querySelectorAll('fieldset')].find(x => x.querySelector('legend')?.textContent === 'Значок комнаты'); return !!f && !!f.querySelector('img.room-icon') && f.textContent.includes('Своё изображение'); })()", 15))
        info = await A.js("(async () => { const rs = await (await fetch('/api/v1/rooms')).json(); const r = rs.find(x => x.name === " + json.dumps(NAME) + "); const res = await fetch(r.icon.url); const b = await res.blob(); return {kind: r.icon.kind, type: res.headers.get('content-type'), size: b.size, cache: res.headers.get('cache-control')}; })()")
        S.check("сервер хранит небольшой WEBP и отдаёт с долгим кэшем", info["kind"] == "image" and info["type"] == "image/webp" and info["size"] < 30000 and "immutable" in (info["cache"] or ""), str(info))
        dims = await A.js("(async () => { const rs = await (await fetch('/api/v1/rooms')).json(); const r = rs.find(x => x.name === " + json.dumps(NAME) + "); const im = new Image(); im.src = r.icon.url; await im.decode(); return [im.naturalWidth, im.naturalHeight]; })()")
        S.check("размер значка 256×256", dims == [256, 256], str(dims))
        # --- показ во всех местах
        await A.goto("/", 3.0)
        S.check("карточка комнаты: загруженное изображение", await A.wait_for(f"[...document.querySelectorAll('.room-card')].some(c => c.textContent.includes({NAME!r}) && c.querySelector('img.room-icon') && c.querySelector('img.room-icon').naturalWidth > 0)", 15))
        await A.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        await A.goto("/", 2.5)
        S.check("390 px: карточка со значком, нет горизонтальной прокрутки", await A.js("document.documentElement.scrollWidth <= innerWidth + 1") and await A.js(f"[...document.querySelectorAll('.room-card')].some(c => c.textContent.includes({NAME!r}) && !!c.querySelector('img.room-icon'))"))
        await A.shot("room-icons-card-390")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await A.goto(f"/rooms/{SLUG}", 3.0)
        S.check("экран входа: значок комнаты рядом с названием", await A.wait_for("!!document.querySelector('.pj-hero img.room-icon') && document.querySelector('.pj-hero img.room-icon').naturalWidth > 0", 15))
        await A.shot("room-icons-prejoin-1366")
        S.check("вход в комнату", await A.join(navigate=False))
        S.check("заголовок комнаты: значок виден", await A.wait_for("!!document.querySelector('.room-head img.room-icon')", 30))
        await A.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label')||'') === 'Выйти')?.click()")
        # --- сброс
        await A.js("sessionStorage.setItem('adminTab', 'rooms')")
        await A.goto("/admin", 3.0)
        await A.js(f"[...document.querySelectorAll('tr')].find(r => r.textContent.includes({NAME!r})).querySelector('button').click()")
        await A.wait_for("document.querySelector('.room-form')", 8)
        await click_text(A, "Сбросить на стандартный")
        S.check("сброс: снова цветной квадрат с буквами", await A.wait_for("(() => { const f = [...document.querySelectorAll('fieldset')].find(x => x.querySelector('legend')?.textContent === 'Значок комнаты'); return !!f.querySelector('.room-icon.std') && f.textContent.includes('Стандартный значок'); })()", 10))
        res = await A.js("(async () => { const rs = await (await fetch('/api/v1/rooms')).json(); const r = rs.find(x => x.name === " + json.dumps(NAME) + "); return r.icon; })()")
        S.check("на сервере значок сброшен", res is None, str(res))
        # --- обычный пользователь: изменить значок нельзя (сервер)
        B = S.Client("B", "bob", "bob-pass", 9772)
        await B.start()
        rid = await A.js("(async () => { const rs = await (await fetch('/api/v1/rooms')).json(); return rs.find(x => x.name === " + json.dumps(NAME) + ").id; })()")
        code = await B.js(f"(async () => {{ const r = {{id: {json.dumps(rid)}}}; const me = await (await fetch('/api/v1/auth/me')).json(); const res = await fetch('/api/v1/rooms/' + r.id + '/icon/library', {{method: 'PUT', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}}, body: JSON.stringify({{key: 'star'}})}}); return res.status; }})()")
        S.check("обычный участник не может изменить значок (404)", code == 404, str(code))
        await B.close()
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:2]))
    finally:
        try: await A.close()
        except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

asyncio.run(main())
