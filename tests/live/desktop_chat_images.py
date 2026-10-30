"""Изображения чата в приложении для Windows (настоящий Electron под X-сервером) и в браузере: отправка из буфера обмена, получение, обмен в обе стороны.

Участники: администратор — в приложении, гость — в обычном браузере (CDP) по гостевой ссылке комнаты; так загрузивший и получатель — разные «личности»
(загрузивший всегда видит своё вложение, получатель — только отправленное и привязанное к сообщению).

Запуск: стенд (любая установка по http://localhost:PORT с ALLOWED_ORIGINS, включающим этот адрес), Xvfb, `cd desktop && node build.mjs`, затем
    DISPLAY=:77 PG_URL=http://localhost:28480 PG_ADMIN_PASSWORD=… PG_ROOM=upd-check PG_GUEST_TOKEN=… python3 tests/live/desktop_chat_images.py
Буфер обмена — настоящий буфер Electron (`clipboard.writeImage` в главном процессе) и настоящая вставка (`webContents.paste()`), а не искусственное событие."""
import asyncio, base64, json, os, struct, sys, zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, OUT, check, results  # noqa: E402
from desktop_app import login, probe_in_shell  # noqa: E402

URL = os.environ["PG_URL"]
PASSWORD = os.environ["PG_ADMIN_PASSWORD"]
ROOM = os.environ.get("PG_ROOM", "upd-check")
GUEST = os.environ["PG_GUEST_TOKEN"]
BROWSER = os.environ.get("BROWSER_PATH", "")


