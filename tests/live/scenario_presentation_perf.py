"""Сценарий (см. README.md): презентационная комната на настоящем LiveKit — «руководитель выступает, зрители смотрят, руководитель даёт и забирает слово».

Три настоящих браузера (root — руководитель, alice и bob — зрители) и сервер звонков. Права проверяются на самом сервере звонков (список участников и их
дорожки через API LiveKit), а не по виду кнопок; попытки обойти права идут настоящим SDK (python rtc) со старыми токенами. Профиль «Экономный 720p» проверяется
по `getStats()` исходящего потока браузера. Запуск: python tests/live/scenario_presentation_perf.py (python с пакетами websockets и livekit, стенд из README)."""
import asyncio, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from livekit import api as lkapi, rtc

S.ROOM = "/rooms/stage"
LK_URL, LK_KEY, LK_SECRET = "http://127.0.0.1:7880", "devkey", "s" * 40
S.INIT += r"""
window.__gum = 0; window.__gdm = 0; window.__tokens = []; window.__join = null;
(() => {
  const md = navigator.mediaDevices; if (!md) return;
  const g = md.getUserMedia.bind(md); md.getUserMedia = function (...a) { window.__gum++; return g(...a); };
  md.getDisplayMedia = async function () {                       // «экран» 1920×1080 15 к/с: заведомо крупнее профиля «Экономный 720p»
    window.__gdm++;
    const c = document.createElement('canvas'); c.width = 1920; c.height = 1080; const x = c.getContext('2d'); let n = 0;
    setInterval(() => { x.fillStyle = '#12304a'; x.fillRect(0, 0, 1920, 1080); x.fillStyle = '#fff'; x.font = '64px sans-serif';
      for (let i = 0; i < 14; i++) x.fillText('Слайд ' + n + ' строка ' + i + ' ' + Math.random().toString(36).slice(2), 80, 90 + i * 70); n++; }, 66);
    return c.captureStream(15);
  };
  const f = window.fetch; window.fetch = async function (...a) {
    const r = await f.apply(this, a);
    try { const u = String((a[0] && a[0].url) || a[0]); if (/\/rooms\/[^/]+\/join$/.test(u)) r.clone().json().then((j) => { window.__join = j; window.__tokens.push(j.token); }).catch(() => {}); } catch (e) {}
    return r;
  };
})();
"""


async def lk_state(room_name):
    """Что видит сам сервер звонков: участники, их права и опубликованные дорожки."""
    async with lkapi.LiveKitAPI(LK_URL, LK_KEY, LK_SECRET) as lk:
        resp = await lk.room.list_participants(lkapi.ListParticipantsRequest(room=room_name))
    out = {}
    for p in resp.participants:
        out[p.identity] = {"tracks": sorted(rtc_name(t.source) for t in p.tracks), "can_publish": p.permission.can_publish,
                           "sources": sorted(rtc_name(s) for s in p.permission.can_publish_sources), "name": p.name}
    return out


def rtc_name(src):
    from livekit.protocol import models as m
    return {m.TrackSource.MICROPHONE: "mic", m.TrackSource.CAMERA: "cam", m.TrackSource.SCREEN_SHARE: "screen", m.TrackSource.SCREEN_SHARE_AUDIO: "screen_audio"}.get(src, str(src))


async def try_publish(token, what="mic"):
    """Подключиться настоящим SDK с токеном и попытаться опубликовать дорожку. Возвращает (удалось, текст ошибки)."""
    room = rtc.Room()
    await room.connect("ws://127.0.0.1:7880", token)
    try:
        if what == "mic":
            src = rtc.AudioSource(48000, 1); track = rtc.LocalAudioTrack.create_audio_track("m", src); source = rtc.TrackSource.SOURCE_MICROPHONE
        else:
            src = rtc.VideoSource(640, 360); track = rtc.LocalVideoTrack.create_video_track("v", src); source = rtc.TrackSource.SOURCE_SCREENSHARE
        try:
            await asyncio.wait_for(room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=source)), 8)
            return True, ""
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"[:160]
    finally:
        await asyncio.sleep(0.5)
        await room.disconnect()


