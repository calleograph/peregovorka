"""Сценарий: установленный пакет Peregovorka в сеансе Wayland (sway без экрана, PipeWire, XDG Desktop Portal wlr) — показ экрана через портал.

Не часть CI: нужны стенд по HTTPS (~/pg-work/restart_dev_tls.sh), репозиторий APT (APT_SITE) и образ desktop/linux/test/wayland.Dockerfile.
Проверяется: программа работает родным клиентом Wayland (не через XWayland), app_id окна совпадает с именем .desktop, вход и встреча, камера,
показ экрана: выбор делает системный портал (своё окно выбора не открывается), PipeWire отдаёт кадры, участник в браузере их видит.
В настоящей системе в окне портала экран выбирает человек; здесь портал настроен выбирать экран сам (chooser_type=none).
"""
import asyncio, json, os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
import linux_pkg_e2e as E
from desktop_lib import check, results

E.IMG = os.environ.get("E2E_IMAGE", "pg-linux-wayland:ubuntu-24.04")
SHARING = E.SHARING


async def main():
    assert E.port_free(E.REPO_PORT) and E.port_free(E.CDP), "порт репозитория или отладки занят — испытание не начато"
    serve = os.path.join(E.OUT, "linux-repo-serve")
    if os.path.lexists(serve): os.remove(serve)
    os.symlink(os.path.abspath(E.SITE), serve)
    srv = subprocess.Popen([sys.executable, os.path.join(E.ROOT, "desktop/linux/test/faultserver.py"), serve, str(E.REPO_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    subprocess.run(["docker", "run", "-d", "--init", "--name", E.C, "--network", "host", "--cap-add", "SYS_ADMIN", "--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined",
                    "--shm-size", "1g", "-v", f"{E.ROOT}/desktop/linux/test:/t:ro", "-v", f"{E.ROOT}/desktop/linux/install.sh:/install.sh:ro", "-v", f"{os.path.expanduser('~/pg-work/tls')}:/tls:ro",
                    "-v", f"{E.SHARE}:/share", E.IMG, "sleep", "infinity"], check=True, capture_output=True)
    app = E.PkgApp()
    bob = S.Client("wl-bob", "bob", "bob-pass", 9671)
    async def start():
        subprocess.run(["docker", "exec", "-d", "-u", "tester", "-e", "HOME=/home/tester", E.C, "bash", "-c", f"/t/run-app-wayland.sh 1600x900 {E.CDP}"], check=True)
        for _ in range(120):
            try:
                if any(t.get("url", "").startswith("app://shell/index.html") for t in app.targets()): return True
            except Exception: pass
            await asyncio.sleep(0.5)
        print(E.sh("tail -20 ~/app.log; tail -5 /tmp/sway.log"))
        return False
    def grim(name):
        E.sh(f". ~/.wayland-env; export WAYLAND_DISPLAY XDG_RUNTIME_DIR; grim /share/wayland-{name}.png")
        return os.path.join(E.SHARE, f"wayland-{name}.png")
    try:
        print("===", E.sh(". /etc/os-release; echo $PRETTY_NAME", user=False), "(Wayland)")
        out = E.sh(f"PEREGOVORKA_APT_URL=http://127.0.0.1:{E.REPO_PORT} PEREGOVORKA_APT_INSECURE_TEST=1 sh /install.sh 2>&1 | tail -1", user=False, timeout=900)
        check("установка из репозитория APT", bool(E.sh("dpkg-query -W -f '${Version}' peregovorka", user=False)), out[-160:])
        check("запуск в сеансе Wayland", await start())
        oz = E.sh("ps -eo args | grep -o -- '--ozone-platform=[a-z0-9]*' | sort -u")
        check("родной клиент Wayland (не XWayland)", "--ozone-platform=wayland" in oz and "x11" not in oz, oz)
        for _ in range(20):           # окно появляется у композитора чуть позже, чем страница оболочки
            if '"app_id": "peregovorka"' in E.sh(". ~/.wayland-env; export WAYLAND_DISPLAY XDG_RUNTIME_DIR SWAYSOCK=$(ls $XDG_RUNTIME_DIR/sway-ipc.*.sock | head -1); swaymsg -t get_tree"): break
            await asyncio.sleep(0.5)
        tree = E.sh(". ~/.wayland-env; export WAYLAND_DISPLAY XDG_RUNTIME_DIR SWAYSOCK=$(ls $XDG_RUNTIME_DIR/sway-ipc.*.sock | head -1); swaymsg -t get_tree | grep -o '\"app_id\": \"[^\"]*\"' | sort -u")
        check("app_id окна — peregovorka (как имя .desktop: значок и группировка в доке)", '"app_id": "peregovorka"' in tree, tree)
        shell = await app.page("app://shell/index.html")
        await shell.wait("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 20)
        grim("01-welcome")
        await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')).click()"); await asyncio.sleep(1)
        await shell.js(f"(() => {{ document.querySelector('#srv').value = {json.dumps(E.URL)}; document.querySelector('form').requestSubmit(); }})()")
        pa = await app.page(E.URL, 40)
        check("вход alice", await E.login(pa, "alice", "alice-pass"))
        await pa.js(f"location.assign('{E.ROOM}')"); await pa.wait("!!document.querySelector('.pj-actions .btn.primary')", 40)
        for _ in range(3):
            await pa.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            if await pa.wait("document.querySelectorAll('.tile').length > 0", 12): break
        await bob.start(); check("браузер bob в комнате", await bob.join())
        check("двое во встрече", await pa.wait("document.querySelectorAll('.tile').length >= 2", 40))
        await E.btn(pa, "Камера выкл."); await asyncio.sleep(4)
        vid = await bob.js("(async () => { const v = [...document.querySelectorAll('.tile video')].filter(x => x.videoWidth > 0); const q = () => v.map(x => x.getVideoPlaybackQuality().totalVideoFrames); const a = q(); await new Promise(r => setTimeout(r, 1500)); const b = q(); return v.filter((x, i) => b[i] > a[i]).length; })()")
        check("bob видит камеру приложения", vid >= 1, str(vid))
        before = {t["id"] for t in app.targets()}
        await pa.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || '') === 'Показать экран')?.click()")
        ok = await pa.wait(SHARING, 30)
        pickers = [t for t in app.targets() if t["id"] not in before and t.get("url", "").startswith("app://shell/picker.html")]
        portal = E.sh("cat /tmp/xdpw.log")
        check("показ экрана: программа обращается к системному порталу ScreenCast (своё окно выбора не открывается)", "start method invoked" in portal and not pickers,
              f"pickers={len(pickers)}; {portal.strip().splitlines()[-1][:160] if portal.strip() else 'журнал портала пуст'}")
        if "unable to receive a valid format" in portal:
            print("СВЕДЕНИЯ: кадры экрана не проверены — композитор sway без видеокарты не отдаёт кадры порталу (wlr_screencopy: нет допустимого формата); это ограничение испытательной среды, не программы")
        else:
            await asyncio.sleep(5)
            scr = await bob.js("(async () => { const v = [...document.querySelectorAll('.screen-stage video')]; const q = () => v.map(x => x.getVideoPlaybackQuality().totalVideoFrames); const a = q(); await new Promise(r => setTimeout(r, 2000)); const b = q(); return v.filter((x, i) => x.videoWidth > 0 && b[i] > a[i]).map(x => x.videoWidth + 'x' + x.videoHeight); })()")
            check("показ экрана из Wayland идёт, bob видит кадры (PipeWire → WebRTC)", ok and len(scr) >= 1, str(scr))
            grim("02-sharing"); await bob.shot("wayland-screen")
            await E.btn(pa, "Остановить показ")
            check("показ завершён", await pa.wait(f"!{SHARING}", 15))
        if not ok:
            await asyncio.sleep(2)
            logged = E.sh("grep -c 'share.sources_failed.*\"portal\":true' ~/.config/Peregovorka/logs/main.log || true")
            wins = E.sh(". ~/.wayland-env; export WAYLAND_DISPLAY XDG_RUNTIME_DIR SWAYSOCK=$(ls $XDG_RUNTIME_DIR/sway-ipc.*.sock | head -1); swaymsg -t get_tree | grep -c '\"app_id\": \"peregovorka\"'")
            check("сбой портала — сообщение «Показ экрана недоступен» с причиной (отдельное окно), встреча продолжается",
                  logged.strip() not in ("", "0") and int(wins or 0) >= 2 and await pa.js("document.querySelectorAll('.tile').length >= 2"), f"журнал={logged} окон={wins}")
    finally:
        for step in (bob.close, lambda: app.stop("TERM")):
            try: await step()
            except Exception: pass
        if not os.environ.get("E2E_KEEP"): subprocess.run(["docker", "rm", "-f", E.C], capture_output=True)
        else: print("испытательная система оставлена:", E.C)
        srv.terminate()
    failed = [r for r in results if not r[1]] + [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(results) + len(S.results) - len(failed)} из {len(results) + len(S.results)}; не прошли: {[r[0] for r in failed]}")


if __name__ == "__main__":
    asyncio.run(main())
