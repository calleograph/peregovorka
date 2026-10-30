"""Сценарий реальной эксплуатации на НАСТОЯЩИХ контейнерах (тестовый экземпляр с включённой видеоподсистемой): основной реестр отвечает, но слои не загружаются (таймаут CDN) →
установщик сам переходит на разрешённое зеркало mirror.gcr.io (настоящее), проверяет дайджест и архитектуру, записывает адрес в .env и запускает настоящий Egress из образа зеркала.

Сбой основного источника подставляется оболочкой docker_cdn_timeout_shim.sh (только для `docker pull` не-зеркальных образов); манифест и загрузка у зеркала — настоящие.
Чтобы образ не пришлось качать заново (≈ 1,3 ГБ), «основное» имя образа — псевдоним реестра registry-1.docker.io; содержимое с тем же дайджестом уже есть в хранилище Docker.
Запуск (нужен sudo без пароля): PG_DIR=~/pg-work/docstand PG_PROJECT=pgdoc python3 tests/live/image_mirror_real.py"""
import json, os, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PG = os.path.expanduser(os.environ.get("PG_DIR", "~/pg-work/docstand")); PROJECT = os.environ.get("PG_PROJECT", "pgdoc")
DIGEST = "sha256:ac244a40268ce1dd1510fbff6eb529609ec68aceff1602343f79be8ca08c355b"
ALIAS = f"registry-1.docker.io/livekit/egress:v1.15.0@{DIGEST}"
MIRROR = f"mirror.gcr.io/livekit/egress@{DIGEST}"
res = []


def check(name, ok, info=""):
    res.append(bool(ok)); print(("OK   " if ok else "FAIL ") + name + (f"  [{str(info)[:260]}]" if info else ""), flush=True)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def envget(key):
    for l in open(os.path.join(PG, ".env"), encoding="utf-8").read().splitlines():
        if l.startswith(key + "="): return l.split("=", 1)[1].strip().strip('"')
    return ""


