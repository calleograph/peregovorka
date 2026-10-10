"""Сценарий (см. README.md): сцена встречи 0.11.0 — несколько показов экрана одновременно, личное закрепление, общая сцена ведущего, модерация показа.
Три браузера: A — руководитель (root), B — участник (alice), C — наблюдатель (bob). Источник «экрана» подменён холстом (в headless настоящего экрана нет).
Кроме функций измеряется производительность: пересоздаются ли элементы <video> и переназначается ли их srcObject при смене раскладки, время до следующего кадра, длинные задачи.
"""
import asyncio, json, os, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/pm"
S.INIT += """
navigator.mediaDevices.getDisplayMedia = async function () {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d'); let n = 0; const me = Math.random().toString(36).slice(2, 6);
  setInterval(() => { g.fillStyle = '#134'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '60px sans-serif'; g.fillText('ЭКРАН ' + me + ' ' + (n++), 100, 300); }, 100);
  return c.captureStream(10);
};
window.__srcSets = 0; window.__long = [];
(() => { const d = Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype, 'srcObject');
  Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', { configurable: true, get() { return d.get.call(this); }, set(v) { if (this.tagName === 'VIDEO') window.__srcSets++; d.set.call(this, v); } }); })();
try { new PerformanceObserver((l) => l.getEntries().forEach((e) => window.__long.push(Math.round(e.duration)))).observe({ entryTypes: ['longtask'] }); } catch (e) {}
"""

CELLS = """[...document.querySelectorAll('.st-cell')].map(c=>({k:c.dataset.key,big:c.classList.contains('big'),vis:c.style.visibility!=='hidden',
  name:(c.querySelector('.tile')?.getAttribute('title')||''),video:!!c.querySelector('video'),vw:(c.querySelector('video')?.videoWidth||0)}))"""
VIEW = "(()=>{const v=document.querySelector('.st-view');return v?{main:v.dataset.main,reason:v.dataset.reason,mode:[...v.classList].find(x=>x.startsWith('mode-'))}:null})()"
SANE = """(()=>{const v=document.querySelector('.st-view').getBoundingClientRect();
 const cs=[...document.querySelectorAll('.st-cell')].filter(c=>c.style.visibility!=='hidden').map(c=>{const r=c.getBoundingClientRect();return {k:c.dataset.key,x:r.left-v.left,y:r.top-v.top,w:r.width,h:r.height}});
 const bad=[];for(const a of cs){if(a.w<40||a.h<30)bad.push('мала '+a.k);if(a.y<-2||a.y+a.h>v.height+2)bad.push('выходит по высоте '+a.k)}
 for(let i=0;i<cs.length;i++)for(let j=i+1;j<cs.length;j++){const a=cs[i],b=cs[j];const ox=Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x),oy=Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y);if(ox>2&&oy>2)bad.push('перекрытие '+a.k+' / '+b.k)}
 return {n:cs.length,bad,vh:Math.round(v.height)}})()"""


def ctx_js(key: str, label: str, confirm: bool = False) -> str:
    """Открыть единое контекстное меню плитки (правая кнопка) и выбрать пункт."""
    return f"""(async()=>{{const c=document.querySelector('.st-cell[data-key="'+CSS.escape({json.dumps(key)})+'"]');if(!c)return 'нет плитки';
  const t=c.firstElementChild;const r=t.getBoundingClientRect();t.dispatchEvent(new MouseEvent('contextmenu',{{bubbles:true,cancelable:true,clientX:r.left+20,clientY:r.top+20}}));
  await new Promise(r=>setTimeout(r,200));const items=[...document.querySelectorAll('.ctx-menu .ctx-item')];const b=items.find(x=>x.textContent.trim()==={json.dumps(label)});
  if(!b){{const all=items.map(x=>x.textContent.trim());window.dispatchEvent(new KeyboardEvent('keydown',{{key:'Escape'}}));return 'нет пункта: '+all.join(' | ')}}
  b.click();{"await new Promise(r=>setTimeout(r,150));const y=document.querySelector('.ctx-confirm .btn.danger');if(!y)return 'нет подтверждения';y.click();" if confirm else ""}return 'ok'}})()"""


def more_js(key: str) -> str:
    """Те же пункты — через кнопку «⋯» (без правой кнопки мыши)."""
    return f"""(async()=>{{const c=document.querySelector('.st-cell[data-key="'+CSS.escape({json.dumps(key)})+'"]');const b=c&&c.querySelector('.tile-more');if(!b)return null;b.click();
  await new Promise(r=>setTimeout(r,200));const all=[...document.querySelectorAll('.ctx-menu .ctx-item')].map(x=>x.textContent.trim());window.dispatchEvent(new KeyboardEvent('keydown',{{key:'Escape'}}));return all}})()"""


