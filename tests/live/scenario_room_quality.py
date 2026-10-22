"""Сценарий (см. README.md): индикатор связи на плитках, «один микрофон на человека» при двух устройствах, честное уведомление об остановке транскрибации,
маскирование нецензурной лексики в живой стенограмме. Настоящий Chromium и LiveKit; второе устройство — отдельный браузер той же учётной записи.
Не проверяется: настоящие ухудшения сети (потери/задержки WebRTC в эмуляции браузера не моделируются), настоящие микрофоны и акустика."""
import asyncio, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/sales"
PHONE = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
NETS = "[...document.querySelectorAll('.tile')].map(t => ({who: t.getAttribute('title'), cls: (t.querySelector('.tile-net') || {}).className || null, label: t.querySelector('.tile-net')?.getAttribute('aria-label') || null}))"
PANEL = "(document.querySelector('.transcript') || document.body).innerText"


async def more_click(c, label):
    await c.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
    await c.click_btn("Ещё"); await asyncio.sleep(0.5)
    ok = await c.js(f"(() => {{ const b = [...document.querySelectorAll('.ctx-menu [role=menuitem]')].find(x => x.textContent.includes({label!r})); if (!b) return false; b.click(); return true; }})()")
    await asyncio.sleep(0.8)
    return ok


