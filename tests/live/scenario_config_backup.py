"""Сценарий: резервная копия конфигурации через интерфейс — скачать на настроенном сервере, поднять НОВЫЙ чистый сервер (другой ключ шифрования), восстановить из файла.
Запускать из venv backend (websockets); стенд перезапускается скриптом RESTART_PS (путь в переменной окружения), который удаляет базу и поднимает backend заново.
Проверяется: повторный ввод пароля, пароль архива показывается один раз и копируется, файл скачан и не содержит открытых секретов, предпросмотр с предупреждениями, применение,
отчёт, настройки и секреты на новом сервере, вход после импорта."""
import asyncio, http.cookiejar, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

B = "http://127.0.0.1:8000/api/v1"
RESTART = os.environ["RESTART_PS"]


class Api:
    def __init__(self, login, pw):
        self.cj = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj)); self.csrf = ""
        self.csrf = self.call("POST", "/auth/login", {"login": login, "password": pw})["csrf_token"]

    def call(self, m, path, body=None):
        req = urllib.request.Request(B + path, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"content-type": "application/json", "X-CSRF-Token": self.csrf})
        try:
            with self.op.open(req) as r:
                t = r.read(); return json.loads(t) if t else {"_status": r.status}
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "_body": e.read().decode()[:300]}


