"""Удаление записи при недоступном SMB на тестовом экземпляре (настоящие Egress и Samba): администратор удаляет запись, сервер SMB выключен —
запись остаётся в списке «удаляется» с причиной, недоступна для просмотра; удаление встречи отклоняется; SMB включается — фоновая задача
удаляет файл и строку сама; затем встреча удаляется. Плюс пароль у завершённого эфира: старые адреса файла перестают действовать.

Запуск: PEREGOVORKA_URL=… PG_ADMIN_PASSWORD=… PG_PROFILE_ID=… BROWSER_PATH=… python3 tests/live/scenario_delete_smb.py"""
import asyncio, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from scenario_broadcast_replay import Anon, api, broadcast, camera_bot, http, lk_room_of, sql, wait_replay
from scenario_retention_smb import SMB, on_smb

PASSWORD = os.environ["PG_ADMIN_PASSWORD"]
PROFILE = os.environ["PG_PROFILE_ID"]
check = S.check


def smb(cmd):
    subprocess.run(["docker", cmd, SMB], capture_output=True)
    if cmd == "start":
        for _ in range(30):
            st = subprocess.run(["docker", "ps", "--filter", f"name={SMB}", "--format", "{{.Status}}"], capture_output=True, text=True).stdout
            if "healthy" in st and "starting" not in st:
                return
            time.sleep(2)


async def main():
    A = S.Client("admin", "admin", PASSWORD, 9665)
    V = Anon("viewer", "admin", PASSWORD, 9666)
    bot = None
    try:
        await A.start(); await V.start()
        await A.send("Page.addScriptToEvaluateOnNewDocument", source="navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('x', 'NotFoundError')); navigator.mediaDevices.enumerateDevices = async () => [];")
        r = await api(A, "PUT", "/admin/settings/video", {"enabled": True, "min_free_gb": 1, "storage": "profile", "profile_id": PROFILE, "keep_local_copy": False})
        check("видеозаписи и записи эфиров сохраняются на SMB", r["s"] == 200, str(r)[:160])
        rid = next(x for x in (await api(A, "GET", "/admin/rooms"))["j"] if x["slug"] == "efir-054")["id"]
        await api(A, "PATCH", f"/rooms/{rid}/live", {"live_replay": True, "live_download": True})
        await A.goto("/rooms/efir-054?as=speaker", 2.5)
        check("администратор в комнате", await A.join(navigate=False))
        bot = await camera_bot(await lk_room_of())
        mid = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>x.room_name==='Эфир 0.54'&&!x.ended_at)||{}).id})()")
        b = await broadcast(A, rid, mid, "Удаление", {"replay": True, "download": True})
        tok = b["view_path"].rsplit("/", 1)[-1]
        await asyncio.sleep(20)
        await api(A, "POST", f"/meetings/{mid}/live", {"enabled": False})
        st, waited = await wait_replay(V, tok, "ready")
        check(f"запись эфира готова ({waited} с)", st.get("replay_state") == "ready")
        rec_id = b["recording_id"]
        path = ""
        for _ in range(60):
            row = sql(f"SELECT path, export_status FROM recordings WHERE id = '{rec_id}'")
            if row.endswith("|exported"):
                path = row.split("|")[0]; break
            await asyncio.sleep(2)
        check("запись перенесена на SMB", bool(path) and on_smb(path), row)
        # пароль у завершённого эфира: выданные адреса файла перестают действовать
        rep = await V.js(f"fetch('/api/v1/live/{tok}/replay',{{method:'POST',headers:{{'content-type':'application/json'}},body:'{{}}'}}).then(r=>r.json())")
        check("без пароля запись открыта, докачка работает", http(rep["url"], {"Range": "bytes=0-99"})[0] == 206)
        r = await api(A, "PATCH", f"/rooms/{rid}/live/broadcasts/{b['id']}", {"password": "posle-efira"})
        check("пароль задан у завершённого эфира", r["s"] == 200, str(r)[:120])
        check("прежний адрес файла и докачка сразу не действуют", http(rep["url"], {"Range": "bytes=100-199"})[0] in (403, 404, 410) and http(rep["download_url"])[0] in (403, 404, 410))
        await V.goto(b["view_path"], 2.0)
        await V.js("[...document.querySelectorAll('button')].find(b=>/Смотреть запись/.test(b.textContent))?.click()")
        check("зритель видит форму пароля", await V.wait_for("!!document.querySelector('.live-pw')", 20))
        await V.js("(()=>{const i=document.querySelector('.live-pw input');const set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(i,'posle-efira');i.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('.live-pw button').click()})()")
        check("с новым паролем запись играет", await V.wait_for("(()=>{const v=document.querySelector('video[aria-label=\"Запись эфира\"]');return !!v&&v.duration>5})()", 40))
        await api(A, "POST", f"/admin/meetings/{mid}/end")
        # удаление при выключенном SMB
        smb("stop")
        r = await api(A, "DELETE", f"/meetings/{mid}/recordings/{rec_id}")
        check("SMB выключен: удаление принято, но отложено (202, причина)", r["s"] == 202 and r["j"]["deferred"] == 1, str(r)[:300])
        row = sql(f"SELECT file_state, delete_attempts, coalesce(delete_error,'') FROM recordings WHERE id = '{rec_id}'")
        check(f"строка записи не потеряна: {row}", row.startswith("deleting|1|"))
        await A.goto(f"/admin?tab=recordings", 3.0)
        check("«Записи встреч»: «удаляется», причина и время повтора", await A.wait_for("document.body.innerText.includes('удаляется') && document.body.innerText.includes('повтор в')", 15))
        await A.shot("recordings-deleting")
        await V.goto(b["view_path"], 2.0)
        check("зрителю: «Запись удалена»", await V.wait_for("document.body.innerText.includes('Запись удалена')", 20))
        r = await api(A, "DELETE", f"/meetings/{mid}")
        check("удаление встречи отклонено, пока файл не удалён", r["s"] == 409, str(r)[:200])
        smb("start")
        due = float(sql(f"SELECT greatest(0, extract(epoch from delete_next_at - now())) FROM recordings WHERE id = '{rec_id}'") or 0)
        print(f"   следующий повтор по расписанию через {int(due)} с (пауза растёт с номером попытки)", flush=True)
        t = time.time()
        gone = False
        while time.time() - t < due + 120:
            if sql(f"SELECT count(*) FROM recordings WHERE id = '{rec_id}'") == "0":
                gone = True; break
            await asyncio.sleep(5)
        check(f"SMB снова доступен: фоновая задача удалила запись сама через {int(time.time() - t)} с", gone)
        check("файла нет в папке на SMB", not on_smb(path))
        r = await api(A, "DELETE", f"/meetings/{mid}")
        check("встреча теперь удаляется", r["s"] == 204, str(r)[:200])
        await api(A, "PUT", "/admin/settings/video", {"storage": "local"})
    finally:
        smb("start")
        if bot:
            bot[1].cancel()
            await bot[0].disconnect()
        await A.close(); await V.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


if __name__ == "__main__":
    asyncio.run(main())
