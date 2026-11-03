"""Сценарий: установленный из репозитория APT пакет Peregovorka для Linux в чистой системе (контейнер Ubuntu/Debian, сеть хоста) против живого стенда.

Не часть CI: нужен стенд по HTTPS (~/pg-work/restart_dev_tls.sh: https://127.0.0.1:8443 и wss://127.0.0.1:7443, тестовый центр сертификации),
репозиторий APT (каталог сайта с подписанными индексами, make-repo.sh + sign-repo.sh) и образ испытательной системы (desktop/linux/test/e2e.Dockerfile).
Программа — настоящая установленная (/usr/bin/peregovorka, песочница Chromium включена), управление — как у человека: через окна (CDP),
клавиатуру и мышь (xdotool), снимки экрана — X-сервера испытательной системы. Камера и микрофон — поддельные устройства Chromium (в контейнере их нет).

Запуск: APT_SITE=<сайт v1> [APT_SITE2=<сайт v1+v2> V2=<версия>] E2E_IMAGE=pg-linux-e2e:ubuntu-24.04 BROWSER_PATH=… BROWSER_FLAGS="--no-sandbox --ignore-certificate-errors" \
        python3 tests/live/linux_pkg_e2e.py
"""
import asyncio, json, os, subprocess, sys, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import smoke_room as S
from desktop_lib import Cdp, check, results

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMG = os.environ.get("E2E_IMAGE", "pg-linux-e2e:ubuntu-24.04")
SITE, SITE2, V2 = os.environ["APT_SITE"], os.environ.get("APT_SITE2"), os.environ.get("V2")
URL = "https://127.0.0.1:8443"
ROOM = "/rooms/pm"                      # в этой комнате вложения в чате разрешены
REPO_PORT, CDP, DISP = int(os.environ.get("REPO_PORT", "18790")), int(os.environ.get("CDP_PORT", "9350")), int(os.environ.get("E2E_DISPLAY", "77"))
C = f"pg-linux-e2e-{os.getpid()}"
OUT = os.environ.get("SMOKE_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
SHARE = os.path.join(OUT, "linux-share"); os.makedirs(SHARE, exist_ok=True)
S.ROOM = ROOM
SHARING = "!!document.querySelector('button[aria-label=\"Остановить показ\"]')"


def sh(cmd, user=True, check_rc=False, timeout=600):
    r = subprocess.run(["docker", "exec", *(["-u", "tester", "-e", "HOME=/home/tester"] if user else []), C, "bash", "-c", cmd], capture_output=True, text=True, timeout=timeout)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"{cmd}: {r.stderr[-500:]}")
    return r.stdout.strip()


def xenv():
    return f"export DISPLAY=:{DISP}; . ~/.dbus-env 2>/dev/null; export DBUS_SESSION_BUS_ADDRESS;"


def mainwin():
    """Главное окно программы — самое большое из её видимых окон (индикатор встречи и окно выбора экрана меньше)."""
    return sh(f"{xenv()} for w in $(xdotool search --class peregovorka); do g=$(xdotool getwindowgeometry $w 2>/dev/null | awk '/Geometry/{{split($2,a,\"x\"); print a[1]*a[2]}}'); echo \"${{g:-0}} $w\"; done | sort -n | tail -1 | cut -d' ' -f2")


class PkgApp:
    """Установленная программа в испытательной системе (CDP — порт отладки Chromium; главный процесс извне недоступен — так и должно быть)."""
    def __init__(self):
        self.pages, self.seen = {}, set()

    async def start(self, res="1600x900", extra=""):
        subprocess.run(["docker", "exec", "-d", "-u", "tester", "-e", "HOME=/home/tester", C, "bash", "-c", f"/t/run-app.sh {DISP} {res} {CDP} {extra}"], check=True)
        for _ in range(120):
            try:
                if any(t.get("url", "").startswith("app://shell/index.html") for t in self.targets()):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.5)
        print(sh("tail -30 ~/app.log"))
        return False

    def targets(self):
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP}/json/list", timeout=15))

    async def page(self, prefix, timeout=20, fresh=False):
        end = time.time() + timeout
        while time.time() < end:
            try: targets = self.targets()
            except Exception: targets = []
            for t in targets:
                if t.get("type") == "page" and t.get("url", "").startswith(prefix):
                    if fresh and t["id"] in self.seen:
                        continue
                    self.seen.add(t["id"])
                    if t["id"] not in self.pages:
                        c = await Cdp.connect(t["webSocketDebuggerUrl"])
                        await c.send("Runtime.enable"); await c.send("Page.enable")
                        self.pages[t["id"]] = c
                    return self.pages[t["id"]]
            await asyncio.sleep(0.3)
        raise RuntimeError(f"нет окна {prefix}: {[t.get('url') for t in self.targets()]}")

    async def stop(self, signal="TERM"):
        for c in self.pages.values():
            await c.close()
        self.pages, self.seen = {}, set()
        # сигнал — только главному процессу (как при выходе из сеанса или kill от человека); процессы Chromium завершает он сам
        sh(f"pkill -{signal} -f '^/opt/Peregovorka/peregovorka --remote' || true")
        for _ in range(80):
            if not sh("pgrep -f '^/opt/Peregovorka/peregovorka' || true"):
                return True
            await asyncio.sleep(0.25)
        return False

    def shot(self, name):
        from PIL import ImageGrab
        p = os.path.join(OUT, f"linux-{name}.png")
        ImageGrab.grab(xdisplay=f"localhost:{DISP}").save(p)
        return p


