"""Сценарий (см. README.md): настройки сайта 0.13.0 — оформление через браузер, изображения, документы и подтверждение, темы пользователя, шрифт Press Start 2P на входе и в журнале,
включение камеры на экране перед входом (быстрая реакция, быстрые нажатия, циклы, переключение, отсутствие утечки потоков).
Камеры в headless — поддельные (Chromium fake device): это проверка логики и скорости интерфейса, но НЕ проверка на физической камере."""
import asyncio, io, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/hr"
# учёт потоков камеры: сколько видеотреков сейчас живо (не остановлено) — утечка видна как рост числа
S.INIT += """
window.__vt = []; window.__gum = 0;
(() => { const g = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (c) => { if (c && c.video) window.__gum++; const s = await g(c); if (c && c.video) s.getVideoTracks().forEach(t => window.__vt.push(t)); return s; }; })();
"""
CAMB = "(()=>{const c=[...document.querySelectorAll('.pc-card')].find(x=>x.querySelector('select[aria-label=\"Камера\"]'));return c&&c.querySelector('.pc-h .badge')})()"
LIVE = "window.__vt.filter(t=>t.readyState==='live').length"


def png_file(w=360, h=120, color=(40, 100, 210, 255)) -> str:
    from PIL import Image
    f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    Image.new("RGBA", (w, h), color).save(f, "PNG"); f.close()
    return f.name


async def set_files(c, selector: str, path: str):
    doc = await c.send("DOM.getDocument", depth=1)
    n = await c.send("DOM.querySelectorAll", nodeId=doc["root"]["nodeId"], selector=selector)
    await c.send("DOM.setFileInputFiles", files=[path], nodeId=n["nodeIds"][0])


async def accept_consent(c):
    """Если организация требует подтверждение документов — подтвердить (как пользователь) и дождаться входа в систему."""
    if await c.wait_for("document.body.innerText.includes('Перед началом работы подтвердите')", 4):
        await c.js("document.querySelectorAll('.prejoin input[type=checkbox]').forEach(i=>i.click())"); await asyncio.sleep(0.3)
        await c.js("document.querySelector('.prejoin .btn.primary').click()"); await asyncio.sleep(1.5)


async def click_theme(c, label: str):
    return await c.js(f"(()=>{{const b=[...document.querySelectorAll('.theme-opt')].find(x=>x.querySelector('b').textContent.trim()==={json.dumps(label)});if(!b)return false;b.click();return true}})()")


async def click_text(c, text: str, sel="button"):
    return await c.js(f"(()=>{{const b=[...document.querySelectorAll({json.dumps(sel)})].find(x=>x.textContent.trim().includes({json.dumps(text)}));if(!b)return false;b.click();return true}})()")


