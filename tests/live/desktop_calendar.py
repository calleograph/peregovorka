"""Сценарий (см. README.md): календарь Outlook и форма «Новая встреча» в настоящем приложении Windows (Electron из desktop/, под Xvfb на Linux) при масштабе экрана 100, 125, 150 и 200 %.
Проверяется: панель (кнопки одной высоты, нет горизонтальной прокрутки), плашки недели (короткие 15/30 минут, пересечения: ничего не обрезано и не наложено), форма (курсор в названии, список времени
в 24 часа целиком в окне, окно и кнопки внизу видны). Подставной Graph и включённый календарь — как в scenario_calendar.py (devserver_live.py, Vite :5180, `/__dev/calendar`).
Не проверяется: настоящий Windows и системное масштабирование Windows (здесь — коэффициент Chromium `--force-device-scale-factor`), настоящий Exchange."""
import asyncio, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from desktop_lib import App, check, results  # noqa: E402
from desktop_app import A_URL, STUB, login, probe_in_shell, reset_sessions  # noqa: E402
from scenario_calendar_visual import HEAD, CHIPS, EXACT, FORM, TIME  # noqa: E402


def setup():
    """Календарь включён (Graph с подставным транспортом), события у alice."""
    import httpx, urllib.request
    api = "http://127.0.0.1:8000/api/v1"
    c = httpx.Client(timeout=20)
    c.post(f"{api}/auth/login", json={"login": "root", "password": "root-pass"}).raise_for_status()
    c.headers["X-CSRF-Token"] = c.get(f"{api}/auth/me").json()["csrf_token"]
    c.put(f"{api}/admin/settings/outlook", json={"enabled": True, "sync_mode": "graph", "graph_tenant_id": "t", "graph_client_id": "c", "graph_client_secret": "s", "calendar_enabled": True, "calendar_write": True, "guest_links": True}).raise_for_status()
    c.post(f"{api}/auth/logout")
    r = urllib.request.Request("http://127.0.0.1:8000/__dev/calendar", data=json.dumps({"reset": True, "down": False}).encode(), method="POST", headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r))["day1"]


async def main():
    day1 = setup()
    for scale in (1.0, 1.25, 1.5, 2.0):
        reset_sessions()
        app = App(scale=scale, port=9340, inspect=9341)
        tag = f"{int(scale * 100)} %"
        try:
            await app.start()
            await app.m(STUB)
            shell = await app.page("app://shell/index.html")
            await shell.wait("[...document.querySelectorAll('button')].some((b) => b.textContent.includes('Добавить организацию'))", 30)
            await shell.js("(() => { const b = [...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')); b.click(); })()")
            await shell.wait("!!document.querySelector('#srv')", 10)
            await probe_in_shell(shell, A_URL)
            page = await app.page(A_URL)
            check(f"{tag}: вход alice в организацию", await login(page, "alice", "alice-pass"))
            await page.wait("!!document.querySelector('.rail-nav a[href=\"/calendar\"], nav a[href=\"/calendar\"]')", 30)
            await page.js("document.querySelector('a[href=\"/calendar\"]').click()")
            await page.wait("!!document.querySelector('.cal-connect, .cal-view')", 20)
            if await page.js("!!document.querySelector('.cal-connect')"):
                await page.js("document.querySelector('.cal-connect input[type=checkbox]').click()")
                await asyncio.sleep(0.4)
                await page.js("document.querySelector('.cal-connect button.primary').click()")
            check(f"{tag}: календарь открыт", await page.wait("!!document.querySelector('.cal-view')", 20))
            await page.js("[...document.querySelectorAll('.cal-head .seg button')].find((b) => b.textContent.trim() === 'Неделя')?.click()")
            await page.js(f"""(() => {{ const i = document.querySelector('.cal-jump input'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(i, {day1!r});
              i.dispatchEvent(new Event('input', {{bubbles: true}})); i.dispatchEvent(new Event('change', {{bubbles: true}})); }})()""")
            check(f"{tag}: события недели загружены", await page.wait("document.querySelectorAll('.cal-slot').length >= 8", 20))
            await asyncio.sleep(0.8)
            vp = await page.js("innerWidth + 'x' + innerHeight")
            h = json.loads(await page.js(f"JSON.stringify({HEAD})"))
            hs = {c["h"] for c in h["ctl"]}
            check(f"{tag} ({vp}): кнопки панели одной высоты, нет горизонтальной прокрутки", len(hs) == 1 and h["sw"] <= h["iw"] and all(c["l"] >= -0.5 and c["r"] <= h["iw"] + 0.5 for c in h["ctl"]), str((sorted(hs), h["sw"], h["iw"])))
            ch = json.loads(await page.js(f"JSON.stringify({CHIPS})"))
            check(f"{tag}: плашки целы — ничего не обрезано, не вылезает и не накладывается ({ch['n']} шт.)", not ch["bad"] and ch["n"] >= 8, str(ch["bad"][:3]))
            ex = json.loads(await page.js(f"JSON.stringify({EXACT})"))
            check(f"{tag}: начало каждой плашки на своей отметке времени", not ex, str(ex[:3]))
            await app.shot_window(f"calendar-desktop-{int(scale * 100)}")
            await page.js("[...document.querySelectorAll('.cal-head-a .btn')].find((b) => b.textContent.includes('Создать')).click()")
            check(f"{tag}: форма открыта, курсор в названии", await page.wait("!!document.querySelector('.modal .cal-form')", 10) and str(json.loads(await page.js(f"JSON.stringify({FORM})"))["active"]).startswith("Например"))
            f = json.loads(await page.js(f"JSON.stringify({FORM})"))
            check(f"{tag}: окно формы и кнопки внизу на экране", f["l"] >= 0 and f["r"] <= f["iw"] and f["t"] >= 0 and f["b"] <= f["ih"] and f["foot"]["b"] <= f["ih"] and f["sw"] <= f["iw"], str(f))
            await page.js("document.querySelector('.tf-btn').click()")
            await asyncio.sleep(0.5)
            t = json.loads(await page.js(f"JSON.stringify({TIME})"))
            check(f"{tag}: список времени (24 ч) у поля, не перекрывает его и целиком в окне", bool(t["list"]) and (t["list"]["t"] >= t["in"]["b"] - 1 or t["list"]["b"] <= t["in"]["t"] + 1) and t["list"]["b"] <= t["ih"] and t["selVisible"] and ":" in t["val"] and "M" not in t["val"], str(t)[:240])
            await app.shot_window(f"calendar-desktop-{int(scale * 100)}-form")
        finally:
            try: await app.main.close()
            except Exception: pass
            try: app.proc.terminate()
            except Exception: pass
            await asyncio.sleep(1.5)
    bad = [r[0] for r in results if not r[1]]
    print(f"ИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {bad}")

if __name__ == "__main__":
    asyncio.run(main())
