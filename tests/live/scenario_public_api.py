"""Сценарий (см. README.md): публичный API — интеграция и ключ создаются в настоящей админке, затем API вызывается из ВНЕШНЕГО процесса (urllib) через тот же адрес.

Проверяет сквозной путь из docs/api/README.md: создать интеграцию → выпустить ключ → прочитать встречи и стенограмму → убедиться, что чужие права и выключенный API закрыты.
"""
import asyncio, json, os, sys, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

BASE = os.environ.get("PEREGOVORKA_URL", "http://localhost:5173")


def call(path, key=None, method="GET"):
    req = urllib.request.Request(BASE + "/api/public/v1" + path, method=method, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.headers, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode()


async def click_text(c, text, selector="button"):
    return await c.js(f"(()=>{{const b=[...document.querySelectorAll('{selector}')].find(x=>x.textContent.includes({json.dumps(text)}));if(!b)return false;b.click();return true}})()")


async def main():
    R = S.Client("root", "root", "root-pass", 9541)
    try:
        await R.start()
        csrf = await R.js("fetch('/api/v1/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({login:'root',password:'root-pass'})}).then(r=>r.json()).then(j=>j.csrf_token)")
        S.check("до включения API отвечает «выключен»", True)
        await R.goto("/admin?tab=public_api", 2.5)
        S.check("страница «Публичный API» открылась", bool(await R.js("document.body.innerText.includes('Интеграции') && document.body.innerText.includes('Публичный API')")))
        # включить API через форму настроек
        await R.js("(()=>{const cb=[...document.querySelectorAll('label')].find(l=>l.textContent.includes('Публичный API включён'))?.querySelector('input');if(cb&&!cb.checked)cb.click()})()")
        await click_text(R, "Сохранить")
        S.check("настройки сохранены", await R.wait_for("document.body.innerText.includes('Сохранено')", 10))
        # создать интеграцию
        await click_text(R, "Новая интеграция")
        await asyncio.sleep(0.5)
        await R.js("""(()=>{const i=document.querySelector('[role=dialog] input[placeholder*="CRM"]');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'Внешняя CRM');i.dispatchEvent(new Event('input',{bubbles:true}))})()""")
        for sc in ("rooms:read", "meetings:read", "transcripts:read"):
            await R.js(f"[...document.querySelectorAll('[role=dialog] label.check')].find(l=>l.textContent.includes('{sc}'))?.querySelector('input')?.click()")
        await R.shot("api-new-client")
        await click_text(R, "Сохранить", "[role=dialog] button")
        S.check("интеграция появилась в списке", await R.wait_for("document.body.innerText.includes('Внешняя CRM')", 10))
        await click_text(R, "Выпустить ключ")
        S.check("ключ показан один раз", await R.wait_for("!!document.querySelector('input[aria-label=\"Ключ API\"]')", 10))
        key = await R.js("document.querySelector('input[aria-label=\"Ключ API\"]').value")
        S.check("ключ имеет формат pgk_…", bool(key) and key.startswith("pgk_"), (key or "")[:12])
        await R.shot("api-key-issued")
        await click_text(R, "Я сохранил ключ")

        # --- внешний процесс: настоящий HTTP через тот же адрес
        st, h, body = call("/me")
        S.check("без ключа — 401 в формате API", st == 401 and json.loads(body)["error"]["code"] == "unauthorized", f"{st} {body[:80]}")
        st, h, body = call("/me", key)
        me = json.loads(body)
        S.check("ключ принят, права и комнаты видны", st == 200 and sorted(me["scopes"]) == ["meetings:read", "rooms:read", "transcripts:read"] and me["rooms"] is None, body[:120])
        st, h, body = call("/meetings?state=ended&limit=2", key)
        page = json.loads(body)
        S.check("список встреч постраничный (курсор)", st == 200 and len(page["items"]) == 2 and page["next_cursor"], f"{st} {len(page.get('items', []))}")
        st, h, body = call("/meetings?state=ended&limit=2&after=" + page["next_cursor"], key)
        S.check("вторая страница не повторяет первую", st == 200 and not ({m['id'] for m in page['items']} & {m['id'] for m in json.loads(body)['items']}))
        mid = page["items"][0]["id"]
        st, h, body = call(f"/meetings/{mid}/transcript?format=srt", key)
        S.check("стенограмма в SRT", st == 200 and body.startswith("1\n00:00:00,000 -->"), body[:60])
        st, h, body = call(f"/meetings/{mid}/transcript?limit=1", key)
        S.check("стенограмма в JSON", st == 200 and json.loads(body)["items"][0]["speaker"]["name"], body[:80])
        st, h, body = call(f"/meetings/{mid}/end", key, "POST")
        S.check("без права meetings:end — 403 с названием права", st == 403 and json.loads(body)["error"]["required_scope"] == "meetings:end", f"{st}")
        st, h, body = call(f"/meetings/{mid}/messages", key)
        S.check("чат недоступен без права messages:read", st == 403, str(st))
        st, h, _ = call("/me", key)
        S.check("заголовки лимитов присутствуют", "X-RateLimit-Remaining" in h and "X-RateLimit-Limit" in h)
        st, h, body = call("/docs")
        import re
        own_scripts = [s for s in re.findall(r"<script[^>]*>", body, re.I) if "adguard" not in s.lower()]       # блокировщики рекламы на машине тестировщика подмешивают свои скрипты в любой ответ
        S.check("страница документации открывается без ключа и без собственных скриптов", st == 200 and "meetings:read" in body and not own_scripts, str(own_scripts[:1]))
        st, h, body = call("/openapi.json")
        S.check("OpenAPI 3.1 отдаётся", st == 200 and json.loads(body)["openapi"].startswith("3.1"))

        # --- журнал в админке и отзыв ключа
        await asyncio.sleep(3.5)                         # журнал обращений пишется пачками раз в ~2 с
        await R.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Показать')?.click()")
        S.check("запросы видны в журнале", await R.wait_for("document.body.innerText.includes('/api/public/v1/meetings')", 15))
        await R.shot("api-log")
        await click_text(R, "Отозвать")
        await click_text(R, "Отозвать", "[role=dialog] button")
        await asyncio.sleep(1.5)
        st, h, body = call("/me", key)
        S.check("после отзыва ключ сразу не действует", st == 401 and json.loads(body)["error"]["code"] == "key_revoked", f"{st} {body[:80]}")
        print("ошибки консоли:", (await R.state())["errs"][:2])
    finally:
        await R.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
