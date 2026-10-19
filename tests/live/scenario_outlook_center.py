"""Сценарий (см. README.md): публичный центр установки Outlook `/outlook` и публичные ресурсы — в чистой сессии БЕЗ входа в Peregovorka.

Проверяется: страница открывается без входа; манифест, пакет, сценарий PowerShell и документация API отдаются без авторизации с корректными типами и без перенаправлений;
защищённые методы остаются закрытыми; проверка «с этого компьютера» запускается и показывает понятный результат; админская диагностика показывает схему HTTPS и причины;
вёрстка на 1366 и 390 px без горизонтальной прокрутки. Не проверяется: настоящий Exchange/Outlook, настоящий внешний прокси (тестовый стенд — http://localhost).
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

SET = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); const h = {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token};
  const r = await fetch('/api/v1/admin/settings/outlook', {method: 'PUT', headers: h, body: JSON.stringify({enabled: true, public_url: 'https://meet.example.org'})}); return r.status; })()"""
HEAD = """(async (p) => { const r = await fetch(p, {redirect: 'manual', credentials: 'omit'}); return [r.status, r.type, r.headers.get('content-type')]; })"""


async def main():
    A = S.Client("alice", "root", "root-pass", 9498)
    try:
        await A.start()
        S.check("администратор включил интеграцию и публичный адрес", (await A.js(SET)) == 200)
        status = await A.js("(async () => (await (await fetch('/api/v1/admin/outlook/status')).json()).checks.map(c => [c.key, c.ok, c.message.slice(0, 160)]))()")
        by = {k: (ok, m) for k, ok, m in status}
        S.check("админ-диагностика: схема HTTPS не угадывается без признаков («Требуется уточнение»)", "topology" in by and "Требуется уточнение" in by["topology"][1], str(by.get("topology")))
        S.check("админ-диагностика: API внутри приложения успешно (отдельно от публичного адреса)", by.get("api_internal", (None,))[0] is True, str(by.get("api_internal")))
        S.check("админ-диагностика: недоступность публичного адреса с сервера — не ошибка и не общий «ConnectError»", by.get("api", (False,))[0] is not False and "ConnectError" not in str(by.get("api")), str(by.get("api")))
        await A.send("Network.clearBrowserCookies")
        await A.goto("/outlook", 2.5)
        S.check("страница /outlook открывается без входа (заголовок)", await A.wait_for("document.querySelector('h1')?.textContent.includes('Надстройка Peregovorka для Outlook')", 20))
        S.check("не перенаправило на страницу входа", await A.js("location.pathname === '/outlook'"))
        text = await A.js("document.body.innerText")
        for needle in ("Скачать манифест", "Классический Outlook для Windows", "Новый Outlook для Windows", "Outlook в браузере", "Для администратора Exchange", "Скачать полный пакет установки",
                       "Subscription Edition", "Microsoft 365", "Mailbox API", "Когда импорт сертификатов нужен", "Открыть документацию API", "управляется администратором Exchange"):
            S.check(f"на странице есть «{needle}»", needle in text)
        S.check("на странице нет секретов и внутренних адресов", not any(x in text.lower() for x in ("password", "secret", "localhost", "127.0.0.1")), "")
        for p, ctype in (("/api/v1/outlook/manifest.xml", "xml"), ("/api/v1/outlook/package", "zip"), ("/api/v1/outlook/scripts/Install-OutlookAddin.ps1", "text/plain"), ("/api/docs", "text/html"), ("/api/docs/openapi.json", "json")):
            st, typ, ct = await A.js(f"({HEAD})({p!r})")
            S.check(f"{p}: 200 без входа и без перенаправления, тип {ctype}", st == 200 and typ == "basic" and ctype in (ct or ""), f"{st} {typ} {ct}")
        for p in ("/api/v1/admin/outlook/status", "/api/v1/admin/outlook/package", "/api/v1/outlook/token"):
            st = (await A.js(f"({HEAD})({p!r})"))[0]
            S.check(f"{p}: без входа закрыт", st in (401, 403, 405), str(st))
        await A.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Проверить с этого компьютера'))?.click()")
        S.check("проверка «с этого компьютера» показывает результаты", await A.wait_for("document.querySelectorAll('[aria-label=\"Результаты проверки\"] li').length >= 3", 20))
        res = await A.js("[...document.querySelectorAll('[aria-label=\"Результаты проверки\"] li')].map(l => l.textContent)")
        S.check("результат: страница без HTTPS на стенде честно помечена ошибкой, API и манифест проверены", any("без HTTPS" in r for r in res) and any("API" in r for r in res) and any("Манифест" in r for r in res), str(res)[:300])
        await A.shot("outlook-center-1366")
        wide = await A.js("document.documentElement.scrollWidth > innerWidth + 2")
        S.check("1366 px: нет горизонтальной прокрутки", not wide)
        await A.send("Emulation.setDeviceMetricsOverride", width=390, height=800, deviceScaleFactor=2, mobile=True); await asyncio.sleep(1.2)
        S.check("390 px: нет горизонтальной прокрутки страницы", not await A.js("document.documentElement.scrollWidth > innerWidth + 2"))
        await A.shot("outlook-center-390")
        S.check("ошибок страницы нет", not (await A.state())["errs"], str((await A.state())["errs"][:3]))
    finally:
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
