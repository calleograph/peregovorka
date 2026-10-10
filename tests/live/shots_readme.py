"""Снимки для README (см. docs/VERSIONING.md: не больше двух, без сообщений об ошибках, стенд с вымышленными данными).
Комната: трое участников, две демонстрации экрана, общая сцена ведущего; администрирование: список переговорок.
На стенде нет сервиса распознавания речи: реплики подаются через /__dev/segment, а пометки «Транскрибация запускается…» на снимке скрываются — в рабочей установке с работающим ASR их нет.
Запуск: python tests/live/shots_readme.py (переменная SHOTS_DIR — куда сохранить; по умолчанию docs/img)."""
import asyncio, base64, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

S.ROOM = "/rooms/arch"
OUT = os.environ.get("SHOTS_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "img"))
S.INIT += """
navigator.mediaDevices.getDisplayMedia = async function () {
  const c = document.createElement('canvas'); c.width = 1600; c.height = 900; const g = c.getContext('2d'); const who = (window.__who || 'Доклад');
  const draw = () => { g.fillStyle = '#f4f6fa'; g.fillRect(0, 0, 1600, 900); g.fillStyle = '#1f3b70'; g.fillRect(0, 0, 1600, 110);
    g.fillStyle = '#fff'; g.font = '600 52px sans-serif'; g.fillText(window.__title || 'Квартальный план', 60, 72);
    const bars = window.__bars || [320, 480, 410, 560, 620]; bars.forEach((h, i) => { g.fillStyle = ['#3b6fd8', '#4f86e8', '#6a9cf0', '#2f5fb3', '#1f4c99'][i % 5]; g.fillRect(140 + i * 260, 820 - h, 170, h); });
    g.fillStyle = '#334'; g.font = '32px sans-serif'; ['Янв', 'Фев', 'Мар', 'Апр', 'Май'].forEach((m, i) => g.fillText(m, 185 + i * 260, 865)); };
  setInterval(draw, 200); draw(); return c.captureStream(5);
};
"""

def save(name, data):
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, name), "wb").write(base64.b64decode(data))

async def shot(c, name, w, h):
    await c.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=1, mobile=False)
    await asyncio.sleep(1.5)
    r = await c.send("Page.captureScreenshot", format="png")
    save(name, r["data"])

async def main():
    A = S.Client("A", "root", "root-pass", 9611)
    B = S.Client("B", "alice", "alice-pass", 9612)
    C = S.Client("C", "bob", "bob-pass", 9613)
    try:
        for c in (A, B, C):
            await c.start(); assert await c.join(), c.label
        await A.js("window.__title='План внедрения'; window.__bars=[260,380,470,540,600]")
        await B.js("window.__title='Бюджет проекта'; window.__bars=[520,430,360,300,240]")
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.st-cell.type-camera').length>=3", 40) for c in (A, B, C)])
        await A.click_btn("Показать экран"); await asyncio.sleep(1); await B.click_btn("Показать экран")
        await C.wait_for("document.querySelectorAll('.st-cell.type-screen video').length===2", 30)
        key = await A.js("[...document.querySelectorAll('.st-cell.type-screen')].map(c=>c.dataset.key).find(k=>k)")
        mine = await A.js("(()=>{const me=[...document.querySelectorAll('.st-cell.type-camera')].find(c=>(c.querySelector('.tile-name')?.textContent||'').includes('(вы)'));return me?'screen:'+me.dataset.key.slice(7):null})()")
        await A.js(f"""(async()=>{{const c=document.querySelector('.st-cell[data-key="'+CSS.escape({json.dumps(mine)})+'"]');const t=c.firstElementChild;const r=t.getBoundingClientRect();
          t.dispatchEvent(new MouseEvent('contextmenu',{{bubbles:true,cancelable:true,clientX:r.left+20,clientY:r.top+20}}));await new Promise(r=>setTimeout(r,200));
          [...document.querySelectorAll('.ctx-menu .ctx-item')].find(x=>x.textContent.trim()==='Показать всем')?.click()}})()""")
        await C.wait_for("document.querySelector('.st-view')?.dataset.reason==='spotlight'", 10)
        mid = await C.js("fetch('/api/v1/meetings?limit=1').then(r=>r.json()).then(m=>m[0].id)")
        ident = mine[len("screen:"):]
        for i, t in enumerate(["Коллеги, начнём с плана внедрения.", "Первый этап — пилот в двух отделах.", "Бюджет на второй квартал уже согласован?"]):
            req = urllib.request.Request("http://127.0.0.1:8000/__dev/segment", method="POST", data=json.dumps({"meeting_id": mid, "identity": ident, "text": t, "offset": i * 4}).encode(),
                                         headers={"content-type": "application/json"})
            urllib.request.urlopen(req).read()
        await C.wait_for("document.body.innerText.includes('Бюджет на второй квартал')", 15)
        await asyncio.sleep(1)
        await C.js("[...document.querySelectorAll('.transcript .alert, .transcript [role=status]')].filter(a=>/распознавания|запускается/.test(a.textContent)).forEach(a=>a.remove())")
        await C.js("[...document.querySelectorAll('.badge.warn')].filter(b=>/Транскрибация запускается/.test(b.textContent)).forEach(b=>b.remove()); document.querySelectorAll('.st-toasts .alert').forEach(a=>a.remove())")
        await C.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        await shot(C, "room.png", 1600, 900)
        await A.goto("/admin?tab=rooms", 3.5)
        await A.wait_for("document.querySelectorAll('table tbody tr').length>=5", 20)
        await A.js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='Понятно').forEach(b=>b.click())")
        await shot(A, "admin.png", 1600, 900)
        print("снимки:", os.path.abspath(OUT))
    finally:
        for c in (A, B, C): await c.close()

asyncio.run(main())