def png(w=160, h=90, color=(200, 40, 40), noise=False):
    """Одноцветная картинка или (noise) несжимаемый «скриншот» большого размера — так загрузка проверяется на реальном объёме."""
    raw = b"".join(b"\x00" + (os.urandom(3 * w) if noise else bytes(color) * w) for _ in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


ORIGIN = URL
HDR = {"Origin": ORIGIN, "Content-Type": "application/json", "X-Forwarded-Proto": "https"}


def gcall(method, path, token, body=None, raw=None, ctype=None):
    """Гость — отдельный участник: все запросы по X-Guest-Token (как делает страница гостя)."""
    import urllib.request
    h = dict(HDR)
    if token:
        h["X-Guest-Token"] = token
    if ctype:
        h["Content-Type"] = ctype
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(URL + "/api/v1" + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


async def chat_state(page):
    """Что видит участник: сколько картинок отрисовано и сколько «не удалось загрузить»."""
    return await page.js("""(() => ({ imgs: [...document.querySelectorAll('.chat-image img')].filter(i => i.complete && i.naturalWidth > 0).length,
      broken: document.querySelectorAll('.chat-file.broken').length, wait: document.querySelectorAll('.chat-image-wait').length,
      pending: document.querySelectorAll('.chat-pending li').length, text: document.querySelector('.chat')?.innerText.slice(-200) }))()""")


async def main():
    app = App(args=[])
    await app.start()
    try:
        shell = await app.page("app://shell/")
        await shell.wait("document.body.innerText.includes('Добавить организацию')", 30)
        await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')).click()")
        await shell.wait("!!document.querySelector('#srv')", 10)
        await shell.js("document.querySelector('#nm').value = 'Стенд'")
        await probe_in_shell(shell, URL)
        pa = await app.page(URL)
        check("вход администратора в приложении", await login(pa, "admin", PASSWORD))
        if os.environ.get("PG_RESUME", "1") == "1":
            # реальный путь пользователя: приложение перезапускают, вход возобновляется по сохранённому токену устройства (а не паролем)
            check("устройство получило токен сохранённого входа", await app.main.wait("!!globalThis.__pg.ctx.trust.get(globalThis.__pg.orgs.list[0].rec.id)", 15))
            await app.stop(keep_data=True)
            app = App(user_data=app.user_data, args=[])
            await app.start()
            pa = await app.page(URL)
            check("после перезапуска вход возобновлён без пароля", await pa.wait("!!document.querySelector('.topbar .usermenu')", 30))
        await pa.send("Page.navigate", url=f"{URL}/rooms/{ROOM}")
        await pa.wait("[...document.querySelectorAll('button')].some(x => /Войти в переговорку|Присоединиться/.test(x.textContent))", 30)
        await pa.js("[...document.querySelectorAll('button')].find(x => /Войти в переговорку|Присоединиться/.test(x.textContent)).click()")
        ok = await pa.wait("document.body.innerText.includes('Транскрипция и чат')", 40)
        check("администратор вошёл в комнату в приложении", ok)
        await asyncio.sleep(1.5)
        mid = (await pa.js("fetch('/api/v1/rooms/' + location.pathname.split('/').pop()).then(r => r.json()).then(j => j.active_meeting_id || (j.meeting && j.meeting.id) || null)")) or await pa.js(
            "fetch('/api/v1/meetings?active=1').then(r => r.json()).then(j => (j[0] || {}).id || null)")
        print("meeting:", mid)

        # --- гость входит в ту же встречу
        s, body, _ = gcall("POST", f"/guest/room/{GUEST}/join", None, {"display_name": "Гость Тест", "accepted_documents": []})
        check("гость вошёл по ссылке", s == 200, f"{s} {body[:120]!r}")
        g = json.loads(body)
        gtok, gmid = g.get("guest_token") or g.get("token"), g.get("meeting_id") or (g.get("meeting") or {}).get("id")
        mid = mid or gmid
        print("guest meeting:", gmid)

        # --- 1. Windows → веб: настоящая вставка из буфера обмена Electron
        png_a = png(1600, 900, noise=True) if os.environ.get("PG_BIG") == "1" else png(color=(200, 40, 40))
        await app.m(f"""const E = process.mainModule.require('electron'); const buf = Buffer.from('{base64.b64encode(png_a).decode()}', 'base64');
          await E.clipboard.write([new E.ClipboardItem({{ 'image/png': new Blob([buf], {{ type: 'image/png' }}) }})]); return await E.clipboard.has('image/png');""")
        await pa.js("""(() => { window.__paste = []; document.addEventListener('paste', (e) => { const cd = e.clipboardData; window.__paste.push({ types: [...cd.types], files: [...cd.files].map(f => f.name + ':' + f.type + ':' + f.size), items: [...cd.items].map(i => i.kind + ':' + i.type), target: e.target.tagName, def: e.defaultPrevented }); }, true);
          const t = document.querySelector('.chat textarea'); t.focus(); })()""")
        await app.m("const wc = pg.orgs.list[0].view.webContents; wc.paste(); return 1;")
        up = await pa.wait("document.querySelectorAll('.chat-pending li').length > 0", 10)
        print("paste events:", await pa.js("JSON.stringify(window.__paste)"), "| active:", await pa.js("document.activeElement.tagName + '.' + document.activeElement.className"))
        check("после вставки в чате появилось вложение (предпросмотр)", up)
        await pa.wait("!document.querySelector('.chat-pending .uploading, .chat-pending [data-state=uploading]')", 15)
        await asyncio.sleep(2)
        pend = await pa.js("document.querySelector('.chat-pending')?.innerText")
        print("pending:", pend)
        await pa.js("[...document.querySelectorAll('.chat button')].find(b => /Отправить/.test(b.getAttribute('aria-label') || b.title || b.textContent))?.click()")
        await asyncio.sleep(3)
        st = await chat_state(pa)
        print("отправитель:", st)
        # гость читает чат и скачивает вложение
        s, body, _ = gcall("GET", f"/meetings/{mid}/chat", gtok)
        msgs = json.loads(body) if s == 200 else []
        msgs = msgs.get("messages", msgs) if isinstance(msgs, dict) else msgs
        atts = [a for m in msgs for a in (m.get("attachments") or [])]
        mine = [a for m in msgs if m.get("author_name") != "Гость Тест" for a in (m.get("attachments") or [])]
        check("гость видит сообщение с вложением от приложения", len(mine) >= 1, f"{s} {len(mine)}")
        if mine:
            a = mine[-1]
            s, data, h = gcall("GET", f"/meetings/{mid}/chat/attachments/{a['id']}", gtok)
            check("гость скачивает картинку, отправленную из приложения", s == 200 and data[:8] == b"\x89PNG\r\n\x1a\n", f"{s} {len(data)} {a}")
            # Chromium при вставке перекодирует картинку из буфера, поэтому сравниваются не байты исходника, а размер на сервере и у скачавшего
            check("размер на сервере совпал с тем, что скачал получатель", len(data) == a["size"] and a["kind"] == "image" and a["mime"] == "image/png", f"{len(data)} vs {a['size']}")

        # --- 2. веб → Windows: гость отправляет картинку
        png_b = png(color=(40, 40, 200))
        s, body, _ = gcall("POST", f"/meetings/{mid}/chat/attachments?name=guest.png", gtok, raw=png_b, ctype="application/octet-stream")
        check("гость загрузил картинку", s == 201, f"{s} {body[:100]!r}")
        att = json.loads(body)
        s, body, _ = gcall("POST", f"/meetings/{mid}/chat", gtok, {"text": "", "attachments": [att["id"]]})
        check("гость отправил сообщение с картинкой", s == 201, f"{s} {body[:100]!r}")
        await asyncio.sleep(4)
        st2 = await chat_state(pa)
        print("приложение после сообщения гостя:", st2)
        check("приложение показывает обе картинки", st2["imgs"] >= 2 and st2["broken"] == 0, str(st2))
        await app.shot_window("chat-images")
        log = app.log()
        print([l for l in log.splitlines() if 'page.attachment' in l][-3:])
        check("журнал приложения: загрузка вложения записана (код ответа)", 'page.attachment' in log and '"op":"upload"' in log and '"result":201' in log, log[-300:])
    finally:
        await app.stop(keep_data=False)


if __name__ == "__main__":
    asyncio.run(main())
    bad = [r for r in results if not r[1]]
    print(f"\nПроверок: {len(results)}, не прошли: {len(bad)}")
    sys.exit(1 if bad else 0)