async def login(page, user, password):
    for attempt in range(3):            # страница входа может перерисоваться между ожиданием и заполнением — тогда повтор
        await page.wait("!!document.querySelector('input[autocomplete=username]') || !!document.querySelector('.topbar .usermenu')", 40)
        if await page.js("!!document.querySelector('.topbar .usermenu')"):
            return True
        try:
            return await _login_once(page, user, password)
        except RuntimeError:
            await asyncio.sleep(2)
    return False


async def _login_once(page, user, password):
    await page.js(f"""(() => {{
      const set = (el, v) => {{ Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v); el.dispatchEvent(new Event('input', {{ bubbles: true }})); }};
      set(document.querySelector('input[autocomplete=username]'), {json.dumps(user)});
      set(document.querySelector('input[type=password]'), {json.dumps(password)});
      document.querySelector('form').requestSubmit();
    }})()""")
    return await page.wait("!!document.querySelector('.topbar .usermenu')", 40)


async def btn(page, label):
    return await page.js(f"(() => {{ const b = [...document.querySelectorAll('button')].find(x => (x.getAttribute('aria-label') || '') === {json.dumps(label)} || x.textContent.trim() === {json.dumps(label)}); if (!b) return false; b.click(); return true; }})()")


async def key(page, k, mods=0, code=None, vk=0):
    for t in ("rawKeyDown", "keyUp"):
        await page.send("Input.dispatchKeyEvent", type=t, key=k, code=code or k, modifiers=mods, windowsVirtualKeyCode=vk)


def port_free(port):
    import socket
    with socket.socket() as so:
        return so.connect_ex(("127.0.0.1", port)) != 0


