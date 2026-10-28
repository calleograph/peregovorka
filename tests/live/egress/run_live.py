"""Сквозная проверка трансляций на НАСТОЯЩИХ LiveKit + Egress + nginx (контур pgtest-*, см. README.md): два выступающих с фейковыми камерами, запуск эфира из меню комнаты,
зритель без входа в системе в отдельном браузере, пароль зрителя, отзыв ссылок, проверка HLS по заголовкам и подписям, остановка, уборка диска, падение Egress.
Запуск: python run_live.py [минут_эфира=5]   (PEREGOVORKA_URL=http://127.0.0.1:5186, nginx — http://127.0.0.1:18088)"""
import asyncio, base64, json, os, re, subprocess, sys, threading, time, urllib.error, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("PEREGOVORKA_URL", "http://127.0.0.1:5186")
import smoke_room as S

NGINX = os.environ.get("NGINX_URL", "http://127.0.0.1:18088")
LIVE_DIR = os.path.expanduser(os.environ.get("LIVE_DIR", "~/pg-work/live-test/live"))
EG = os.environ.get("EG_CONTAINER", "pgtest-egress-live")
MINUTES = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
check = S.check


async def api(c, method, path, body=None):
    js = (f"(async()=>{{const me=await (await fetch('/api/v1/auth/me')).json();const r=await fetch('/api/v1{path}',{{method:'{method}',headers:{{'content-type':'application/json',"
          f"'X-CSRF-Token':me.csrf_token||''}},body:{json.dumps(json.dumps(body)) if body is not None else 'undefined'}}});let j=null;try{{j=await r.json()}}catch(e){{}}return {{s:r.status,j}}}})()")
    return await c.js(js)


class Anon(S.Client):
    """Зритель: браузер без входа в систему (куки пусты) — как внешний человек со ссылкой."""
    async def start(self):
        try:
            await super().start()
        except AssertionError:
            raise
        await self.send("Network.clearBrowserCookies")
        await self.send("Page.addScriptToEvaluateOnNewDocument", source="window.__net=[];const _f=window.fetch;window.__ws=[];const _W=window.WebSocket;window.WebSocket=function(u,p){window.__ws.push(String(u));return p===undefined?new _W(u):new _W(u,p)};window.WebSocket.prototype=_W.prototype;Object.assign(window.WebSocket,_W);window.fetch=function(u,o){const k=String(u);return _f.apply(this,arguments).then(r=>{if(k.includes('/live/'))window.__net.push(k.split('/api/v1')[1]+' '+r.status);return r},e=>{window.__net.push(k+' ERR '+e);throw e})};")


def http(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def secret():
    return re.search(r'"([0-9a-f]+)"', open(os.path.join(LIVE_DIR, "nginx", "secret.conf")).read()).group(1)


def sign(acc, exp):
    import hashlib
    return base64.urlsafe_b64encode(hashlib.md5(f"{exp}{acc} {secret()}".encode()).digest()).decode().rstrip("=")


def stats_sampler(stop, out):
    while not stop.is_set():
        r = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}|{{.NetIO}}", EG], capture_output=True, text=True)
        if r.stdout.strip():
            cpu, mem, net = r.stdout.strip().split("|")
            out.append((time.time(), float(cpu.strip("%")), mem.split("/")[0].strip(), net))


async def menu_live(c, label):
    """Меню «Ещё» → пункт «Трансляция…» → кнопка в окне."""
    await c.js("document.querySelector('button[aria-label=\"Ещё\"]')?.click()")
    await asyncio.sleep(0.6)
    ok = await c.js("(()=>{const it=[...document.querySelectorAll('[role=menuitem],[role=menu] button')].find(b=>/Трансляция/.test(b.textContent));if(!it)return false;it.click();return true})()")
    if not ok:
        return False
    await asyncio.sleep(0.8)
    return await c.js(f"(()=>{{const d=document.querySelector('[role=dialog], .modal');const b=d&&[...d.querySelectorAll('button')].find(x=>/{label}/.test(x.textContent)&&!x.disabled);if(!b)return false;b.click();return true}})()")


