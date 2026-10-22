"""Сценарий (см. README.md): интеграция с Outlook без настоящего Outlook — надстройка (commands.js, auth.js) выполняется в настоящем браузере с поддельным Office.js,
сервер и страницы — настоящие: токен через окно входа, создание и повторное нажатие кнопки, страница обратного отсчёта /m/<код>, окно входа за N минут, перенос.
Не проверяется: настоящий Outlook, Exchange и установка манифеста (нужен Microsoft 365/Exchange)."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

ENABLE = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json();
  const r = await fetch('/api/v1/admin/settings/outlook', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}, body: JSON.stringify({enabled: true, early_minutes: 10, guest_links: true})});
  return r.status; })()"""

FAKE_OFFICE = """
window.__body = '<p>Повестка</p>'; window.__loc = ''; window.__props = {}; window.__parent = null; window.__handler = null; window.__notes = [];
window.__start = new Date(Date.now() + 3 * 3600 * 1000); window.__end = new Date(Date.now() + 3.5 * 3600 * 1000);
const ok = (v) => (cb) => cb({status: 'succeeded', value: v});
window.Office = {
  AsyncResultStatus: {Succeeded: 'succeeded'}, EventType: {DialogEventReceived: 'e', DialogMessageReceived: 'm'},
  onReady: (cb) => cb(),
  actions: {associate: (n, f) => { if (n === 'insertPeregovorkaMeeting') window.__handler = f; }},
  context: {
    ui: {messageParent: (m) => { window.__parent = m; },
         displayDialogAsync: (url, o, cb) => { throw new Error('диалог: токен должен быть уже сохранён'); }},
    mailbox: {item: {
      loadCustomPropertiesAsync: (cb) => cb({status: 'succeeded', value: {get: (k) => window.__props[k], set: (k, v) => { window.__props[k] = v; }, saveAsync: (c) => c({status: 'succeeded'})}}),
      subject: {getAsync: (cb) => cb({status: 'succeeded', value: 'Согласование бюджета'})},
      start: {getAsync: (cb) => cb({status: 'succeeded', value: window.__start})}, end: {getAsync: (cb) => cb({status: 'succeeded', value: window.__end})},
      recurrence: {getAsync: (cb) => cb({status: 'succeeded', value: null})},
      body: {getAsync: (t, cb) => cb({status: 'succeeded', value: window.__body}), setAsync: (v, o, cb) => { window.__body = v; cb({status: 'succeeded'}); }},
      location: {setAsync: (v, cb) => { window.__loc = v; cb({status: 'succeeded'}); }},
      notificationMessages: {replaceAsync: (id, m) => window.__notes.push(m.message)},
    }},
  },
};
"""
PRESS = "new Promise(res => window.__handler({completed: (o) => res(!o || o.allowEvent !== false)}))"      # команда завершается без allowEvent (оно — только для OnSend)
LOADJS = "new Promise(res => { const s = document.createElement('script'); s.src = '/outlook/commands.js'; s.onload = () => res(true); s.onerror = () => res(false); document.head.appendChild(s); })"


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    A = S.Client("alice", "alice", "alice-pass", 9495)
    B = S.Client("bob", "bob", "bob-pass", 9496)
    try:
        for c in (R, A, B):
            await c.start()
            await c.send("Network.setBlockedURLs", urls=["*appsforoffice.microsoft.com*"])        # настоящий office.js здесь не нужен: он подменён
        S.check("администратор включил интеграцию", (await R.js(ENABLE)) == 200)
        # окно входа: страница auth.html на этом сервере выпускает токен и отдаёт его «Outlook» через messageParent
        await A.send("Page.addScriptToEvaluateOnNewDocument", source=FAKE_OFFICE)
        await A.goto("/outlook/auth.html", 3.0)
        got = await A.wait_for("window.__parent !== null", 15)
        tok = json.loads(await A.js("window.__parent")) if got else {}
        S.check("окно входа выдало токен надстройки", bool(tok.get("token", "").startswith("pgo_")), str(tok)[:60])
        # команда надстройки выполняется в настоящем браузере против настоящего сервера
        await A.goto("/outlook/commands.html", 2.0)
        await A.js(f"localStorage.setItem('pg_outlook_token', JSON.stringify({{token: {json.dumps(tok.get('token'))}, exp: Date.now() + 3600000}}))")
        S.check("commands.js загрузился (CSP /outlook/ пропускает скрипт)", await A.js(LOADJS))
        S.check("команда зарегистрирована", await A.wait_for("typeof window.__handler === 'function'", 5))
        S.check("кнопка «Добавить конференцию» сработала", await A.js(PRESS) is True)
        loc = await A.js("window.__loc")
        S.check("в «Место» записана ссылка /m/<код>", "/m/" in (loc or ""), str(loc))
        code = (loc or "").rsplit("/", 1)[-1]
        S.check("в тело встречи вставлен блок со ссылкой", (await A.js("window.__body")).count("<!--pg-conf-->") == 1 and loc in await A.js("window.__body"))
        S.check("повторное нажатие не создаёт дубль блока и возвращает ту же ссылку", await A.js(PRESS) is True and (await A.js("window.__body")).count("<!--pg-conf-->") == 1 and await A.js("window.__loc") == loc)
        # страница обратного отсчёта
        await B.goto(f"/m/{code}", 3.0)
        S.check("до открытия: страница показывает название и обратный отсчёт", await B.wait_for("document.body.innerText.includes('Согласование бюджета') && document.body.innerText.includes('Подключиться можно за 10 мин')", 15))
        S.check("до открытия Боб остаётся на странице, в комнату его не пускает", await B.js("location.pathname") == f"/m/{code}")
        await B.shot("outlook-countdown")
        # перенос на «через 5 минут»: окно входа уже открыто
        await A.js("window.__start = new Date(Date.now() + 5 * 60000); window.__end = new Date(Date.now() + 35 * 60000)")
        S.check("кнопка нажата повторно после переноса времени", await A.js(PRESS) is True)
        S.check("страница сама перешла в комнату, когда вход открылся", await B.wait_for(f"location.pathname === '/rooms/ol-{code}'", 40), await B.js("location.pathname"))
        S.check("перед входом показана страница проверки камеры и микрофона", await B.wait_for("!!document.querySelector('.pj-actions .btn.primary')", 25))
        S.check("Боб вошёл в конференцию", await B.join(navigate=False))
        # перенос назад в будущее: новый вход закрыт
        await A.js("window.__start = new Date(Date.now() + 5 * 3600000); window.__end = new Date(Date.now() + 6 * 3600000)")
        S.check("повторное нажатие после переноса в будущее", await A.js(PRESS) is True)
        await asyncio.sleep(1)
        st = await B.js(f"fetch('/api/v1/outlook/public/{code}').then(r => r.json()).then(j => j.state)")
        S.check("состояние конференции с идущей встречей — live (перенос не выбрасывает участников)", st == "live", str(st))
        S.check("ошибок страницы нет", not (await B.state())["errs"], str((await B.state())["errs"][:2]))
    finally:
        for c in (R, A, B):
            await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
