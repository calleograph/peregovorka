"""Сценарий (см. README.md): раздел «Интеграции → Microsoft Outlook» — состояние и проверки, пакет надстройки, история конференций, журнал ошибок надстройки, настройки сверки.
Настоящих Outlook, Exchange и Microsoft 365 здесь нет: проверяется интерфейс администратора и API; установка пакета и работа надстройки в Outlook НЕ проверяются."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

SETUP = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json();
  const h = {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token};
  const a = await fetch('/api/v1/admin/settings/outlook', {method: 'PUT', headers: h, body: JSON.stringify({enabled: true, public_url: 'https://meet.example.org', sync_mode: 'graph',
    graph_tenant_id: 'tenant-live', graph_client_id: 'app-live', graph_client_secret: 'LiveSecret-8x!', mail_kind: 'exchange', sync_on_send: true})});
  const t = await (await fetch('/api/v1/outlook/token', {method: 'POST', headers: h})).json();
  const ah = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + t.token};
  const s = new Date(Date.now() + 3600e3), e = new Date(Date.now() + 5400e3);
  const m = await fetch('/api/v1/outlook/meetings', {method: 'POST', headers: ah, body: JSON.stringify({key: 'live' + Date.now() + 'abcdefghij', subject: 'Живая планёрка', start: s.toISOString(), end: e.toISOString(),
    recurring: false, organizer: 'owner@example.org', ews_id: 'AAMkLiveEwsId0123456789', rest_id: 'AAMkLiveRestId0123456789', tz: 'Europe/Moscow'})});
  const l = await fetch('/api/v1/outlook/log', {method: 'POST', headers: ah, body: JSON.stringify({where: 'insert', message: 'Живая ошибка: не удалось вставить ссылку'})});
  return [a.status, m.status, l.status]; })()"""


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    try:
        await R.start()
        res = await R.js(SETUP)
        S.check("настройки, конференция из «Outlook» и запись об ошибке созданы через API", res == [200, 200, 204], str(res))
        await R.goto("/admin?tab=outlook", 3.0)
        S.check("раздел «Microsoft Outlook» открыт", await R.wait_for("document.body.innerText.includes('Состояние и проверки')", 15))
        S.check("в меню раздел находится в «Интеграции»", await R.js("(() => { const el = [...document.querySelectorAll('aside *, nav *')].find(x => x.textContent.trim() === 'Microsoft Outlook'); return !!el; })()"))
        S.check("проверки показаны с отметками (адрес, сертификат, API, файлы, манифест, сверка)", await R.wait_for("document.querySelectorAll('[aria-label=\"Результаты проверок\"] li').length >= 6", 40),
                str(await R.js("[...document.querySelectorAll('[aria-label=\"Результаты проверок\"] li')].map(l => l.innerText.slice(0, 70))")))
        S.check("сервер не видит example.org — проверка адреса честно показывает проблему, а не «всё хорошо»", await R.js("document.querySelectorAll('[aria-label=\"Результаты проверок\"] li.bad, [aria-label=\"Результаты проверок\"] li.skip').length >= 1"))
        S.check("кнопка «Скачать надстройку для этого сервера» ведёт на пакет", await R.js("[...document.querySelectorAll('a')].some(a => a.textContent.trim() === 'Скачать надстройку для этого сервера' && a.getAttribute('href') === '/api/v1/admin/outlook/package')"))
        zipinfo = await R.js("""(async () => { const r = await fetch('/api/v1/admin/outlook/package'); const b = new Uint8Array(await r.arrayBuffer());
          const txt = new TextDecoder('latin1').decode(b);
          return [r.status, r.headers.get('content-type'), b[0] === 0x50 && b[1] === 0x4b, ['Peregovorka-Outlook.xml', 'Install-OutlookAddin.ps1', 'Test-OutlookAddin.ps1', 'README.txt'].every(n => txt.includes(n)), txt.includes('LiveSecret')]; })()""")
        S.check("пакет скачивается: ZIP с четырьмя файлами, без секретов", zipinfo == [200, "application/zip", True, True, False], str(zipinfo))
        S.check("в настройках видны поля серверной сверки (Graph) и секрет не показан", await R.wait_for("document.body.innerText.includes('Идентификатор каталога (Tenant ID)')", 10) and not await R.js("document.body.innerText.includes('LiveSecret-8x!') || [...document.querySelectorAll('input')].some(i => i.value.includes('LiveSecret'))"))
        S.check("история: конференция «Живая планёрка» с организатором и отметкой «ещё не сверялась»", await R.wait_for("(() => { const t = document.querySelector('[aria-label=\"История конференций\"]')?.innerText || ''; return t.includes('Живая планёрка') && t.includes('owner@example.org') && t.includes('ещё не сверялась'); })()", 15))
        S.check("журнал надстройки показывает ошибку пользователя", await R.wait_for("(document.querySelector('[aria-label=\"Журнал ошибок надстройки\"]')?.innerText || '').includes('Живая ошибка')", 15))
        await R.js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Сверить сейчас').click()")
        S.check("«Сверить сейчас»: Graph недоступен — итог показан, комната не отменена", await R.wait_for("(document.querySelector('[aria-label=\"Проверка сверки календаря\"]')?.innerText || '').includes('Проверено: 1')", 40),
                await R.js("document.querySelector('[aria-label=\"Проверка сверки календаря\"]').innerText"))
        S.check("после сверки в истории — причина ошибки, состояние не «отменена»", await R.wait_for("(() => { const t = document.querySelector('[aria-label=\"История конференций\"]')?.innerText || ''; return /Graph|Entra|связи/.test(t) && !t.includes('отменена'); })()", 20),
                await R.js("document.querySelector('[aria-label=\"История конференций\"]').innerText.slice(0, 300)"))
        await R.shot("outlook-admin-1366")
        await R.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        await R.goto("/admin?tab=outlook", 3.0)
        await R.wait_for("document.body.innerText.includes('Состояние и проверки')", 15)
        S.check("390 px: нет горизонтальной прокрутки страницы", await R.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), str(await R.js("[document.documentElement.scrollWidth, window.innerWidth]")))
        await R.shot("outlook-admin-390")
        S.check("ошибок страницы нет", not (await R.state())["errs"], str((await R.state())["errs"][:2]))
    finally:
        await R.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
