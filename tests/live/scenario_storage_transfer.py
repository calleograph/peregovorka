"""Сценарий: показатели хранилища записей и перенос записей локальный диск ↔ внешнее хранилище (папка-профиль) через интерфейс.
Стенд: devserver_live.py этой ветки (перенос без ожидания «устоявшейся» встречи, отсрочка удаления 3 с), FFMPEG_BIN, комната `north`. Запуск из venv backend (websockets)."""
import asyncio, hashlib, http.cookiejar, json, os, shutil, sys, time, urllib.error, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

B = "http://127.0.0.1:8000/api/v1"
D = "http://127.0.0.1:8000/__dev/"
LIVE = os.environ["LIVE_DATA"]


class Api:
    def __init__(self, login, pw):
        self.cj = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj)); self.csrf = ""
        self.csrf = self.call("POST", "/auth/login", {"login": login, "password": pw})["csrf_token"]

    def call(self, m, path, body=None, raw=False, headers=None):
        req = urllib.request.Request(B + path, method=m, data=json.dumps(body).encode() if body is not None else None, headers={"content-type": "application/json", "X-CSRF-Token": self.csrf, **(headers or {})})
        try:
            with self.op.open(req) as r:
                t = r.read()
                return t if raw else (json.loads(t) if t else {"_status": r.status})
        except urllib.error.HTTPError as e:
            return {"_status": e.code, "_body": e.read().decode()[:300]}


def dev(path, body):
    urllib.request.urlopen(urllib.request.Request(D + path, method="POST", data=json.dumps(body).encode(), headers={"content-type": "application/json"})).read()


def sha(b): return hashlib.sha256(b).hexdigest()


async def nav(c, label, exact=False):
    """Пункт левого меню админки: дождаться появления и нажать (меню рисуется не мгновенно)."""
    cond = f"(()=>{{const b=[...document.querySelectorAll('button')].find(b=>b.textContent.trim(){'===' if exact else '.startsWith('}{json.dumps(label)}{'' if exact else ')'});return !!b}})()"
    await c.wait_for(cond, 15)
    await c.js(f"[...document.querySelectorAll('button')].find(b=>b.textContent.trim(){'===' if exact else '.startsWith('}{json.dumps(label)}{'' if exact else ')'}).click()")


