"""Сценарий (см. README.md): забытая встреча и причины отключения в «Журнале событий».

Комната: «Автозавершение при бездействии» = 1 минута (поле в настройках комнаты), микрофон при входе выключен (поддельный микрофон браузера «пищит»,
и это считалось бы речью). Боб входит и выходит кнопкой «Выйти» — в журнале «вышел сам». Алиса остаётся одна и ничего не делает — через минуту
предупреждение, ещё через 2 минуты встреча завершается; в журнале — автозавершение и сколько минут без действий. Пока в комнате двое, предупреждения нет.
Нужен LiveKit с webhook на стенд (`LIVE_LK_PORT` у devserver_live.py).
"""
import asyncio, os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
SLUG = "legal"
S.ROOM = f"/rooms/{SLUG}"
WARN = "!!document.querySelector('.idle-warn')"


async def api(c, method, path, body=None):
    return await c.js(f"""(async () => {{ const me = await (await fetch('/api/v1/auth/me')).json();
      const r = await fetch('/api/v1{path}', {{method: '{method}', headers: {{'content-type': 'application/json', 'X-CSRF-Token': me.csrf_token}}, body: {json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});
      return {{status: r.status, body: await r.json().catch(() => null)}}; }})()""")


async def main():
    R = S.Client("root", "root", "root-pass", 9631)
    A = S.Client("alice", "alice", "alice-pass", 9632)
    B = S.Client("bob", "bob", "bob-pass", 9633)
    try:
        await R.start()
        rooms = (await api(R, "GET", "/admin/rooms"))["body"]
        room = next(r for r in rooms if r["slug"] == SLUG)
        S.check("по умолчанию — 30 минут", room.get("idle_end_minutes") == 30, str(room.get("idle_end_minutes")))
        await R.goto(f"/admin?tab=rooms"); await asyncio.sleep(1)
        r = await api(R, "PATCH", f"/admin/rooms/{room['id']}", {"idle_end_minutes": 1, "mute_on_join": True})
        S.check("время меняется в настройках комнаты", r["status"] == 200 and r["body"]["idle_end_minutes"] == 1, str(r["status"]))
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        await asyncio.sleep(70)
        S.check("вдвоём — молча слушать можно: предупреждения нет", not await A.js(WARN) and not await B.js(WARN))
        S.check("Боб: «Выйти»", await B.click_btn("Выйти") or await B.js("(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^Выйти/.test(x.textContent.trim()));if(!b)return false;b.click();return true})()"))
        t0 = time.time()
        S.check("Алиса одна и ничего не делает: через минуту — предупреждение", await A.wait_for(WARN, 100), f"{time.time() - t0:.0f} с")
        await A.shot("idle-warning")
        S.check("текст предупреждения", "завершится через" in (await A.js("document.querySelector('.idle-warn')?.textContent || ''")))
        S.check("через 2 минуты встреча завершается сама", await A.wait_for("location.pathname.startsWith('/history/')", 150), f"{time.time() - t0:.0f} с")
        await asyncio.sleep(25)                       # уход из LiveKit и запись журнала
        j = (await api(R, "GET", "/admin/journal?limit=200"))["body"]
        items = j.get("items", j)
        left = [e for e in items if e["event"] == "participant_left" and e.get("room") == room["name"]]
        print("\n".join(f"  {e['at'][11:19]} {e['message']} | {json.dumps(e.get('data'), ensure_ascii=False)[:160]}" for e in left[:6]))
        S.check("журнал: Боб вышел сам", any("Борис" in (e["message"] or "") and e["data"]["reason"] == "left" for e in left))
        idle = [e for e in left if e["data"]["reason"] == "idle"]
        S.check("журнал: Алиса — автозавершение по бездействию, с минутами", bool(idle) and "без действий" in idle[0]["message"] and "Алиса" in idle[0]["message"],
                idle[0]["message"] if idle else "")
        S.check("журнал: событие завершения встречи", any(e["event"] == "idle_end" and e.get("room") == room["name"] for e in items))
        await api(R, "PATCH", f"/admin/rooms/{room['id']}", {"idle_end_minutes": 30, "mute_on_join": False})
        print("errs:", (await A.state() or {}).get("errs", [])[:2])
    finally:
        for c in (R, A, B): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