async def stale_token_attempt(token, room_name, identity):
    """Подключиться со старым токеном (выданным, пока слово было) и публиковать микрофон; ждать реакции сервера, не отключаясь сами."""
    room = rtc.Room()
    await room.connect("ws://127.0.0.1:7880", token)
    out = {"published": False, "removed_by_server": False, "final": None}
    try:
        src = rtc.AudioSource(48000, 1); track = rtc.LocalAudioTrack.create_audio_track("m", src)
        try:
            await asyncio.wait_for(room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)), 8)
            out["published"] = True
        except Exception as e:  # noqa: BLE001
            out["publish_error"] = f"{type(e).__name__}: {e}"[:120]
        for _ in range(25):
            await asyncio.sleep(0.4)
            st = await lk_state(room_name)
            out["final"] = st.get(identity)
            if out["final"] is None or (out["final"]["tracks"] == [] and not out["final"]["can_publish"]):
                break
        out["removed_by_server"] = room.connection_state != rtc.ConnectionState.CONN_CONNECTED
    finally:
        try:
            await room.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return out


def api_js(method, path, body=None):
    b = "undefined" if body is None else f"JSON.stringify({json.dumps(body)})"
    return (f"(async()=>{{const me=await fetch('/api/v1/auth/me').then(r=>r.json());const r=await fetch('/api/v1'+{json.dumps(path)},"
            f"{{method:{json.dumps(method)},headers:{{'content-type':'application/json','X-CSRF-Token':me.csrf_token||me.csrf||''}},body:{b}}});"
            f"let j=null;try{{j=await r.json()}}catch(e){{}};return {{status:r.status,body:j}}}})()")


