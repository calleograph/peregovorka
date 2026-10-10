"""Сценарий: плавающий плеер записей — волна, субтитры, стенограмма, мини-плеер, переходы между разделами, перетаскивание, мобильный режим.
Данные: два участника (A: речь 0–14 с с паузой 5–9 с; B: речь 10–14 с, одновременно с A), настоящее сведение ffmpeg, реплики стенограммы подсунуты через /__dev/segment
с временем, привязанным к тем же отметкам t0, что и звук. Запускать из venv backend (нужны websockets); стенд — с FFMPEG_BIN; комната `north`."""
import asyncio, base64, http.cookiejar, json, os, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

B = "http://127.0.0.1:8000/api/v1"
D = "http://127.0.0.1:8000/__dev/"
S.ROOM = "/rooms/north"


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


def dev(path, body=None):
    urllib.request.urlopen(urllib.request.Request(D + path, method="POST", data=json.dumps(body or {}).encode(), headers={"content-type": "application/json"})).read()


def prepare():
    root, alice, bob = Api("root", "root-pass"), Api("alice", "alice-pass"), Api("bob", "bob-pass")
    rooms = {x["slug"]: x for x in root.call("GET", "/rooms")}
    ja, jb = alice.call("POST", f"/rooms/{rooms['north']['id']}/join", {}), bob.call("POST", f"/rooms/{rooms['north']['id']}/join", {})
    mid = ja["meeting_id"]
    t0 = time.time()
    dev("pcm", {"meeting_id": mid, "identity": ja["identity"], "seconds": 14, "freq": 440, "start_offset": 0, "silence": [[5, 9]]})
    dev("pcm", {"meeting_id": mid, "identity": jb["identity"], "seconds": 4, "freq": 660, "start_offset": 10})

    def seg(ident, text, a, b):                           # время реплики — от того же момента, что и t0 звука
        now = time.time() - t0
        urllib.request.urlopen(urllib.request.Request(D + "segment", method="POST", data=json.dumps({"meeting_id": mid, "identity": ident, "text": text, "offset": a - now}).encode(), headers={"content-type": "application/json"})).read()
    # ASR публикует длительность реплики 2 с (стенд); для проверок важно положение начала
    seg(ja["identity"], "Предлагаю утвердить бюджет проекта", 0.5, 4.5)
    seg(ja["identity"], "Решили: бюджет утверждаем", 9.5, 13)
    seg(jb["identity"], "Согласен, сроки уточню", 10.5, 13.5)
    time.sleep(2)
    e = root.call("POST", f"/meetings/{mid}/end")
    assert e.get("_status") in (200, 204), e
    mix = None
    for _ in range(60):
        m = root.call("GET", f"/meetings/{mid}/media")
        mix = next((x for x in m.get("mixes", []) if x["kind"] == "mix_audio"), None)
        if mix and mix["status"] != "processing":
            break
        time.sleep(1)
    assert mix and mix["status"] == "ready", mix
    return mid, mix, m


PEAK_COLS = """(()=>{const c=document.querySelector('.plr canvas');if(!c)return null;const g=c.getContext('2d');const w=c.width,h=c.height;const d=g.getImageData(0,0,w,h).data;
  const col=(x)=>{let n=0;for(let y=0;y<h;y++){if(d[(y*w+x)*4+3]>0)n++}return n/h};
  const at=(f)=>{const x=Math.floor(w*f);let m=0;for(let k=-3;k<=3;k++){m=Math.max(m,col(Math.min(w-1,Math.max(0,x+k))))}return m};
  return {a:at(0.20),gap:at(0.50),b:at(0.82)}})()"""


