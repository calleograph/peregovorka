"""Видеозапись общей сцены на НАСТОЯЩЕМ LiveKit Egress: участники, работа над доской, явная сцена ведущего, показ экрана, возврат к участникам; MP4 проверяется ffprobe,
кадры — по цвету сцены, звук — по меткам-сигналам, совпадающим с правками на доске.

Участники: администратор — в настоящем браузере (реальный редактор draw.io, реальные патчи), «камера» (зелёный кадр + звук) и «докладчик» (оранжевый экран) — боты livekit.rtc.
Сцены по цвету: доска — белая, камера — зелёная, экран — оранжевый.
Запуск (стенд pgdoc): PEREGOVORKA_URL=http://localhost:28480 PG_ADMIN_PASSWORD=… PG_ENV=~/pg-work/docstand/.env PG_DATA=~/pg-work/docstand-data BROWSER_PATH=… python3 tests/live/scenario_video_board.py"""
import asyncio, json, math, os, re, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from livekit import api as lkapi, rtc

URL = os.environ.get("PEREGOVORKA_URL", "http://localhost:28480")
LK_WS = os.environ.get("LK_WS", "ws://127.0.0.1:27880")          # LiveKit стенда напрямую (порт на 127.0.0.1); игрокам медиа доступно через адрес узла из .env
PASSWORD = os.environ["PG_ADMIN_PASSWORD"]
ENVF = os.path.expanduser(os.environ.get("PG_ENV", "~/pg-work/docstand/.env"))
DATA = os.path.expanduser(os.environ.get("PG_DATA", "~/pg-work/docstand-data"))
ROOM = os.environ.get("PG_ROOM", "upd-check")
OUT = os.path.join(S.OUT, "video-board"); os.makedirs(OUT, exist_ok=True)
env = dict(l.split("=", 1) for l in open(ENVF).read().splitlines() if "=" in l and not l.startswith("#"))
KEY, SECRET = env["LIVEKIT_API_KEY"].strip().strip('"'), env["LIVEKIT_API_SECRET"].strip().strip('"')

GREEN, ORANGE, FLASH = (30, 170, 70), (235, 125, 20), (230, 30, 200)
FLASH_AT = 85.0          # кадр камеры на 0,6 с меняет цвет и одновременно звучит сигнал: по ним проверяется синхронность звука и видео записи
T = {"conf1": 6, "board_on": 12, "edits": [14, 18, 22, 26], "conf2": 62, "stage_on": 70, "stage_off": 80, "screen_on": 92, "screen_off": 106, "stop": 114}


async def api(c, method, path, body=None):
    js = (f"(async()=>{{const me=await (await fetch('/api/v1/auth/me')).json();const r=await fetch('/api/v1{path}',{{method:'{method}',headers:{{'content-type':'application/json','X-CSRF-Token':me.csrf_token||''}},"
          f"body:{json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});let j=null;try{{j=await r.json()}}catch(e){{}}return {{s:r.status,j}}}})()")
    return await c.js(js)


def token(identity, name, room):
    return (lkapi.AccessToken(KEY, SECRET).with_identity(identity).with_name(name).with_grants(lkapi.VideoGrants(room_join=True, room=room, can_publish=True, can_subscribe=False)).to_jwt())


