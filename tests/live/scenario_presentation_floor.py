"""Сценарий (см. README.md): презентационная комната: слушатель, «дать слово», завершить встречу для всех может только руководитель."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
S.ROOM = "/rooms/town"
async def main():
    R = S.Client("root", "root", "root-pass", 9497)
    B = S.Client("bob", "bob", "bob-pass", 9498)
    try:
        for c in (R, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (R, B)])
        await asyncio.sleep(3)
        lab = await B.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label')+(b.disabled?'[off]':''))")
        print("кнопки Боба:", lab)
        S.check("Боб (слушатель): микрофон недоступен («Слушаете»)", any("Слушаете" in x for x in lab))
        s = await B.js(S.STATS); S.check("Боб ничего не публикует (исходящих аудиопакетов нет)", s["aout"] < 5, str(s))
        # руководитель даёт слово через меню плитки
        await R.js("""(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Борис'));t.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:300,clientY:300,button:2}))})()""")
        await asyncio.sleep(0.5)
        items = await R.js("[...document.querySelectorAll('.ctx-item')].map(i=>i.textContent.trim())")
        print("меню руководителя:", items)
        S.check("в меню плитки есть «Дать слово»", any("Дать слово" in i for i in items))
        await R.js("[...document.querySelectorAll('.ctx-item')].find(i=>i.textContent.includes('Дать слово'))?.click()")
        ok = await B.wait_for("[...document.querySelectorAll('.rbtn')].some(b=>/^Микрофон/.test(b.getAttribute('aria-label')||'')&&!b.disabled)", 15)
        S.check("Боб получил слово: микрофон стал доступен", ok)
        await asyncio.sleep(1)
        lab2 = await B.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label'))")
        print("кнопки Боба после слова:", lab2)
        labR = await R.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label'))")
        S.check("у руководителя кнопка «Завершить для всех» есть", "Завершить для всех" in labR, str(labR))
        S.check("у слушателя (после слова — но не руководителя) кнопки «Завершить для всех» нет", "Завершить для всех" not in lab2, str(lab2))
        code = await B.js("fetch('/api/v1/auth/me').then(r=>r.json()).then(m=>fetch('/api/v1/meetings?limit=1').then(r=>r.json()).then(l=>fetch('/api/v1/meetings/'+l[0].id+'/end',{method:'POST',headers:{'X-CSRF-Token':m.csrf_token}}).then(r=>r.status)))")
        S.check("API: слушатель не может завершить встречу для всех (403)", code == 403, str(code))
        print("errs:", (await R.state())["errs"][:2], (await B.state())["errs"][:2])
    finally:
        for c in (R, B): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