async def main():
    mid, mix, media = prepare()
    parts = media["participants"]
    A = S.Client("P-root", "root", "root-pass", 9621)
    await A.start()
    S.check("плеер: данные подготовлены (общая запись и два файла участников)", len(parts) == 2 and mix["duration_s"] >= 13)
    await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=800, deviceScaleFactor=1, mobile=False)
    await A.js("localStorage.clear()")
    await A.goto(f"/history/{mid}", 3)
    ok = await A.wait_for("!!document.querySelector('.rec-block')", 15)
    S.check("в истории есть блок «Записи»", ok)
    # --- 1. открытие: окно без затемнения, страница остаётся доступной
    await A.js("[...document.querySelectorAll('.rec-block button')].find(b=>b.textContent.trim()==='Слушать').click()")
    S.check("плеер открылся как плавающее окно", await A.wait_for("!!document.querySelector('.plr.full')", 10))
    S.check("нет затемнения и блокировки страницы", await A.js("!document.querySelector('.modal-back,.mp-back') && getComputedStyle(document.querySelector('.plr')).position==='fixed'"),
            str(await A.js("[...document.querySelectorAll('.modal-back,.mp-back')].map(e=>e.className+': '+e.innerText.slice(0,60))")))
    S.check("страница под плеером доступна: ссылка «История» в шапке кликабельна (не перекрыта)", await A.js("(()=>{const a=[...document.querySelectorAll('.topnav a')].find(x=>x.textContent.includes('История'));const r=a.getBoundingClientRect();const e=document.elementFromPoint(r.left+r.width/2,r.top+r.height/2);return a.contains(e)})()"))
    S.check("данные записи загружены (метаданные, длительность ≈ 14 с)", await A.wait_for("(()=>{const a=document.querySelector('.plr audio');return a&&a.readyState>=1&&a.duration>12&&a.duration<17})()", 15))
    # --- 2. волновая форма
    S.check("волновая форма нарисована по пикам (не обычная шкала)", await A.wait_for("!!document.querySelector('.plr .wf.has-peaks canvas')", 15))
    await asyncio.sleep(0.5)
    pk = await A.js(PEAK_COLS)
    S.check("волна соответствует звуку: речь высоко, пауза низко, речь снова высоко", bool(pk) and pk["a"] > 0.45 and pk["gap"] < 0.2 and pk["b"] > 0.45, str(pk))
    await A.shot("player-full")
    # --- 3. перемотка по волне: клик, перетаскивание, касание
    box = await A.js("(()=>{const r=document.querySelector('.plr .wf').getBoundingClientRect();return {x:r.left,y:r.top+r.height/2,w:r.width}})()")
    async def mouse(t, x, y, **kw):
        await A.send("Input.dispatchMouseEvent", type=t, x=x, y=y, button="left", clickCount=1, buttons=1 if t != "mouseReleased" else 0, **kw)
    dur = await A.js("document.querySelector('.plr audio').duration")
    await mouse("mouseMoved", box["x"] + box["w"] * 0.5, box["y"]); await mouse("mousePressed", box["x"] + box["w"] * 0.5, box["y"]); await mouse("mouseReleased", box["x"] + box["w"] * 0.5, box["y"])
    ct = await A.js("document.querySelector('.plr audio').currentTime")
    S.check("щелчок по волне перематывает на соответствующее время", abs(ct - dur * 0.5) < 0.7, f"{ct:.1f} из {dur:.1f}")
    await mouse("mousePressed", box["x"] + box["w"] * 0.2, box["y"]); await mouse("mouseMoved", box["x"] + box["w"] * 0.5, box["y"]); await mouse("mouseMoved", box["x"] + box["w"] * 0.8, box["y"])
    tip = await A.js("document.querySelector('.plr .wf-tip')?.textContent || ''")
    await mouse("mouseReleased", box["x"] + box["w"] * 0.8, box["y"])
    ct = await A.js("document.querySelector('.plr audio').currentTime")
    S.check("перетаскивание позиции по волне работает", abs(ct - dur * 0.8) < 0.7, f"{ct:.1f}")
    S.check("при наведении показывается время выбранной позиции", bool(tip) and ":" in tip, tip)
    await A.send("Input.dispatchTouchEvent", type="touchStart", touchPoints=[{"x": box["x"] + box["w"] * 0.3, "y": box["y"]}])
    await A.send("Input.dispatchTouchEvent", type="touchEnd", touchPoints=[])
    ct = await A.js("document.querySelector('.plr audio').currentTime")
    S.check("касание пальцем по волне перематывает запись", abs(ct - dur * 0.3) < 0.8, f"{ct:.1f}")
    # --- 4. субтитры
    S.check("субтитры по умолчанию выключены", await A.js("!document.querySelector('.plr .plr-subs')"))
    await A.js("document.querySelector('.plr button[aria-label=\"Субтитры\"]').click()")
    await A.js("(()=>{const a=document.querySelector('.plr audio');a.pause();a.currentTime=2})()"); await asyncio.sleep(0.4)
    t1 = await A.js("document.querySelector('.plr .plr-subs')?.innerText || ''")
    S.check("на 2-й секунде показана реплика первого участника с его именем", "Предлагаю утвердить бюджет" in t1 and "Алиса" in t1, t1)
    await A.js("document.querySelector('.plr audio').currentTime=7"); await asyncio.sleep(0.4)
    S.check("в паузе старый текст не висит", (await A.js("document.querySelector('.plr .plr-subs')?.innerText || ''")).strip() == "")
    await A.js("document.querySelector('.plr audio').currentTime=11.5"); await asyncio.sleep(0.4)
    t3 = await A.js("document.querySelector('.plr .plr-subs')?.innerText || ''")
    S.check("при одновременной речи видны обе реплики с именами", "Решили" in t3 and "Согласен" in t3 and "Борис" in t3, t3)
    await A.js("(()=>{const s=document.querySelector('.plr select.plr-speed');const set=Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set;set.call(s,'1.5');s.dispatchEvent(new Event('change',{bubbles:true}))})()")
    await A.js("(()=>{const a=document.querySelector('.plr audio');a.currentTime=8.8;a.play()})()")
    await asyncio.sleep(2.2)
    st = await A.js("(()=>{const a=document.querySelector('.plr audio');return {t:a.currentTime,r:a.playbackRate,s:document.querySelector('.plr .plr-subs')?.innerText||''}})()")
    S.check("скорость 1,5× применена, субтитры синхронны (с 8,8 с через 2,2 с ≈ 12 с играют реплики из конца записи)", st["r"] == 1.5 and 11 < st["t"] < 13.5 and ("Решили" in st["s"] or "Согласен" in st["s"]), str(st))
    await A.js("document.querySelector('.plr audio').pause()")
    # --- 5. стенограмма
    await A.js("document.querySelector('.plr button[aria-label=\"Стенограмма\"]').click()")
    S.check("панель «Стенограмма» открылась и показывает три реплики с именами", await A.wait_for("document.querySelectorAll('.plr .plr-cue').length===3 && !!document.querySelector('.plr .plr-cue-s')", 8),
            str(await A.js("[...document.querySelectorAll('.plr .plr-cue')].map(c=>c.innerText.replace(/\\s+/g,' '))")))
    await A.js("document.querySelector('.plr audio').currentTime=10.6"); await asyncio.sleep(0.5)
    S.check("текущая реплика подсвечена", await A.js("!!document.querySelector('.plr .plr-cue.now') && document.querySelector('.plr .plr-cue.now').innerText.includes('Согласен') || document.querySelector('.plr .plr-cue.now').innerText.includes('Решили')"))
    await A.js("[...document.querySelectorAll('.plr .plr-cue')].find(b=>b.innerText.includes('Предлагаю')).click()"); await asyncio.sleep(0.3)
    ct = await A.js("document.querySelector('.plr audio').currentTime")
    S.check("щелчок по реплике перематывает запись к её началу", abs(ct - 0.5) < 0.8, f"{ct:.2f}")
    await A.js("(()=>{const i=document.querySelector('.plr .plr-search input');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'бюджет');i.dispatchEvent(new Event('input',{bubbles:true}))})()"); await asyncio.sleep(0.4)
    S.check("поиск находит реплики со словом «бюджет» (2 из 3)", await A.js("document.querySelectorAll('.plr .plr-cue.hit').length===2"))
    await A.shot("player-transcript")
    S.check("панель стенограммы сворачивается", await A.js("(()=>{document.querySelector('.plr button[aria-label=\"Стенограмма\"]').click();return true})()") and await A.wait_for("!document.querySelector('.plr .plr-tr')", 4))
    # --- 6. мини-плеер и переходы между разделами
    await A.js("(()=>{const a=document.querySelector('.plr audio');a.__mark='same';a.currentTime=1;a.play()})()"); await asyncio.sleep(1.0)
    await A.js("document.querySelector('.plr button[aria-label=\"Свернуть в мини-плеер\"]').click()")
    S.check("плеер свернулся в компактное окно без остановки звука", await A.wait_for("!!document.querySelector('.plr.mini') && !document.querySelector('.plr audio').paused", 4))
    S.check("в мини-плеере нет стенограммы и тяжёлых элементов управления", await A.js("!document.querySelector('.plr .plr-tr') && !document.querySelector('.plr .plr-ctl')"))
    t_before = await A.js("document.querySelector('.plr audio').currentTime")
    for path, name in (("a[href=\"/\"].topnav, .topnav a[href=\"/\"]", "Переговорки"), (".topnav a[href=\"/history\"]", "История")):
        await A.js(f"document.querySelector('{path}')?.click()"); await asyncio.sleep(1.2)
    await A.js("(()=>{window.history.pushState({}, '', '/admin'); window.dispatchEvent(new PopStateEvent('popstate'))})()"); await asyncio.sleep(1.5)
    st = await A.js("(()=>{const a=document.querySelector('.plr audio');return {mark:a&&a.__mark,paused:a&&a.paused,t:a&&a.currentTime,path:location.pathname,n:document.querySelectorAll('audio').length}})()")
    S.check("после переходов История → Переговорки → Администрирование звук идёт, тот же элемент, позиция не сброшена", st["mark"] == "same" and not st["paused"] and st["t"] > t_before and st["n"] == 1 and st["path"] == "/admin", str(st))
    await A.js("(()=>{window.history.pushState({}, '', '/profile'); window.dispatchEvent(new PopStateEvent('popstate'))})()"); await asyncio.sleep(1.2)
    S.check("и при переходе в личный кабинет плеер на месте и играет", await A.js("!!document.querySelector('.plr.mini') && !document.querySelector('.plr audio').paused"))
    await A.js("(()=>{window.history.pushState({}, '', '/history/" + mid + "'); window.dispatchEvent(new PopStateEvent('popstate'))})()"); await asyncio.sleep(1.5)
    # --- 7. перетаскивание и границы
    r0 = await A.js("(()=>{const r=document.querySelector('.plr').getBoundingClientRect();return {x:r.left,y:r.top,w:r.width,h:r.height}})()")
    hx, hy = r0["x"] + 120, r0["y"] + 16
    await mouse("mousePressed", hx, hy); await mouse("mouseMoved", hx - 300, hy - 200); await mouse("mouseReleased", hx - 300, hy - 200)
    r1 = await A.js("(()=>{const r=document.querySelector('.plr').getBoundingClientRect();return {x:r.left,y:r.top}})()")
    S.check("окно перетаскивается за заголовок во время воспроизведения (звук не прервался)", abs((r0["x"] - r1["x"]) - 300) < 6 and abs((r0["y"] - r1["y"]) - 200) < 6 and not await A.js("document.querySelector('.plr audio').paused"), f"{r0} → {r1}")
    hx, hy = r1["x"] + 120, r1["y"] + 16
    await mouse("mousePressed", hx, hy); await mouse("mouseMoved", -4000, -4000); await mouse("mouseReleased", -4000, -4000)
    r2 = await A.js("(()=>{const r=document.querySelector('.plr').getBoundingClientRect();return {x:r.left,y:r.top,w:r.width}})()")
    S.check("окно нельзя утащить за пределы экрана (заголовок остаётся доступным)", r2["y"] >= 0 and r2["x"] + r2["w"] >= 80, str(r2))
    saved = await A.js("localStorage.getItem('pg:player:rect:mini')")
    S.check("положение мини-плеера запоминается в браузере", bool(saved) and "x" in saved, str(saved))
    await A.js("document.querySelector('.plr .plr-head').dispatchEvent(new MouseEvent('dblclick',{bubbles:true}))")
    S.check("двойной щелчок по заголовку разворачивает плеер", await A.wait_for("!!document.querySelector('.plr.full')", 3))
    # --- 8. клавиатура и Esc
    await A.js("document.querySelector('.plr audio').pause()")
    await A.js("document.body.focus(); document.activeElement && document.activeElement.blur()")
    await A.send("Input.dispatchKeyEvent", type="keyDown", key="Escape", code="Escape", windowsVirtualKeyCode=27); await A.send("Input.dispatchKeyEvent", type="keyUp", key="Escape", code="Escape", windowsVirtualKeyCode=27)
    await asyncio.sleep(0.3)
    S.check("Esc вне плеера не закрывает его", await A.js("!!document.querySelector('.plr')"))
    await A.js("document.querySelector('.plr').focus()")
    await A.send("Input.dispatchKeyEvent", type="keyDown", key=" ", code="Space", text=" ", windowsVirtualKeyCode=32); await A.send("Input.dispatchKeyEvent", type="keyUp", key=" ", code="Space", windowsVirtualKeyCode=32)
    await asyncio.sleep(0.3)
    S.check("пробел в плеере запускает воспроизведение", not await A.js("document.querySelector('.plr audio').paused"))
    await A.js("document.querySelector('.plr audio').pause()")
    # --- 9. другая запись заменяет текущую; субтитры файла участника — только его реплики
    await A.js("document.querySelector('.rec-individual').open=true"); await asyncio.sleep(0.3)
    await A.js("document.querySelector('.plr audio').play()")
    names = await A.js("[...document.querySelectorAll('.rec-individual .rec-row')].map(r=>r.innerText.replace(/\\s+/g,' '))")
    await A.js("document.querySelectorAll('.rec-individual .rec-row button')[0].click()"); await asyncio.sleep(1.5)
    S.check("открытие другой записи останавливает прежнюю: на странице один аудио-элемент", await A.js("document.querySelectorAll('audio,video').length===1"), str(names))
    S.check("название в плеере сменилось на файл участника", await A.js("document.querySelector('.plr .plr-title b').textContent.includes('.wav')"))
    await A.js("(()=>{const b=document.querySelector('.plr button[aria-label=\"Субтитры\"]'); if(b.getAttribute('aria-pressed')!=='true') b.click(); document.querySelector('.plr button[aria-label=\"Стенограмма\"]').click()})()")
    await asyncio.sleep(1.0)
    cues = await A.js("[...document.querySelectorAll('.plr .plr-cue')].map(c=>c.innerText.replace(/\\s+/g,' '))")
    S.check("у записи участника в стенограмме только его реплики", 1 <= len(cues) <= 2 and all("Согласен" not in c for c in cues) or all("Предлагаю" not in c and "Решили" not in c for c in cues), str(cues))
    await A.shot("player-participant")
    # --- 10. старая запись без волны: обычная шкала, затем волна
    await A.js("document.querySelector('.plr button[aria-label=\"Закрыть плеер (Esc)\"]').click()")
    S.check("крестик закрывает плеер и останавливает звук", await A.wait_for("!document.querySelector('.plr') && document.querySelectorAll('audio,video').length===0", 4))
    dev("forget_waveforms")
    await A.js("[...document.querySelectorAll('.rec-block button')].find(b=>b.textContent.trim()==='Слушать').click()")
    S.check("для записи без волны сразу показана обычная шкала, плеер открылся без задержки", await A.wait_for("!!document.querySelector('.plr .wf.plain')", 5))
    S.check("волна построена по требованию в фоне и появилась без перезапуска плеера", await A.wait_for("!!document.querySelector('.plr .wf.has-peaks')", 25))
    # --- 11. мобильный режим
    await A.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
    await A.goto(f"/history/{mid}", 3)
    await A.js("[...document.querySelectorAll('.rec-block button')].find(b=>b.textContent.trim()==='Слушать').click()"); await asyncio.sleep(1.2)
    await A.js("document.querySelector('.plr button[aria-label=\"Свернуть в мини-плеер\"]')?.click()"); await asyncio.sleep(0.6)
    mb = await A.js("(()=>{const r=document.querySelector('.plr').getBoundingClientRect();return {l:r.left,r:r.right,b:r.bottom,h:innerHeight,w:document.documentElement.clientWidth,mini:!!document.querySelector('.plr.mini')}})()")
    S.check("на телефоне мини-плеер закреплён у нижнего края на всю ширину", mb["mini"] and abs(mb["b"] - mb["h"]) < 3 and mb["l"] <= 1 and mb["r"] >= mb["w"] - 20, str(mb))
    await A.shot("player-mobile")
    errs = await A.js("window.__errs.slice(0,5)")
    S.check("ошибок страницы нет", not errs, str(errs))
    await A.close()
    ok = [r for r in S.results if r[1]]
    print(f"\nИТОГО: {len(ok)} из {len(S.results)}")


asyncio.run(main())
