"""Сценарий: установленный пакет Peregovorka на экранах разного размера и масштаба (X11): 1280×720 @100 %, 1920×1080 @150 %, 2560×1440 @200 %.
Проверяется: окно открывается, оболочка (мастер, настройки) без горизонтальной прокрутки, кнопки видны целиком, масштаб применён; снимки экрана.
Нужны: репозиторий APT (APT_SITE) и образ desktop/linux/test/e2e.Dockerfile; стенд не нужен."""
import asyncio, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import linux_pkg_e2e as E
from desktop_lib import check, results

CASES = [("1280x720", 1.0, 70), ("1920x1080", 1.5, 71), ("2560x1440", 2.0, 72)]
LAYOUT = """(() => { const de = document.documentElement; const out = [...document.querySelectorAll('button, input, a')].filter(e => { const r = e.getBoundingClientRect();
  return r.width > 0 && (r.right > innerWidth + 1 || r.left < -1); }).map(e => e.textContent.trim().slice(0, 30));
  return { dpr: devicePixelRatio, w: innerWidth, h: innerHeight, overflow: de.scrollWidth - de.clientWidth, outside: out }; })()"""


async def main():
    assert E.port_free(E.REPO_PORT) and E.port_free(E.CDP)
    serve = os.path.join(E.OUT, "linux-repo-serve")
    if os.path.lexists(serve): os.remove(serve)
    os.symlink(os.path.abspath(E.SITE), serve)
    srv = subprocess.Popen([sys.executable, os.path.join(E.ROOT, "desktop/linux/test/faultserver.py"), serve, str(E.REPO_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    subprocess.run(["docker", "run", "-d", "--init", "--name", E.C, "--network", "host", "--cap-add", "SYS_ADMIN", "--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined",
                    "--shm-size", "1g", "-v", f"{E.ROOT}/desktop/linux/test:/t:ro", "-v", f"{E.ROOT}/desktop/linux/install.sh:/install.sh:ro", "-v", f"{os.path.expanduser('~/pg-work/tls')}:/tls:ro",
                    E.IMG, "sleep", "infinity"], check=True, capture_output=True)
    try:
        E.sh(f"PEREGOVORKA_APT_URL=http://127.0.0.1:{E.REPO_PORT} PEREGOVORKA_APT_INSECURE_TEST=1 sh /install.sh >/dev/null 2>&1", user=False, timeout=900)
        for res, scale, disp in CASES:
            E.DISP = disp
            app = E.PkgApp()
            ok = await app.start(res, f"--force-device-scale-factor={scale}")
            shell = await app.page("app://shell/index.html")
            await shell.wait("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 20)
            await asyncio.sleep(1)
            m = await shell.js(LAYOUT)
            app.shot(f"scale-{res}-{int(scale * 100)}")
            check(f"{res} @{int(scale * 100)} %: мастер без горизонтальной прокрутки, кнопки в окне, масштаб применён",
                  ok and m["dpr"] == scale and m["overflow"] <= 0 and not m["outside"], json.dumps(m, ensure_ascii=False))
            await shell.js("document.querySelector('.side [aria-label=\"Настройки\"]')?.click() || [...document.querySelectorAll('button')].find(b => b.textContent.includes('Настройки'))?.click()")
            await asyncio.sleep(1)
            m = await shell.js(LAYOUT)
            app.shot(f"scale-{res}-{int(scale * 100)}-settings")
            check(f"{res} @{int(scale * 100)} %: настройки без горизонтальной прокрутки", m["overflow"] <= 0 and not m["outside"], json.dumps(m, ensure_ascii=False))
            await app.stop("TERM")
    finally:
        subprocess.run(["docker", "rm", "-f", E.C], capture_output=True)
        srv.terminate()
    bad = [r for r in results if not r[1]]
    print(f"ИТОГО: {len(results) - len(bad)} из {len(results)}; не прошли: {[b[0] for b in bad]}")


if __name__ == "__main__":
    asyncio.run(main())