def restart():
    """Новый чистый сервер: скрипт перезапуска запускается без каналов (иначе поднятый им backend наследует их и вызов не возвращается)."""
    subprocess.Popen(["pwsh", "-NoProfile", "-File", RESTART, "-Clean"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(8)


async def open_page(label, port, dl):
    c = S.Client(label, "root", "root-pass", port)
    await c.start()
    await c.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
    await c.send("Page.setDownloadBehavior", behavior="allow", downloadPath=dl)
    await c.goto("/admin", 3)
    await c.js("window.confirm = () => true")                       # подтверждение применения — внутри страницы; в headless диалог не нужен
    await c.wait_for("[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Сервер')", 15)
    await c.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Сервер')?.click()")
    await c.wait_for("[...document.querySelectorAll('button')].some(b=>b.textContent.trim().startsWith('Резервная копия конфигурации'))", 10)
    await c.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim().startsWith('Резервная копия конфигурации'))?.click()")
    ok = await c.wait_for("!!document.querySelector('[aria-label=\"Резервная копия конфигурации\"]')", 10)
    return c, ok


async def main():
    dl = tempfile.mkdtemp(prefix="cfg-dl-")
    # --- сервер А: настроить
    root = Api("root", "root-pass")
    r = root.call("PUT", "/admin/settings/llm", {"enabled": True, "provider": "external", "type": "openai_compatible", "base_url": "https://llm.old-corp.test/v1", "model": "m1", "allow_http": False, "api_key": "LLM-KEY-LIVE"})
    S.check("сервер А: настройки языковой модели сохранены", "_status" not in r, str(r))
    root.call("POST", "/admin/storages", {"name": "Файловый сервер", "kind": "smb", "config": {"smb_server": "files.old-corp.test", "smb_share": "rec", "smb_username": "svc"}, "secret": "SMB-LIVE-PASS"})
    rooms = {x["slug"]: x for x in root.call("GET", "/rooms")}
    A, ok = await open_page("CFG-A", 9691, dl)
    S.check("раздел «Сервер → Резервная копия конфигурации» открывается", ok)
    S.check("перед скачиванием видно, что входит и что нет", await A.wait_for("document.body.innerText.includes('Что войдёт в архив и что нет')", 15))
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Скачать конфигурацию').click()")
    await asyncio.sleep(0.4)
    await A.js("(()=>{const i=document.querySelector('input[type=password]');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'неверный');i.dispatchEvent(new Event('input',{bubbles:true}))})()")
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Скачать').click()")
    S.check("неверный пароль администратора — отказ, архив не выдан", await A.wait_for("document.body.innerText.includes('Неверный пароль администратора')", 8) and not os.listdir(dl))
    await A.js("(()=>{const i=document.querySelector('input[type=password]');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'root-pass');i.dispatchEvent(new Event('input',{bubbles:true}))})()")
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Скачать').click()")
    S.check("после подтверждения показан пароль архива", await A.wait_for("!!document.querySelector('[data-testid=archive-password]')", 20))
    pw = (await A.js("document.querySelector('[data-testid=archive-password]').textContent")).replace(" ", "")
    S.check("пароль — 20 символов", len(pw) == 20, pw)
    await A.shot("config-password")
    for _ in range(20):
        files = [f for f in os.listdir(dl) if f.endswith(".pgcfg")]
        if files:
            break
        await asyncio.sleep(0.5)
    S.check("файл конфигурации скачан браузером", bool(files), str(os.listdir(dl)))
    path = os.path.join(dl, files[0])
    raw = open(path, "rb").read()
    S.check("в файле нет открытых секретов и адресов", all(x.encode() not in raw for x in ("LLM-KEY-LIVE", "SMB-LIVE-PASS", "old-corp")), str(len(raw)))
    S.check("кнопка закрытия пароля недоступна, пока не отмечено «Я сохранил пароль»", await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Закрыть и забыть пароль')).disabled"))
    await A.js("document.querySelector('.alert.warn input[type=checkbox]').click()"); await asyncio.sleep(0.2)
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Закрыть и забыть пароль')).click()")
    S.check("после закрытия пароль со страницы исчезает", await A.wait_for("!document.querySelector('[data-testid=archive-password]')", 4) and pw not in await A.js("document.body.innerText"))
    await A.close()

    # --- новый чистый сервер Б (другая база, другой ключ шифрования)
    restart()
    for _ in range(40):
        try:
            if urllib.request.urlopen("http://127.0.0.1:8000/api/v1/health/ready", timeout=2).status == 200:
                break
        except Exception:
            time.sleep(1)
    root2 = Api("root", "root-pass")
    S.check("сервер Б чистый: настроек А нет", root2.call("GET", "/admin/settings/llm").get("base_url") in (None, ""), str(root2.call("GET", "/admin/settings/llm"))[:120])
    Bp, ok = await open_page("CFG-B", 9692, dl)
    S.check("на сервере Б раздел открывается", ok)
    await Bp.send("DOM.enable")
    doc = await Bp.send("DOM.getDocument", depth=-1)
    node = await Bp.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector="input[type=file]")
    await Bp.send("DOM.setFileInputFiles", files=[path], nodeId=node["nodeId"])
    S.check("после выбора файла видны версия и дата", await Bp.wait_for("document.body.innerText.includes('создан') && document.body.innerText.includes('на версии')", 10))
    await Bp.js("(()=>{const i=[...document.querySelectorAll('input')].find(x=>x.placeholder&&x.placeholder.includes('xxxx'));const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'zzzz zzzz zzzz zzzz zzzz');i.dispatchEvent(new Event('input',{bubbles:true}))})()")
    await Bp.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Проверить архив').click()")
    S.check("неверный пароль архива — понятная ошибка", await Bp.wait_for("document.body.innerText.includes('Неверный пароль или архив повреждён')", 15))
    await Bp.js("(()=>{const i=[...document.querySelectorAll('input')].find(x=>x.placeholder&&x.placeholder.includes('xxxx'));const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'" + pw + "');i.dispatchEvent(new Event('input',{bubbles:true}))})()")
    await Bp.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Проверить архив').click()")
    S.check("предпросмотр: что будет восстановлено и предупреждения о привязке к старому серверу", await Bp.wait_for("document.body.innerText.includes('Что будет восстановлено') && document.body.innerText.includes('привязанные к старому серверу')", 20))
    body_text = await Bp.js("document.body.textContent")
    S.check("в предпросмотре виден адрес старого сервера, а сам секрет — нет", "old-corp" in body_text and "LLM-KEY-LIVE" not in body_text and "SMB-LIVE-PASS" not in body_text)
    await Bp.shot("config-preview")
    S.check("«Применить» недоступно без подтверждения предупреждений и пароля администратора", await Bp.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Применить конфигурацию').disabled"))
    await Bp.js("[...document.querySelectorAll('input[type=checkbox]')].find(c=>c.parentElement.innerText.includes('прочитал')).click()")
    await Bp.js("(()=>{const i=[...document.querySelectorAll('input[type=password]')].pop();const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'root-pass');i.dispatchEvent(new Event('input',{bubbles:true}))})()")
    await asyncio.sleep(0.3)
    await Bp.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Применить конфигурацию').click()")
    applied = await Bp.wait_for("document.body.innerText.includes('Конфигурация применена') || !!document.querySelector('.alert.error')", 60)
    S.check("конфигурация применена, показан отчёт", applied and "Конфигурация применена" in await Bp.js("document.body.innerText"), str(await Bp.js("[...document.querySelectorAll('.alert.error')].map(e=>e.innerText).join(' | ')")))
    await Bp.shot("config-report")
    S.check("отчёт честно показывает, что хранилище старого сервера не отвечает", "требует внимания" in await Bp.js("document.body.innerText"))
    llm = root2.call("GET", "/admin/settings/llm")
    S.check("настройки модели перенесены (адрес и признак секрета)", llm.get("base_url") == "https://llm.old-corp.test/v1" and llm.get("api_key_set", llm.get("api_key")) not in (None, False, ""), str(llm)[:200])
    S.check("комнаты перенесены", len(root2.call("GET", "/rooms")) >= len(rooms) - 1)
    S.check("вход после импорта работает", Api("root", "root-pass").csrf != "")
    await Bp.close()
    ok = [r for r in S.results if r[1]]
    print(f"\nИТОГО: {len(ok)} из {len(S.results)}")


asyncio.run(main())