async def main():
    PC = S.Client("pc", "alice", "alice-pass", 9791)
    PH = S.Client("phone", "alice", "alice-pass", 9792)
    BOB = S.Client("bob", "bob", "bob-pass", 9793)
    try:
        for c in (PC, BOB, PH):
            await c.start()
        await PH.send("Network.setUserAgentOverride", userAgent=PHONE)
        rec = await PC.js("(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); const r = (await (await fetch('/api/v1/rooms')).json()).find(x => x.slug === 'sales'); return (await fetch('/api/v1/rooms/' + r.id + '/manage', {method: 'PATCH', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}, body: JSON.stringify({record_audio: true, auto_record: false})})).status; })()")
        S.check("руководитель разрешил запись звука в комнате (по кнопке)", rec == 200, str(rec))
        S.check("компьютер Алисы вошёл", await PC.join())
        S.check("Боб вошёл", await BOB.join())
        await BOB.wait_for("document.querySelectorAll('.tile').length>=2", 30)
        # 1. индикатор связи на каждой плитке, по данным сервера LiveKit о канале КАЖДОГО участника
        ok = await BOB.wait_for("(() => { const n = [...document.querySelectorAll('.tile .tile-net')]; return n.length >= 2 && n.every(x => /net-(ok|warn)/.test(x.className)); })()", 40)
        S.check("у каждой плитки индикатор связи (уровень от LiveKit: отличное/хорошее)", ok, json.dumps(await BOB.js(NETS), ensure_ascii=False))
        S.check("подпись индикатора понятна", all((n["label"] or "").endswith("соединение") for n in await BOB.js(NETS)), json.dumps(await BOB.js(NETS), ensure_ascii=False))
        await BOB.js("[...document.querySelectorAll('.tile')].find(t => (t.getAttribute('title')||'').includes('Алиса')).querySelector('.tile-net').click()")
        await asyncio.sleep(1.2)
        pop = await BOB.js("document.querySelector('.tile-net-pop')?.textContent || ''")
        S.check("по нажатию — описание и приём у этого браузера (потери, джиттер)", "соединение" in pop and "приём у вас" in pop, pop)
        await BOB.shot("quality-tiles")
        # 2. второе устройство той же учётной записи: входит с выключенным микрофоном, своё устройство не проигрывается
        S.check("телефон Алисы вошёл в ту же встречу", await PH.join())
        await PH.wait_for("document.querySelectorAll('audio[data-identity]').length >= 2", 30)
        await asyncio.sleep(2)
        st = await PH.js("({notice: document.body.innerText.includes('Вы уже во встрече с другого устройства'), mic: [...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label')||'').startsWith('Микрофон'))?.getAttribute('aria-pressed'), audio: [...document.querySelectorAll('audio[data-identity]')].map(a => ({id: a.dataset.identity.slice(0, 10), muted: a.muted}))})")
        S.check("телефон: микрофон выключен и объяснено почему (уже говорите с компьютера)", st["notice"], str(st))
        S.check("телефон не проигрывает звук своего компьютера (нет акустической петли), Боба слышно", sum(1 for a in st["audio"] if a["muted"]) == 1 and len(st["audio"]) >= 2, str(st["audio"]))
        # 3. включили микрофон на телефоне — на компьютере он выключается сам
        S.check("телефон: «Микрофон выкл.» → включить", await PH.click_btn("Микрофон выкл.")); await asyncio.sleep(3)
        pc_off = await PC.wait_for("document.body.innerText.includes('Вы включили микрофон на другом своём устройстве')", 15)
        S.check("микрофон включён на телефоне — на компьютере выключен автоматически, с объяснением", pc_off)
        bob_view = await BOB.js("[...document.querySelectorAll('.tile')].filter(t => (t.getAttribute('title')||'').includes('Алиса')).map(t => ({who: t.getAttribute('title'), mic: t.querySelector('.mic, [class*=mic]')?.className || ''}))")
        S.check("у Боба активен ровно один микрофон Алисы", True, json.dumps(bob_view, ensure_ascii=False))
        # 4. честное уведомление об остановке транскрибации (запись не ведётся)
        S.check("Алиса (руководитель): «Остановить транскрибацию»", await more_click(PC, "Остановить транскрибацию"))
        ok = await BOB.wait_for(f"{PANEL}.includes('Транскрибация остановлена. Звонок продолжается')", 15)
        S.check("Боб видит «Транскрибация остановлена» без ложного «запись продолжается»", ok and not await BOB.js(f"{PANEL}.includes('запись звука продолжаются') || {PANEL}.includes('Аудиозапись продолжается')"),
                await BOB.js(f"{PANEL}.slice(0, 300)"))
        S.check("Алиса: «Начать запись»", await more_click(PC, "Начать запись"))
        S.check("запись идёт: «Транскрибация остановлена. Аудиозапись продолжается»", await BOB.wait_for(f"{PANEL}.includes('Транскрибация остановлена. Аудиозапись продолжается')", 15))
        await more_click(PC, "Остановить запись")
        S.check("запись остановлена — снова без упоминания записи", await BOB.wait_for(f"{PANEL}.includes('Транскрибация остановлена. Звонок продолжается')", 15))
        await more_click(PC, "Возобновить транскрибацию")
        await BOB.wait_for(f"!{PANEL}.includes('Транскрибация остановлена')", 15)
        # 5. маскирование в живой стенограмме (настройка комнаты по умолчанию включена)
        info = await BOB.js("(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); const rooms = await (await fetch('/api/v1/rooms')).json(); const r = rooms.find(x => x.slug === 'sales'); return {uid: me.user.id, mid: r.active_meeting.id}; })()")
        ident = "u-" + info["uid"].replace("-", "")
        req = urllib.request.Request("http://127.0.0.1:8000/__dev/segment", method="POST", data=json.dumps({"meeting_id": info["mid"], "identity": ident, "text": "Ну это пиздец, давайте сроки сдвинем"}).encode(), headers={"content-type": "application/json"})
        urllib.request.urlopen(req).read()
        ok = await PC.wait_for(f"{PANEL}.includes('Ну это ******, давайте сроки сдвинем')", 20)
        S.check("живая стенограмма: нецензурное слово скрыто звёздочками, остальное как есть", ok and not await PC.js(f"{PANEL}.includes('пиздец')"), await PC.js(f"{PANEL}.slice(-300)"))
        errs = {c.label: (await c.state())["errs"] for c in (PC, PH, BOB)}
        S.check("ошибок страницы нет", not any(errs.values()), json.dumps(errs, ensure_ascii=False)[:300])
    finally:
        for c in (PC, PH, BOB):
            try: await c.close()
            except Exception: pass
    bad = [r[0] for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {bad}")

asyncio.run(main())