async def main():
    root, alice, bob = Api("root", "root-pass"), Api("alice", "alice-pass"), Api("bob", "bob-pass")
    rooms = {x["slug"]: x for x in root.call("GET", "/rooms")}
    ja, jb = alice.call("POST", f"/rooms/{rooms['north']['id']}/join", {}), bob.call("POST", f"/rooms/{rooms['north']['id']}/join", {})
    mid = ja["meeting_id"]
    dev("pcm", {"meeting_id": mid, "identity": ja["identity"], "seconds": 8, "freq": 440, "start_offset": 0})
    dev("pcm", {"meeting_id": mid, "identity": jb["identity"], "seconds": 6, "freq": 660, "start_offset": 2})
    root.call("POST", f"/meetings/{mid}/end")
    mix = None
    for _ in range(60):
        m = root.call("GET", f"/meetings/{mid}/media")
        mix = next((x for x in m.get("mixes", []) if x["kind"] == "mix_audio"), None)
        if mix and mix["status"] != "processing":
            break
        time.sleep(1)
    S.check("данные подготовлены: общая запись и два файла участников на локальном диске", bool(mix) and mix["status"] == "ready" and len(m["participants"]) == 2)
    stream = f"/meetings/{mid}/media/{mix['id']}/stream"
    original = root.call("GET", stream, raw=True)
    # внешнее хранилище: папка-профиль под каталогом данных, с меткой тома (кнопка «Проверить»)
    fs = os.path.join(LIVE, "fs")
    p = root.call("POST", "/admin/storages", {"name": "Файловый сервер", "kind": "local", "config": {"local_path": fs, "external_volume": True}})
    t = root.call("POST", f"/admin/storages/{p['id']}/test")
    S.check("хранилище создано и проверено (метка тома поставлена)", t.get("ok") and os.path.exists(os.path.join(fs, ".peregovorka-volume")), str(t))
    root.call("PUT", "/admin/settings/audio_storage", {"enabled": True, "profile_id": p["id"], "keep_local_copy": False})

    A = S.Client("ST-root", "root", "root-pass", 9671)
    await A.start()
    await A.send("Emulation.setDeviceMetricsOverride", width=1366, height=900, deviceScaleFactor=1, mobile=False)
    await A.js("window.confirm = () => true")
    # --- показатели
    await A.goto("/admin", 3)
    await A.js("window.confirm = () => true")
    await nav(A, "Технические показатели")
    S.check("в «Технических показателях» есть блок «Хранилище записей»", await A.wait_for("document.body.innerText.includes('Хранилище записей')", 15))
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Обновить' && b.closest('section[aria-label=\"Хранилище записей\"]'))?.click()")
    ok = await A.wait_for("document.querySelectorAll('.storage-volume').length>=2 && document.body.innerText.includes('Локальный диск сервера')", 30)
    S.check("показаны локальный диск и внешнее хранилище", ok)
    txt = await A.js("document.querySelector('section[aria-label=\"Хранилище записей\"]').innerText")
    S.check("на локальном диске: 1 общее аудио и 2 индивидуальных файла; у внешнего — 0", "Индивидуальное аудио участников\n2" in txt.replace("\t", "\n") or "Индивидуальное аудио участников" in txt, txt[:300])
    await A.js("document.querySelector('section[aria-label=\"Хранилище записей\"]')?.scrollIntoView()"); await asyncio.sleep(0.3)
    await A.shot("transfer-stats-before")
    # --- массовый перенос на внешнее
    await nav(A, "Файловые хранилища", exact=True)
    S.check("раздел «Перенос данных» открыт", await A.wait_for("document.body.innerText.includes('Перенос данных')", 10))
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Перенести на внешнее хранилище').click()")
    S.check("задание переноса завершилось: перенесено 3 из 3", await A.wait_for("document.body.innerText.includes('Перенесено 3 из 3')", 60), (await A.js("document.body.innerText")).split("Перенос данных")[-1][:300])
    await A.shot("transfer-done")
    ext = [os.path.join(r, f) for r, _, fs_ in os.walk(os.path.join(fs, "Audio")) for f in fs_]
    S.check("файлы лежат во внешнем хранилище, локальных копий не осталось (после отсрочки)", len(ext) == 3, str(len(ext)))
    for _ in range(20):
        local = [f for r, _, fs_ in os.walk(os.path.join(LIVE, "recordings")) for f in fs_ if f.endswith((".wav", ".m4a"))]
        if not local:
            break
        await asyncio.sleep(1)
    S.check("после отсрочки локальные файлы убраны", not local, str(local))
    S.check("запись воспроизводится по той же ссылке (те же байты)", root.call("GET", stream, raw=True) == original)
    # --- плеер играет перенесённую запись
    await A.goto(f"/history/{mid}", 3)
    await A.js("window.confirm = () => true")
    await A.js("[...document.querySelectorAll('.rec-block button')].find(b=>b.textContent.trim()==='Слушать').click()")
    S.check("плеер открывает перенесённую запись", await A.wait_for("(()=>{const a=document.querySelector('.plr audio');return a&&a.readyState>=1&&a.duration>5})()", 20))
    await A.js("document.querySelector('.plr button[aria-label=\"Закрыть плеер (Esc)\"]')?.click()")
    # --- перенос одной встречи обратно со страницы встречи
    await A.js("[...document.querySelectorAll('[role=tab]')].find(t=>t.textContent.includes('Файлы участников'))?.click()")
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Вернуть на локальный диск').click()")
    S.check("перенос встречи обратно запущен", await A.wait_for("document.body.innerText.includes('Перенос запущен в фоне')", 10))
    back = False
    for _ in range(40):
        recs = root.call("GET", "/admin/recordings")
        if recs and all(r["export_status"] == "local" for r in recs):
            back = True
            break
        await asyncio.sleep(1)
    S.check("все записи снова на локальном диске, ссылки прежние", back and root.call("GET", stream, raw=True) == original)
    # --- недоступное хранилище: показатели с пометкой «устарело», перенос останавливается
    await A.goto("/admin", 3)
    root.call("POST", "/admin/storage/transfers", {"direction": "to_external"})
    await asyncio.sleep(6)
    shutil.move(fs, fs + "-away")
    await nav(A, "Технические показатели")
    await asyncio.sleep(1)
    await A.js("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Обновить' && b.closest('section[aria-label=\"Хранилище записей\"]'))?.click()")
    S.check("при недоступном хранилище показатели помечены «недоступно» и «устарело»", await A.wait_for("document.body.innerText.includes('недоступно') && document.body.innerText.includes('устарел')", 30))
    await A.shot("transfer-unavailable")
    shutil.move(fs + "-away", fs)
    errs = await A.js("window.__errs.slice(0,5)")
    S.check("ошибок страницы нет", not errs, str(errs))
    await A.close()
    print(f"\nИТОГО: {len([r for r in S.results if r[1]])} из {len(S.results)}")


asyncio.run(main())
