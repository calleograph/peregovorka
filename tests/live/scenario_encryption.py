"""Сценарий (см. README.md): шифрование хранимых чата и стенограмм. Администратор включает режим у комнаты, участники переписываются на настоящем LiveKit;
проверяется: при шифровании только хранилища значка в шапке нет (индикатор — только для E2EE), текст сообщения в базе — шифртекст, а в чате у собеседника — обычный текст.
Для проверки базы нужен доступ к её файлу (каталог `LIVE_DATA` стенда, SQLite). Не проверяется: PostgreSQL (в проде) — там то же поведение на уровне ORM."""
import asyncio, os, sqlite3, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

DB = os.path.join(os.environ.get("LIVE_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")), "dev.db")
SET = """(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); const h = {'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token};
  const rooms = await (await fetch('/api/v1/admin/rooms')).json(); const room = rooms.find(r => r.slug === 'sales');
  const r = await fetch('/api/v1/admin/rooms/' + room.id, {method: 'PATCH', headers: h, body: JSON.stringify({encrypt_storage: true, e2ee_mode: 'off'})});
  return r.status; })()"""


async def main():
    R = S.Client("root", "root", "root-pass", 9494)
    A = S.Client("alice", "alice", "alice-pass", 9495)
    B = S.Client("bob", "bob", "bob-pass", 9496)
    secret = f"Бюджет утверждён {uuid.uuid4().hex[:10]}"
    try:
        for c in (R, A, B):
            await c.start()
        S.check("администратор включил шифрование хранения у комнаты", (await R.js(SET)) == 200)
        for c in (A, B):
            S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        S.check("шифрование только хранилища: значка шифрования в шапке нет (индикатор относится к E2EE)", await A.js("!document.querySelector('.prot-badge')"))
        # сообщение в чате
        sent = await A.js(f"""(async () => {{ const mid = (await (await fetch('/api/v1/rooms')).json()).find(r => r.slug === 'sales').active_meeting.id;
          const me = await (await fetch('/api/v1/auth/me')).json();
          const r = await fetch('/api/v1/meetings/' + mid + '/chat', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': me.csrf_token}}, body: JSON.stringify({{text: {secret!r}}})}});
          return r.status; }})()""")
        S.check("сообщение отправлено", sent == 201, str(sent))
        await asyncio.sleep(1)
        con = sqlite3.connect(DB)
        rows = [r[0] for r in con.execute("select text from meeting_chat_messages order by id desc limit 5")]
        con.close()
        S.check("в базе сообщение — шифртекст, открытого текста нет", any(r.startswith("pgenc1:") for r in rows) and not any(secret in r for r in rows), str([r[:20] for r in rows]))
        got = await B.js(f"""(async () => {{ const mid = (await (await fetch('/api/v1/rooms')).json()).find(r => r.slug === 'sales').active_meeting.id;
          const j = await (await fetch('/api/v1/meetings/' + mid + '/chat')).json(); const items = Array.isArray(j) ? j : j.messages; return items.map(m => m.text); }})()""")
        S.check("у Боба в чате — обычный текст", secret in (got or []), str(got)[:120])
        S.check("ошибок страницы нет", not (await A.state())["errs"] and not (await B.state())["errs"], str((await A.state())["errs"][:2]))
    finally:
        for c in (R, A, B):
            await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
