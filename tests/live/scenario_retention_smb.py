"""Сроки хранения видео с внешним хранилищем SMB на тестовом экземпляре (настоящие Egress и Samba): видеозапись переносится на SMB без локальной копии,
общие сроки ждут даты начала очистки, свой срок комнаты действует сразу, недоступный SMB не теряет файл (строка остаётся, повтор), затем файл удаляется и с SMB.

Нужен контейнер Samba `pgtest-smb` в сети экземпляра и профиль хранилища PG_PROFILE_ID, указывающий на него.
Запуск: PEREGOVORKA_URL=http://localhost:28480 PG_ADMIN_PASSWORD=… PG_PROFILE_ID=… BROWSER_PATH=… python3 tests/live/scenario_retention_smb.py"""
import asyncio, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from scenario_broadcast_replay import api, camera_bot, lk_room_of, sql

PASSWORD = os.environ["PG_ADMIN_PASSWORD"]
PROFILE = os.environ["PG_PROFILE_ID"]
SMB = os.environ.get("SMB_CONTAINER", "pgtest-smb")
ROOM = os.environ.get("PG_ROOM", "upd-check")
check = S.check


def smb_files(name):
    return subprocess.run(["docker", "exec", SMB, "sh", "-c", f"find /share -name '{name}' | head"], capture_output=True, text=True).stdout.split()


async def main():
    A = S.Client("admin", "admin", PASSWORD, 9661)
    bot = None
    try:
        await A.start()
        await A.send("Page.addScriptToEvaluateOnNewDocument", source="navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('нет устройств', 'NotFoundError')); navigator.mediaDevices.enumerateDevices = async () => [];")
        r = await api(A, "PUT", "/admin/settings/video", {"enabled": True, "min_free_gb": 1, "storage": "profile", "profile_id": PROFILE, "keep_local_copy": False})
        check("видеозаписи сохраняются на SMB без локальной копии", r["s"] == 200, str(r)[:200])
        room = next(x for x in (await api(A, "GET", "/admin/rooms"))["j"] if x["slug"] == ROOM)
        await api(A, "PATCH", f"/rooms/{room['id']}/manage", {"video_mode": "manual", "video_quality": "720p"})
        await A.goto(f"/rooms/{ROOM}", 2.0)
        check("администратор в комнате", await A.join(navigate=False))
        lkroom = await lk_room_of()
        bot = await camera_bot(lkroom)
        mid = await A.js("(async()=>{const m=await (await fetch('/api/v1/meetings?limit=5')).json();return (m.find(x=>!x.ended_at)||{}).id})()")
        r = await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": True})
        check("видеозапись начата", r["s"] in (200, 201), str(r)[:200])
        await asyncio.sleep(20)
        await api(A, "POST", f"/meetings/{mid}/video-recording", {"enabled": False})
        rid = path = ""
        for _ in range(90):
            row = sql(f"SELECT id, path, export_status FROM recordings WHERE meeting_id = '{mid}' AND kind = 'mix_video' ORDER BY created_at DESC LIMIT 1")
            if row.endswith("|exported"):
                rid, path, _ = row.split("|"); break
            await asyncio.sleep(2)
        name = os.path.basename(path)
        check(f"MP4 перенесён на SMB ({name})", bool(rid) and bool(smb_files(name)), row)
        await api(A, "POST", f"/admin/meetings/{mid}/end")
        sql(f"UPDATE recordings SET created_at = now() - interval '40 days' WHERE id = '{rid}'")
        gen = (await api(A, "GET", "/admin/settings/general"))["j"]
        check(f"общий срок видео — {gen.get('default_video_retention_days')} дней, очистка общих сроков начнётся {gen.get('retention_cleanup_from')}",
              gen.get("default_video_retention_days") == 30 and bool(gen.get("retention_cleanup_from")), str(gen)[:200])
        st = (await api(A, "POST", "/admin/retention/run"))["j"]
        check("запись 40 дней при общем сроке 30, но до даты начала очистки — не удалена", sql(f"SELECT count(*) FROM recordings WHERE id = '{rid}'") == "1" and bool(smb_files(name)), str(st))
        r = await api(A, "PATCH", f"/admin/rooms/{room['id']}", {"video_retention_custom": True, "video_retention_days": 60})
        st = (await api(A, "POST", "/admin/retention/run"))["j"]
        check("свой срок комнаты 60 дней: запись 40 дней остаётся", r["s"] == 200 and sql(f"SELECT count(*) FROM recordings WHERE id = '{rid}'") == "1", str(st))
        await api(A, "PATCH", f"/admin/rooms/{room['id']}", {"video_retention_custom": True, "video_retention_days": 30})
        subprocess.run(["docker", "pause", SMB], capture_output=True)
        try:
            st = (await api(A, "POST", "/admin/retention/run"))["j"]
        finally:
            subprocess.run(["docker", "unpause", SMB], capture_output=True)
        check("SMB недоступен: строка записи осталась для повтора (файл не «потерян» на SMB)", sql(f"SELECT count(*) FROM recordings WHERE id = '{rid}'") == "1" and (st or {}).get("deferred_external") == 1, str(st))
        st = (await api(A, "POST", "/admin/retention/run"))["j"]
        check("SMB снова доступен: запись удалена из базы и с SMB", sql(f"SELECT count(*) FROM recordings WHERE id = '{rid}'") == "0" and not smb_files(name) and (st or {}).get("video_recordings") == 1, str(st))
        aud = (await api(A, "GET", "/admin/audit?limit=20"))["j"]
        items = aud["items"] if isinstance(aud, dict) else aud
        check("запуск очистки записан в журнал аудита", any(a["action"] == "retention.run" for a in items))
        # вернуть настройки экземпляра
        await api(A, "PATCH", f"/admin/rooms/{room['id']}", {"video_retention_custom": False})
        await api(A, "PUT", "/admin/settings/video", {"storage": "local"})
    finally:
        if bot:
            bot[1].cancel()
            await bot[0].disconnect()
        await A.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")


asyncio.run(main())
