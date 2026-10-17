"""Сценарий (см. README.md): SIP в админке — инструкция для инженера АТС, проверка подключения, политика, журнал вызовов. Реальной АТС и службы SIP нет:
проверяется интерфейс и то, что инструкция строится по фактическим настройкам; сеть АТС и звук этим сценарием НЕ проверяются."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

SECRET = "Sip-Live-Pass-9z!"
SETUP = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json();
  const h = {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token};
  const list = await (await fetch('/api/v1/admin/sip/profiles')).json();
  for (const p of list.items.filter(p => p.name === 'Живая АТС')) await fetch('/api/v1/admin/sip/profiles/' + p.id, {method: 'DELETE', headers: h});
  const r = await fetch('/api/v1/admin/sip/profiles', {method: 'POST', headers: h, body: JSON.stringify({name: 'Живая АТС', host: 'pbx.example.local', port: 5060, transport: 'udp',
    direction: 'both', username: 'peregovorka', secret: '%s', caller_id: '+70001112233', allowed_addresses: ['192.0.2.0/24'], codecs: ['PCMU', 'PCMA'], inbound_numbers: ['100']})});
  const p = await fetch('/api/v1/admin/settings/sip_policy', {method: 'PUT', headers: h, body: JSON.stringify({blocked_prefixes: '00, 810', max_concurrent: 5})});
  return [r.status, p.status]; })()""" % SECRET
CLICK = "(t) => { const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === t); if (!b) return false; b.click(); return true; }"


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    try:
        await R.start()
        S.check("профиль и политика созданы через API", (await R.js(SETUP)) == [201, 200], str(await R.js(SETUP)))
        await R.goto("/admin?tab=sip", 3.0)
        S.check("страница SIP открыта, есть кнопка «Инструкция по подключению АТС»", await R.wait_for("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Инструкция по подключению АТС')", 15))
        S.check("рядом есть кнопка «Проверить подключение»", await R.js("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Проверить подключение')"))
        await R.shot("sip-page")
        await R.js(f"({CLICK})('Инструкция по подключению АТС')")
        S.check("открылось окно с четырьмя вкладками", await R.wait_for("[...document.querySelectorAll('[role=dialog] [role=tab]')].map(t => t.textContent.trim()).join('|') === 'Схема подключения|Сетевые порты|Настройки АТС|Диагностика'", 15),
                str(await R.js("[...document.querySelectorAll('[role=dialog] [role=tab]')].map(t => t.textContent.trim())")))
        S.check("схема: SIP-телефон → АТС → SIP-служба → LiveKit → участники, отмечено правило брандмауэра",
                await R.wait_for("(() => { const t = document.querySelector('.sg-flow')?.innerText || ''; return t.includes('SIP-телефон') && t.includes('LiveKit') && t.includes('Участники конференции') && t.includes('брандмауэр'); })()", 15))
        S.check("параметры: порт 5060 и профиль «Живая АТС» (pbx.example.local)", await R.js("(() => { const t = document.querySelector('[role=dialog]').innerText; return t.includes('5060') && t.includes('pbx.example.local'); })()"))
        await R.shot("sip-guide-scheme")
        await R.js("[...document.querySelectorAll('[role=dialog] [role=tab]')].find(t => t.textContent.trim() === 'Сетевые порты').click()")
        S.check("порты: таблица правил АТС → Peregovorka и обратно", await R.wait_for("(() => { const t = document.querySelector('[role=dialog] table')?.innerText || ''; return t.includes('SIP-сигнализация') && t.includes('RTP-аудио') && /20000/.test(t); })()", 10))
        await R.shot("sip-guide-ports")
        await R.js("[...document.querySelectorAll('[role=dialog] [role=tab]')].find(t => t.textContent.trim() === 'Настройки АТС').click()")
        S.check("«Настройки АТС»: готовый текст, блоки «Передайте…» и «Уточните…»", await R.wait_for("(() => { const t = document.querySelector('[role=dialog]').innerText; return t.includes('Передайте инженеру SIP следующие данные') && t.includes('Уточните у инженера АТС') && t.includes('ПОДКЛЮЧЕНИЕ АТС К PEREGOVORKA'); })()", 10))
        S.check("пароль профиля в инструкции не показан", not await R.js(f"document.body.innerText.includes('{SECRET}')"))
        S.check("есть кнопки «Скопировать инструкцию» и «Скачать инструкцию»", await R.js("['Скопировать инструкцию', 'Скачать инструкцию'].every(l => [...document.querySelectorAll('[role=dialog] button')].some(b => b.textContent.trim() === l))"))
        await R.js(f"({CLICK})('Скопировать инструкцию')")
        S.check("после копирования показано сообщение (в headless буфер может быть запрещён — тогда подсказка про «Скачать»)", await R.wait_for("document.querySelector('[role=dialog] .alert.ok') !== null", 5))
        await R.shot("sip-guide-pbx")
        await R.js("[...document.querySelectorAll('[role=dialog] [role=tab]')].find(t => t.textContent.trim() === 'Диагностика').click()")
        S.check("диагностика: кнопка проверки и шесть строк результатов", await R.wait_for("document.querySelectorAll('.sip-summary li').length === 6", 10))
        await R.js("[...document.querySelectorAll('[role=dialog] button')].find(b => b.textContent.trim() === 'Проверить подключение').click()")
        S.check("проверка выполнилась: этапы показаны (АТС example.local недоступна — это ожидаемо)", await R.wait_for("(() => { const t = document.querySelector('[role=dialog]').innerText; return t.includes('Проверка не пройдена') || t.includes('Проверка пройдена'); })()", 40),
                (await R.js("document.querySelector('[role=dialog]').innerText"))[:400])
        S.check("конфигурация отмечена корректной, ответ АТС — не успешный (нет настоящей АТС)", await R.js("(() => { const li = [...document.querySelectorAll('.sip-summary li')]; return li[0].classList.contains('ok') && !li[2].classList.contains('ok'); })()"))
        await R.shot("sip-guide-diag")
        await R.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        await R.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
        S.check("Escape закрывает окно", await R.wait_for("document.querySelector('[role=dialog]') === null", 5))
        S.check("журнал вызовов виден на странице", await R.js("document.body.innerText.includes('Журнал вызовов')"))
        await R.goto("/admin?tab=sip_policy", 2.5)
        S.check("страница «Политика телефонии» показывает сохранённые значения", await R.wait_for("[...document.querySelectorAll('input')].some(i => i.value === '00,810')", 15), await R.js("[...document.querySelectorAll('input')].map(i => i.value).join('|').slice(0, 200)"))
        # узкий экран
        await R.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=1, mobile=True)
        await R.goto("/admin?tab=sip", 2.5)
        await R.wait_for("[...document.querySelectorAll('button')].some(b => b.textContent.trim() === 'Инструкция по подключению АТС')", 15)
        await R.js(f"({CLICK})('Инструкция по подключению АТС')")
        S.check("390 px: окно инструкции открылось, вкладки видны", await R.wait_for("(() => { const t = document.querySelector('[role=dialog] [role=tab]'); return !!t && t.getBoundingClientRect().height > 10; })()", 15))
        S.check("390 px: окно не вызывает горизонтальной прокрутки страницы", await R.js("document.documentElement.scrollWidth <= window.innerWidth + 1"), str(await R.js("[document.documentElement.scrollWidth, window.innerWidth]")))
        await R.shot("sip-guide-390")
        S.check("ошибок страницы нет", not (await R.state())["errs"], str((await R.state())["errs"][:2]))
    finally:
        await R.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