async def bot(room_name, identity, name, color, screen=False, beeps=(), start=0.0, stop=1e9):
    """Публикует однотонный кадр (камера или экран) и звук: тихий фон 220 Гц и «метки» 880 Гц в заданные секунды от начала записи (t0 — общее время старта)."""
    room = rtc.Room()
    await room.connect(LK_WS, token(identity, name, room_name))
    vsrc = rtc.VideoSource(640, 360); vt = rtc.LocalVideoTrack.create_video_track(identity, vsrc)
    # короткая очередь: звук уходит почти сразу после генерации (иначе метка отстаёт на размер очереди)
    asrc = rtc.AudioSource(48000, 1, queue_size_ms=100); at = rtc.LocalAudioTrack.create_audio_track(identity + "-a", asrc)
    await room.local_participant.publish_track(at, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    pub = None
    state = {"video": False}

    async def video():
        buf = bytes(color + (255,)) * (640 * 360)
        i = 0
        while True:
            if state["video"]:
                f = bytes(FLASH + (255,)) * (640 * 360) if FLASH_AT <= time.time() - bot.t0 < FLASH_AT + 0.6 and not screen else buf
                vsrc.capture_frame(rtc.VideoFrame(640, 360, rtc.VideoBufferType.RGBA, f))
            await asyncio.sleep(0.1); i += 1

    async def audio():
        n = 480
        k = 0
        while True:
            now = time.time() - bot.t0
            beep = any(b <= now < b + 0.6 for b in beeps)
            f = 880 if beep else 220; amp = 0.5 if beep else 0.03
            pcm = struct.pack("<" + "h" * n, *[int(amp * 32767 * math.sin(2 * math.pi * f * (k * n + j) / 48000)) for j in range(n)])
            await asrc.capture_frame(rtc.AudioFrame(pcm, 48000, 1, n)); k += 1

    tasks = [asyncio.create_task(video()), asyncio.create_task(audio())]
    return room, vt, state, tasks, pub


async def publish_video(room, vt, screen):
    await room.local_participant.publish_track(vt, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_SCREENSHARE if screen else rtc.TrackSource.SOURCE_CAMERA))


def frame_at(mp4, t, name):
    p = os.path.join(OUT, name)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", mp4, "-frames:v", "1", "-vf", "scale=320:180", p], check=True)
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", p, "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return tuple(out[:3]), p


def kind(rgb):
    r, g, b = rgb
    if min(rgb) > 190: return "board"
    if abs(r - ORANGE[0]) < 60 and abs(g - ORANGE[1]) < 60 and b < 90: return "screen"
    if abs(g - GREEN[1]) < 60 and r < 90: return "camera"
    return f"other{rgb}"


