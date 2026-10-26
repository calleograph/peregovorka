"""Сценарий (см. README.md): кнопки быстрого копирования ссылок на карточках главной страницы в настоящем браузере и на настоящем сервере. Зелёная — для сотрудников, красная — гостевая.
Проверяется: «Предстоящие встречи» (встреча из Outlook и временная комната) и постоянные комнаты, плитка и список; гостевая кнопка только если сервер разрешил гостей и человек вправе
ею делиться; именно эта встреча (ссылка в буфере совпадает с её гостевой ссылкой, а не комнаты и не другой встречи); ссылка выпускается один раз; одинаковая геометрия на одной линии без наложения
на название; подсказки при наведении и с клавиатуры; меню «⋯» на месте; ссылка прежняя после переноса встречи; светлая и тёмная темы, узкие окна."""
import asyncio, json, os, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

PUT = """(async (path, body) => { const me = await (await fetch('/api/v1/auth/me')).json(); return (await fetch('/api/v1/admin/settings/' + path, {method: 'PUT', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}, body: JSON.stringify(body)})).status; })"""
API = """(async (method, path, body, headers) => { const me = await (await fetch('/api/v1/auth/me')).json(); const r = await fetch('/api/v1' + path, {method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token, ...(headers || {})}, body: body ? JSON.stringify(body) : undefined}); let j = null; try { j = await r.json(); } catch (e) {} return {status: r.status, json: j}; })"""
CLIP = "(() => { window.__copied = null; Object.defineProperty(navigator, 'clipboard', {value: {writeText: (t) => { window.__copied = t; return Promise.resolve(); }}, configurable: true}); })()"
GEO = """(name) => { const cards = [...document.querySelectorAll('.upc-card, .upc-row, .rc-cell, .rm')]; const c = cards.find((x) => x.innerText.includes(name)); if (!c) return null;
  const q = (s) => [...c.querySelectorAll(s)].map((b) => { const r = b.getBoundingClientRect(); return {l: r.left, r: r.right, t: r.top, b: r.bottom, w: r.width, h: r.height}; });
  const cr = c.getBoundingClientRect(); const title = c.querySelector('h2, h3, .upc-row-name, .rm-name'); const tr = title ? title.getBoundingClientRect() : null;
  return {staff: q('.lc-staff')[0] || null, guest: q('.lc-guest')[0] || null, more: q('.rm-more')[0] || null, card: {l: cr.left, r: cr.right, t: cr.top, b: cr.bottom, h: cr.height}, title: tr && {l: tr.left, r: tr.right, t: tr.top, b: tr.bottom}}; }"""


async def geo(A, name):
    return await A.js(f"({GEO})({name!r})")


def aligned(g):
    bs = [b for b in (g["staff"], g["guest"], g["more"]) if b]
    same = all(abs(b["w"] - bs[0]["w"]) < 0.6 and abs(b["h"] - bs[0]["h"]) < 0.6 for b in bs) and all(abs(b["t"] - bs[0]["t"]) < 1.0 for b in bs)
    gaps = [round(bs[i + 1]["l"] - bs[i]["r"], 1) for i in range(len(bs) - 1)]
    return same and (len(set(gaps)) <= 1), gaps


async def copy_click(A, name, kind):
    await A.js(CLIP)
    ok = await A.js(f"(() => {{ const c = [...document.querySelectorAll('.upc-card, .upc-row, .rc-cell, .rm')].find((x) => x.innerText.includes({name!r})); const b = c && c.querySelector('.lc-{kind}'); if (!b) return false; b.click(); return true; }})()")
    for _ in range(20):                        # выпуск гостевой ссылки идёт на сервер: ждём копирования до четырёх секунд
        await asyncio.sleep(0.2)
        if await A.js("window.__copied"):
            break
    return ok, await A.js("window.__copied")