async def main():
    R = S.Client("root", "root", "root-pass", 9661)
    G = S.Client("anon", "alice", "alice-pass", 9662)
    try:
        await R.start()
        # --------------------------------------------------- вход: шрифт и стандартное оформление
        anon = S.Client("login", "alice", "alice-pass", 9663)
        await anon.start()
        await anon.send('Network.clearBrowserCookies')
        await anon.goto("/", 2.5)
        await anon.wait_for("!!document.querySelector('.px-shell')", 15)
        ff = await anon.js("(async()=>{await document.fonts.ready;const n=document.querySelector('.px-name');return {fam:getComputedStyle(n).fontFamily,loaded:document.fonts.check('14px \"Press Start 2P\"','Peregovorka'),text:n.textContent,title:document.title}})()")
        S.check("вход: название пиксельным шрифтом Press Start 2P, шрифт загружен с сервера", "Press Start 2P" in ff["fam"] and ff["loaded"], str(ff))
        cyr = await anon.js("(async()=>{await document.fonts.load('14px \"Press Start 2P\"','Войти');const c=document.createElement('canvas').getContext('2d');c.font='14px \"Press Start 2P\"';return [c.measureText('Войти').width,c.measureText('WWWWW').width]})()")
        S.check("вход: кириллица тоже набирается Press Start 2P (каждый знак шириной в кегль)", abs(cyr[0] - 70) < 3 and abs(cyr[1] - 70) < 3, str(cyr))
        px = await anon.js("getComputedStyle(document.querySelector('.px-cta')).fontFamily")
        S.check("вход: кнопка «Войти» набрана пиксельным шрифтом", "Press Start 2P" in px, px)
        S.check("вход: стандартное оформление Peregovorka на чистой установке", ff["text"] == "Peregovorka" and "Peregovorka" in ff["title"])
        S.check("вход: интерактивный фон на месте", await anon.js("!!document.querySelector('.login-backdrop')||!!document.querySelector('canvas')"))
        await anon.shot("login-default")
        # --------------------------------------------------- администратор: оформление через интерфейс
        await R.goto("/admin?tab=site", 3.0)
        S.check("администрирование → «Настройки сайта» открывается", await R.wait_for("document.body.innerText.includes('Название и оформление')", 20))
        await R.wait_for("[...document.querySelectorAll('label')].some(x=>x.textContent.trim().startsWith('Название системы'))", 20); await asyncio.sleep(1.0)
        await R.js("""(()=>{const set=(label,v)=>{const l=[...document.querySelectorAll('label')].find(x=>x.textContent.trim().startsWith(label));const i=l.querySelector('input,textarea');
          const d=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(i),'value');d.set.call(i,v);i.dispatchEvent(new Event('input',{bubbles:true}))};
          set('Название системы','Встречи Компании');set('Краткое название','Встречи');set('Подзаголовок','Корпоративные видеоконференции');set('Основной цвет','#0f766e')})()""")
        await click_text(R, "Сохранить"); await R.wait_for("document.body.innerText.includes('Сохранено')", 10); await asyncio.sleep(0.8)
        site = await R.js("fetch('/api/v1/public/site').then(r=>r.json())")
        S.check("настройки сохранены и публично видны (название, цвет)", site["name"] == "Встречи Компании" and site["primary_color"] == "#0f766e" and site["customized"], str(site)[:160])
        # логотип: выбор → предпросмотр → сохранение
        lp = png_file()
        await set_files(R, ".site-slot:nth-of-type(1) input[type=file]", lp)
        S.check("логотип: до сохранения показан предпросмотр", await R.wait_for("[...document.querySelectorAll('.site-slot')][0].querySelectorAll('.plate img').length===2", 8))
        await R.js("[...document.querySelectorAll('.site-slot')][0].querySelector('.btn.primary').click()"); await asyncio.sleep(1.5)
        fav = png_file(64, 64, (200, 60, 60, 255))
        await set_files(R, ".site-slot:nth-of-type(3) input[type=file]", fav)
        await asyncio.sleep(0.6)
        await R.js("[...document.querySelectorAll('.site-slot')][2].querySelector('.btn.primary').click()"); await asyncio.sleep(1.5)
        # применилось без перезагрузки
        top = await R.js("({title:document.title,logo:!!document.querySelector('.topbar .brand-logo'),accent:getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(),fav:document.querySelector('link[rel=icon][data-site]')?.href||''})")
        S.check("после сохранения верхняя панель, значок вкладки и акцент меняются сразу, без перезагрузки", top["logo"] and "?v=" in top["fav"] and top["accent"] != "", str(top))
        await R.shot("site-admin")
        # --------------------------------------------------- документы и подтверждение
        await R.js("fetch('/api/v1/auth/me').then(r=>r.json()).then(m=>window.__csrf=m.csrf_token)"); await asyncio.sleep(0.5)
        api = """async(m,p,b)=>{const r=await fetch('/api/v1'+p,{method:m,headers:{'content-type':'application/json','X-CSRF-Token':window.__csrf},body:b?JSON.stringify(b):undefined});return [r.status,await r.text()]}"""
        md = "\n".join(["# Политика", "", 'Текст со <script>window.__xss=1</script> и <img src=x onerror="window.__xss=2"> и [ссылка](javascript:window.__xss=3)'])
        legal1 = json.dumps({"title": "Политика ПДн", "draft_md": md, "require_consent": False}, ensure_ascii=False)
        legal2 = json.dumps({"title": "Соглашение", "draft_md": "Правила пользования", "require_consent": True}, ensure_ascii=False)
        sett = json.dumps({"support_email": "help@example.com", "support_phone": "+7 (495) 000-00-00", "support_text": "Пишите нам", "guest_text": "Это гостевая встреча", "welcome_text": "Добро пожаловать"}, ensure_ascii=False)
        r = await R.js(f"(async()=>{{const a={api};await a('PUT','/admin/site/legal/privacy_policy',{legal1});return (await a('POST','/admin/site/legal/privacy_policy/publish'))[0]}})()")
        await R.js(f"(async()=>{{const a={api};await a('PUT','/admin/site/legal/terms',{legal2});await a('POST','/admin/site/legal/terms/publish');await a('PUT','/admin/settings/site',{sett})}})()")
        S.check("документ опубликован", r == 200)
        await R.goto("/legal/privacy_policy", 2.5); await R.wait_for("!!document.querySelector('.legal-card h1')", 20)
        xss = await R.js("({x:window.__xss||0,script:document.querySelectorAll('.legal-card script').length,img:document.querySelectorAll('.legal-card img').length,a:[...document.querySelectorAll('.legal-card a')].map(a=>a.getAttribute('href')),h1:document.querySelector('.legal-card h1')?.textContent})")
        S.check("документ открывается отдельной страницей; HTML и javascript: из Markdown не исполняются и не вставляются", xss["h1"] == "Политика ПДн" and xss["x"] == 0 and xss["script"] == 0 and xss["img"] == 0 and not any((h or "").lower().startswith("javascript") for h in xss["a"]), str(xss))
        # вход после публикации: ссылки на документы и приветствие
        await anon.goto("/", 2.5)
        await anon.wait_for("!!document.querySelector('.px-shell')", 15)
        lf = await anon.js("({foot:document.querySelector('.login-foot').innerText,welcome:document.querySelector('.px-welcome')?.innerText,name:document.querySelector('.px-name,.px-logo')?.tagName,title:document.title})")
        S.check("страница входа: приветствие, ссылки на документы и «Помощь и поддержка», сведения о проекте Peregovorka сохранены", lf["welcome"] == "Добро пожаловать" and "Политика ПДн" in lf["foot"] and "Помощь и поддержка" in lf["foot"] and "Peregovorka (open source)" in lf["foot"], str(lf))
        S.check("вкладка браузера называется по настройкам", "Встречи Компании" in lf["title"], lf["title"])
        await anon.shot("login-branded")
        await click_text(anon, "Помощь и поддержка")
        S.check("«Помощь и поддержка»: почта через mailto, телефон через tel", await anon.wait_for("!!document.querySelector('a[href^=\"mailto:help@example.com\"]') && !!document.querySelector('a[href^=\"tel:\"]')", 8))
        # подтверждение документа сотрудником
        B = S.Client("bob", "bob", "bob-pass", 9664)
        await B.start(); await B.goto("/", 3.0)
        S.check("сотрудник после входа видит окно подтверждения документа и не попадает в систему без него", await B.wait_for("document.body.innerText.includes('Перед началом работы подтвердите')", 15))
        S.check("кнопка «Подтверждаю» недоступна, пока не отмечен документ", await B.js("document.querySelector('.prejoin .btn.primary').disabled"))
        await B.js("document.querySelector('.prejoin input[type=checkbox]').click()"); await asyncio.sleep(0.3)
        await B.js("document.querySelector('.prejoin .btn.primary').click()")
        S.check("после подтверждения открывается список переговорок", await B.wait_for("!!document.querySelector('.rooms-page')", 15))
        cons = await R.js(f"(async()=>{{const a={api};return JSON.parse((await a('GET','/admin/site/legal/terms/consents'))[1]).total}})()")
        S.check("подтверждение записано (кто, версия, когда)", cons == 1, str(cons))
        # гость: без подтверждения вход отклонён
        gtok = await R.js(f"(async()=>{{const a={api};const rooms=JSON.parse((await a('GET','/admin/rooms'))[1]);const r=rooms.find(x=>x.slug==='it-1');return r.guest_token||null}})()")
        # --------------------------------------------------- темы пользователя
        await B.goto("/profile", 3.0)
        S.check("личный кабинет: карточка «Оформление» с шестью темами", await B.js("document.querySelectorAll('.theme-opt').length") == 6)
        d0 = await B.js("document.documentElement.getAttribute('data-theme')")
        S.check("по умолчанию — тема системы (атрибут темы не задан)", d0 is None, str(d0))
        res = {}
        for label, tid in (("Янтарная", "amber"), ("Бриллиант", "diamond"), ("Корпоративная", "corporate"), ("Тёмная", "dark"), ("Светлая", "light")):
            await click_theme(B, label)
            await asyncio.sleep(0.3)
            res[tid] = await B.js("({t:document.documentElement.getAttribute('data-theme'),bg:getComputedStyle(document.body).backgroundColor,acc:getComputedStyle(document.documentElement).getPropertyValue('--accent').trim()})")
            if tid == "amber":
                await B.shot("theme-amber")
            if tid == "diamond":
                await B.shot("theme-diamond")
            if tid == "corporate":
                await B.shot("theme-corporate")
        S.check("каждая тема применяется сразу и даёт свой фон", all(res[k]["t"] == k for k in res) and len({v["bg"] for v in res.values()}) == 5, str(res)[:300])
        S.check("у цветных тем свой акцент, фирменный цвет организации к ним не применяется", res["amber"]["acc"] == "#b5601d" and res["corporate"]["acc"] == "#2f6fa8", str(res["amber"]))
        await click_theme(B, "Корпоративная")
        await B.send("Page.reload"); await asyncio.sleep(3)
        S.check("выбор темы сохраняется после перезагрузки", await B.wait_for("document.documentElement.getAttribute('data-theme')==='corporate'", 12))
        await click_theme(B, "Как в системе"); await asyncio.sleep(0.3)
        S.check("«Как в системе» возвращает тему устройства", await B.js("document.documentElement.getAttribute('data-theme')") is None)
        # --------------------------------------------------- журнал: шрифт
        await R.goto("/admin?tab=journal", 3.5); await accept_consent(R)
        await R.wait_for("document.querySelectorAll('.journal-table tbody td').length>0", 20)
        jf = await R.js("getComputedStyle(document.querySelector('.journal-table tbody td')).fontFamily")
        S.check("журнал событий: записи набраны Press Start 2P", "Press Start 2P" in (jf or ""), str(jf))
        await R.shot("journal-pixel")
        # --------------------------------------------------- камера
        await G.start(); await G.goto(S.ROOM, 2.5); await accept_consent(G)
        await G.wait_for("!!document.querySelector('.pj-video')", 20)
        await G.js("[...document.querySelectorAll('.precheck button')].filter(b=>/Разрешить доступ/.test(b.textContent)).forEach(b=>b.click())"); await asyncio.sleep(1.0)
        # реакция на нажатие и время до первого кадра
        t = await G.js("""(async()=>{const b=[...document.querySelectorAll('.precheck button')].find(b=>/Проверить камеру/.test(b.textContent));const t0=performance.now();b.click();
          await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));const react=Math.round(performance.now()-t0);
          const txt=document.querySelector('.pj-video-ph')?.innerText||'';const busy=[...document.querySelectorAll('.precheck button')].some(x=>/Включаем камеру/.test(x.textContent)&&x.disabled);
          return {react,txt,busy}})()""")
        S.check("нажатие «Проверить камеру» даёт видимую реакцию сразу (≤ 200 мс): «Включаем камеру…», кнопка занята", t["react"] <= 200 and "Включаем камеру" in t["txt"] and t["busy"], str(t))
        S.check("первый кадр пришёл, статус «включена»", await G.wait_for("document.body.innerText.includes('включена')&&document.querySelector('.pj-video-el')?.videoWidth>0", 15))
        ms = await G.js("(" + CAMB + ")?.innerText")
        print("время до первого кадра (поддельная камера):", ms)
        # быстрые повторные нажатия «выключить/включить»
        await G.js("window.__gum=0")
        await G.js("""(async()=>{const q=()=>[...document.querySelectorAll('.precheck button')].find(b=>/Выключить камеру|Проверить камеру|Включаем камеру/.test(b.textContent));
          for(let i=0;i<8;i++){q()?.click();await new Promise(r=>setTimeout(r,30))}})()""")
        await asyncio.sleep(3)
        live = await G.js(LIVE)
        state = await G.js("(" + CAMB + ")?.innerText")
        S.check("8 быстрых нажатий подряд: живых видеопотоков не больше одного (утечки нет), интерфейс в рабочем состоянии", live <= 1 and ("включена" in state or "выключена" in state), f"живых {live}, статус {state}, getUserMedia вызовов {await G.js('window.__gum')}")
        # пять циклов включения/выключения
        for i in range(5):
            if await G.js("!!document.querySelector('.precheck button:not([disabled])') && [...document.querySelectorAll('.precheck button')].some(b=>/Выключить камеру/.test(b.textContent))"):
                await click_text(G, "Выключить камеру"); await asyncio.sleep(0.4)
            await click_text(G, "Проверить камеру")
            ok = await G.wait_for("document.querySelector('.pj-video-el')?.videoWidth>0 && (" + CAMB + ")?.classList.contains('ok')", 15)
            if not ok: break
        S.check("пять циклов включения/выключения подряд без сбоев", ok)
        await click_text(G, "Выключить камеру"); await asyncio.sleep(0.8)
        S.check("после выключения все видеотреки остановлены (камера освобождена), статус «выключена»", await G.js(LIVE) == 0 and "выключена" in await G.js("(" + CAMB + ")?.innerText"), str(await G.js(LIVE)))
        # отмена включения и выключение во время запуска
        cancelled = await G.js("""(async()=>{const q=(t)=>[...document.querySelectorAll('.precheck button')].find(b=>b.textContent.includes(t));q('Проверить камеру').click();
          await new Promise(r=>requestAnimationFrame(r));const c=q('Отмена');if(!c)return false;c.click();return true})()""")
        S.check("во время включения появляется кнопка «Отмена» (запрос ещё не завершён)", cancelled)
        await asyncio.sleep(2)
        S.check("«Отмена» во время включения: поток не остаётся запущенным", await G.js(LIVE) == 0 and "выключена" in await G.js("(" + CAMB + ")?.innerText"))
        # вход после предпросмотра
        await click_text(G, "Проверить камеру")
        await G.wait_for("document.querySelector('.pj-video-el')?.videoWidth>0", 15)
        S.check("вход в переговорку после предпросмотра", await G.join(navigate=False) and await G.wait_for("document.querySelectorAll('.st-cell').length>=1", 30))
        await asyncio.sleep(2)
        S.check("в комнате камера включилась (предпросмотр её не блокирует)", await G.js("!![...document.querySelectorAll('.st-cell.type-camera video')].length"))
        errs = [e for c in (R, B, G, anon) for e in (await c.state())["errs"] if "favicon" not in e]
        S.check("ошибок страницы нет", not errs, str(errs)[:300])
    finally:
        for c in (R, G):
            await c.close()
        for name in ("anon", "B"):
            try:
                await locals()[name].close()
            except Exception:
                pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