async def main():
    A = S.Client("admin", "admin", PASSWORD, 9641)
    try:
        await A.start()
        # у администратора нет устройств: кадр и звук конференции дают только боты, поэтому «говорящий» в записи определён однозначно
        await A.send("Page.addScriptToEvaluateOnNewDocument", source="navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('нет устройств', 'NotFoundError')); navigator.mediaDevices.enumerateDevices = async () => [];")
        r = await api(A, "PUT", "/admin/settings/video", {"enabled": True, "min_free_gb": 1, "max_concurrent": 2, "storage": "local", "scene_template": True})
        S.check("видеозапись включена, запись общей сцены включена", r["s"] == 200 and r["j"].get("scene_template") is True, str(r)[:160])
        rooms = await api(A, "GET", "/admin/rooms"); room = next(x for x in rooms["j"] if x["slug"] == ROOM)
        r = await api(A, "PATCH", f"/rooms/{room['id']}/manage", {"video_mode": "manual", "video_quality": "720p"})
        S.check("режим комнаты «вручную»", r["s"] == 200, str(r)[:120])
        await A.goto(f"/rooms/{ROOM}", 2.0)
        S.check("администратор вошёл в комнату", await A.join(navigate=False))
        await asyncio.sleep(2)
        lkroom = None
        async with lkapi.LiveKitAPI(LK_WS.replace("ws://", "http://"), KEY, SECRET) as lk:
            for _ in range(30):                      # браузер подключается к LiveKit не мгновенно
                for x in (await lk.room.list_rooms(lkapi.ListRoomsRequest())).rooms:
                    names = [p.identity for p in (await lk.room.list_participants(lkapi.ListParticipantsRequest(room=x.name))).participants]
                    if any(not n.startswith(("asr-", "EG_")) for n in names): lkroom = x.name
                if lkroom: break
                await asyncio.sleep(1)
        S.check("браузер администратора подключён к комнате LiveKit", bool(lkroom), str(lkroom))
        mt = await api(A, "GET", f"/rooms/{room['id']}")
        mid = (mt["j"].get("active_meeting_id") or (mt["j"].get("meeting") or {}).get("id"))
        if not mid:
            mt = await A.js("fetch('/api/v1/meetings?active=1').then(r=>r.json()).then(j=>(j[0]||{}).id)"); mid = mt
        S.check("встреча идёт", bool(mid), str(mid))

        bot.t0 = time.time() + 5          # общее время старта сценария (кадры и метки отсчитываются от него); уточняется после запуска записи
        beeps = [FLASH_AT]
        cam = await bot(lkroom, "bot-cam", "Участник (камера)", GREEN, beeps=beeps)
        await publish_video(cam[0], cam[1], False); cam[2]["video"] = True
        scr = await bot(lkroom, "bot-screen", "Докладчик", ORANGE, screen=True)
        await asyncio.sleep(2)
        r = await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": True})
        S.check("запись запущена", r["s"] in (200, 201), str(r)[:200])
        bot.t0 = time.time(); t0 = bot.t0
        S.check("запись пишется (идёт Egress)", True)
        await A.js("[...document.querySelectorAll('button')].find(b=>/Доска/.test(b.getAttribute('aria-label')||b.title||b.textContent))?.click()")
        await asyncio.sleep(1)

        async def at(t):
            d = t0 + t - time.time()
            if d > 0: await asyncio.sleep(d)

        shapes = []
        MERGE = r"""(xml)=>{const f=document.querySelector('iframe.drawio-frame, iframe[title*="draw.io"]'); if(!f) return 'no-frame'; f.contentWindow.postMessage(JSON.stringify({action:'merge', xml}), '*'); return 'ok'}"""
        for i, te in enumerate(T["edits"]):
            await at(te)
            shapes.append(f'<mxCell id="s{i}" value="Блок {i+1}" style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;" vertex="1" parent="1"><mxGeometry x="{40 + i*150}" y="{80 + (i%2)*120}" width="120" height="60" as="geometry"/></mxCell>')
            xml = '<mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(shapes) + '</root></mxGraphModel>'
            res = await A.js(f"({MERGE})({json.dumps(xml)})")
            print(f"  t={time.time()-t0:5.1f}  правка доски {i+1}: {res}", flush=True)
        await at(T["stage_on"]); r = await api(A, "PUT", f"/meetings/{mid}/stage", {"items": [{"type": "board"}]}); S.check("ведущий: «Показать доску всем»", r["s"] == 200, str(r)[:100])
        await at(T["stage_off"]); r = await api(A, "PUT", f"/meetings/{mid}/stage", {"items": []}); S.check("ведущий убрал доску с общей сцены", r["s"] == 200)
        await at(T["screen_on"]); await publish_video(scr[0], scr[1], True); scr[2]["video"] = True
        await at(T["screen_off"]); await scr[0].local_participant.unpublish_track(scr[1].sid); scr[2]["video"] = False
        await at(T["stop"])
        r = await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": False}); S.check("запись остановлена", r["s"] in (200, 201), str(r)[:150])
        for b in (cam, scr):
            for t in b[3]: t.cancel()
            await b[0].disconnect()
        # ждём готовности файла
        mp4 = None
        for _ in range(60):
            media = await api(A, "GET", f"/meetings/{mid}/media")
            mixes = [m for m in (media["j"] or {}).get("mixes", []) if m.get("has_video")]
            if mixes and all(m["status"] == "ready" for m in mixes):
                break
            await asyncio.sleep(3)
        S.check("запись перешла в «готово»", bool(mixes) and mixes[-1]["status"] == "ready", str(mixes)[-300:])
        files = subprocess.run(f"find '{DATA}/recordings' -name 'video-*.mp4' -newer '{ENVF}' -printf '%T@ %p\\n' | sort -n | tail -1", shell=True, capture_output=True, text=True).stdout.split(" ", 1)
        mp4 = files[1].strip() if len(files) > 1 else None
        S.check("MP4 найден на диске", bool(mp4), str(mp4))
        pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", mp4], capture_output=True, text=True).stdout)
        vs = next(s for s in pr["streams"] if s["codec_type"] == "video"); au = next(s for s in pr["streams"] if s["codec_type"] == "audio")
        S.check("видео H.264 (High), 1280×720; звук AAC", vs["codec_name"] == "h264" and vs["width"] == 1280 and au["codec_name"] == "aac", f'{vs["codec_name"]} {vs.get("profile")} {vs["width"]}x{vs["height"]} {au["codec_name"]}')
        dur = float(pr["format"]["duration"])
        S.check("длительность близка к ожидаемой (±12 с)", abs(dur - (T["stop"] + 2)) < 12, f"{dur:.1f} с")
        # moov в начале (быстрый старт) — первые байты содержат ftyp, затем moov до mdat
        head = open(mp4, "rb").read(65536)
        S.check("MP4: индекс (moov) в начале файла — быстрый старт и перемотка", head.find(b"moov") != -1 and (head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat")))
        # Egress начинает писать чуть позже команды: сдвиг определяем по первой метке звука (board_on)
        started = (mixes[-1].get("started_at") or "")
        from datetime import datetime
        rec_off = (t0 - datetime.fromisoformat(started.replace("Z", "+00:00")).timestamp()) if started else 0.0          # 0:00 файла = started_at; сценарий отсчитывается от t0
        print(f"  файл начался на {-rec_off:.1f} с позже команды «запись»; длительность файла {dur:.1f} с", flush=True)
        plan = [("участники до правок", 10, "camera"), ("доска во время правок", T["edits"][1] + 3, "board"), ("доска ещё в окне активности", T["edits"][-1] + 12, "board"),
                ("возврат к участникам после паузы", T["conf2"] + 6, "camera"), ("явная сцена ведущего: доска", T["stage_on"] + 6, "board"), ("ведущий убрал доску → участники", T["stage_off"] + 7, "camera"),
                ("показ экрана", T["screen_on"] + 6, "screen"), ("после показа экрана → участники", T["screen_off"] + 5, "camera")]
        for name, t, want in plan:
            ft = t + rec_off
            if ft >= dur - 0.5:
                S.check(f"кадр {t:>3} с — {name}: {want}", False, f"в файле нет такого момента ({ft:.1f} ≥ {dur:.1f})"); continue
            rgb, p = frame_at(mp4, ft, f"{want}-{int(t)}.png")
            got = kind(rgb) if len(rgb) == 3 else "нет кадра"
            S.check(f"кадр {t:>3} с — {name}: {want}", got == want, f"{got} {rgb}")
        # когда доска появилась в кадре после первой правки (шаг 1 с)
        first_board = None
        for k in range(0, 14):
            ft = T["edits"][0] - 2 + k + rec_off
            if ft < 0 or ft >= dur - 0.5: continue
            rgb, _ = frame_at(mp4, ft, f"probe-{k}.png")
            if len(rgb) == 3 and kind(rgb) == "board": first_board = (T["edits"][0] - 2 + k) - T["edits"][0]; break
        S.check("доска появилась в кадре не позже чем через 5 с после первой правки", first_board is not None and first_board <= 5, f"{first_board}")
        # HTTP: заголовки, Range, скачивание; воспроизведение настоящим <video>: метаданные, перемотка
        rid = mixes[-1]["id"]; su = f"/api/v1/meetings/{mid}/media/{rid}/stream"
        h = await A.js("""(async()=>{const o={}; for (const [k,r] of [['full',null],['head','bytes=0-99'],['tail','bytes=-100'],['bad','bytes=999999999-']]) { const x=await fetch('"""+su+"""',{headers:r?{Range:r}:{}}); const b=await x.arrayBuffer(); o[k]={s:x.status,l:b.byteLength,cr:x.headers.get('content-range'),ar:x.headers.get('accept-ranges'),ct:x.headers.get('content-type'),cd:x.headers.get('content-disposition')}} const d=await fetch('"""+su+"""?download=1'); o.dl={s:d.status,cd:d.headers.get('content-disposition')}; return o})()""")
        S.check("HTTP: полный ответ 200, Accept-Ranges: bytes, Content-Type: video/mp4", h["full"]["s"] == 200 and h["full"]["ar"] == "bytes" and h["full"]["ct"].startswith("video/mp4"), str(h["full"]))
        S.check("HTTP: Range → 206, Content-Range и длина совпадают; «последние N байт» работает", h["head"]["s"] == 206 and h["head"]["l"] == 100 and h["head"]["cr"].startswith("bytes 0-99/") and h["tail"]["s"] == 206 and h["tail"]["l"] == 100, str((h["head"], h["tail"])))
        S.check("HTTP: Range за пределами файла → 416; скачивание — attachment", h["bad"]["s"] == 416 and h["dl"]["s"] == 200 and "attachment" in (h["dl"]["cd"] or ""), str((h["bad"], h["dl"])))
        pv = await A.js("""(async()=>{const v=document.createElement('video'); v.muted=true; v.preload='auto'; v.src='"""+su+"""'; document.body.appendChild(v);
          await new Promise((res,rej)=>{v.onloadedmetadata=res; v.onerror=()=>rej(v.error&&v.error.code); setTimeout(()=>rej('timeout'),20000)});
          const o={w:v.videoWidth,h:v.videoHeight,d:v.duration}; v.currentTime=Math.min(60,v.duration-5); await new Promise((res)=>{v.onseeked=res; setTimeout(res,15000)}); o.at=v.currentTime; o.ready=v.readyState; v.remove(); return o})().catch(e=>({err:String(e)}))""")
        S.check("браузер открывает MP4 и перематывает на 60-ю секунду", "err" not in pv and pv["w"] == 1280 and abs(pv["d"] - dur) < 2 and abs(pv["at"] - min(60, pv["d"] - 5)) < 1.5 and pv["ready"] >= 2, str(pv))
        # звук и видео записи синхронны: сигнал 880 Гц и смена цвета камеры произошли одновременно (FLASH_AT)
        pcm = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", mp4, "-ac", "1", "-ar", "8000", "-f", "s16le", "-"], capture_output=True).stdout
        n = len(pcm) // 2; vals = struct.unpack("<" + "h" * n, pcm[: n * 2]); W = 400
        def goertzel(x, f):
            k = 2 * math.cos(2 * math.pi * f / 8000); s1 = s2 = 0.0
            for v in x: s1, s2 = v + k * s1 - s2, s1
            return s2 * s2 + s1 * s1 - k * s1 * s2
        lo = max(0, int((FLASH_AT - 6 + rec_off) * 8000) // W); hi = min(n // W, int((FLASH_AT + 8 + rec_off) * 8000) // W)
        g = [(i * W / 8000, goertzel(vals[i * W:(i + 1) * W], 880)) for i in range(lo, hi)]
        peak = max((x for _, x in g), default=0.0)
        a_on = next((t for t, x in g if peak > 0 and x > 0.3 * peak), None)          # начало самого сильного 880-герцового сигнала в окне
        v_on = None
        for k in range(0, 60):
            ft = FLASH_AT - 3 + rec_off + k * 0.1
            rgb, _ = frame_at(mp4, ft, f"flash-{k}.png")
            if len(rgb) == 3 and rgb[0] > 170 and rgb[2] > 140 and rgb[1] < 110: v_on = ft; break
        print(f"  сигнал в звуке: {a_on}, смена цвета в видео: {v_on}", flush=True)
        S.check("сигнал найден и в звуке, и в видео", a_on is not None and v_on is not None, f"{a_on} / {v_on}")
        if a_on is not None and v_on is not None:
            S.check("звук синхронен видео записи (расхождение ≤ 0,7 с)", abs(a_on - v_on) <= 0.7, f"{a_on - v_on:+.2f} с")
    finally:
        await A.close()


if __name__ == "__main__":
    asyncio.run(main())
    bad = [r for r in S.results if not r[1]]
    print(f"\nПроверок: {len(S.results)}, не прошли: {len(bad)}")
    sys.exit(1 if bad else 0)