def layout_js(label: str) -> str:
    """«Макет» → пункт; время от выбора пункта до второго кадра после перестройки."""
    return f"""(async()=>{{document.querySelector('button[aria-label="Макет"]').click();await new Promise(r=>setTimeout(r,150));
  const b=[...document.querySelectorAll('.ctx-menu .ctx-item')].find(x=>x.textContent.trim().replace(/^✓\\s*/,'')==={json.dumps(label)});if(!b)return -1;
  const t0=performance.now();b.click();await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return Math.round((performance.now()-t0)*10)/10}})()"""


async def key_of(c, prefix: str, name: str):
    cells = await c.js(CELLS)
    cam = next((x["k"] for x in cells if x["k"].startswith("camera:") and name in x["name"]), None)
    if not cam:
        return None
    return cam if prefix == "camera" else "screen:" + cam[len("camera:"):]


async def view(c):
    return await c.js(VIEW)


async def mark_videos(c):
    await c.js("document.querySelectorAll('.st-view video').forEach(v=>v.__m=1); window.__srcBase=window.__srcSets; true")


async def videos_kept(c):
    return await c.js("({fresh:[...document.querySelectorAll('.st-view video')].filter(v=>!v.__m).length,total:document.querySelectorAll('.st-view video').length,srcSets:window.__srcSets-window.__srcBase})")


def dev_segment(mid: str, ident: str, text: str):
    req = urllib.request.Request("http://127.0.0.1:8000/__dev/segment", method="POST", data=json.dumps({"meeting_id": mid, "identity": ident, "text": text, "offset": 0}).encode(),
                                 headers={"content-type": "application/json"})
    urllib.request.urlopen(req).read()