async def main():
    assert port_free(REPO_PORT), f"порт репозитория {REPO_PORT} занят (остался сервер прошлого прогона?) — испытание не начато"
    assert port_free(CDP), f"порт отладки {CDP} занят — испытание не начато"
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "desktop/linux/test/faultserver.py"), os.path.join(OUT, "linux-repo-serve"), str(REPO_PORT)], stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    serve = os.path.join(OUT, "linux-repo-serve")
    def use(site):
        if os.path.islink(serve) or os.path.exists(serve): os.remove(serve)
        os.symlink(os.path.abspath(site), serve)
    use(SITE)
    subprocess.run(["docker", "run", "-d", "--init", "--name", C, "--network", "host", "--cap-add", "SYS_ADMIN", "--security-opt", "seccomp=unconfined", "--security-opt", "apparmor=unconfined",
                    "--shm-size", "1g", "-v", f"{ROOT}/desktop/linux/test:/t:ro", "-v", f"{ROOT}/desktop/linux/install.sh:/install.sh:ro", "-v", f"{os.path.expanduser('~/pg-work/tls')}:/tls:ro",
                    "-v", f"{SHARE}:/share", IMG, "sleep", "infinity"], check=True, capture_output=True)
    app = PkgApp()
    bob = S.Client("linux-bob", "bob", "bob-pass", 9661)
    try:
        osname = sh(". /etc/os-release; echo $PRETTY_NAME", user=False)
        print("===", osname)
        out = sh(f"PEREGOVORKA_APT_URL=http://127.0.0.1:{REPO_PORT} PEREGOVORKA_APT_INSECURE_TEST=1 sh /install.sh 2>&1 | tail -3", user=False, timeout=900)
        v1 = sh("dpkg-query -W -f '${Version}' peregovorka", user=False)
        check(f"установка из репозитория APT (install.sh): {v1}", bool(v1), out[-200:])

        # ---------------- первый запуск, мастер, вход
        check("запуск от обычного пользователя (песочница Chromium включена)", await app.start())
        shell = await app.page("app://shell/index.html")
        check("мастер первого запуска", await shell.wait("document.querySelector('h1')?.textContent === 'Добро пожаловать в Peregovorka'", 20))
        check("текст мастера — для Linux (уведомления рабочего стола)", "рабочего стола" in (await shell.js("document.querySelector('.lead')?.textContent || ''")))
        app.shot("01-welcome")
        wm = sh(f"{xenv()} xprop -id {mainwin()} WM_CLASS")
        desk = sh("grep ^StartupWMClass= /usr/share/applications/peregovorka.desktop | cut -d= -f2")
        check("класс окна совпадает со StartupWMClass .desktop (значок в доке, группировка окон)", f'"{desk}"' in wm, f"{wm} / {desk}")
        await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.includes('Добавить организацию')).click()")
        await shell.wait("!!document.querySelector('#srv')", 5)
        await shell.js("document.querySelector('#nm').value = 'Стенд Linux'")
        await shell.js(f"(() => {{ document.querySelector('#srv').value = {json.dumps(URL)}; document.querySelector('form').requestSubmit(); }})()")
        pa = await app.page(URL, 40)
        check("организация подключена по HTTPS (сертификат внутреннего центра — из NSS пользователя)", True)
        check("вход alice", await login(pa, "alice", "alice-pass"))
        app.shot("02-org")

        # ---------------- комната: звук, видео, показ экрана
        await pa.js(f"location.assign('{ROOM}')")
        await pa.wait("!!document.querySelector('.pj-actions .btn.primary')", 40)
        for _ in range(3):
            await pa.js("document.querySelector('.pj-actions .btn.primary')?.click()")
            if await pa.wait("document.querySelectorAll('.tile').length > 0", 12):
                break
        await bob.start(); check("браузер bob: вошёл в комнату", await bob.join())
        check("двое во встрече (в приложении и в браузере)", await pa.wait("document.querySelectorAll('.tile').length >= 2", 40) and await bob.wait_for("document.querySelectorAll('.tile').length >= 2", 40))
        if await pa.js("!!document.querySelector('button[aria-label=\"Микрофон выкл.\"]')"): await btn(pa, "Микрофон выкл.")
        await btn(pa, "Камера выкл."); await asyncio.sleep(5)
        st = await bob.state()
        heard = [a for a in (st or {}).get("audio", []) if a["live"]]
        check("bob слышит приложение (живая звуковая дорожка)", len(heard) >= 1, json.dumps(st.get("audio") if st else None))
        vid = await bob.js("(async () => { const v = [...document.querySelectorAll('.tile video')].filter(x => x.videoWidth > 0); const q = () => v.map(x => x.getVideoPlaybackQuality().totalVideoFrames); const a = q(); await new Promise(r => setTimeout(r, 1500)); const b = q(); return v.filter((x, i) => b[i] > a[i]).map(x => x.videoWidth + 'x' + x.videoHeight); })()")
        check("bob видит камеру приложения (кадры идут)", len(vid) >= 1, str(vid))
        got = await pa.js("(async () => { const a = [...document.querySelectorAll('audio')].filter(x => x.srcObject && x.srcObject.getAudioTracks().some(t => t.readyState === 'live')); return a.length; })()")
        check("приложение получает звук bob", got >= 1, str(got))
        await pa.js("[...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || '') === 'Показать экран')?.click()")
        picker = await app.page("app://shell/picker.html", 20, fresh=True)
        check("показ экрана: окно выбора (X11) с миниатюрами", await picker.wait("document.querySelectorAll('.src').length > 0", 15))
        app.shot("03-picker")
        await picker.js("setTimeout(() => document.querySelector('footer .btn.primary').click(), 0)")
        check("показ экрана идёт", await pa.wait(SHARING, 20))
        await asyncio.sleep(4)
        scr = await bob.js("(async () => { const v = [...document.querySelectorAll('.screen-stage video')]; const q = () => v.map(x => x.getVideoPlaybackQuality().totalVideoFrames); const a = q(); await new Promise(r => setTimeout(r, 1500)); const b = q(); return v.filter((x, i) => x.videoWidth > 0 && b[i] > a[i]).map(x => x.videoWidth + 'x' + x.videoHeight); })()")
        check("bob видит экран приложения (кадры идут)", len(scr) >= 1, str(scr))
        await bob.shot("linux-app-screen")
        app.shot("04-call")

        # ---------------- полноэкранный режим, Esc
        await pa.send("Runtime.evaluate", expression="document.documentElement.requestFullscreen()", userGesture=True, awaitPromise=True)
        await asyncio.sleep(1.5)
        geo = sh(f"{xenv()} xdotool getwindowgeometry {mainwin()} | tail -1")
        check("полноэкранный режим: окно на весь экран", await pa.js("!!document.fullscreenElement") and "1600x900" in geo, geo)
        sh(f"{xenv()} xdotool key --window {mainwin()} Escape"); await asyncio.sleep(1)        # настоящее событие клавиатуры X, как у человека
        check("Esc выходит из полноэкранного режима", not await pa.js("!!document.fullscreenElement"))
        await btn(pa, "Остановить показ"); await asyncio.sleep(1)

        # ---------------- чат и файл
        await btn(pa, "Чат"); await asyncio.sleep(1)
        open(os.path.join(SHARE, "отчёт о встрече.txt"), "w", encoding="utf-8").write("Проверка вложения из Linux\n")
        doc = await pa.send("DOM.getDocument", depth=-1)
        node = await pa.send("DOM.querySelector", nodeId=doc["root"]["nodeId"], selector=".chat-box input[type=file]")
        await pa.send("DOM.setFileInputFiles", files=["/share/отчёт о встрече.txt"], nodeId=node["nodeId"])
        up = await pa.wait("[...document.querySelectorAll('.chat-pend')].some(x => x.textContent.includes('загружено'))", 30)
        check("файл прикреплён и загружен на сервер", up, json.dumps({"node": node.get("nodeId"), "inputs": await pa.js("document.querySelectorAll('input[type=file]').length"),
              "compose": await pa.js("(document.querySelector('.chat-compose')?.innerText || 'нет панели чата').slice(0, 200)")}, ensure_ascii=False))
        await pa.js("(() => { const t = document.querySelector('textarea[aria-label=\"Текст сообщения\"]'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(t, 'Привет из Linux'); t.dispatchEvent(new Event('input', { bubbles: true })); })()")
        await asyncio.sleep(0.3)
        await btn(pa, "Отправить сообщение")
        await bob.click_btn("Чат")
        check("bob получил сообщение и файл", await bob.wait_for("document.querySelector('.chat-list')?.innerText.includes('Привет из Linux') && document.querySelector('.chat-list')?.innerText.includes('отчёт о встрече')", 20))

        # ---------------- буфер обмена (Ctrl+C / Ctrl+V)
        await pa.js("(() => { const t = document.querySelector('textarea[aria-label=\"Текст сообщения\"]'); Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(t, 'буфер-обмена-42'); t.dispatchEvent(new Event('input', { bubbles: true })); t.focus(); t.select(); })()")
        await key(pa, "c", mods=2, code="KeyC", vk=67); await asyncio.sleep(0.5)
        check("Ctrl+C: текст в системном буфере обмена", sh(f"{xenv()} xclip -o -selection clipboard 2>/dev/null") == "буфер-обмена-42")
        sh(f"{xenv()} printf 'вставлено-из-системы' | xclip -i -selection clipboard")
        await pa.js("(() => { const t = document.querySelector('textarea[aria-label=\"Текст сообщения\"]'); t.focus(); t.select(); })()")
        await key(pa, "v", mods=2, code="KeyV", vk=86); await asyncio.sleep(0.5)
        check("Ctrl+V: текст из системного буфера вставлен", "вставлено-из-системы" in (await pa.js("document.querySelector('textarea[aria-label=\"Текст сообщения\"]').value")))

        # ---------------- свернуть во время встречи: индикатор поверх окон; уведомление о сообщении
        mw = mainwin(); sh(f"{xenv()} xdotool windowminimize {mw}")
        await asyncio.sleep(2)
        ind = [t for t in app.targets() if t.get("url", "").startswith("app://shell/indicator.html")]
        check("свёрнуто во время встречи — индикатор встречи поверх окон", len(ind) == 1, sh(f"{xenv()} xprop -id {mw} _NET_WM_STATE WM_STATE; xdotool getwindowgeometry {mw}") + " vis=" + str(await shell.js("document.visibilityState")))
        # личное сообщение в корпоративном чате (уведомления рабочего стола — о чатах и встречах; objectGUID alice — со стенда devserver_live.py)
        dm = await bob.js("""(async () => { const me = await (await fetch('/api/v1/auth/me')).json(); const h = {'content-type': 'application/json', 'X-CSRF-Token': me.csrf_token};
          const c = await (await fetch('/api/v1/chats/direct', {method: 'POST', headers: h, body: JSON.stringify({ref: '11111111-1111-4111-8111-111111111111', login: 'alice'})})).json();
          const r = await fetch('/api/v1/chats/' + c.id + '/messages', {method: 'POST', headers: h, body: JSON.stringify({text: 'Алиса, ответьте по договору', client_id: crypto.randomUUID()})});
          return r.status; })()""")
        await asyncio.sleep(5)
        note = sh("cat ~/notify.log")
        check("уведомление рабочего стола о личном сообщении (Notify через D-Bus, libnotify)", "ответьте по договору" in note, f"отправка {dm}; " + note[-300:])
        app.shot("05-minimized")
        # ---------------- значок в трее (StatusNotifierItem): зарегистрирован у «хозяина» значков, щелчок по нему разворачивает окно
        reg = sh("tail -1 ~/tray.log 2>/dev/null")
        check("значок в трее зарегистрирован (StatusNotifierWatcher, как в KDE и GNOME с AppIndicator)", bool(reg), reg)
        if reg:
            dest, path = reg.split()[:2]
            title = sh(f"{xenv()} dbus-send --session --print-reply --dest={dest} {path} org.freedesktop.DBus.Properties.Get string:org.kde.StatusNotifierItem string:ToolTip; "
                       f"dbus-send --session --print-reply --dest={dest} {path} org.freedesktop.DBus.Properties.Get string:org.kde.StatusNotifierItem string:Title")
            check("значок: подсказка и название — Peregovorka, идёт встреча", "Peregovorka" in title and "встреча" in title, title[-200:])
            sh(f"{xenv()} dbus-send --session --type=method_call --dest={dest} {path} org.kde.StatusNotifierItem.Activate int32:0 int32:0")
        else:
            sh(f"{xenv()} xdotool windowactivate {mainwin()} 2>/dev/null || true")
        await asyncio.sleep(2)
        check("щелчок по значку в трее разворачивает окно", await shell.wait("document.visibilityState === 'visible'", 10))
        ind = [t for t in app.targets() if t.get("url", "").startswith("app://shell/indicator.html")]
        check("окно восстановлено — индикатор убран", len(ind) == 0)

        # ---------------- автозапуск и настройки
        await btn(pa, "Выйти"); await asyncio.sleep(2)
        await shell.js("document.querySelector('.side [aria-label=\"Настройки\"]').click()")
        await shell.wait("!!document.querySelector('#tab-general')", 5)
        await shell.js("[...document.querySelectorAll('.seg button, button')].find(b => b.textContent.trim() === 'Свёрнутым в трей')?.click()")
        await asyncio.sleep(1)
        auto = sh("cat ~/.config/autostart/peregovorka.desktop 2>/dev/null")
        check("автозапуск «свёрнутым в трей» — ~/.config/autostart/peregovorka.desktop с --hidden", "Exec=peregovorka --hidden" in auto, auto)
        await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Тёмное')?.click()")
        await shell.js("document.querySelector('#tab-about')?.click()"); await asyncio.sleep(0.5)
        check("«О программе»: Peregovorka для Linux", "Peregovorka для Linux" in (await shell.js("document.querySelector('main').innerText")))
        await shell.js("document.querySelector('#tab-updates')?.click()"); await asyncio.sleep(0.5)
        upd = await shell.js("document.querySelector('main').innerText")
        check("«Обновления»: через систему (apt), без своего установщика", "sudo apt update && sudo apt upgrade" in upd and "Скачать обновление" not in upd, upd[-200:])
        await shell.js("document.querySelector('#tab-diag')?.click()")
        await shell.wait("document.querySelector('main').innerText.includes('Хранилище входа')", 15)
        diag = await shell.js("document.querySelector('main').innerText")
        check("диагностика: вход защищён связкой ключей рабочего стола", "защищено связкой ключей" in diag, diag[-300:])
        app.shot("06-settings")

        # ---------------- завершение процесса и повторный запуск: организация, вход и настройки сохранились
        check("завершение процесса (SIGTERM)", await app.stop("TERM"))
        check("повторный запуск", await app.start())
        shell = await app.page("app://shell/index.html")
        pa = await app.page(URL, 40)
        check("после перезапуска организация на месте и вход сохранён (токен устройства из связки ключей)", await pa.wait("!!document.querySelector('.topbar .usermenu')", 40))
        check("после перезапуска настройки сохранены (тема «тёмное»)", json.loads(sh("cat ~/.config/Peregovorka/settings.json")).get("theme") == "dark")
        check("аварийное завершение (SIGKILL) и запуск снова", await app.stop("KILL") and await app.start())
        pa = await app.page(URL, 40)
        check("после аварийного завершения — вход сохранён", await pa.wait("!!document.querySelector('.topbar .usermenu')", 40))
        shell = await app.page("app://shell/index.html")

        # ---------------- потеря сети: TLS-прокси стенда остановлен на 20 с
        subprocess.run(["bash", "-c", "kill $(ps -eo pid,args | grep '[t]ls_proxy.py' | awk '{print $1}')"])
        await asyncio.sleep(20)
        off = await shell.js("[...document.querySelectorAll('.side [title], .side .org')].map(x => x.getAttribute('title') || x.textContent).join(' | ')")
        subprocess.Popen(["python3", os.path.join(ROOT, "tests/live/tls_proxy.py"), os.path.expanduser("~/pg-work/tls/srv.crt"), os.path.expanduser("~/pg-work/tls/srv.key"), "8443=5183", "7443=7880"],
                         stdout=open(os.path.expanduser("~/pg-work/tls-proxy.log"), "a"), stderr=subprocess.STDOUT, start_new_session=True)
        back = await pa.wait("fetch('/api/v1/auth/me').then(r => r.ok).catch(() => false)", 60)
        check("после возврата сети приложение снова на связи с сервером без перезапуска", back, off[:200])

        # ---------------- обновление через apt update && apt upgrade при запущенной программе
        # ---------------- источник обновлений недоступен: apt update с ошибкой, работающая программа не замечает и не ломается
        use(os.path.join(OUT, "linux-repo-missing"))
        out = sh("apt-get update 2>&1 | tail -3; echo rc=${PIPESTATUS[0]}", user=False, timeout=300)
        alive = await pa.wait("fetch('/api/v1/auth/me').then(r => r.ok).catch(() => false)", 20)
        st = await shell.js("window.pg.call('shell:state').then(s => s.update.status)")
        check("репозиторий недоступен: apt update сообщает об ошибке, программа работает, обновление не «сломано»",
              "rc=0" not in out and alive and st != "error", f"{out[-200:]} status={st}")
        if SITE2 and V2:
            use(SITE2)
            out = sh("apt-get update -qq && apt-get upgrade -y -qq 2>&1 | tail -2", user=False, timeout=900)
            check(f"apt update && apt upgrade: {v1} → {V2}", sh("dpkg-query -W -f '${Version}' peregovorka", user=False) == V2, out)
            check("работающая программа сообщила: «установлена новая версия — перезапустите»", await shell.wait(
                "window.pg.call('shell:state').then(s => s.update.status === 'restart')", 90))
            await shell.js("document.querySelector('.side [aria-label=\"Настройки\"]').click()"); await asyncio.sleep(0.5)
            await shell.js("document.querySelector('#tab-updates')?.click()"); await asyncio.sleep(0.5)
            app.shot("07-restart-needed")
            await shell.js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === 'Перезапустить')?.click()")
            await asyncio.sleep(6)
            app.pages, app.seen = {}, set()
            shell = await app.page("app://shell/index.html", 40)
            ver = await shell.js("window.pg.call('shell:state').then(s => s.version)")
            check(f"«Перезапустить»: работает новая версия {V2}, организация и вход на месте", ver == V2 and await (await app.page(URL, 40)).wait("!!document.querySelector('.topbar .usermenu')", 40), str(ver))
    finally:
        for step in (bob.close, lambda: app.stop("TERM")):
            try: await step()
            except Exception: pass
        if not os.environ.get("E2E_KEEP"): subprocess.run(["docker", "rm", "-f", C], capture_output=True)
        else: print("испытательная система оставлена:", C)
        srv.terminate()
    failed = [r for r in results if not r[1]] + [r for r in S.results if not r[1]]
    print(f"ИТОГО: {len(results) + len(S.results) - len(failed)} из {len(results) + len(S.results)}; не прошли: {[r[0] for r in failed]}")


if __name__ == "__main__":
    asyncio.run(main())
