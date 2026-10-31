"""Эфир и его запись по ссылке зрителя на НАСТОЯЩИХ LiveKit + Egress + nginx (тестовый экземпляр): автоматическая запись, ссылка эфира, прямой эфир без входа,
«Запись готовится», запись по той же ссылке, скачивание и Range, пароль, второй эфир со своей ссылкой и записью, закрытие доступа, истечение срока, удалённая запись;
затем завершение встречи администратором из «Комнаты и доступ» и забытая одиночная встреча (время страницы сдвигается, сервер проверяет состав по LiveKit).

Участники: администратор — браузер без устройств (руководитель эфира), «камера» — бот livekit.rtc (кадр и звук), зритель — второй браузер без cookies.
Запуск (стенд pgdoc): PEREGOVORKA_URL=http://localhost:28480 PG_ADMIN_PASSWORD=… PG_ENV=~/pg-work/docstand/.env BROWSER_PATH=… python3 tests/live/scenario_broadcast_replay.py"""
import asyncio, json, os, subprocess, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from livekit import api as lkapi, rtc

URL = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480")
LK_WS = os.environ.get("LK_WS", "ws://127.0.0.1:27880")
PASSWORD = os.environ.get("PG_ADMIN_PASSWORD", "")
ENVF = os.path.expanduser(os.environ.get("PG_ENV", "~/pg-work/docstand/.env"))
PROJECT = os.environ.get("PG_PROJECT", "pgdoc")
EFIR_S = float(os.environ.get("EFIR_S", "40"))
env = dict(l.split("=", 1) for l in open(ENVF).read().splitlines() if "=" in l and not l.startswith("#"))
KEY, SECRET = env["LIVEKIT_API_KEY"].strip().strip('"'), env["LIVEKIT_API_SECRET"].strip().strip('"')
check = S.check
NOW_SHIFT = "(()=>{const n=Date.now;window.__shift=0;Date.now=()=>n()+window.__shift})();"          # сдвиг часов страницы для проверки забытой встречи


async def api(c, method, path, body=None):
    js = (f"(async()=>{{const me=await (await fetch('/api/v1/auth/me')).json();const r=await fetch('/api/v1{path}',{{method:'{method}',headers:{{'content-type':'application/json','X-CSRF-Token':me.csrf_token||''}},"
          f"body:{json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});let j=null;try{{j=await r.json()}}catch(e){{}}return {{s:r.status,j}}}})()")
    return await c.js(js)


def sql(q):
    r = subprocess.run(["docker", "exec", f"{PROJECT}-postgres-1", "psql", "-U", env["POSTGRES_USER"].strip(), "-d", env["POSTGRES_DB"].strip(), "-tAc", q], capture_output=True, text=True)
    return r.stdout.strip()


def http(url, headers=None):
    req = urllib.request.Request(URL + url if url.startswith("/") else url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read(4096)
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read(4096)


class Anon(S.Client):
    async def start(self):
        await super().start()
        await self.send("Network.clearBrowserCookies")


async def camera_bot(room_name):
    tok = lkapi.AccessToken(KEY, SECRET).with_identity("bot-cam").with_name("Камера").with_grants(lkapi.VideoGrants(room_join=True, room=room_name, can_publish=True, can_subscribe=False)).to_jwt()
    room = rtc.Room()
    await room.connect(LK_WS, tok)
    vsrc = rtc.VideoSource(640, 360)
    await room.local_participant.publish_track(rtc.LocalVideoTrack.create_video_track("cam", vsrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_CAMERA))
    asrc = rtc.AudioSource(48000, 1)
    await room.local_participant.publish_track(rtc.LocalAudioTrack.create_audio_track("mic", asrc), rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))

    async def frames():
        i = 0
        while True:
            c = (30 + i % 200, 170, 70, 255)
            vsrc.capture_frame(rtc.VideoFrame(640, 360, rtc.VideoBufferType.RGBA, bytes(c) * (640 * 360)))
            await asrc.capture_frame(rtc.AudioFrame(b"\0\0" * 4800, 48000, 1, 4800))
            await asyncio.sleep(0.1); i += 1
    return room, asyncio.create_task(frames())


async def lk_room_of(identity_prefix="u-"):
    async with lkapi.LiveKitAPI(LK_WS.replace("ws://", "http://"), KEY, SECRET) as lk:
        for _ in range(30):
            for x in (await lk.room.list_rooms(lkapi.ListRoomsRequest())).rooms:
                ids = [p.identity for p in (await lk.room.list_participants(lkapi.ListParticipantsRequest(room=x.name))).participants]
                if any(i.startswith(identity_prefix) for i in ids):
                    return x.name
            await asyncio.sleep(1)