async def main():
    A = S.Client("A-root", "root", "root-pass", 9601)
    B = S.Client("B-alice", "alice", "alice-pass", 9602)
    C = S.Client("C-bob", "bob", "bob-pass", 9603)
    perf = {}
    try:
        # 1. вход троих
        for c in (A, B, C):
            await c.start(); S.check(f"1. {c.label}: вошёл", await c.join())
        ok = await asyncio.gather(*[c.wait_for("document.querySelectorAll('.st-cell.type-camera').length>=3", 40) for c in (A, B, C)])
        S.check("1. у всех троих сцена с тремя участниками", all(ok))
        S.check("сцена: старой сетки .tiles больше нет, плитки — в одном контейнере", await C.js("!document.querySelector('.tiles') && !!document.querySelector('.st-view > .st-cell')"))
        # 2–3. камеры
        S.check("2. A: камера включена", await A.click_btn("Камера выкл."))
        S.check("3. B: камера включена", await B.click_btn("Камера выкл."))
        S.check("2–3. C видит видео обеих камер", await C.wait_for(f"(()=>{{const cs={CELLS};return cs.filter(x=>x.k.startsWith('camera:')&&x.vw>0).length>=2}})()", 25))
        # 4–5. два показа экрана одновременно
        S.check("4. A: показ экрана начат", await A.click_btn("Показать экран"))
        await asyncio.sleep(1.5)
        S.check("5. B: второй показ экрана начат (одновременно с A)", await B.click_btn("Показать экран"))
        # 6. C видит оба
        ok = await C.wait_for(f"(()=>{{const cs={CELLS};return cs.filter(x=>x.k.startsWith('screen:')&&x.vw>0).length===2}})()", 30)
        S.check("6. C видит оба показа экрана (два элемента сцены, кадры идут)", ok, str([x["k"][:16] for x in await C.js(CELLS)]))
        v = await view(C)
        bScreen = await key_of(C, "screen", "Алиса"); aScreen = await key_of(C, "screen", "Админ"); bCam = await key_of(C, "camera", "Алиса")
        S.check("6. у C крупно — самый свежий показ (B), режим «сцена»", v and v["main"] == bScreen and v["reason"] == "screen", str(v))
        await C.shot("06-two-screens")
        s = await C.js(S.STATS)
        S.check("6. у C декодируются видеокадры", s["vin"] > 20, str(s))
        # «⋯» даёт те же пункты, что и правая кнопка
        menu = await C.js(more_js(bCam))
        S.check("меню «⋯» на плитке участника — те же пункты (закрепить, крупно, заглушить для себя)", bool(menu) and "Закрепить для себя" in menu and "Открыть крупно" in menu and "Заглушить для себя" in menu, str(menu))
        S.check("у наблюдателя нет пунктов модерации и «Показать всем»", bool(menu) and not any(x in menu for x in ("Показать всем", "Выключить камеру", "Удалить из встречи")), str(menu))

        # 7. C закрепляет B для себя
        await mark_videos(C)
        S.check("7. C: «Закрепить для себя» на камере B", await C.js(ctx_js(bCam, "Закрепить для себя")) == "ok")
        await asyncio.sleep(0.6)
        v = await view(C)
        S.check("7. у C крупно камера B (личное закрепление)", v and v["main"] == bCam and v["reason"] == "pins", str(v))
        S.check("7. отметка «закреплено у вас» на плитке", await C.js(f"!!document.querySelector('.st-cell[data-key=\"'+CSS.escape({json.dumps(bCam)})+'\"] .st-badge.pin')"))
        kept = await videos_kept(C)
        S.check("7. смена главного элемента не пересоздала ни одного <video> и не переназначила srcObject", kept["fresh"] == 0 and kept["srcSets"] == 0, str(kept))
        vb = await view(B)
        S.check("7. у B раскладка не изменилась (закрепление — только у C)", vb and vb["reason"] != "pins", str(vb))

        # 8. A показывает свой экран всем
        aScreenA = await key_of(A, "screen", "Админ")
        S.check("8. A: «Показать всем» на своём экране", await A.js(ctx_js(aScreenA, "Показать всем")) == "ok")
        okB = await B.wait_for(f"document.querySelector('.st-view')?.dataset.main==={json.dumps(await key_of(B, 'screen', 'Админ'))} && document.querySelector('.st-view').dataset.reason==='spotlight'", 10)
        S.check("8. у B (без закреплений) крупно — экран A по общей сцене", okB, str(await view(B)))
        v = await view(C)
        S.check("8. у C личное закрепление важнее общей сцены", v and v["main"] == bCam and v["reason"] == "pins", str(v))
        S.check("8. у C — подсказка «ведущий показывает всем» с кнопкой «Смотреть»", await C.wait_for("(()=>{const b=document.querySelector('.st-spotbar');return !!b&&b.innerText.includes('показывает всем')&&!!b.querySelector('button')})()", 8))
        S.check("8. отметка «Всем» на экране A у B", await B.js("!!document.querySelector('.st-cell.type-screen .st-badge.spot')"))
        await B.shot("08-spotlight"); await C.shot("08-own-pin")

        # 9. C локально выбирает другой экран
        S.check("9. C: «Открыть крупно» на экране B", await C.js(ctx_js(bScreen, "Открыть крупно")) == "ok")
        await asyncio.sleep(0.6)
        v = await view(C)
        S.check("9. у C крупно экран B, у остальных — без изменений", v and v["main"] == bScreen and (await view(B))["reason"] == "spotlight", str(v))
        perf["layout_ms"] = {}
        await mark_videos(C)
        for lay in ("Сетка", "Рядом", "Сцена", "Авто"):
            perf["layout_ms"][lay] = await C.js(layout_js(lay))
            await asyncio.sleep(0.5)
            sane = await C.js(SANE)
            S.check(f"«Макет» → {lay}: раскладка без перекрытий и выходов за край", not sane["bad"], str(sane))
        kept = await videos_kept(C)
        S.check("смена четырёх раскладок: ни одного нового <video>, srcObject не переназначался", kept["fresh"] == 0 and kept["srcSets"] == 0, str(kept))
        await C.shot("09-own-choice")

        # 10–11. B останавливает показ
        S.check("10. B: «Остановить показ»", await B.click_btn("Остановить показ"))
        ok = await C.wait_for(f"!document.querySelector('.st-cell[data-key=\"'+CSS.escape({json.dumps(bScreen)})+'\"]')", 15)
        S.check("10. у C элемент экрана B исчез", ok)
        v = await view(C)
        S.check("11. у C сцена не сломалась: закреплённого экрана нет — показана общая сцена (экран A)", v and v["main"] == aScreen and v["reason"] == "spotlight", str(v))
        sane = await C.js(SANE)
        S.check("11. раскладка без перекрытий", not sane["bad"], str(sane))
        # 12. B снова показывает
        S.check("12. B: снова «Показать экран»", await B.click_btn("Показать экран"))
        ok = await C.wait_for(f"document.querySelector('.st-view')?.dataset.main==={json.dumps(bScreen)}", 20)
        S.check("12. у C экран B вернулся на его место (закрепление по ключу участника, а не по SID дорожки)", ok, str(await view(C)))
        # 13. A останавливает показ B как ведущий
        bScreenA = await key_of(A, "screen", "Алиса")
        await A.wait_for(f"!!document.querySelector('.st-cell[data-key=\"'+CSS.escape({json.dumps(bScreenA)})+'\"] video')", 15)
        S.check("13. A: «Остановить показ экрана» в меню экрана B", await A.js(ctx_js(bScreenA, "Остановить показ экрана")) == "ok")
        ok = await B.wait_for("!!document.querySelector('button[aria-label=\"Показать экран\"]') && document.body.innerText.includes('остановил ваш показ экрана')", 15)
        S.check("13. у B показ прекращён, B получил уведомление", ok)
        ok = await C.wait_for(f"!document.querySelector('.st-cell[data-key=\"'+CSS.escape({json.dumps(bScreen)})+'\"]')", 15)
        S.check("13. у C экран B исчез", ok)
        # 14. показ A продолжается
        await asyncio.sleep(1)
        s0 = await C.js(S.STATS); await asyncio.sleep(2); s1 = await C.js(S.STATS)
        S.check("14. показ A продолжается (элемент на месте, кадры идут)", await C.js(f"!!document.querySelector('.st-cell[data-key=\"'+CSS.escape({json.dumps(aScreen)})+'\"] video')") and s1["vin"] > s0["vin"], f"{s0['vin']}→{s1['vin']}")
        # запрет повторного показа и снятие
        S.check("13a. A: «Остановить и запретить показ» для B (с подтверждением)", await A.js(ctx_js(await key_of(A, "camera", "Алиса"), "Остановить и запретить показ", confirm=True)) == "ok")
        ok = await B.wait_for("(()=>{const b=document.querySelector('button[aria-label=\"Показать экран\"]');return !!b&&b.disabled})()", 12)
        S.check("13a. у B кнопка показа недоступна (право снято на сервере)", ok)
        # права проверяет сервер, а не интерфейс: участник с настоящим CSRF-токеном пытается сменить сцену и остановить чужой показ
        r = await B.js("""(async()=>{const me=await (await fetch('/api/v1/auth/me')).json();const m=await (await fetch('/api/v1/meetings?limit=1')).json();const h={'content-type':'application/json','X-CSRF-Token':me.csrf_token};
          const a=await fetch('/api/v1/meetings/'+m[0].id+'/stage',{method:'PUT',headers:h,body:JSON.stringify({items:[{type:'board'}]})});
          const b=await fetch('/api/v1/meetings/'+m[0].id+'/moderation/stop-share',{method:'POST',headers:h,body:JSON.stringify({identity:'u:00000000-0000-0000-0000-000000000000'})});
          return [a.status,(await a.text()).slice(0,80),b.status]})()""")
        S.check("права: участник не может менять общую сцену и останавливать показ (403 от сервера, не CSRF)", r and r[0] == 403 and r[2] == 403 and "CSRF" not in r[1], str(r))
        S.check("13b. A: «Разрешить показ экрана»", await A.js(ctx_js(await key_of(A, "camera", "Алиса"), "Разрешить показ экрана")) == "ok")
        ok = await B.wait_for("(()=>{const b=document.querySelector('button[aria-label=\"Показать экран\"]');return !!b&&!b.disabled})()", 12)
        S.check("13b. у B кнопка показа снова доступна", ok)

        # 15. перезагрузка C
        pins_before = await C.js("JSON.stringify(Object.entries(localStorage).filter(([k])=>k.startsWith('pg:stage:')))")
        await C.send("Page.reload"); await asyncio.sleep(3)
        await C.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        ok = await C.join(navigate=False) and await C.wait_for("document.querySelectorAll('.st-cell.type-camera').length>=3", 35)
        S.check("15. C после перезагрузки снова в комнате", ok)
        ok = await C.wait_for(f"document.querySelector('.st-view')?.dataset.main==={json.dumps(aScreen)}", 20)
        S.check("15. после перезагрузки C видит общую сцену (экран A) и сохранённые закрепления", ok and await C.js("JSON.stringify(Object.entries(localStorage).filter(([k])=>k.startsWith('pg:stage:')))") == pins_before, str(await view(C)))
        # 16. обрыв сети у C
        await C.send("Network.emulateNetworkConditions", offline=True, latency=0, downloadThroughput=0, uploadThroughput=0)
        await asyncio.sleep(6)
        await C.send("Network.emulateNetworkConditions", offline=False, latency=0, downloadThroughput=-1, uploadThroughput=-1)
        ok = await C.wait_for(f"!!document.querySelector('.st-cell[data-key=\"'+CSS.escape({json.dumps(aScreen)})+'\"] video') && document.querySelector('.st-view').dataset.main==={json.dumps(aScreen)}", 30)
        S.check("16. C после обрыва сети: сцена восстановилась (экран A крупно)", ok, str(await view(C)))
        # 17. заглушить для себя, чат, рука, стенограмма
        aCamC = await key_of(C, "camera", "Админ")
        S.check("17. C: «Заглушить для себя» A", await C.js(ctx_js(aCamC, "Заглушить для себя")) == "ok")
        st = await C.state()
        S.check("17. звук A у C заглушён только у C", any(a["muted"] for a in st["audio"]) and not any(a["muted"] for a in (await A.state())["audio"]), str(st["audio"]))
        bar0 = await A.js("Math.round(document.querySelector('.rbar').getBoundingClientRect().top)")
        S.check("17. C: «Поднять руку»", await C.click_btn("Поднять руку"))
        ok = await A.wait_for("!!document.querySelector('.hands-bar') && !!document.querySelector('.tile-hand')", 12)
        bar1 = await A.js("Math.round(document.querySelector('.rbar').getBoundingClientRect().top)")
        S.check("17. рука C видна у A; панель управления A не сдвинулась (очередь рук — поверх сцены)", ok and bar0 == bar1, f"{bar0}→{bar1}")
        await A.shot("17-hand")
        await C.click_btn("Опустить руку")
        await B.click_btn("Чат"); await asyncio.sleep(0.6)
        await B.js("document.querySelector('textarea[aria-label=\"Текст сообщения\"]').focus()")
        await B.send("Input.insertText", text=f"Сцена {S.RUN_ID}")
        for t in ("rawKeyDown", "keyUp"):
            await B.send("Input.dispatchKeyEvent", type=t, key="Enter", code="Enter", windowsVirtualKeyCode=13, nativeVirtualKeyCode=13)
        S.check("17. чат: сообщение B дошло до C", await C.wait_for(f"document.body.innerText.includes('Сцена {S.RUN_ID}')", 12))
        mid = await C.js("fetch('/api/v1/meetings?limit=1').then(r=>r.json()).then(m=>m[0].id)")
        ident = aCamC[len("camera:"):]
        dev_segment(mid, ident, f"Реплика стенограммы {S.RUN_ID}")
        S.check("17. стенограмма: реплика доходит до C во время показа экрана", await C.wait_for(f"document.body.innerText.includes('Реплика стенограммы {S.RUN_ID}')", 12))
        # очистить общую сцену
        S.check("A: «Макет» → «Очистить общую сцену»", await A.js("""(async()=>{document.querySelector('button[aria-label="Макет"]').click();await new Promise(r=>setTimeout(r,150));
          const b=[...document.querySelectorAll('.ctx-menu .ctx-item')].find(x=>x.textContent.trim()==='Очистить общую сцену');if(!b)return false;b.click();return true})()"""))
        S.check("после очистки у B снова своя раскладка", await B.wait_for("document.querySelector('.st-view')?.dataset.reason!=='spotlight' && !document.querySelector('.st-spotbar')", 10), str(await view(B)))
        # телефон: один главный элемент и лента
        await C.send("Emulation.setDeviceMetricsOverride", width=390, height=844, deviceScaleFactor=2, mobile=True)
        await asyncio.sleep(1.2)
        mv = await C.js("(()=>{const v=document.querySelector('.st-view');return {mobile:v.classList.contains('mobile'),big:document.querySelectorAll('.st-cell.big').length,scroll:document.documentElement.scrollWidth<=window.innerWidth+1}})()")
        S.check("телефон: крупно один элемент, лента, без горизонтальной прокрутки страницы", mv["mobile"] and mv["big"] == 1 and mv["scroll"], str(mv))
        await C.shot("mobile")
        await C.send("Emulation.setDeviceMetricsOverride", width=1366, height=768, deviceScaleFactor=1, mobile=False)

        perf["long_tasks"] = {c.label: await c.js("window.__long.length + ' шт., макс. ' + (Math.max(0,...window.__long)) + ' мс'") for c in (A, B, C)}
        errs = {c.label: [e for e in (await c.state())["errs"] if "favicon" not in e] for c in (A, B, C)}
        print("ошибки консоли:", json.dumps(errs, ensure_ascii=False)[:800])
        S.check("ошибок страницы нет ни у кого", not any(errs.values()), json.dumps(errs, ensure_ascii=False)[:300])
        print("ПРОИЗВОДИТЕЛЬНОСТЬ:", json.dumps(perf, ensure_ascii=False))
    finally:
        for c in (A, B, C): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