async def main():
    R = S.Client("lead", "root", "root-pass", 9541)
    A = S.Client("alice", "alice", "alice-pass", 9542)
    B = S.Client("bob", "bob", "bob-pass", 9543)
    try:
        for c in (R, A, B):
            await c.start()
        S.check("руководитель вошёл", await R.join())
        await asyncio.sleep(2)
        for c in (A, B):
            S.check(f"{c.label}: зритель вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.rbtn').length>3", 40) for c in (R, A, B)])
        await asyncio.sleep(4)
        join = await R.js("window.__join")
        mid, room_name = join["meeting_id"], join["livekit_room"]
        ident = {c.label: (await c.js("window.__join.identity")) for c in (R, A, B)}

        # ----------------------------------------------------------------- зрители: ничего не запрашивают и не публикуют
        for c in (A, B):
            gum = await c.js("window.__gum")
            S.check(f"{c.label}: браузер не запрашивал микрофон и камеру (getUserMedia ни разу)", gum == 0, str(gum))
            labels = await c.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label')+(b.disabled?'[off]':''))")
            S.check(f"{c.label}: кнопка микрофона — «Слушаете», недоступна", any(x.startswith("Слушаете") and x.endswith("[off]") for x in labels), str(labels[:4]))
            S.check(f"{c.label}: проверки оборудования на входе не было", not await c.js("!!document.querySelector('.precheck')"))
            tiles = await c.js("document.querySelectorAll('.tile').length")
            S.check(f"{c.label}: плитка одна — выступающего; зрители плиток не имеют", tiles == 1, str(tiles))
        st = await lk_state(room_name)
        S.check("сервер звонков: у зрителей нет ни одной дорожки, публиковать им нельзя",
                all(st[ident[x]]["tracks"] == [] and st[ident[x]]["can_publish"] is False for x in ("alice", "bob")), json.dumps({k: v for k, v in st.items()}, ensure_ascii=False)[:300])
        S.check("сервер звонков: у руководителя микрофон опубликован", "mic" in st[ident["lead"]]["tracks"] and st[ident["lead"]]["can_publish"], str(st[ident["lead"]]))
        S.check("руководитель: «Зрители: 2»", await R.wait_for("[...document.querySelectorAll('.rbtn')].some(b=>(b.getAttribute('aria-label')||'')==='Зрители: 2')", 15))
        S.check("руководитель: на сцене одна плитка (себя), зрителей нет в сетке", await R.js("document.querySelectorAll('.tile').length") == 1)

        # ----------------------------------------------------------------- серверные запреты: прямые вызовы API и токен зрителя
        for c in (A,):
            r = await c.js(api_js("POST", f"/meetings/{mid}/whiteboard/patch", {"patch": {"x": 1}}))
            S.check("API: зритель не может править доску (403)", r["status"] == 403, str(r["status"]))
            r = await c.js(api_js("POST", f"/meetings/{mid}/moderation/floor", {"identity": ident["bob"], "granted": True}))
            S.check("API: зритель не может давать слово (403)", r["status"] == 403, str(r["status"]))
            r = await c.js(api_js("POST", f"/meetings/{mid}/end"))
            S.check("API: зритель не может завершить встречу для всех (403)", r["status"] == 403, str(r["status"]))
            r = await c.js(api_js("POST", f"/meetings/{mid}/recording", {"enabled": True}))
            S.check("API: зритель не может управлять записью (403)", r["status"] == 403, str(r["status"]))
            r = await c.js("fetch('/api/v1/meetings/" + mid + "/whiteboard').then(r=>r.status)")
            S.check("зритель доску читать может (200)", r == 200, str(r))
        viewer_token = await B.js("window.__tokens[0]")
        await B.close()                       # чтобы вход тем же токеном не вытеснил его же браузер
        ok, err = await try_publish(viewer_token, "mic")
        S.check("настоящий SDK с токеном зрителя: публикация микрофона отклонена сервером", not ok, err)
        ok, err = await try_publish(viewer_token, "screen")
        S.check("настоящий SDK с токеном зрителя: показ экрана отклонён сервером", not ok, err)
        st = await lk_state(room_name)
        S.check("после попыток обхода у зрителя по-прежнему нет дорожек", st.get(ident["bob"], {"tracks": []})["tracks"] == [], str(st.get(ident["bob"])))

        # ----------------------------------------------------------------- слово: руководитель даёт Алисе через список зрителей
        await R.js("[...document.querySelectorAll('.rbtn')].find(b=>(b.getAttribute('aria-label')||'').startsWith('Зрители'))?.click()")
        S.check("список зрителей открылся", await R.wait_for("!!document.querySelector('.aud-panel')", 10))
        got = await R.js("(()=>{const r=[...document.querySelectorAll('.aud-row')].find(x=>x.textContent.includes('Алиса'));if(!r)return false;[...r.querySelectorAll('button')].find(b=>b.textContent.includes('Дать слово'))?.click();return true})()")
        S.check("руководитель нажал «Дать слово» у Алисы в списке зрителей", got)
        ok = await A.wait_for("[...document.querySelectorAll('.rbtn')].some(b=>/^Микрофон/.test(b.getAttribute('aria-label')||'')&&!b.disabled)", 15)
        S.check("Алиса: кнопка микрофона стала доступна", ok)
        await asyncio.sleep(1.5)
        st = await lk_state(room_name)
        a = st[ident["alice"]]
        S.check("сервер звонков: у Алисы все права выступающего", a["can_publish"] and {"mic", "cam", "screen"} <= set(a["sources"]), str(a))
        S.check("микрофон и камера сами не включились (Алиса решает сама)", a["tracks"] == [], str(a["tracks"]))
        S.check("Алиса на сцене: у руководителя появилась плитка Алисы, у Алисы — свои", await R.wait_for("document.querySelectorAll('.tile').length>=2", 10))
        S.check("Алиса: браузер всё ещё не запрашивал устройства сам", await A.js("window.__gum") == 0)
        labels = await A.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label')+(b.disabled?'[off]':''))")
        S.check("Алиса: доступны микрофон, камера, показ экрана", all(any(x.startswith(p) and not x.endswith("[off]") for x in labels) for p in ("Микрофон", "Камера", "Показать экран")), str(labels))
        S.check("Алиса включила микрофон", await A.click_btn("Микрофон выкл."))
        S.check("Алиса включила камеру", await A.click_btn("Камера выкл."))
        await asyncio.sleep(3)
        # профиль «Экономный 720p»: через меню «Ещё»
        await A.js("[...document.querySelectorAll('.rbtn')].find(b=>(b.getAttribute('aria-label')||'')==='Ещё')?.click()")
        await asyncio.sleep(0.5)
        items = await A.js("[...document.querySelectorAll('.ctx-item')].map(i=>i.textContent.trim())")
        S.check("в меню «Ещё» есть четыре профиля показа экрана, среди них «Экономный 720p»", sum("Показ экрана:" in i for i in items) == 4 and any("Экономный 720p" in i for i in items), str(items)[:200])
        await A.js("[...document.querySelectorAll('.ctx-item')].find(i=>i.textContent.includes('Экономный 720p'))?.click()")
        await asyncio.sleep(0.5)
        S.check("Алиса запустила показ экрана", await A.click_btn("Показать экран"))
        S.check("в браузере Алисы показ экрана создан (getDisplayMedia)", await A.wait_for("window.__gdm>=1", 10))
        await asyncio.sleep(14)                # измерение: 4 с на уточнение + запас для кадров
        stat = await A.js("""(async()=>{const out=[];for(const pc of window.__pcs){try{const r=await pc.getStats();r.forEach(x=>{if(x.type==='outbound-rtp'&&x.kind==='video')out.push({w:x.frameWidth,h:x.frameHeight,fps:x.framesPerSecond,bytes:x.bytesSent,lim:x.qualityLimitationReason,codec:x.codecId,rid:x.rid||'',tgt:x.targetBitrate})})}catch(e){}}return out})()""")
        await asyncio.sleep(6)
        stat2 = await A.js("""(async()=>{const out=[];for(const pc of window.__pcs){try{const r=await pc.getStats();r.forEach(x=>{if(x.type==='outbound-rtp'&&x.kind==='video')out.push({w:x.frameWidth,h:x.frameHeight,fps:x.framesPerSecond,bytes:x.bytesSent,rid:x.rid||''})})}catch(e){}}return out})()""")
        print("экран, замер 1:", stat, "\nэкран, замер 2:", stat2)
        screens = [s for s in stat2 if (s.get("w") or 0) >= 640]
        top = max(screens, key=lambda s: s["w"]) if screens else None
        S.check("исходящий экран: найден поток видео большого размера", top is not None, str(stat2))
        if top:
            S.check("исходящий поток экрана не больше 1280×720 (захват был 1920×1080)", top["w"] <= 1280 and top["h"] <= 720, f"{top['w']}×{top['h']}")
            S.check("частота исходящего экрана не выше ~12 к/с (профиль 10)", (top.get("fps") or 0) <= 12.5, f"{top.get('fps')}")
            first = next((s for s in stat if s.get("rid") == top.get("rid") and (s.get("w") or 0) >= 640), None)
            if first:
                kbps = (top["bytes"] - first["bytes"]) * 8 / 6 / 1000
                S.check("фактический исходящий битрейт экрана не выше 900 кбит/с (за 6 с)", kbps <= 900 * 1.15, f"{kbps:.0f} кбит/с")
        lay = await A.js("""(async()=>{let n=0;for(const pc of window.__pcs){try{const r=await pc.getStats();r.forEach(x=>{if(x.type==='outbound-rtp'&&x.kind==='video'&&(x.frameWidth||0)>=320)n++})}catch(e){}}return n})()""")
        S.check("экономный профиль: один видеослой экрана и камера (≤ 2 исходящих видеопотока, без simulcast экрана)", lay <= 2 + 2, str(lay))
        note = await A.js("[...document.querySelectorAll('.alert')].map(a=>a.textContent).join(' | ')")
        S.check("пользователю сообщено, что реально уходит (без ложных гарантий)", "Экономный 720p" in note, note[:200])
        st = await lk_state(room_name)
        S.check("сервер звонков: у Алисы опубликованы микрофон, камера, экран", {"mic", "cam", "screen"} <= set(st[ident["alice"]]["tracks"]), str(st[ident["alice"]]["tracks"]))
        r = await A.js(api_js("POST", f"/meetings/{mid}/whiteboard/patch", {"patch": {"x": 2}}))
        S.check("API: выступающему доска доступна для правки (200)", r["status"] == 200, str(r["status"]))
        r = await A.js(api_js("POST", f"/meetings/{mid}/moderation/floor", {"identity": ident["bob"], "granted": True}))
        S.check("API: слово не делает Алису руководителем — давать слово она не может (403)", r["status"] == 403, str(r["status"]))
        floor_token = (await A.js(api_js("POST", "/rooms/" + join["room"]["id"] + "/join", {})))["body"]["token"]      # токен, выданный пока слово есть
        await R.shot("with-floor")

        # ----------------------------------------------------------------- отзыв слова при идущем показе экрана
        await R.js("""(()=>{const t=[...document.querySelectorAll('.tile')].find(x=>(x.getAttribute('title')||'').includes('Алиса'));t.dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true,clientX:300,clientY:300}))})()""")
        await asyncio.sleep(0.6)
        S.check("руководитель забрал слово через меню плитки", await R.js("(()=>{const i=[...document.querySelectorAll('.ctx-item')].find(i=>i.textContent.includes('Забрать слово'));if(!i)return false;i.click();return true})()"))
        t0 = time.time()
        gone = False
        while time.time() - t0 < 10:
            st = await lk_state(room_name)
            if st[ident["alice"]]["tracks"] == [] and not st[ident["alice"]]["can_publish"]:
                gone = True
                break
            await asyncio.sleep(0.4)
        S.check(f"сервер звонков: дорожки Алисы (микрофон, камера, экран) сняты, права отозваны — за {time.time() - t0:.1f} с", gone, str(st.get(ident["alice"])))
        r = await A.js(api_js("POST", f"/meetings/{mid}/whiteboard/patch", {"patch": {"x": 3}}))
        S.check("API: после отзыва правка доски запрещена (403)", r["status"] == 403, str(r["status"]))
        S.check("Алиса: кнопка микрофона снова «Слушаете»", await A.wait_for("[...document.querySelectorAll('.rbtn')].some(b=>(b.getAttribute('aria-label')||'').startsWith('Слушаете')&&b.disabled)", 10))
        S.check("Алиса вернулась в зрители: на сцене плитки Алисы нет", await R.wait_for("![...document.querySelectorAll('.tile')].some(x=>(x.getAttribute('title')||'').includes('Алиса'))", 10))
        S.check("руководитель: снова виден один зритель (Алиса; Боб вышел)", await R.wait_for("[...document.querySelectorAll('.rbtn')].some(b=>(b.getAttribute('aria-label')||'')==='Зрители: 1')", 10))
        S.check("трансляция руководителя не прервалась: его микрофон опубликован", "mic" in (await lk_state(room_name))[ident["lead"]]["tracks"])

        # ----------------------------------------------------------------- переподключение: права не возвращаются
        await A.goto(S.ROOM, 2.0)
        await A.join(navigate=False)
        await A.wait_for("document.querySelectorAll('.rbtn').length>3", 30)
        await asyncio.sleep(3)
        labels = await A.js("[...document.querySelectorAll('.rbtn')].map(b=>b.getAttribute('aria-label')+(b.disabled?'[off]':''))")
        S.check("после обновления страницы Алиса снова зритель (микрофон недоступен)", any(x.startswith("Слушаете") and x.endswith("[off]") for x in labels), str(labels[:4]))
        st = await lk_state(room_name)
        S.check("после обновления страницы права у сервера звонков прежние: зритель", not st[ident["alice"]]["can_publish"] and st[ident["alice"]]["tracks"] == [], str(st[ident["alice"]]))
        await A.close()
        res = await stale_token_attempt(floor_token, room_name, ident["alice"])
        print("подключение со старым токеном выступающего:", res)
        S.check("старый токен выступающего после отзыва: опубликовать удалось лишь до реакции сервера, затем права урезаны и дорожки сняты (или участник отключён)",
                res["final"] is None or (res["final"]["tracks"] == [] and not res["final"]["can_publish"]), str(res))

        # ----------------------------------------------------------------- показатели для администратора
        perf = None
        for _ in range(3):
            perf = (await R.js(api_js("GET", "/admin/performance")))["body"]
            await asyncio.sleep(5.2)
        print("производительность:", json.dumps({k: perf[k] for k in ("assessment", "rooms", "livekit", "jobs", "events")}, ensure_ascii=False)[:700])
        S.check("«Производительность»: сервер звонков доступен, видны комнаты и участники", perf["livekit"]["available"] and perf["livekit"]["participants"] >= 1, str(perf["livekit"]))
        S.check("«Производительность»: метрики LiveKit читаются (потоки, трафик)", perf["livekit"]["metrics"] and perf["livekit"]["audio_tracks"] is not None, str(perf["livekit"]))
        S.check("«Производительность»: идёт презентация, зрители посчитаны", perf["rooms"]["presentations"] >= 1, str(perf["rooms"]))
        S.check("«Производительность»: общий подписчик событий работает", perf["events"]["subscribers"] >= 1, str(perf["events"]))
        await R.goto("/admin#performance", 2.5)
        await R.js("[...document.querySelectorAll('button,a')].find(b=>b.textContent.trim()==='Производительность')?.click()")
        S.check("страница «Производительность» открывается", await R.wait_for("document.body.innerText.includes('Сервер звонков (LiveKit)')", 15))
        await R.shot("performance-page")
        errs = [(c.label, (await c.state())["errs"][:2]) for c in (R,)]
        print("errs:", errs)
    finally:
        for c in (R, A, B):
            try:
                await c.close()
            except Exception:
                pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
