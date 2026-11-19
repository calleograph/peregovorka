"""Загрузка образа при сбое CDN основного реестра (сценарий из реальной эксплуатации) — на настоящем Docker и настоящих реестрах.

Что происходит: поднимаются два настоящих реестра `registry:2`. В «зеркале» (порт MIRROR) лежит образ; «основной» (порт PRIMARY) — прокси, который отдаёт индекс и манифесты
(реестр доступен, как Docker Hub с ответом 401/200 на /v2/), но слои (blobs) не отдаёт: заголовки ответа не приходят — Docker сообщает «timeout awaiting response headers».
Дальше выполняется НАСТОЯЩИЙ img_ensure из scripts/lib/imglib.sh: повторы, переход на разрешённое зеркало, проверка дайджеста и архитектуры, запись в .env, и затем
`docker compose config` / `docker run` показывают, что именно этот образ используется без обращения к основному реестру.

Дальше: «плохое» зеркало с другим образом под тем же именем (отклоняется по дайджесту), PostgreSQL через POSTGRES_IMAGE в compose, и сборка docker compose build
с базовым образом из зеркала аргументом --build-arg при выключенных основном реестре и зеркале (образ берётся с сервера).

Запуск: python3 tests/live/image_mirror.py    (нужны docker и образ registry:2; ~5 минут; ничего чужого не трогает, всё убирает за собой)
"""
import http.client, http.server, json, os, shutil, socketserver, subprocess, sys, tempfile, threading, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PRIMARY, MIRROR = int(os.environ.get("PG_PRIMARY_PORT", "35600")), int(os.environ.get("PG_MIRROR_PORT", "35601"))
BAD = int(os.environ.get("PG_BAD_PORT", "35602"))          # «плохое» зеркало: под тем же именем лежит ДРУГОЙ образ
TAG = f"pgmirror{os.getpid()}"
results = []


def check(name, ok, info=""):
    results.append((name, bool(ok)))
    print(("OK   " if ok else "FAIL ") + name + (f"  [{str(info)[:240]}]" if info else ""), flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True, **kw)


class Proxy(http.server.BaseHTTPRequestHandler):
    """Основной реестр: манифесты и /v2/ — как есть, слои не отдаёт (ответ не приходит)."""
    calls = {"manifests": 0, "blobs": 0}
    down = False                               # реестр недоступен целиком (503 на любой запрос)
    default_request_version = "HTTP/1.1"       # иначе на запрос TLS-рукопожатия отвечает «HTTP/0.9» и Docker не переходит на http для localhost

    def log_message(self, *a): pass

    def do_GET(self):
        if Proxy.down:
            self.send_response(503); self.send_header("Content-Length", "0"); self.end_headers(); return
        if "/blobs/" in self.path:
            Proxy.calls["blobs"] += 1
            time.sleep(120)        # заголовки ответа не приходят → таймаут у клиента
            return
        if "/manifests/" in self.path: Proxy.calls["manifests"] += 1
        c = http.client.HTTPConnection("127.0.0.1", MIRROR, timeout=30)
        c.request("GET", self.path, headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
        r = c.getresponse(); body = r.read()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in ("transfer-encoding", "connection"): self.send_header(k, v)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    do_HEAD = do_GET


def push_image(port, repo, tag):
    """Публикует в реестр образ, которого нет в локальном хранилище Docker (иначе pull обошёлся бы без слоёв): слой и конфигурация собираются здесь, манифест кладётся по HTTP API."""
    import gzip, hashlib, io, tarfile
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        data = os.urandom(2_000_000); ti = tarfile.TarInfo("payload.bin"); ti.size = len(data); tf.addfile(ti, io.BytesIO(data))
    tar = raw.getvalue(); layer = gzip.compress(tar)
    cfg = json.dumps({"architecture": "amd64", "os": "linux", "config": {}, "rootfs": {"type": "layers", "diff_ids": ["sha256:" + hashlib.sha256(tar).hexdigest()]}}).encode()
    def blob(b):
        d = "sha256:" + hashlib.sha256(b).hexdigest()
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
        c.request("POST", f"/v2/{repo}/blobs/uploads/"); r = c.getresponse(); r.read(); loc = r.getheader("Location")
        c.request("PUT", loc + ("&" if "?" in loc else "?") + "digest=" + d, body=b, headers={"Content-Type": "application/octet-stream"}); r = c.getresponse(); r.read()
        assert r.status == 201, r.status
        return d
    man = json.dumps({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                      "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": blob(cfg), "size": len(cfg)},
                      "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": blob(layer), "size": len(layer)}]}).encode()
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    c.request("PUT", f"/v2/{repo}/manifests/{tag}", body=man, headers={"Content-Type": "application/vnd.oci.image.manifest.v1+json"}); r = c.getresponse(); r.read()
    assert r.status == 201, r.status
    return "sha256:" + hashlib.sha256(man).hexdigest()


