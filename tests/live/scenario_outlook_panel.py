"""Сценарий (см. README.md): боковая панель надстройки Outlook (ShowTaskpane) в настоящем браузере. Сервер, API и страницы — настоящие; вместо Outlook — подставной Office.js
(настоящий office.js с appsforoffice.microsoft.com заблокирован, чтобы не перезаписал подстановку). Проверяется: вход в панели, создание, повторное открытие, повтор без дубля,
прошедшая встреча, неверный интервал, сбой сети, сбой вставки ссылки и восстановление, истёкший вход, выход, второй фактор (окно входа), узкие ширины, тёмная тема.
Не проверяется: настоящий Outlook и Exchange (см. docs/OUTLOOK_ADDIN.md, «Совместимость»)."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

PANEL = "/outlook/taskpane.html"
H = 3600 * 1000
ENABLE = """(async (extra) => { const me = await (await fetch('/api/v1/auth/me')).json(); const h = {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token};
  const r = await fetch('/api/v1/admin/settings/outlook', {method: 'PUT', headers: h, body: JSON.stringify(Object.assign({enabled: true}, extra))}); return r.status; })"""
SECURITY = """(async (body) => { const me = await (await fetch('/api/v1/auth/me')).json();
  return (await fetch('/api/v1/admin/settings/auth_security', {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}, body: JSON.stringify(body)})).status; })"""

# Подставной Office.js: состояние встречи живёт в sessionStorage (переживает перезагрузку панели, как пользовательские свойства и текст встречи в Outlook)
FAKE = r"""
(() => {
  const S = sessionStorage, get = (k, d) => { try { const v = S.getItem('m.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } }, put = (k, v) => S.setItem('m.' + k, JSON.stringify(v));
  if (get('init', false) === false) { put('init', true); put('body', '<p>Повестка: бюджет</p>'); put('loc', ''); put('props', {}); put('start', Date.now() + 3 * 3600e3); put('end', Date.now() + 3.5 * 3600e3);
    put('subject', 'Согласование бюджета на четвёртый квартал'); put('bodyFails', false); put('dialogs', []); }
  window.__m = {get, put};
  const ok = (cb, v) => cb({status: 'succeeded', value: v});
  window.Office = {
    AsyncResultStatus: {Succeeded: 'succeeded', Failed: 'failed'}, MailboxEnums: {RestVersion: {v2_0: 'v2.0'}},
    EventType: {DialogEventReceived: 'e', DialogMessageReceived: 'm', AppointmentTimeChanged: 't', RecurrenceChanged: 'r'},
    onReady: (cb) => setTimeout(() => cb({host: 'Outlook', platform: 'PC'}), 30),
    actions: {associate: () => {}},
    context: {
      diagnostics: {host: 'Outlook', version: '16.0.18129', platform: 'PC'},
      officeTheme: get('dark', false) ? {bodyBackgroundColor: '#1f1f1f'} : {bodyBackgroundColor: '#ffffff'},
      requirements: {isSetSupported: (n, v) => (n === 'Mailbox' && parseFloat(v.split('.')[1]) <= 8) || (n === 'DialogApi' && v === '1.1')},
      ui: {messageParent: (m) => put('sent', m), displayDialogAsync: (url, o, cb) => { const d = get('dialogs', []); d.push(url); put('dialogs', d);
        cb({status: 'succeeded', value: {addEventHandler: (t, f) => { if (t === 'e') setTimeout(() => f({error: 12006}), 300); }, close: () => {}}}); }},
      mailbox: {
        userProfile: {emailAddress: 'owner@example.org'}, convertToRestId: (id) => 'rest-' + id, diagnostics: {hostName: 'Outlook', hostVersion: '16.0.18129'},
        item: {
          getItemIdAsync: (cb) => ok(cb, 'AAMkEWSITEMID0123456789'),
          loadCustomPropertiesAsync: (cb) => ok(cb, {get: (k) => get('props', {})[k], set: (k, v) => { const p = get('props', {}); p[k] = v; put('pending', p); },
            saveAsync: (c) => { put('props', get('pending', get('props', {}))); c({status: 'succeeded'}); }}),
          subject: {getAsync: (cb) => ok(cb, get('subject', ''))},
          start: {getAsync: (cb) => ok(cb, new Date(get('start', 0)))}, end: {getAsync: (cb) => ok(cb, new Date(get('end', 0)))},
          recurrence: {getAsync: (cb) => ok(cb, null)},
          body: {getAsync: (t, cb) => ok(cb, get('body', '')), setAsync: (v, o, cb) => { if (get('bodyFails', false)) { cb({status: 'failed', error: {code: 9041, message: 'нет доступа'}}); return; } put('body', v); cb({status: 'succeeded'}); }},
          location: {setAsync: (v, cb) => { put('loc', v); cb({status: 'succeeded'}); }},
          addHandlerAsync: (t, h, cb) => { if (cb) cb({status: 'succeeded'}); },
          notificationMessages: {replaceAsync: () => {}},
        },
      },
    },
  };
})();
"""
STATE = """(() => ({state: document.body.dataset.state, auth: document.getElementById('acct').dataset.auth, form: !document.getElementById('login').hidden, go: document.getElementById('go').hidden ? null : document.getElementById('go-label').textContent,
  msg: document.getElementById('msg').hidden ? null : document.getElementById('msg-title').textContent + ' | ' + document.getElementById('msg-text').textContent,
  result: document.getElementById('result').hidden ? null : document.getElementById('result-title').textContent, link: document.getElementById('link').href,
  body: window.__m.get('body', ''), loc: window.__m.get('loc', ''), key: window.__m.get('props', {}).pg_key || null, sw: document.documentElement.scrollWidth, iw: innerWidth,
  goBottom: Math.round(document.getElementById(document.getElementById('login').hidden ? 'go' : 'lg-go').getBoundingClientRect().bottom), ih: innerHeight}))()"""


async def main():
    R = S.Client("root", "root", "root-pass", 9781)
    A = S.Client("A", "alice", "alice-pass", 9782)
    try:
        await R.start()
        S.check("интеграция включена (по умолчанию вход действует 7 суток)", await R.js(f"{ENABLE}({{}})") == 200)
        await A.start()
        S.check("сеанса браузера нет: панель от него не зависит", await A.js("(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); await fetch('/api/v1/auth/logout', {method: 'POST', headers: {'X-CSRF-Token': me.csrf_token}}); return (await fetch('/api/v1/auth/me')).status; })()") == 401)
        await A.send("Network.setBlockedURLs", urls=["*appsforoffice.microsoft.com*", "*cdn.office.net*"])
        await A.send("Page.addScriptToEvaluateOnNewDocument", source=FAKE)
        await A.send("Emulation.setDeviceMetricsOverride", width=320, height=640, deviceScaleFactor=1, mobile=False)

        async def open_panel(wait="document.body.dataset.state !== 'loading'"):
            await A.goto(PANEL, 1.0)
            await A.wait_for(wait, 15)
            await asyncio.sleep(0.6)
            return await A.js(STATE)

        async def type_(sel, text):
            await A.js(f"(() => {{ const i = document.querySelector({sel!r}); i.focus(); i.value = {text!r}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()")

        # 1–2: первое открытие без входа — форма в панели, основная кнопка спрятана, всё в узкой панели
        await A.goto(PANEL, 0.3); await A.js("localStorage.clear(); sessionStorage.clear()")
        st = await open_panel()
        S.check("панель загрузилась: тема и время встречи, форма «Логин / Пароль / Войти»", st["form"] and st["go"] is None and await A.js("document.getElementById('subj').textContent.includes('бюджета')"), str(st))
        S.check("320 px: нет горизонтальной прокрутки, «Войти» в первом экране", st["sw"] <= st["iw"] and st["goBottom"] <= st["ih"], f"{st['sw']}/{st['iw']} кнопка до {st['goBottom']} из {st['ih']}")
        await A.shot("outlook-panel-login-320")
        # 3: неверный пароль — понятное сообщение
        await type_("#lg-user", "alice"); await type_("#lg-pass", "wrong")
        await A.js("document.getElementById('lg-go').click()")
        await A.wait_for("!document.getElementById('msg').hidden", 10)
        st = await A.js(STATE)
        S.check("неверный пароль: «Неверный логин или пароль», без HTTP-кодов и JSON", st["msg"] and "Неверный логин или пароль" in st["msg"] and "401" not in st["msg"] and "{" not in st["msg"], st["msg"])
        # 4: верный пароль — вход в панели, конференция создаётся сразу
        await type_("#lg-pass", "alice-pass")
        await A.js("document.getElementById('lg-go').click()")
        S.check("после входа конференция создана и ссылка в приглашении", await A.wait_for("document.body.dataset.state === 'done'", 20), str(await A.js(STATE)))
        st = await A.js(STATE)
        S.check("ссылка в тексте и в «Месте», кнопка стала «Обновить конференцию», форма входа скрыта", st["body"].count("<!--pg-conf-->") == 1 and st["link"] in st["body"] and st["loc"] == st["link"] and st["go"] == "Обновить конференцию" and not st["form"], str(st)[:400])
        tok = await A.js("JSON.parse(localStorage.getItem('pg_outlook_token') || 'null')")
        S.check("токен сохранён в панели на срок из настроек (7 суток)", bool(tok) and tok["life"] >= 167 * H, str({k: (v if k != "token" else "…") for k, v in (tok or {}).items()}))
        S.check("пароль не остался в поле", await A.js("document.getElementById('lg-pass').value === ''"))
        await A.shot("outlook-panel-done-320")
        room1, key1 = st["link"], st["key"]
        # 5: повторное открытие панели — вход не нужен, связь с комнатой сохранена, новая комната не создана
        st = await open_panel("document.body.dataset.state === 'done' || document.body.dataset.state === 'error'")
        S.check("повторное открытие: без входа, «Конференция уже добавлена», та же ссылка", not st["form"] and st["result"] and "уже добавлена" in st["result"] and st["link"] == room1, str(st)[:300])
        # 6: изменили время и тему — «Обновить конференцию» обновляет ту же комнату, блок не дублируется
        await A.js("window.__m.put('start', Date.now() + 5 * 3600e3); window.__m.put('end', Date.now() + 6 * 3600e3); window.__m.put('subject', 'Бюджет: финальное согласование')")
        await A.js("window.dispatchEvent(new Event('focus'))"); await asyncio.sleep(0.5)
        S.check("изменение времени замечено: подсказка «нажмите «Обновить конференцию»»", "изменились" in await A.js("document.getElementById('hint').textContent"))
        await A.js("document.getElementById('go').click()")
        await A.wait_for("document.body.dataset.state === 'done'", 15)
        st = await A.js(STATE)
        rooms = (await R.js("(async () => (await (await fetch('/api/v1/admin/outlook/meetings')).json()))()"))["items"]
        S.check("обновление: та же комната, один блок, ключ события прежний", st["link"] == room1 and st["key"] == key1 and st["body"].count("<!--pg-conf-->") == 1, str(st)[:300])
        S.check("на сервере одна конференция для этой встречи с новым названием", sum(1 for x in rooms if "Бюджет" in x["name"]) == 1, json.dumps(rooms, ensure_ascii=False)[:300])
        # 7: прошедшая встреча (новая встреча — другой ключ) — понятное сообщение без запроса к серверу
        await A.js("sessionStorage.clear()"); await A.js("window.__m.put('init', true); window.__m.put('props', {}); window.__m.put('body', ''); window.__m.put('subject', 'Вчерашняя встреча'); window.__m.put('start', Date.now() - 26 * 3600e3); window.__m.put('end', Date.now() - 25 * 3600e3)")
        st = await open_panel()
        S.check("прошедшая встреча: «Встреча уже завершилась. Укажите актуальные дату и время»", st["msg"] and "Встреча уже завершилась" in st["msg"] and st["result"] is None and not st["key"], str(st)[:300])
        # 8: время исправили в Outlook — повтор без перезапуска
        await A.js("window.__m.put('start', Date.now() + 2 * 3600e3); window.__m.put('end', Date.now() + 1 * 3600e3)")
        await A.js("window.dispatchEvent(new Event('focus'))"); await asyncio.sleep(0.5)
        S.check("неверный интервал: «Время окончания должно быть позже времени начала»", "окончания должно быть позже" in (await A.js(STATE))["msg"])
        await A.js("window.__m.put('end', Date.now() + 3 * 3600e3)"); await A.js("window.dispatchEvent(new Event('focus'))"); await asyncio.sleep(0.5)
        await A.js("document.getElementById('go').click()")
        S.check("после исправления времени та же кнопка создаёт конференцию", await A.wait_for("document.body.dataset.state === 'done'", 15))
        # 9: сбой вставки ссылки — комната есть, ссылка видна, «Вставить ссылку ещё раз» не создаёт новую
        await A.js("sessionStorage.clear()"); await A.js("window.__m.put('init', true); window.__m.put('props', {}); window.__m.put('body', '<p>x</p>'); window.__m.put('subject', 'Сбой вставки'); window.__m.put('start', Date.now() + 2 * 3600e3); window.__m.put('end', Date.now() + 3 * 3600e3); window.__m.put('bodyFails', true)")
        st = await open_panel("document.body.dataset.state === 'error' || document.body.dataset.state === 'done'")
        S.check("сбой вставки: «Конференция создана, ссылка не вставлена», ссылка показана, кнопка «Вставить ссылку ещё раз»",
                st["result"] and "ссылка не вставлена" in st["result"] and st["link"].startswith("http") and st["go"] == "Вставить ссылку ещё раз", str(st)[:400])
        partial = st["link"]
        await A.js("window.__m.put('bodyFails', false)"); await A.js("document.getElementById('go').click()")
        await A.wait_for("document.body.dataset.state === 'done'", 15)
        st = await A.js(STATE)
        S.check("повторная вставка: ссылка в приглашении, комната та же", st["link"] == partial and st["body"].count("<!--pg-conf-->") == 1)
        # 10: сеть недоступна — объяснение, а не зависание
        await A.js("sessionStorage.clear()"); await A.js("window.__m.put('init', true); window.__m.put('props', {}); window.__m.put('body', ''); window.__m.put('subject', 'Без сети'); window.__m.put('start', Date.now() + 2 * 3600e3); window.__m.put('end', Date.now() + 3 * 3600e3)")
        await A.send("Network.setBlockedURLs", urls=["*appsforoffice.microsoft.com*", "*cdn.office.net*", "*/api/v1/outlook/meetings*"])
        st = await open_panel("document.body.dataset.state === 'error' || document.body.dataset.state === 'done'")
        S.check("нет связи с API: «Нет связи с сервером», кнопка «Попробовать снова»", st["msg"] and "Нет связи с сервером" in st["msg"] and st["go"] == "Попробовать снова", str(st)[:300])
        await A.send("Network.setBlockedURLs", urls=["*appsforoffice.microsoft.com*", "*cdn.office.net*"])
        await A.js("document.getElementById('go').click()")
        S.check("сеть вернулась — повтор успешен", await A.wait_for("document.body.dataset.state === 'done'", 15))
        # 11: вход истёк — форма с «Срок входа истёк», без окна
        await A.js("(() => { const t = JSON.parse(localStorage.getItem('pg_outlook_token')); t.exp = Date.now() - 1000; localStorage.setItem('pg_outlook_token', JSON.stringify(t)); })()")
        st = await open_panel()
        S.check("истёкший вход: «Срок входа истёк — войдите снова» и форма входа", st["auth"] == "expired" and st["form"] and "истёк" in await A.js("document.getElementById('acct-text').textContent"), str(st)[:300])
        await type_("#lg-user", "alice"); await type_("#lg-pass", "alice-pass"); await A.js("document.getElementById('lg-go').click()")
        await A.wait_for("document.getElementById('acct').dataset.auth === 'ok'", 15)
        # 12: выход — токен отозван на сервере
        tok = await A.js("JSON.parse(localStorage.getItem('pg_outlook_token')).token")
        await A.js("document.getElementById('logout').click()"); await asyncio.sleep(0.8)
        code = await A.js(f"fetch('/api/v1/outlook/me', {{headers: {{Authorization: 'Bearer {tok}'}}}}).then(r => r.status)")
        S.check("«Выйти»: форма входа, токен отозван на сервере (401)", (await A.js(STATE))["form"] and code == 401, str(code))
        # 13: второй фактор обязателен — в панели предлагается окно входа, оно открывается по кнопке
        S.check("политика 2FA «для всех» включена", await R.js(f"{SECURITY}({{totp_policy: 'all'}})") == 200)
        await type_("#lg-user", "alice"); await type_("#lg-pass", "alice-pass"); await A.js("document.getElementById('lg-go').click()")
        await A.wait_for("!document.getElementById('msg').hidden", 10)
        st = await A.js(STATE)
        S.check("2FA: «Нужен ещё один шаг» и кнопка «Продолжить вход в окне»", "Нужен ещё один шаг" in (st["msg"] or "") and await A.js("document.getElementById('lg-window').textContent") == "Продолжить вход в окне", str(st["msg"]))
        await A.js("document.getElementById('lg-window').click()"); await asyncio.sleep(1.0)
        dl = await A.js("window.__m.get('dialogs', [])")
        S.check("окно входа открыто на странице этого сервера; закрытие окна — «Вход отменён», без критической ошибки",
                dl and dl[-1].endswith("/outlook/auth.html?step=browser") and "Вход отменён" in ((await A.js(STATE))["msg"] or ""), str(dl))
        await R.js(f"{SECURITY}({{totp_policy: 'optional'}})")
        # 13а: окно входа (старый манифест ExecuteFunction и прочие пути) без сеанса браузера — своя форма логина и пароля, без основного приложения
        #      (основное приложение не работает во встроенном IE 11 классического Outlook); неверный пароль — ошибка в окне, верный — токен уходит в Outlook
        await A.goto("/outlook/auth.html", 0.5); await A.wait_for("!document.getElementById('form').hidden", 10)
        S.check("окно входа без сеанса: своя форма «Логин/Пароль/Войти», а не переход на основное приложение",
                (await A.js("location.pathname")) == "/outlook/auth.html" and not await A.js("document.getElementById('form').hidden"))
        await type_("#login", "alice"); await type_("#password", "wrong-pass"); await A.js("document.getElementById('submit').click()")
        await A.wait_for("document.getElementById('status').className === 'err'", 10)
        S.check("окно входа: неверный пароль — понятная ошибка в окне, токена нет", not await A.js("window.__m.get('sent', null)"), await A.js("document.getElementById('status').textContent"))
        await type_("#password", "alice-pass"); await A.js("document.getElementById('submit').click()")
        await A.wait_for("window.__m.get('sent', null) !== null", 10)
        sent = json.loads(await A.js("window.__m.get('sent', null)"))
        S.check("окно входа: верный пароль — токен надстройки передан в Outlook (messageParent)", str(sent.get("token", "")).startswith("pgo_") and sent.get("expires_in", 0) > 3600, str(sent)[:80])
        await A.js("window.__m.put('sent', null)"); await open_panel()                         # дальше — снова панель
        # 14: ширины и тёмная тема
        for w in (300, 360, 480):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=600, deviceScaleFactor=1, mobile=False); await asyncio.sleep(0.4)
            st = await A.js(STATE)
            S.check(f"{w} px: без горизонтальной прокрутки, основное действие в первом экране", st["sw"] <= st["iw"] and st["goBottom"] <= st["ih"], f"{st['sw']}/{st['iw']}, {st['goBottom']}/{st['ih']}")
        await A.js("window.__m.put('dark', true)")
        await type_("#lg-user", "alice"); await type_("#lg-pass", "alice-pass"); await A.js("document.getElementById('lg-go').click()")
        await A.wait_for("document.body.dataset.state === 'done'", 15)
        await A.send("Emulation.setDeviceMetricsOverride", width=340, height=600, deviceScaleFactor=1, mobile=False)
        st = await open_panel("document.body.dataset.state === 'done'")
        S.check("тёмная тема Outlook: панель в тёмном оформлении", await A.js("document.documentElement.dataset.theme") == "dark")
        await A.shot("outlook-panel-dark-340")
        log = await R.js("(async () => JSON.stringify((await (await fetch('/api/v1/admin/journal?limit=200')).json())))()")
        S.check("журнал администратора: итог операций (addin_ok), ошибки с этапом и кодом, клиент Outlook; токенов нет", "addin_ok" in log and "[api/network]" in log and "Mailbox 1.8" in log and "pgo_" not in log, log[:300])
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        for c in (A, R):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