async def main():
    R = S.Client("root", "root", "root-pass", 9831)
    A = S.Client("A", "alice", "alice-pass", 9832)
    try:
        await R.start()
        S.check("администратор: Outlook включён, гости во встречах и во временных комнатах разрешены",
                await R.js(f"({PUT})('outlook', {{enabled: true, guest_links: true}})") == 200 and await R.js(f"({PUT})('general', {{temp_rooms_enabled: true, temp_room_allow_guest_link: true}})") == 200)
        perm = (await R.js(f"({API})('POST', '/admin/rooms', {{slug: 'gst-' + Math.random().toString(36).slice(2, 8), name: 'Совет директоров', guest_access_enabled: true, acl: [{{subject_type: 'group', subject_ref: 'cn=staff,ou=groups,dc=corp,dc=test'}}]}})"))
        S.check("создана постоянная комната с гостевым входом", perm["status"] == 201, str(perm)[:200])
        nog = (await R.js(f"({API})('POST', '/admin/rooms', {{slug: 'nog-' + Math.random().toString(36).slice(2, 8), name: 'Без гостей комната', acl: [{{subject_type: 'group', subject_ref: 'cn=staff,ou=groups,dc=corp,dc=test'}}]}})"))
        await A.start()
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        await A.goto("/", 2.0)
        tr = await A.js(f"({API})('POST', '/rooms/temporary', {{name: 'Быстрый созвон по инциденту с очень длинным названием для переноса'}})")
        S.check("Алиса создала временную комнату с длинным названием", tr["status"] in (200, 201), str(tr)[:240])
        tmp = tr["json"]
        key = "k" + uuid.uuid4().hex
        tok = (await A.js(f"({API})('POST', '/outlook/token')"))["json"]["token"]
        hdr = "{Authorization: 'Bearer %s'}" % tok
        body = {"key": key, "subject": "Согласование бюджета (Outlook)", "start": "2099-01-01T10:00:00Z", "end": "2099-01-01T11:00:00Z"}
        import datetime as dt
        s = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=3)
        body["start"], body["end"] = s.strftime("%Y-%m-%dT%H:%M:%SZ"), (s + dt.timedelta(minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        m1 = (await A.js(f"({API})('POST', '/outlook/meetings', {json.dumps(body)}, {hdr})"))["json"]
        body2 = {**body, "key": "k" + uuid.uuid4().hex, "subject": "Совсем другая встреча"}
        m2 = (await A.js(f"({API})('POST', '/outlook/meetings', {json.dumps(body2)}, {hdr})"))["json"]
        S.check("встречи из Outlook созданы с гостевыми ссылками, у них разные ссылки", bool(m1.get("guest_url")) and bool(m2.get("guest_url")) and m1["guest_url"] != m2["guest_url"], str((m1.get("guest_url"), m2.get("guest_url"))))
        # ---------- плитки
        await A.goto("/", 2.5)
        S.check("на главной есть «Предстоящие встречи» и постоянные комнаты", await A.wait_for("document.querySelectorAll('.upc-card').length >= 3", 20))
        g = await geo(A, "Согласование бюджета (Outlook)")
        S.check("встреча из Outlook: зелёная, красная и «⋯» на карточке", g and g["staff"] and g["guest"] and g["more"], str(g)[:200])
        ok, gaps = aligned(g)
        S.check("кнопки одного размера на одной линии, интервалы равны", ok, str(gaps))
        S.check("порядок: зелёная, красная, «⋯»; всё справа вверху внутри карточки", g["staff"]["r"] <= g["guest"]["l"] <= g["guest"]["r"] <= g["more"]["l"] and g["more"]["r"] <= g["card"]["r"] and g["staff"]["t"] - g["card"]["t"] < 30)
        S.check("название не накрыто кнопками", g["title"]["b"] <= g["staff"]["t"] + 1 or g["title"]["r"] <= g["staff"]["l"] or g["title"]["t"] >= g["staff"]["b"] - 1, str((g["title"], g["staff"])))
        await A.shot("card-links-tiles")
        okc, copied = await copy_click(A, "Согласование бюджета (Outlook)", "staff")
        S.check("зелёная копирует ссылку встречи для сотрудников (/m/…)", okc and copied and copied.endswith("/m/" + m1["url"].rsplit("/", 1)[1]), str(copied))
        S.check("после копирования показано подтверждение", await A.js("document.body.innerText.includes('Ссылка для сотрудников скопирована')"))
        okc, copied = await copy_click(A, "Согласование бюджета (Outlook)", "guest")
        path = lambda u: "/guest/" + u.rsplit("/guest/", 1)[1] if u and "/guest/" in u else u
        S.check("красная копирует именно гостевую ссылку этой встречи", okc and path(copied) == path(m1["guest_url"]), f"{copied} / {m1['guest_url']}")
        okc, copied2 = await copy_click(A, "Совсем другая встреча", "guest")
        S.check("у другой встречи — её собственная гостевая ссылка", okc and path(copied2) == path(m2["guest_url"]) and copied2 != copied, str(copied2))
        # подсказки: наведение и фокус с клавиатуры
        await A.js("document.querySelector('.upc-card:has(.lc-guest) .lc-staff').scrollIntoView({block: 'center'})")
        r = await A.js("(() => { const b = document.querySelector('.upc-card .lc-guest').getBoundingClientRect(); return {x: b.left + b.width / 2, y: b.top + b.height / 2}; })()")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=r["x"], y=r["y"])
        S.check("подсказка красной при наведении: «Копировать гостевую ссылку»", await A.wait_for("document.querySelector('.lc-tip')?.innerText === 'Копировать гостевую ссылку'", 4))
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=5, y=5)
        await A.js("document.activeElement.blur()")
        await A.send("Input.dispatchKeyEvent", type="keyDown", key="Shift", code="ShiftLeft", windowsVirtualKeyCode=16)
        await A.send("Input.dispatchKeyEvent", type="keyUp", key="Shift", code="ShiftLeft", windowsVirtualKeyCode=16)
        await A.js("document.querySelector('.upc-card:has(.lc-guest) .lc-staff').focus()")
        await A.send("Input.dispatchKeyEvent", type="keyDown", key="Tab", code="Tab", windowsVirtualKeyCode=9)
        await A.send("Input.dispatchKeyEvent", type="keyUp", key="Tab", code="Tab", windowsVirtualKeyCode=9)
        await asyncio.sleep(0.4)
        S.check("подсказка появляется и при фокусе с клавиатуры", await A.js("document.querySelector('.lc-tip')?.innerText?.length > 5"), str(await A.js("document.activeElement.className + ' | ' + (document.querySelector('.lc-tip')?.innerText || '')")))
        # ---------- временная комната: ссылка выпускается один раз
        gt = await geo(A, "Быстрый созвон по инциденту")
        S.check("временная комната: красная кнопка есть (выпустить можно), геометрия та же", gt and gt["guest"] and aligned(gt)[0], str(gt)[:160])
        n0 = await A.js(f"({API})('GET', '/rooms/upcoming')")
        okc, c1 = await copy_click(A, "Быстрый созвон по инциденту", "guest")
        okc, c2 = await copy_click(A, "Быстрый созвон по инциденту", "guest")
        S.check("первое нажатие выпускает и копирует гостевую ссылку, второе копирует ту же", bool(c1) and "/guest/" in c1 and c1 == c2, f"{c1} / {c2}")
        up = (await A.js(f"({API})('GET', '/rooms/upcoming')"))["json"]["items"]
        S.check("сервер хранит одну ссылку: в списке теперь её токен", any(i["id"] == tmp["id"] and i["guest_token"] and c1.endswith(i["guest_token"]) for i in up))
        # ---------- «⋯»
        await A.goto("/", 2.5)                      # чистая страница: меню проверяется отдельно от предыдущих действий с подсказками и фокусом
        await A.wait_for("document.querySelectorAll('.upc-card').length >= 3", 20)
        await A.js("document.querySelector('.upc-card:has(.lc-guest) .rm-more').scrollIntoView({block: 'center', behavior: 'instant'})")
        await asyncio.sleep(1.2)
        r = await A.js("(() => { const b = document.querySelector('.upc-card:has(.lc-guest) .rm-more').getBoundingClientRect(); return {x: b.left + b.width / 2, y: b.top + b.height / 2}; })()")
        await A.js("(() => { window.__seen = 0; window.__scrolls = 0; window.__blurs = 0; new MutationObserver(() => { if (document.querySelector('.ctx-menu')) window.__seen++; }).observe(document.body, {childList: true, subtree: true}); addEventListener('scroll', () => window.__scrolls++, true); addEventListener('blur', () => window.__blurs++); })()")
        await A.send("Input.dispatchMouseEvent", type="mouseMoved", x=r["x"], y=r["y"])
        await A.send("Input.dispatchMouseEvent", type="mousePressed", x=r["x"], y=r["y"], button="left", buttons=1, clickCount=1)
        await A.send("Input.dispatchMouseEvent", type="mouseReleased", x=r["x"], y=r["y"], button="left", buttons=0, clickCount=1)
        S.check("меню «⋯» открывается и содержит прежние действия", await A.wait_for("(() => { const t = document.querySelector('.ctx-menu')?.innerText || ''; if (t) window.__menuText = t; return t.includes('Открыть встречу') && t.includes('Скопировать ссылку'); })()", 6),
                str(await A.js("document.querySelector('.ctx-menu')?.innerText || ('нет меню; в точке: ' + (document.elementFromPoint(%d, %d)?.className || '?') + '; на странице .rm-more: ' + document.querySelectorAll('.rm-more').length + ' текст=[' + (window.__menuText || '') + '] показано=' + window.__seen + ' scroll=' + window.__scrolls + ' blur=' + window.__blurs)" % (r["x"], r["y"]))))
        await A.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        await asyncio.sleep(0.3)
        # ---------- постоянные комнаты: алиса не руководитель — гостевой кнопки нет; администратор видит
        gp = await geo(A, "Совет директоров")
        S.check("постоянная комната: у сотрудника только зелёная и «⋯» (гостевая — руководителям)", gp and gp["staff"] and not gp["guest"] and gp["more"], str(gp)[:160])
        okc, cp = await copy_click(A, "Совет директоров", "staff")
        S.check("зелёная копирует ссылку комнаты", okc and cp and "/rooms/" in cp, str(cp))
        await R.goto("/", 2.5)
        await R.wait_for("document.querySelectorAll('.rc-cell').length >= 3", 20)
        gr = await geo(R, "Совет директоров")
        gn = await geo(R, "Без гостей комната")
        S.check("администратор: у комнаты с гостевым входом обе кнопки, у комнаты без него — только зелёная", gr and gr["staff"] and gr["guest"] and gn and gn["staff"] and not gn["guest"], str((gr and bool(gr["guest"]), gn and bool(gn["guest"]))))
        S.check("в постоянной комнате кнопки одного размера на одной линии", aligned(gr)[0], str(aligned(gr)[1]))
        okc, cg = await copy_click(R, "Совет директоров", "guest")
        S.check("красная копирует гостевую ссылку комнаты /guest/…", okc and cg and "/guest/" in cg, str(cg))
        await R.shot("card-links-root")
        # ---------- список
        await A.js("[...document.querySelectorAll('.viewseg button')].find((b) => b.textContent.includes('Список')).click()")
        await asyncio.sleep(1.2)
        gl = await geo(A, "Согласование бюджета (Outlook)")
        S.check("список: у встречи те же две кнопки и «⋯» в одну линию", gl and gl["staff"] and gl["guest"] and gl["more"] and aligned(gl)[0], str(gl)[:200])
        await A.shot("card-links-list")
        okc, cl = await copy_click(A, "Согласование бюджета (Outlook)", "guest")
        S.check("в списке красная копирует ту же гостевую ссылку", okc and path(cl) == path(m1["guest_url"]), str(cl))
        await A.js("[...document.querySelectorAll('.viewseg button')].find((b) => b.textContent.includes('Плитка')).click()")
        # ---------- перенос встречи: ссылки те же
        body["start"] = (s + dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ"); body["end"] = (s + dt.timedelta(hours=2, minutes=45)).strftime("%Y-%m-%dT%H:%M:%SZ")
        moved = (await A.js(f"({API})('POST', '/outlook/meetings', {json.dumps(body)}, {hdr})"))["json"]
        await A.goto("/", 2.5)
        await A.wait_for("document.querySelectorAll('.upc-card').length >= 3", 20)
        okc, cm = await copy_click(A, "Согласование бюджета (Outlook)", "guest")
        S.check("после переноса встречи гостевая ссылка прежняя", moved["id"] == m1["id"] and path(cm) == path(m1["guest_url"]), str(cm))
        # ---------- политика: гости запрещены — красной нет
        S.check("администратор запретил гостей во встречах Outlook", await R.js(f"({PUT})('outlook', {{guest_links: false}})") == 200)
        await A.goto("/", 2.5)
        await A.wait_for("document.querySelectorAll('.upc-card').length >= 3", 20)
        g0 = await geo(A, "Согласование бюджета (Outlook)")
        S.check("при запрете гостей красной кнопки нет, остаются зелёная и «⋯»", g0 and g0["staff"] and not g0["guest"] and g0["more"], str(g0)[:160])
        await R.js(f"({PUT})('outlook', {{guest_links: true}})")
        # ---------- узкие окна и тёмная тема
        await A.goto("/", 2.5)
        await A.wait_for("document.querySelectorAll('.upc-card').length >= 3", 20)
        for w in (700, 390):
            await A.send("Emulation.setDeviceMetricsOverride", width=w, height=900, deviceScaleFactor=1, mobile=False)
            await asyncio.sleep(0.8)
            gw = await geo(A, "Согласование бюджета (Outlook)")
            sw = await A.js("[document.documentElement.scrollWidth, innerWidth]")
            gl2 = await geo(A, "Быстрый созвон по инциденту")
            S.check(f"{w} px: кнопки в карточке и на одной линии, длинное название не ломает карточку, горизонтальной прокрутки нет",
                    gw and aligned(gw)[0] and gw["more"]["r"] <= gw["card"]["r"] + 1 and gl2 and gl2["more"]["r"] <= gl2["card"]["r"] + 1 and sw[0] <= sw[1], f"{sw} {aligned(gw)[1]} gw={gw['more']['r']}/{gw['card']['r']} gl2={gl2 and (gl2['more']['r'], gl2['card']['r'])}")
        await A.shot("card-links-390")
        await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
        for theme in ("dark", "glass"):
            await A.js(f"document.documentElement.setAttribute('data-theme', '{theme}')")
            await asyncio.sleep(0.6)
            col = await A.js("(() => { const g = (s) => { const b = document.querySelector(s); const c = getComputedStyle(b); return [c.color, c.backgroundColor]; }; return {ok: g('.upc-card .lc-staff'), bad: g('.upc-card .lc-guest')}; })()")
            S.check(f"тема «{theme}»: у зелёной и красной свои цвета, текст отличается от фона", col["ok"][0] != col["ok"][1] and col["bad"][0] != col["bad"][1] and col["ok"] != col["bad"], str(col))
            await A.shot(f"card-links-{theme}")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        for c in (A, R):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