def setenv(key, val):
    p = os.path.join(PG, ".env"); lines = open(p, encoding="utf-8").read().splitlines(); done = False
    for i, l in enumerate(lines):
        if l.startswith(key + "="): lines[i] = f"{key}={val}"; done = True
    if not done: lines.append(f"{key}={val}")
    open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def egress(wait=0):
    """Состояние контейнера Egress; wait — подождать здоровья (проверка здоровья начинается через 30 с после запуска)."""
    import time
    for _ in range(max(1, wait // 3)):
        v = egress_now()
        if wait == 0 or v.endswith("|healthy"): return v
        time.sleep(3)
    return egress_now()


def egress_now():
    r = sh(f"docker inspect -f '{{{{.Config.Image}}}}|{{{{.State.Status}}}}|{{{{.State.Health.Status}}}}' {PROJECT}-livekit-egress-1")
    return r.stdout.strip()


def main():
    shim = tempfile.mkdtemp(prefix="pgshim-"); shutil.copy(os.path.join(ROOT, "tests/live/docker_cdn_timeout_shim.sh"), os.path.join(shim, "docker"))
    data = os.path.expanduser(envget("DATA_ROOT") or "~/pg-work/docstand-data")
    saved = envget("VIDEO_IMAGE")
    try:
        check("исходно служба записи работает", "running|healthy" in egress(), egress())
        # имя зеркала, оставшееся от прошлого прогона, — только дополнительная метка (содержимое образа остаётся под основным именем): снимаем её, чтобы загрузка действительно прошла
        sh(f"docker image rm {MIRROR} >/dev/null 2>&1")
        setenv("VIDEO_IMAGE", ALIAS)
        sh(f"sudo -n rm -f '{data}/state/image-sources' '{data}/state/image-error-egress.json'")
        r = sh(f"cd {PG} && sudo -n env PATH={shim}:$PATH IMAGE_MIRRORS=registry-1.docker.io=mirror.gcr.io IMAGE_PULL_PAUSE=1 IMAGE_PULL_ATTEMPTS=2 scripts/video.sh enable --env .env --yes", timeout=900)
        out = r.stdout + r.stderr
        print(out[-2500:])
        check("основной источник: две попытки, причина названа (таймаут слоёв)", out.count("попытка") >= 2 and "таймауту" in out, out[-200:])
        check("настоящее зеркало mirror.gcr.io: манифест найден, образ загружен и проверен", "из зеркала mirror.gcr.io и проверен" in out, out[-300:])
        check("в выводе нет подписи URL", "SECRETSIG" not in out)
        check(".env: VIDEO_IMAGE указывает на образ зеркала с тем же дайджестом", envget("VIDEO_IMAGE") == MIRROR, envget("VIDEO_IMAGE"))
        e = egress(120); check("Egress запущен из образа зеркала и здоров", e == f"{MIRROR}|running|healthy", e)
        st = sh(f"cd {PG} && sudo -n bash -c 'set -a; source .env; set +a; source scripts/lib/common.sh; ENV_FILE=.env; DATA_ROOT={data}; source scripts/lib/complib.sh; comp_state_json'")
        j = json.loads(st.stdout.strip().splitlines()[-1]) if st.stdout.strip() else {}
        check("состояние для панели: образ есть, источник — зеркало, ошибки нет", j.get("images", {}).get("egress") is True and j.get("image_sources", {}).get("egress") == "mirror" and j.get("image_errors", {}).get("egress") is None, str(j)[:300])
        r2 = sh(f"cd {PG} && sudo -n env PATH={shim}:$PATH IMAGE_MIRRORS=registry-1.docker.io=mirror.gcr.io scripts/video.sh enable --env .env --yes", timeout=600)
        check("повторное включение: сеть не нужна (образ уже загружен), служба остаётся здоровой", "попытка" not in (r2.stdout + r2.stderr) and egress(120).endswith("|running|healthy"), egress())

        # сбой без зеркала: нет такого образа ни в основном реестре, ни у зеркала — причина в панели и в журнале, служба остаётся прежней
        setenv("VIDEO_IMAGE", "registry-1.docker.io/livekit/egress:v0.0.0@sha256:" + "0" * 64)
        sh(f"sudo -n rm -f '{data}/state/image-sources'")
        r3 = sh(f"cd {PG} && sudo -n env IMAGE_MIRRORS=registry-1.docker.io=mirror.gcr.io IMAGE_PULL_PAUSE=1 IMAGE_PULL_ATTEMPTS=2 scripts/video.sh enable --env .env --yes", timeout=900)
        o3 = r3.stdout + r3.stderr
        check("несуществующий образ: установка остановлена с понятной причиной, а не «нет места на диске»", r3.returncode != 0 and "диск" not in o3.lower().replace("диске", "") and "Образ не скачан" in o3, o3[-400:])
        st = sh(f"cd {PG} && sudo -n bash -c 'set -a; source .env; set +a; source scripts/lib/common.sh; ENV_FILE=.env; DATA_ROOT={data}; source scripts/lib/complib.sh; comp_state_json'")
        j = json.loads(st.stdout.strip().splitlines()[-1]); er = (j.get("image_errors") or {}).get("egress") or {}
        check("в состоянии для панели есть причина (код, этап, пробованные источники)", er.get("code") and er.get("stage") and "mirror.gcr.io" in er.get("tried", ""), str(er)[:300])
    finally:
        setenv("VIDEO_IMAGE", saved)
        sh(f"sudo -n rm -f '{data}/state/image-sources' '{data}/state/image-error-egress.json'")
        r = sh(f"cd {PG} && sudo -n scripts/video.sh enable --env .env --yes", timeout=900)       # возврат к штатному состоянию: образ по умолчанию
        e = egress(120); check("тестовый экземпляр возвращён к штатному образу, Egress здоров", e.endswith("|running|healthy") and MIRROR not in e, e)
        shutil.rmtree(shim, ignore_errors=True)


if __name__ == "__main__":
    main()
    print(f"\nПроверок: {len(res)}, не прошли: {res.count(False)}")
    sys.exit(1 if False in res else 0)
