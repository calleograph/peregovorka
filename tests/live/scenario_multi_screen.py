"""Сценарий (см. README.md): одновременный показ экрана пятью участниками; шестой получает понятный отказ; освободившееся место доступно сразу;
у зрителя все показы крупно и кадры каждого декодируются; каждый показ завершается отдельно; уход участников — в «Журнале событий» с причиной.

Нужен LiveKit с webhook на стенд (предел показов соблюдает сервер по событию track_published): `LIVE_LK_PORT` у devserver_live.py — порт такого LiveKit.
Источник экрана подменён холстом (в headless настоящего экрана нет); шесть браузеров — четыре учётные записи стенда, у двух по два устройства.
"""
import asyncio, os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
S.ROOM = "/rooms/support"
S.INIT += """
navigator.mediaDevices.getDisplayMedia = async function () {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d'); let n = 0;
  const hue = Math.floor(Math.random() * 360);
  setInterval(() => { g.fillStyle = 'hsl(' + hue + ' 45% 30%)'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '64px sans-serif'; g.fillText('ЭКРАН ' + (n++), 100, 300); }, 100);
  return c.captureStream(10);
};
"""
# сколько чужих показов видно крупно и сколько из них реально показывают кадры (видео с размером и растущим числом кадров)
SCREENS = r"""(async () => {
  const v = [...document.querySelectorAll('.screen-stage video')];
  const f0 = v.map(x => x.getVideoPlaybackQuality ? x.getVideoPlaybackQuality().totalVideoFrames : 0);
  await new Promise(r => setTimeout(r, 1500));
  const f1 = v.map(x => x.getVideoPlaybackQuality ? x.getVideoPlaybackQuality().totalVideoFrames : 0);
  const big = [...document.querySelectorAll('.screen-stage.big')].length;
  return { stages: document.querySelectorAll('.screen-stage').length, big, live: v.filter((x, i) => x.videoWidth > 0 && f1[i] > f0[i]).length };
})()"""
ERR = "(document.querySelector('.ctl-err')?.textContent || '')"
SHARING = "!!document.querySelector('button[aria-label=\"Остановить показ\"]')"
WHO = [("alice", "alice-pass"), ("bob", "bob-pass"), ("dave", "dave-pass"), ("root", "root-pass"), ("alice", "alice-pass"), ("bob", "bob-pass")]


async def main():
    cs = [S.Client(f"{login}{i}", login, pw, 9611 + i) for i, (login, pw) in enumerate(WHO)]
    try:
        for c in cs:
            await c.start(); S.check(f"{c.label}: вошёл", await c.join())
        S.check("у всех шесть плиток", all(await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=6", 60) for c in cs])))
        for c in cs[:4]:
            S.check(f"{c.label}: показ начат", await c.click_btn("Показать экран") and await c.wait_for(SHARING, 15))
            await asyncio.sleep(1.0)
        # пятый и шестой нажимают одновременно: на странице оба видят четыре показа, предел соблюдает сервер
        await asyncio.gather(cs[4].click_btn("Показать экран"), cs[5].click_btn("Показать экран"))
        await asyncio.sleep(6)
        st = [await c.js(SHARING) for c in cs]
        errs = [await c.js(ERR) for c in cs]
        S.check("показывают ровно пятеро", sum(map(bool, st)) == 5, str(st))
        loser = next((i for i in (4, 5) if not st[i]), None)
        S.check("шестой получил понятный отказ сервера", loser is not None and "это предел" in errs[loser], errs[loser] if loser is not None else "")
        watcher = cs[loser] if loser is not None else cs[5]
        s = await watcher.js(SCREENS)
        S.check("у шестого все пять показов крупно и кадры каждого идут", s["big"] == 5 and s["live"] == 5, json.dumps(s))
        await watcher.shot("five-screens")
        s0 = await cs[0].js(SCREENS)
        S.check("у показывающего — четыре чужих показа крупно, свой в ленте", s0["big"] == 4 and s0["live"] >= 4, json.dumps(s0))
        # шестой нажимает ещё раз, пока мест нет: отказ сразу, без окна выбора экрана
        await watcher.js("document.querySelector('.ctl-err .x')?.click()")
        await watcher.click_btn("Показать экран"); await asyncio.sleep(1.5)
        S.check("повторная попытка при занятых местах — сообщение сразу", "это предел" in (await watcher.js(ERR)) and not await watcher.js(SHARING))
        # место освободилось — сразу доступно
        S.check(f"{cs[1].label}: свой показ завершён", await cs[1].click_btn("Остановить показ") and await cs[1].wait_for(f"!{SHARING}", 10))
        await asyncio.sleep(1.5)
        await watcher.js("document.querySelector('.ctl-err .x')?.click()")
        S.check("освободившееся место — сразу доступно", await watcher.click_btn("Показать экран") and await watcher.wait_for(SHARING, 15))
        await asyncio.sleep(4)
        s1 = await cs[1].js(SCREENS)
        S.check("у завершившего — снова пять чужих показов", s1["big"] == 5 and s1["live"] == 5, json.dumps(s1))
        await cs[1].shot("five-screens-after-swap")
        # каждый завершает свой показ — у зрителя показы исчезают по одному
        sharing = [c for c in cs if await c.js(SHARING)]
        for n, c in enumerate(sharing):
            await c.click_btn("Остановить показ")
            left = len(sharing) - n - 1
            ok = await cs[1].wait_for(f"document.querySelectorAll('.screen-stage').length==={left}", 15)
            S.check(f"после завершения {n + 1}-го показа у зрителя осталось {left}", ok)
        print("errs:", [((await c.state()) or {}).get("errs", [])[:2] for c in cs])
    finally:
        for c in cs: await c.close()
    bad = [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(S.results) - len(bad)} из {len(S.results)}; не прошли: {[b[0] for b in bad]}")
asyncio.run(main())
