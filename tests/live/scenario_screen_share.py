"""Сценарий (см. README.md): показ экрана (источник подменён холстом: в headless настоящего экрана нет)."""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
S.ROOM = "/rooms/support"
# подмена getDisplayMedia: холст как «экран» (в headless настоящего экрана нет)
S.INIT += """
navigator.mediaDevices.getDisplayMedia = async function () {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d'); let n = 0;
  setInterval(() => { g.fillStyle = '#134'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '60px sans-serif'; g.fillText('ЭКРАН ' + (n++), 100, 300); }, 100);
  return c.captureStream(10);
};
"""
async def main():
    A = S.Client("alice", "alice", "alice-pass", 9495)
    B = S.Client("bob", "bob", "bob-pass", 9496)
    try:
        for c in (A, B):
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=2", 40) for c in (A, B)])
        S.check("Алиса: кнопка «Показать экран» нажата", await A.click_btn("Показать экран"))
        ok = await B.wait_for("!!document.querySelector('.screen-stage, [class*=stage] video, .stage video')", 25)
        S.check("Боб: видит показ экрана Алисы (сцена с видео)", ok)
        await asyncio.sleep(3)
        s = await B.js(S.STATS)
        S.check("Боб: видеокадры экрана декодируются", s["vin"] > 5, str(s))
        await B.shot("screen-share")
        S.check("Алиса: кнопка сменилась на «Остановить показ»", await A.click_btn("Остановить показ"))
        S.check("у Боба показ экрана закончился", await B.wait_for("!document.querySelector('.screen-stage, [class*=stage] video, .stage video')", 15))
        print("errs:", (await A.state())["errs"][:2], (await B.state())["errs"][:2])
    finally:
        for c in (A, B): await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