async def viewer_state(V, token):
    return await V.js(f"fetch('/api/v1/live/{token}').then(r=>r.json())")


async def wait_replay(V, token, want, timeout=300):
    t = time.time()
    while time.time() - t < timeout:
        st = await viewer_state(V, token)
        if st.get("replay_state") == want:
            return st, int(time.time() - t)
        await asyncio.sleep(3)
    return await viewer_state(V, token), int(time.time() - t)


async def broadcast(A, rid, mid, title, opts):
    r = await api(A, "POST", f"/meetings/{mid}/live", {"enabled": True, "options": {"title": title, **opts}})
    check(f"«{title}»: эфир начат с параметрами этого эфира", r["s"] == 200, str(r)[:200])
    st = None
    for _ in range(40):
        st = (await api(A, "GET", f"/rooms/{rid}/live"))["j"]
        if st["state"]["live"] and st["state"]["hls_ready"]:
            break
        await asyncio.sleep(2)
    cur = next((b for b in st.get("broadcasts", []) if b["status"] in ("starting", "live")), None)
    check(f"«{title}»: эфир идёт, плейлист готов, у эфира своя ссылка и своя запись", bool(st["state"]["live"] and cur and cur["view_path"] and cur.get("recording")), json.dumps(st.get("state"), default=str)[:200])
    cur["recording_id"] = (cur.get("recording") or {}).get("id")
    return cur