async def main():
    A, B, R = S.Client("alice", "alice", "alice-pass", 9741), S.Client("bob", "bob", "bob-pass", 9742), S.Client("root", "root", "root-pass", 9743)
    V1, V2 = Anon("viewer1", "alice", "alice-pass", 9744), Anon("viewer2", "alice", "alice-pass", 9745)
    clients = [A, B, R, V1, V2]
    stop, samples = threading.Event(), []
    try:
        for c in clients:
            await c.start()
        # ---------------------------------------------------------------- настройка и создание комнаты
        r = await api(R, "PUT", "/admin/settings/live", {"enabled": True, "min_free_gb": 1, "max_concurrent": 1, "segment_keep_min": 3, "after_end_keep_min": 0, "view_link_ttl_s": 300})
        check("администратор включил трансляции", r["s"] == 200, str(r)[:200])
        rooms = (await api(R, "GET", "/admin/rooms"))["j"]
        sales = next(x for x in rooms if x["slug"] == "sales")
        r = await api(R, "POST", "/admin/rooms", {"slug": "webinar", "name": "Вебинар", "room_type": "broadcast", "acl": sales["acl"], "moderators": sales["moderators"], "max_participants": 20})
        check("создана трансляционная комната (тип «Трансляционная»)", r["s"] == 201 and r["j"]["room_type"] == "broadcast", str(r)[:200])
        room_id = r["j"]["id"]
        lr = (await api(A, "GET", f"/rooms/{room_id}/live"))["j"]
        token = lr["view_path"].split("/")[-1]
        check("ссылка зрителя и ссылка на поток выпущены сразу", bool(lr["view_path"]) and bool(lr["stream_path"]) and lr["enabled"], str(lr)[:200])
        # ---------------------------------------------------------------- выбор роли и вход выступающих
        await A.goto("/rooms/webinar", 2.5)
        check("выбор «Выступающий / Зритель»: две карточки", await A.wait_for("document.querySelectorAll('.bc-card').length===2", 15) and "Выступающий" in await A.js("document.body.innerText") and "Зритель" in await A.js("document.body.innerText"))
        await A.shot("chooser")
        await A.js("document.querySelector('.bc-card').click()")
        check("Алиса вошла выступающей", await A.join(navigate=False))
        await B.goto("/rooms/webinar?as=speaker", 2.0)
        check("ссылка выступающих (?as=speaker) пропускает выбор роли и ведёт ко входу", await B.wait_for("!document.querySelector('.bc-card') && !!document.querySelector('.pj-actions .btn.primary')", 15))
        check("Борис вошёл выступающим", await B.join(navigate=False))
        await asyncio.sleep(2)
        for c in (A, B):
            await c.wait_for("!!document.querySelector('button[aria-label=\"Камера выкл.\"]')", 20)
            await c.click_btn("Камера выкл.")
        await asyncio.sleep(3)
        # ---------------------------------------------------------------- запуск эфира из комнаты
        before_dirs = set(os.listdir(os.path.join(LIVE_DIR, "b")))
        th = threading.Thread(target=stats_sampler, args=(stop, samples), daemon=True)
        th.start()
        t0 = time.time()
        check("«Ещё → Трансляция… → Начать эфир»", await menu_live(A, "Начать эфир"))
        check("у выступающих метка «ПРЯМОЙ ЭФИР» (Алиса)", await A.wait_for("[...document.querySelectorAll('.rec-badge.live')].some(b=>b.textContent.includes('ПРЯМОЙ ЭФИР'))", 25))
        check("у выступающих метка «ПРЯМОЙ ЭФИР» (Борис) и уведомление о внешней трансляции", await B.wait_for("[...document.querySelectorAll('.rec-badge.live')].some(b=>b.textContent.includes('ПРЯМОЙ ЭФИР'))", 25))
        await A.js("document.querySelector('.modal-close, [aria-label=\"Закрыть\"]')?.click()")
        await A.shot("live-room")
        mid = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>x.room_name==='Вебинар'&&!x.ended_at)||{}).id})()")
        # ---------------------------------------------------------------- метка на главной
        await B.goto("/", 2.0)
        check("на главной у комнаты метка «ЭФИР»", await B.wait_for("[...document.querySelectorAll('.pill.onair')].some(p=>p.textContent.includes('ЭФИР'))", 20))
        await B.shot("rooms-onair")
        await B.goto("/rooms/webinar?as=speaker", 1.5)
        # ---------------------------------------------------------------- зритель без входа
        await V1.goto(f"/live/{token}", 2.5)
        played = await V1.wait_for("(()=>{const v=document.querySelector('.live-stage video');return !!v&&v.videoWidth>0&&v.currentTime>2})()", 90)
        check("зритель без входа: эфир идёт (videoWidth>0, время растёт)", played, (await V1.js("document.body.innerText"))[:200])
        print("   сеть зрителя (первые 12):", await V1.js("JSON.stringify((window.__net||[]).slice(0,12))"), flush=True)
        print("   запросов /access за время ожидания:", await V1.js("performance.getEntriesByType('resource').filter(e=>e.name.includes('/access')).length"), "| /live/<токен>:", await V1.js("performance.getEntriesByType('resource').filter(e=>/\\/api\\/v1\\/live\\/[^/]+$/.test(e.name)).length"), flush=True)
        eng = await V1.js("document.querySelector('.live-stage video')?.dataset.engine")
        info = await V1.js("(()=>{const v=document.querySelector('.live-stage video');return v?{w:v.videoWidth,h:v.videoHeight,t:v.currentTime,paused:v.paused,engine:v.dataset.engine}:null})()")
        print("   плеер:", info, flush=True)
        check("страница зрителя без элементов конференции", not await V1.js("!!document.querySelector('.tile,[aria-label=\"Микрофон\"],[aria-label=\"Камера\"],.chat-panel')"))
        check("метка «ЭФИР» в плеере", "ЭФИР" in await V1.js("document.querySelector('.live-bar')?.innerText||''") or "Прямой эфир" in await V1.js("document.body.innerText"))
        await V1.shot("viewer-live")
        wsu = await V1.js("JSON.stringify((window.__ws||[]).filter(u=>!/token=|vite/.test(u)))")
        npc = await V1.js("window.__pcs.length")
        check("зритель не подключается к LiveKit (нет WebRTC-соединений и сокетов LiveKit)", npc == 0 and not re.search(r"/rtc|7890|/livekit/", wsu), f"pc={npc} ws={wsu}")
        # ---------------------------------------------------------------- HLS: заголовки, подпись, срок, чужие имена
        acc = (await V1.js(f"fetch('/api/v1/live/{token}/access',{{method:'POST',headers:{{'content-type':'application/json'}},body:'{{}}'}}).then(r=>r.json())"))
        assert "path" in acc, acc
        path = acc["path"]
        s, h, body = http(NGINX + path)
        check("плейлист: 200, application/vnd.apple.mpegurl, короткий кэш, CORS", s == 200 and "mpegurl" in h.get("Content-Type", "") and "max-age=1" in h.get("Cache-Control", "") and h.get("Access-Control-Allow-Origin") == "*", f"{s} {h}")
        seg = re.findall(r"seg_\d+\.ts", body.decode())[-1]
        s2, h2, b2 = http(NGINX + path.rsplit("/", 1)[0] + "/" + seg)
        check("сегмент: 200, video/mp2t, длинный кэш, содержимое не пусто", s2 == 200 and "mp2t" in h2.get("Content-Type", "") and "immutable" in h2.get("Cache-Control", "") and len(b2) > 1000, f"{s2} {h2.get('Content-Type')} {len(b2)}")
        _, _, a, exp, sig, _ = path.split("/")
        base = f"{NGINX}/hls/{a}/{exp}/"
        check("чужая подпись → 403", http(base + "A" * 22 + "/index.m3u8")[0] == 403)
        check("подпись от другого срока → 403", http(f"{NGINX}/hls/{a}/{int(exp) + 600}/{sig}/index.m3u8")[0] == 403)
        past = int(time.time()) - 100
        check("истёкший срок при верной подписи → 410", http(f"{NGINX}/hls/{a}/{past}/{sign(a, past)}/index.m3u8")[0] == 410)
        check("верная подпись, но неизвестный идентификатор доступа → 404", http(f"{NGINX}/hls/{'0' * 24}/{exp}/{sign('0' * 24, int(exp))}/index.m3u8")[0] == 404)
        check("внутренний event.m3u8 и чужие имена файлов не отдаются (404)", http(base + sig + "/event.m3u8")[0] == 404 and http(base + sig + "/../b/x.ts")[0] in (400, 404))
        s3, _, _ = http(f"http://127.0.0.1:5186{path}")
        check("тот же адрес работает и через прокси разработки (как за web-контейнером)", s3 == 200)
        # ---------------------------------------------------------------- пароль зрителя: защищает и поток
        r = await api(A, "PUT", f"/rooms/{room_id}/live/password", {"password": "zritel-2026"})
        check("владелец (руководитель) задал пароль зрителя", r["s"] == 200 and r["j"]["password_enabled"], str(r)[:160])
        sold, _, _ = http(NGINX + path)
        check("после смены пароля ПРЕЖНЯЯ подписанная ссылка на видео сразу не открывается (404)", sold == 404, str(sold))
        check("у смотревшего зрителя плеер вернулся к форме пароля", await V1.wait_for("!!document.querySelector('.live-pw')", 45), (await V1.js("document.body.innerText"))[:160])
        await V2.goto(f"/live/{token}", 2.5)
        check("новый зритель (режим инкогнито/новый браузер) видит форму пароля, а не видео", await V2.wait_for("!!document.querySelector('.live-pw')", 20) and not await V2.js("!!document.querySelector('.live-stage video')"))
        check("прямой запрос API без пароля отклонён", (await V2.js(f"fetch('/api/v1/live/{token}/access',{{method:'POST',headers:{{'content-type':'application/json'}},body:'{{}}'}}).then(r=>r.status)")) == 403)
        await V2.js("(()=>{const i=document.querySelector('.live-pw input');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'не-тот-пароль');i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.live-pw button').click()})()")
        check("неверный пароль — понятное сообщение", await V2.wait_for("document.body.innerText.includes('Неверный пароль')", 15))
        await V2.js("(()=>{const i=document.querySelector('.live-pw input');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'zritel-2026');i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.live-pw button').click()})()")
        check("верный пароль — эфир идёт", await V2.wait_for("(()=>{const v=document.querySelector('.live-stage video');return !!v&&v.videoWidth>0&&v.currentTime>2})()", 90))
        await V2.shot("viewer-password-ok")
        # ссылка на поток владельца: отдельный идентификатор, независимый отзыв
        lr = (await api(A, "GET", f"/rooms/{room_id}/live"))["j"]
        sp = lr["stream_path"]
        check("ссылка на поток владельца открывается (она сама — пропуск, пароль страницы её не защищает)", http(NGINX + sp)[0] == 200)
        r = await api(A, "POST", f"/rooms/{room_id}/live/links/stream/revoke")
        check("ссылка на поток отозвана — прежний адрес 404, новый выпущен", http(NGINX + sp)[0] == 404 and r["j"]["stream_path"] != sp)
        check("отзыв потока не отключил зрителя страницы", (await V2.js("(()=>{const v=document.querySelector('.live-stage video');return !!v&&!v.paused&&v.currentTime>2})()")))
        # ---------------------------------------------------------------- отзыв ссылки зрителя
        r = await api(A, "POST", f"/rooms/{room_id}/live/links/view/revoke")
        newtoken = r["j"]["view_path"].split("/")[-1]
        await V1.goto(f"/live/{token}", 2.0)
        check("старая ссылка зрителя после отзыва: «Трансляция не найдена»", await V1.wait_for("document.body.innerText.includes('Трансляция не найдена')", 15))
        check("зритель по старой ссылке выброшен и в открытом плеере (V2: форма или сообщение)", await V2.wait_for("!!document.querySelector('.live-pw') || document.body.innerText.includes('не найдена')", 60))
        r = await api(A, "PUT", f"/rooms/{room_id}/live/password", {"password": None})
        await V1.goto(f"/live/{newtoken}", 2.5)
        check("новая ссылка без пароля: эфир идёт", await V1.wait_for("(()=>{const v=document.querySelector('.live-stage video');return !!v&&v.videoWidth>0&&v.currentTime>2})()", 90))
        # ---------------------------------------------------------------- параллельная обычная конференция
        D = S.Client("dave", "dave", "dave-pass", 9746)
        await D.start(); clients.append(D)
        await D.goto("/rooms/sales", 2.0)
        check("параллельно обычная конференция «Продажи»: вход работает", await D.join(navigate=False))
        # ---------------------------------------------------------------- ждём, чтобы проверить уборку сегментов
        left = MINUTES * 60 - (time.time() - t0)
        if left > 0:
            print(f"   эфир идёт {int(time.time() - t0)} с; ждём ещё {int(left)} с (уборка сегментов старше 3 минут)…", flush=True)
            await asyncio.sleep(left)
        bdirs = [d for d in os.listdir(os.path.join(LIVE_DIR, "b"))]
        files = sorted(os.listdir(os.path.join(LIVE_DIR, "b", bdirs[0]))) if bdirs else []
        segs = [f for f in files if f.startswith("seg_")]
        newest = max(int(re.findall(r"\d+", f)[0]) for f in segs) if segs else 0
        age_ok = (time.time() - t0) < 200 or len(segs) <= 3 * 60 / 2 + 40
        check(f"сегменты не копятся: {len(segs)} шт. на диске после {int(time.time() - t0)} с эфира (≈{int((time.time() - t0) / 2)} записано)", age_ok, str(len(segs)))
        # ---------------------------------------------------------------- остановка
        check("«Остановить эфир» из меню комнаты", await menu_live(A, "Остановить эфир"))
        check("метка «ПРЯМОЙ ЭФИР» у выступающих погасла", await B.wait_for("![...document.querySelectorAll('.rec-badge.live')].some(b=>b.textContent.includes('ПРЯМОЙ ЭФИР'))", 40))
        check("зритель увидел «Эфир завершён»", await V1.wait_for("document.body.innerText.includes('Эфир завершён')", 60), (await V1.js("document.body.innerText"))[:160])
        await V1.shot("viewer-ended")
        stop.set()
        ready = None
        for _ in range(60):
            m = await api(A, "GET", f"/meetings/{mid}/media")
            vids = [x for x in m["j"]["mixes"] if x["has_video"]]
            if vids and vids[-1]["status"] in ("ready", "failed"):
                ready = vids[-1]; break
            await asyncio.sleep(2)
        check("MP4-запись эфира сохранена в историю (ready)", bool(ready) and ready["status"] == "ready", json.dumps(ready, default=str)[:200])
        await asyncio.sleep(75)
        now_dirs = set(os.listdir(os.path.join(LIVE_DIR, "b")))
        dangling = [x for x in os.listdir(os.path.join(LIVE_DIR, "acc")) if os.path.islink(os.path.join(LIVE_DIR, "acc", x)) and not os.path.exists(os.path.join(LIVE_DIR, "acc", x))]
        check("после конца эфира каталог HLS этого эфира убран уборщиком", not (now_dirs - before_dirs), str(now_dirs - before_dirs))
        check("висячих ссылок доступа на удалённые каталоги нет", not dangling, str(dangling))
        if samples:
            cpus = [c for _, c, _, _ in samples]
            print("НАГРУЗКА egress:", json.dumps({"samples": len(samples), "cpu_avg_%": round(sum(cpus) / len(cpus)), "cpu_max_%": round(max(cpus)), "mem_max_MiB": max(round(float(m.replace("GiB", "")) * 1024) if m.endswith("GiB") else round(float(re.sub("[A-Za-z]", "", m))) for _, _, m, _ in samples)}, ensure_ascii=False), flush=True)
        # ---------------------------------------------------------------- повторный запуск и падение Egress
        await A.goto("/rooms/webinar?as=speaker", 2.0)
        check("Алиса снова во встрече", await A.join(navigate=False))
        r = await api(A, "GET", f"/rooms/{room_id}/live")
        mid2 = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>x.room_name==='Вебинар'&&!x.ended_at)||{}).id})()")
        r = await api(A, "POST", f"/meetings/{mid2}/live", {"enabled": True})
        check("эфир можно начать снова", r["s"] == 200, str(r))
        await asyncio.sleep(25)
        subprocess.run(["docker", "kill", EG], capture_output=True)
        print("   Egress остановлен посреди эфира; ждём, пока сервер закроет «идущий» эфир…", flush=True)
        tk = time.time(); failed = False
        while time.time() - tk < 150:
            st = (await api(A, "GET", f"/meetings/{mid2}/live"))["j"]
            if not st["live"]:
                failed = True; break
            await asyncio.sleep(5)
        check(f"упавший Egress: эфир не остаётся «идущим» ({int(time.time() - tk)} с), причина показана", failed and st["status"] == "failed" and "перестала отвечать" in (st.get("error") or ""), str(st)[:200])
        check("конференция выступающих продолжает работать после падения службы эфира", bool(await A.js("document.querySelectorAll('.tile').length>0")))
        subprocess.run(["docker", "start", EG], capture_output=True)
        await asyncio.sleep(12)
        r = await api(A, "POST", f"/meetings/{mid2}/live", {"enabled": True})
        check("после перезапуска Egress эфир запускается снова", r["s"] == 200, str(r))
        await asyncio.sleep(6)
        await api(A, "POST", f"/meetings/{mid2}/live", {"enabled": False})
    finally:
        stop.set()
        for c in clients:
            try:
                await c.close()
            except Exception:
                pass
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
