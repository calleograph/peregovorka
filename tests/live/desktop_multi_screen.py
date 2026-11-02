"""Сценарий: несколько показов экрана одновременно с участием приложения Peregovorka для Windows (настоящий Electron под X-сервером, его окно выбора экрана,
настоящий сервер и LiveKit). Приложение и два браузера показывают экран; зритель в браузере видит три показа крупно, приложение — два чужих;
приложение завершает свой показ — место освобождается, у зрителя остаётся два. Под Linux это сборка того же клиента, а не Windows.

Запуск: Xvfb на :99, стенд и Vite (адрес — PEREGOVORKA_URL, должен быть в ALLOWED_ORIGINS стенда), `cd desktop && node build.mjs`, затем
    DISPLAY=:99 PEREGOVORKA_URL=… BROWSER_PATH=… ~/pg-work/venv/bin/python tests/live/desktop_multi_screen.py"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import desktop_app as D
import smoke_room as S
from desktop_lib import App, check, results

URL = os.environ.get("PEREGOVORKA_URL", "http://127.0.0.1:5180")
ROOM = "/rooms/arch"
D.A_URL = URL
S.ROOM = ROOM
S.INIT += """
navigator.mediaDevices.getDisplayMedia = async function () {
  const c = document.createElement('canvas'); c.width = 1280; c.height = 720; const g = c.getContext('2d'); let n = 0;
  setInterval(() => { g.fillStyle = '#245'; g.fillRect(0, 0, 1280, 720); g.fillStyle = '#fff'; g.font = '64px sans-serif'; g.fillText('ЭКРАН ' + (n++), 100, 300); }, 100);
  return c.captureStream(10);
};
"""
SCREENS = r"""(async () => {
  const v = [...document.querySelectorAll('.screen-stage video')];
  const f0 = v.map(x => x.getVideoPlaybackQuality().totalVideoFrames); await new Promise(r => setTimeout(r, 1500));
  const f1 = v.map(x => x.getVideoPlaybackQuality().totalVideoFrames);
  return { big: document.querySelectorAll('.screen-stage.big').length, live: v.filter((x, i) => x.videoWidth > 0 && f1[i] > f0[i]).length };
})()"""
SHARING = "!!document.querySelector('button[aria-label=\"Остановить показ\"]')"


async def main():
    D.reset_sessions(("alice", "bob"))
    app = App()
    web = [S.Client("bob", "bob", "bob-pass", 9641), S.Client("root", "root", "root-pass", 9642)]
    try:
        await app.start()
        await app.m(D.STUB)
        shell = await app.page("app://shell/index.html")
        await shell.wait("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 15)
        await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')).click()")
        await shell.wait("!!document.querySelector('#srv')", 5)
        await shell.js("document.querySelector('#nm').value = 'Стенд'")
        await D.probe_in_shell(shell, URL)
        pa = await app.page(URL)
        check("приложение: вход alice", await D.login(pa, "alice", "alice-pass"))
        await pa.js(f"location.assign('{ROOM}')")
        await pa.wait("!!document.querySelector('.pj-actions .btn.primary')", 30)
        for _ in range(3):
            await pa.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            if await pa.wait("document.querySelectorAll('.tile').length > 0", 10):
                break
        for c in web:
            await c.start(); check(f"браузер {c.label}: вошёл", await c.join())
        check("у всех три участника", await pa.wait("document.querySelectorAll('.tile').length >= 3", 40) and all(await asyncio.gather(*[c.wait_for("document.querySelectorAll('.tile').length>=3", 40) for c in web])))
        # приложение: «Показать экран» → своё окно выбора → экран X-сервера
        await pa.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || '') === 'Показать экран')?.click()")
        picker = await app.page("app://shell/picker.html", 15, fresh=True)
        check("приложение: окно выбора экрана", await picker.wait("document.querySelectorAll('.src').length > 0", 15))
        await picker.js("setTimeout(() => document.querySelector('footer .btn.primary').click(), 0)")
        check("приложение: показ экрана идёт", await pa.wait(SHARING, 20))
        for c in web:
            check(f"браузер {c.label}: показ начат", await c.click_btn("Показать экран") and await c.wait_for(SHARING, 15))
        await asyncio.sleep(5)
        s = await web[0].js(SCREENS)
        await web[0].shot("desktop-plus-web-screens")
        check("зритель в браузере: показ приложения и второго браузера крупно, кадры идут", s["big"] == 2 and s["live"] >= 2, str(s))
        s = await pa.js(SCREENS)
        check("приложение: два чужих показа крупно, кадры идут", s["big"] == 2 and s["live"] >= 2, str(s))
        await app.shot_window("multi-screen-in-app")
        await pa.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || '') === 'Остановить показ')?.click()")
        check("приложение: показ завершён", await pa.wait(f"!{SHARING}", 15))
        check("у зрителя остался один чужой показ", await web[0].wait_for("document.querySelectorAll('.screen-stage').length === 2 && document.querySelectorAll('.screen-stage.big').length === 1", 15))
    finally:
        for c in web:
            await c.close()
        await app.stop(keep_data=False)
    failed = [r for r in results if not r[1]] + [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(results) + len(S.results) - len(failed)} из {len(results) + len(S.results)}; не прошли: {[r[0] for r in failed]}")


if __name__ == "__main__":
    asyncio.run(main())