async def main():
    A = S.Client("admin", "admin", PASSWORD, 9651)
    V = Anon("viewer", "admin", PASSWORD, 9652)
    bots = []
    try:
        await A.start(); await V.start()
        await A.send("Page.addScriptToEvaluateOnNewDocument", source="navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('нет устройств', 'NotFoundError')); navigator.mediaDevices.enumerateDevices = async () => [];" + NOW_SHIFT)
        r = await api(A, "PUT", "/admin/settings/live", {"enabled": True, "min_free_gb": 1, "max_concurrent": 1, "after_end_keep_min": 0})
        check("трансляции включены", r["s"] == 200, str(r)[:160])
        rooms = (await api(A, "GET", "/admin/rooms"))["j"]
        room = next((x for x in rooms if x["slug"] == "efir-054"), None)
        if room is None:
            r = await api(A, "POST", "/admin/rooms", {"slug": "efir-054", "name": "Эфир 0.54", "room_type": "broadcast", "acl": [], "moderators": [], "max_participants": 20})
            check("создана трансляционная комната", r["s"] == 201, str(r)[:200]); room = r["j"]
        rid = room["id"]
        r = await api(A, "PATCH", f"/rooms/{rid}/live", {"live_replay": False, "live_download": False, "live_link_days": 5})
        check("в комнате по умолчанию запись зрителям не показывается", r["s"] == 200 and r["j"]["replay"] is False and r["j"]["link_days"] == 5, str(r)[:200])

        await A.goto("/rooms/efir-054?as=speaker", 2.5)
        check("администратор вошёл выступающим", await A.join(navigate=False))
        lkroom = await lk_room_of()
        check("комната LiveKit найдена", bool(lkroom), str(lkroom))
        bots.append(await camera_bot(lkroom))
        mid = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>x.room_name==='Эфир 0.54'&&!x.ended_at)||{}).id})()")
        check("встреча идёт", bool(mid), str(mid))

        # ---------------------------------------------------------------- эфир 1: запись открыта, скачивание разрешено
        b1 = await broadcast(A, rid, mid, "Эфир первый", {"replay": True, "download": True, "link_days": 5})
        t1 = b1["view_path"].rsplit("/", 1)[-1]
        await V.goto(b1["view_path"], 3.0)
        played = await V.wait_for("(()=>{const v=document.querySelector('.live-stage video');return !!v&&v.videoWidth>0&&v.currentTime>2})()", 90)
        check("зритель без входа по ссылке эфира: прямой эфир идёт", played, (await V.js("document.body.innerText"))[:200])
        check("на странице зрителя название эфира", "Эфир первый" in await V.js("document.body.innerText"))
        check("страница зрителя: без cookies, без элементов конференции", not await V.js("document.cookie.includes('session')") and not await V.js("!!document.querySelector('.tile,.chat-panel')"))
        await V.shot("bc-live")
        await asyncio.sleep(EFIR_S)
        r = await api(A, "POST", f"/meetings/{mid}/live", {"enabled": False})
        check("эфир 1 остановлен", r["s"] == 200, str(r)[:120])
        st = await viewer_state(V, t1)
        check("сразу после эфира: «Запись готовится» (или уже готова)", st.get("replay_state") in ("preparing", "ready"), json.dumps(st, ensure_ascii=False)[:200])
        await V.goto(b1["view_path"], 2.0)
        if st.get("replay_state") == "preparing":
            check("страница зрителя показывает «Запись готовится»", await V.wait_for("document.body.innerText.includes('Запись готовится')", 20), (await V.js("document.body.innerText"))[:200])
            await V.shot("bc-preparing")
        st, waited = await wait_replay(V, t1, "ready")
        check(f"запись эфира 1 готова (MP4 закрыт Egress) через {waited} с", st.get("replay_state") == "ready", json.dumps(st, ensure_ascii=False)[:200])
        await V.goto(b1["view_path"], 2.5)
        ok = await V.wait_for("(()=>{const v=document.querySelector('video[aria-label=\"Запись эфира\"]');return !!v&&v.videoWidth>0&&v.duration>10})()", 60)
        if not ok:
            await V.js("[...document.querySelectorAll('button')].find(b=>/Смотреть запись/.test(b.textContent))?.click()")
            ok = await V.wait_for("(()=>{const v=document.querySelector('video[aria-label=\"Запись эфира\"]');return !!v&&v.videoWidth>0&&v.duration>10})()", 60)
        dur1 = await V.js("document.querySelector('video[aria-label=\"Запись эфира\"]')?.duration||0")
        check(f"та же ссылка после эфира открывает запись (длительность {dur1:.0f} с)", ok and dur1 >= EFIR_S - 5, (await V.js("document.body.innerText"))[:200])
        await V.js("(()=>{const v=document.querySelector('video[aria-label=\"Запись эфира\"]');v.muted=true;v.play();v.currentTime=Math.max(1,v.duration-5)})()")
        check("перемотка записи (Range)", await V.wait_for("(()=>{const v=document.querySelector('video[aria-label=\"Запись эфира\"]');return v.currentTime>=v.duration-6&&!v.seeking})()", 30))
        await V.shot("bc-replay")
        rep = await V.js(f"fetch('/api/v1/live/{t1}/replay',{{method:'POST',headers:{{'content-type':'application/json'}},body:'{{}}'}}).then(r=>r.json())")
        dl = rep.get("download_url")
        check("кнопка «Скачать запись» есть (скачивание разрешено)", bool(dl) and await V.js("[...document.querySelectorAll('a,button')].some(b=>/Скачать запись/.test(b.textContent))"), str(rep)[:200])
        s, h, body = http(dl, {"Range": "bytes=0-1023"})
        check("скачивание: 206, MP4, attachment", s == 206 and "attachment" in h.get("content-disposition", "") and body[4:8] == b"ftyp", f"{s} {h.get('content-disposition')} {body[:12]!r}")
        s, h, _ = http(rep["url"], {"Range": "bytes=1000-1999"})
        check("просмотр: Range 206 без attachment", s == 206 and "attachment" not in h.get("content-disposition", ""), f"{s} {h.get('content-disposition')}")

        # ---------------------------------------------------------------- эфир 2: своя ссылка, запись без скачивания
        # пароль зрителя: действует на эфир, который идёт с ним, и на запись этого эфира (у прошедших эфиров — их прежние правила)
        r = await api(A, "PUT", f"/rooms/{rid}/live/password", {"password": "zritel-2026"})
        check("задан пароль зрителя перед эфиром 2", r["s"] == 200, str(r)[:120])
        b2 = await broadcast(A, rid, mid, "Эфир второй", {"replay": True, "download": False, "link_days": 2})
        t2 = b2["view_path"].rsplit("/", 1)[-1]
        check("у второго эфира новая ссылка и новая запись", t2 != t1 and b2["recording_id"] != b1["recording_id"])
        await V.goto(b2["view_path"], 2.0)
        check("эфир 2 с паролем: зритель видит форму пароля, а не видео", await V.wait_for("!!document.querySelector('.live-pw')", 20) and not await V.js("!!document.querySelector('.live-stage video')"))
        await asyncio.sleep(EFIR_S / 2)
        await api(A, "POST", f"/meetings/{mid}/live", {"enabled": False})
        st, waited = await wait_replay(V, t2, "ready")
        s_noauth = await V.js(f"fetch('/api/v1/live/{t2}/replay',{{method:'POST',headers:{{'content-type':'application/json'}},body:'{{}}'}}).then(r=>r.status)")
        check("запись эфира 2 без пароля не выдаётся (403)", s_noauth == 403, f"{s_noauth} {json.dumps(st, ensure_ascii=False)[:200]}")
        await V.goto(b2["view_path"], 2.0)
        await V.wait_for("[...document.querySelectorAll('button')].some(b=>/Смотреть запись/.test(b.textContent))", 20)
        await V.js("[...document.querySelectorAll('button')].find(b=>/Смотреть запись/.test(b.textContent))?.click()")
        check("после эфира 2: «Смотреть запись» спрашивает пароль", await V.wait_for("!!document.querySelector('.live-pw')", 20), (await V.js("document.body.innerText"))[:200])
        await V.js("(()=>{const i=document.querySelector('.live-pw input');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'zritel-2026');i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.live-pw button').click()})()")
        check("с паролем открывается запись эфира 2", await V.wait_for("(()=>{const v=document.querySelector('video[aria-label=\"Запись эфира\"]');return !!v&&v.duration>5})()", 40), (await V.js("document.body.innerText"))[:200])
        await V.shot("bc-replay-password")
        await api(A, "PUT", f"/rooms/{rid}/live/password", {"password": None})
        st1 = await viewer_state(V, t1)
        check("пароль, заданный после эфира 1, не меняет правила его записи (параметры зафиксированы у эфира)", st1.get("replay_state") == "ready" and not st1.get("password_required"), json.dumps(st1, ensure_ascii=False)[:160])
        check(f"запись эфира 2 готова через {waited} с", st.get("replay_state") == "ready", json.dumps(st, ensure_ascii=False)[:200])
        rep2 = await V.js(f"fetch('/api/v1/live/{t2}/replay',{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{password:'zritel-2026'}})}}).then(r=>r.json())")
        check("эфир 2: скачивание не выдаётся, прямой адрес скачивания отклонён", not rep2.get("download_url") and http(rep2["url"].replace("d=0", "d=1"))[0] in (403, 404), str(rep2)[:200])
        st1 = await viewer_state(V, t1)
        check("ссылка эфира 1 по-прежнему ведёт на свою запись", st1.get("replay_state") == "ready" and st1.get("title") == "Эфир первый", json.dumps(st1, ensure_ascii=False)[:200])
        lst = (await api(A, "GET", f"/rooms/{rid}/live"))["j"]["broadcasts"]
        e1 = next(b for b in lst if b["id"] == b1["id"]); e2 = next(b for b in lst if b["id"] == b2["id"])
        check("список эфиров: срок ссылки от фактического окончания (5 и 2 дня)", bool(e1["access_until"]) and bool(e2["access_until"]) and e1["link_days"] == 5 and e2["link_days"] == 2, json.dumps([e1, e2], default=str)[:300])

        # ---------------------------------------------------------------- закрытие доступа, срок, удалённая запись
        old_url = rep["url"]
        r = await api(A, "PATCH", f"/rooms/{rid}/live/broadcasts/{b1['id']}", {"revoke": True})
        check("руководитель закрыл доступ к эфиру 1", r["s"] == 200, str(r)[:160])
        await V.goto(b1["view_path"], 2.0)
        check("зритель эфира 1: «Организатор закрыл доступ к записи»", await V.wait_for("document.body.innerText.includes('закрыл доступ')", 20), (await V.js("document.body.innerText"))[:200])
        check("ранее выданный адрес файла эфира 1 больше не отдаёт видео", http(old_url, {"Range": "bytes=0-99"})[0] in (403, 404, 410))
        await V.shot("bc-revoked")
        r = await api(A, "PATCH", f"/rooms/{rid}/live/broadcasts/{b1['id']}", {"revoke": False})
        check("«Открыть снова» возвращает доступ", r["s"] == 200 and (await viewer_state(V, t1)).get("replay_state") == "ready")
        sql(f"UPDATE broadcasts SET access_until = now() - interval '1 minute' WHERE id = '{b1['id']}'")
        await V.goto(b1["view_path"], 2.0)
        check("срок ссылки истёк: «Срок просмотра записи по этой ссылке завершён»", await V.wait_for("document.body.innerText.includes('Срок просмотра')", 20), (await V.js("document.body.innerText"))[:200])
        check("при этом файл записи эфира 1 не удалён (виден в «Истории»)", sql(f"SELECT status FROM recordings WHERE id = '{b1['recording_id']}'") == "ready")
        await V.shot("bc-expired")
        r = await api(A, "DELETE", f"/admin/recordings/{b2['recording_id']}")
        if r["s"] not in (200, 204):
            r = await api(A, "DELETE", f"/meetings/{mid}/recordings/{b2['recording_id']}")
        await V.goto(b2["view_path"], 2.0)
        check("запись эфира 2 удалена администратором: «Запись удалена»", await V.wait_for("document.body.innerText.includes('Запись удалена')", 20), f"{r['s']} " + (await V.js("document.body.innerText"))[:160])

        # ---------------------------------------------------------------- «Комнаты и доступ»: идущая встреча и завершение для всех
        row = next(x for x in (await api(A, "GET", "/admin/rooms"))["j"] if x["id"] == rid)
        check(f"«Комнаты и доступ»: идёт встреча, людей {row['active_people']}, подключений {row['active_connections']}", row["active_meeting_id"] == mid and row["active_people"] >= 1 and (row["active_connections"] or 0) >= 2, str(row)[:200])
        await A.goto("/admin?tab=rooms", 2.5)
        await A.js("[...document.querySelectorAll('button')].find(b=>/^Завершить встречу$/.test(b.textContent.trim()))?.click()")
        dlg = await A.wait_for("[...document.querySelectorAll('[role=dialog]')].some(d=>/Завершить встречу в «Эфир 0.54» для всех/.test(d.innerText)&&/участник/.test(d.innerText))", 15)
        check("подтверждение: название комнаты, число участников, предупреждение о записях", dlg, (await A.js("document.querySelector('[role=dialog]')?.innerText||''"))[:300])
        await A.shot("admin-end-confirm")
        await A.js("[...document.querySelectorAll('[role=dialog] button')].find(b=>/Завершить встречу для всех/.test(b.textContent))?.click()")
        ended = False
        for _ in range(20):
            if sql(f"SELECT end_reason FROM meetings WHERE id = '{mid}'") == "admin":
                ended = True; break
            await asyncio.sleep(1)
        check("встреча завершена администратором (end_reason=admin), комната осталась", ended and sql(f"SELECT is_enabled FROM rooms WHERE id = '{rid}'") == "t")
        aud = (await api(A, "GET", "/admin/audit?limit=20"))["j"]
        items = aud["items"] if isinstance(aud, dict) else aud
        check("в журнале аудита — meeting.force_end", any(a["action"] == "meeting.force_end" for a in items))
        for b in bots:
            b[1].cancel(); await b[0].disconnect()
        bots.clear()

        # ---------------------------------------------------------------- забытая одиночная встреча (обычная комната, администратор один)
        r = await api(A, "PUT", "/admin/settings/general", {"idle_end_mode": "standard"})
        await A.goto("/rooms/upd-check", 2.0)
        check("администратор один в обычной комнате", await A.join(navigate=False))
        mid2 = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>!x.ended_at)||{}).id})()")
        await A.js("window.__shift = 16*60*1000")
        check("один 16 минут без действий: «Похоже, встреча больше не используется…»", await A.wait_for("document.body.innerText.includes('Похоже, встреча больше не используется')", 15))
        await A.shot("idle-warning")
        await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.includes('Продолжить встречу'))?.click()")
        check("«Продолжить встречу» убирает предупреждение", await A.wait_for("!document.body.innerText.includes('Похоже, встреча больше не используется')", 10))
        await A.js("window.__shift += 11*60*1000")
        check("снова 10 минут без действий — предупреждение", await A.wait_for("document.body.innerText.includes('Похоже, встреча больше не используется')", 15))
        await A.js("window.__shift += 3*60*1000")
        done = False
        for _ in range(30):
            if sql(f"SELECT end_reason FROM meetings WHERE id = '{mid2}'") == "idle":
                done = True; break
            await asyncio.sleep(1)
        check("без реакции 2 минуты — встреча завершена сервером (end_reason=idle)", done, sql(f"SELECT coalesce(end_reason,'-') FROM meetings WHERE id = '{mid2}'"))
        check("страница перешла к материалам встречи", await A.wait_for(f"location.pathname.includes('/history/{mid2}')", 20), await A.js("location.pathname"))
    finally:
        for b in bots:
            b[1].cancel()
            try:
                await b[0].disconnect()
            except Exception:
                pass
        for c in (A, V):
            try:
                await c.close()
            except Exception:
                pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


if __name__ == "__main__":
    asyncio.run(main())
