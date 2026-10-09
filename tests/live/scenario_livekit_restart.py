"""Сценарий (см. README.md): надёжность: перезапуск LiveKit во время встречи. Два браузера в комнате → LiveKit останавливается на 10 с → запускается снова → клиенты должны вернуться сами."""
import asyncio, os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S

LK_DIR = os.environ.get("LIVEKIT_DIR", ".")          # каталог с livekit-server и lk.yaml
S.ROOM = "/rooms/arch"


def start_lk():
    return subprocess.Popen([os.path.join(LK_DIR, "livekit-server.exe"), "--config", "lk.yaml"], cwd=LK_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def main():
    A = S.Client("alice", "alice", "alice-pass", 9471)
    B = S.Client("bob", "bob", "bob-pass", 9472)
    lk = None
    try:
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        ok = await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        S.check("оба в комнате до сбоя", all(ok))
        # остановить LiveKit
        subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process livekit-server -ErrorAction SilentlyContinue | Stop-Process -Force"], capture_output=True)
        await asyncio.sleep(10)
        st = await A.js("document.body.innerText.slice(0, 600).replace(/\\n/g, ' | ')")
        print("Алиса во время простоя LiveKit:", st[:300])
        await A.shot("lk-down")
        lk = start_lk()
        await asyncio.sleep(3)
        back = await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2 && [...document.querySelectorAll('audio[data-identity]')].some(a=>a.srcObject&&a.srcObject.getAudioTracks().some(t=>t.readyState==='live'))", 70) for c in (A, B)])
        S.check("Алиса сама вернулась в комнату после перезапуска LiveKit (плитки и живой звук)", back[0])
        S.check("Боб сам вернулся в комнату после перезапуска LiveKit", back[1])
        await asyncio.sleep(3)
        for c in (A, B):
            s = await c.js(S.STATS)
            S.check(f"{c.label}: после возврата аудио снова идёт (пакеты)", s["ain"] > 20 and s["aout"] > 20, str(s))
        await A.shot("lk-back")
        print("ошибки Алисы:", [e for e in (await A.state())["errs"] if "hooks" in e or "Uncaught" in e][:3])
    finally:
        for c in (A, B):
            await c.close()
        if lk: lk.terminate()
        subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process livekit-server -ErrorAction SilentlyContinue | Stop-Process -Force"], capture_output=True)
        start_lk()          # оставить стенд в рабочем состоянии
    bad = [r for r in S.results if not r[1]]
    print(f"\nИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")

asyncio.run(main())