class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def main():
    tmp = tempfile.mkdtemp(prefix="pgimg-")
    reg = f"pgmirror-reg-{os.getpid()}"; bad = f"pgmirror-bad-{os.getpid()}"; built = f"pgimgtest-build:{os.getpid()}"
    try:
        r = sh(f"docker run -d --rm --name {reg} -p 127.0.0.1:{MIRROR}:5000 registry:2")
        check("поднят настоящий реестр-зеркало (registry:2)", r.returncode == 0, r.stderr)
        for _ in range(30):
            if sh(f"curl -fs http://127.0.0.1:{MIRROR}/v2/").returncode == 0: break
            time.sleep(1)
        digest = push_image(MIRROR, "library/probe", TAG)
        check("образ опубликован в зеркале (его слоёв нет в локальном хранилище Docker), получен дайджест", digest.startswith("sha256:"), digest)
        srv = TS(("127.0.0.1", PRIMARY), Proxy); threading.Thread(target=srv.serve_forever, daemon=True).start()

        primary_ref = f"127.0.0.1:{PRIMARY}/library/probe:{TAG}@{digest}"
        mirror_ref = f"127.0.0.1:{MIRROR}/library/probe@{digest}"
        sh(f"docker rmi {mirror_ref} >/dev/null 2>&1")
        direct = sh(f"timeout 45 docker pull {primary_ref}")
        check("основной реестр доступен, но слои приходят с таймаутом (воспроизведена реальная ошибка: pull не завершается)", direct.returncode != 0, direct.stderr[-300:])

        env = os.path.join(tmp, ".env"); data = os.path.join(tmp, "data"); os.makedirs(data)
        open(env, "w").write(f"COMPOSE_PROJECT_NAME=pgimgtest\nDATA_ROOT={data}\nVIDEO_ENABLED=yes\n")
        script = f"""set -u; export IMAGE_PULL_ATTEMPTS=2 IMAGE_PULL_PAUSE=1 IMAGE_PULL_TIMEOUT=45 IMAGE_MIRRORS='127.0.0.1:{PRIMARY}=127.0.0.1:{MIRROR}' ENV_FILE='{env}'
source '{ROOT}/scripts/lib/common.sh'; load_env "$ENV_FILE"; DATA_ROOT='{data}'
img_ensure egress VIDEO_IMAGE '{primary_ref}'; echo "RC=$?"; echo "VIDEO_IMAGE=${{VIDEO_IMAGE:-}}"
"""
        t0 = time.time(); out = sh(["bash", "-c", script]); dt = time.time() - t0
        print(out.stdout[-1500:], out.stderr[-1500:])
        check("img_ensure: перешёл на зеркало и вернул успех", "RC=0" in out.stdout and "зеркала" in (out.stdout + out.stderr), f"{dt:.0f} с")
        check("основной реестр получил запросы манифестов и слоёв (повторы), слои не пришли", Proxy.calls["blobs"] >= 2, Proxy.calls)
        envtxt = open(env).read()
        check(".env: VIDEO_IMAGE указывает на зеркало с тем же дайджестом", f"VIDEO_IMAGE={mirror_ref}" in envtxt.replace('"', ""), envtxt)
        check("образ действительно лежит локально под именем зеркала", sh(f"docker image inspect {mirror_ref}").returncode == 0)
        cfg = sh(f"docker compose -f {ROOT}/deployment/compose.yml --env-file {env} --profile video config --images 2>/dev/null | grep -c probe || true")
        # compose требует полный .env; достаточно показать подстановку image напрямую
        sub = sh(["bash", "-c", f"set -a; . '{env}'; set +a; echo \"${{VIDEO_IMAGE:-default}}\""])
        check("значение, которое получит compose (`image: ${VIDEO_IMAGE:-…}`), — образ зеркала", sub.stdout.strip() == mirror_ref, sub.stdout)
        run = sh(f"docker create --pull never {mirror_ref} /bin/true")
        check("контейнер создаётся из образа зеркала без обращения к реестрам (--pull never)", run.returncode == 0, run.stderr[-200:])
        if run.returncode == 0: sh(f"docker rm {run.stdout.strip()} >/dev/null 2>&1")

        calls_before = dict(Proxy.calls)
        out2 = sh(["bash", "-c", script.replace("img_ensure", "img_ensure")])
        check("повторный вызов: образ уже загружен — сеть не нужна (счётчики основного реестра не выросли)", Proxy.calls == calls_before and "RC=0" in out2.stdout, Proxy.calls)

        # новая закреплённая версия: зеркальное значение снимается, загрузка идёт с основного источника
        fake_new = f"127.0.0.1:{PRIMARY}/library/probe:{TAG}-new@sha256:{'e' * 64}"
        out3 = sh(["bash", "-c", script.replace(f"'{primary_ref}'", f"'{fake_new}'")])
        envtxt = open(env).read()
        check("при новой закреплённой версии зеркальный адрес снят с .env (возврат на основной источник)", f"VIDEO_IMAGE={mirror_ref}" not in envtxt.replace('"', ""), envtxt)
        check("загрузка новой версии начата с основного источника", "возвращаюсь к основному источнику" in out3.stdout, out3.stdout[-300:])

        # --- «плохое» зеркало первым в списке: у него под тем же именем другой образ — по дайджесту он не находится, берётся следующее зеркало
        sh(f"docker run -d --rm --name {bad} -p 127.0.0.1:{BAD}:5000 registry:2")
        for _ in range(30):
            if sh(f"curl -fs http://127.0.0.1:{BAD}/v2/").returncode == 0: break
            time.sleep(1)
        other = push_image(BAD, "library/probe", TAG)
        check("в «плохом» зеркале под тем же именем и тегом — другой образ (другой дайджест)", other != digest, other)
        Proxy.down = True
        iid = sh(f"docker image inspect -f '{{{{.Id}}}}' {mirror_ref}").stdout.strip()      # образ целиком (все имена и слои), иначе основной реестр «отдаст» его без слоёв
        sh(f"docker rmi -f {iid} >/dev/null 2>&1"); open(env, "w").write(f"COMPOSE_PROJECT_NAME=pgimgtest\nDATA_ROOT={data}\n"); shutil.rmtree(os.path.join(data, "state"), ignore_errors=True)
        two = script.replace(f"IMAGE_MIRRORS='127.0.0.1:{PRIMARY}=127.0.0.1:{MIRROR}'", f"IMAGE_MIRRORS='127.0.0.1:{PRIMARY}=127.0.0.1:{BAD} 127.0.0.1:{PRIMARY}=127.0.0.1:{MIRROR}'")
        out4 = sh(["bash", "-c", two.replace("img_ensure egress VIDEO_IMAGE", "img_ensure postgres POSTGRES_IMAGE").replace("VIDEO_IMAGE=${VIDEO_IMAGE:-}", "POSTGRES_IMAGE=${POSTGRES_IMAGE:-}")])
        print(out4.stdout[-2500:], out4.stderr[-2500:])
        check("подменённый образ в первом зеркале отклонён, образ взят из второго с тем же дайджестом", "RC=0" in out4.stdout and f"POSTGRES_IMAGE={mirror_ref}" in out4.stdout
              and sh(f"docker image inspect 127.0.0.1:{BAD}/library/probe@{other}").returncode != 0, (out4.stdout + out4.stderr)[-500:])
        pg_line = next(l for l in open(os.path.join(ROOT, "deployment/compose.yml"), encoding="utf-8") if "POSTGRES_IMAGE" in l)      # та же строка, что в настоящем compose
        mini = os.path.join(tmp, "pg-compose.yml"); open(mini, "w").write("services:\n  postgres:\n" + pg_line)
        sub = sh(f"docker compose -f '{mini}' --env-file '{env}' config --images")
        check("compose подставляет PostgreSQL из POSTGRES_IMAGE (адрес зеркала с тем же дайджестом)", sub.stdout.strip() == mirror_ref, (sub.stdout + sub.stderr)[-300:])
        check("без POSTGRES_IMAGE compose берёт закреплённый образ Docker Hub", sh(f"docker compose -f '{mini}' --env-file /dev/null config --images").stdout.strip().startswith("postgres:"))

        # --- сборка: базовый образ из зеркала уходит аргументом, а сама сборка не обращается ни к основному реестру, ни к зеркалу (оба выключены)
        ctx = os.path.join(tmp, "ctx"); os.makedirs(ctx)
        open(os.path.join(ctx, "Dockerfile"), "w").write(f"ARG PROBE_IMAGE={primary_ref}\nFROM ${{PROBE_IMAGE}}\nLABEL peregovorka.test=1\n")
        open(os.path.join(ctx, "compose.yml"), "w").write(f"services:\n  probe:\n    build: .\n    image: {built}\n")
        sh(f"docker rm -f {reg} {bad} >/dev/null 2>&1"); srv.shutdown(); srv.server_close()
        calls_before = dict(Proxy.calls)
        b = sh(["bash", "-c", script.split("img_ensure")[0] + f"""img_build_bases probe '{ctx}/Dockerfile'; echo "ARGS=${{BUILD_BASE_ARGS[*]}}"
COMPOSE_BAKE=false DOCKER_BUILDKIT=1 docker compose -f '{ctx}/compose.yml' build "${{BUILD_BASE_ARGS[@]}}" probe 2>&1 | tail -5; echo "BUILD=${{PIPESTATUS[0]}}"
"""], timeout=300)
        print(b.stdout[-1200:], b.stderr[-600:])
        check("базовый образ сборки: найден на сервере под именем зеркала, передан аргументом --build-arg", f"ARGS=--build-arg PROBE_IMAGE={mirror_ref}" in b.stdout, b.stdout[-300:])
        check("docker compose build (BuildKit) собрал образ при выключенных основном реестре и зеркале — базовый образ взят с сервера", "BUILD=0" in b.stdout
              and sh(f"docker image inspect {built}").returncode == 0 and Proxy.calls == calls_before, b.stdout[-400:])
    finally:
        sh(f"docker rm -f {reg} {bad} >/dev/null 2>&1"); sh(f"docker rmi {built} >/dev/null 2>&1")
        sh(f"docker rmi 127.0.0.1:{MIRROR}/library/probe@{digest if 'digest' in dir() else ''} >/dev/null 2>&1")
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
    bad = [r for r in results if not r[1]]
    print(f"\nПроверок: {len(results)}, не прошли: {len(bad)}")
    sys.exit(1 if bad else 0)
