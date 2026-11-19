"""Настоящие зеркала Docker Hub из deployment/image-mirrors.conf: img_ensure берёт через каждое из них небольшой образ по дайджесту и проверяет его.

Docker Hub подменён недоступным адресом (`hub.invalid`: имя не разрешается), поэтому каждый прогон идёт по настоящему пути отказа: повторы → зеркало.
Образ — hello-world (≈ 10 КБ), закреплён дайджестом индекса. После каждого зеркала образ удаляется целиком; данные слоёв containerd может держать
у себя и дольше, поэтому для второго и третьего зеркала надёжно проверяется манифест по дайджесту, а полностью слои качаются в первом прогоне.
Отдельно: недоступное первое зеркало пропускается, берётся следующее.

Запуск: python3 tests/live/hub_mirrors_real.py    (нужен docker и выход в интернет; ~1–2 минуты; результат зависит от того, откуда запускать)
"""
import os, shutil, subprocess, sys, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DIGEST = "sha256:1e4c05b897a751332ce85c7e9449da2d93514ea2dea6dbb0e2753c9e949b2bfa"       # hello-world:latest, индекс OCI (Docker Hub, 2026-11-19)
REF = f"hub.invalid/library/hello-world:latest@{DIGEST}"
res = []


def check(name, ok, info=""):
    res.append(bool(ok)); print(("OK   " if ok else "FAIL ") + name + (f"  [{str(info)[-300:]}]" if info else ""), flush=True)


def sh(cmd):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True)


def mirrors():
    return [l.split("#")[0].strip() for l in open(os.path.join(ROOT, "deployment/image-mirrors.conf"), encoding="utf-8") if l.split("#")[0].strip()]


def drop(ref):
    iid = sh(f"docker image inspect -f '{{{{.Id}}}}' {ref}").stdout.strip()
    if iid: sh(f"docker rmi -f {iid}")


def ensure(tmp, spec):
    env = os.path.join(tmp, ".env"); open(env, "w").write(f"COMPOSE_PROJECT_NAME=pghubtest\nDATA_ROOT={tmp}\n")
    shutil.rmtree(os.path.join(tmp, "state"), ignore_errors=True)
    script = f"""set -u; export IMAGE_PULL_ATTEMPTS=2 IMAGE_PULL_PAUSE=1 IMAGE_PULL_TIMEOUT=120 IMAGE_MIRRORS='{spec}' ENV_FILE='{env}'
source '{ROOT}/scripts/lib/common.sh'; load_env "$ENV_FILE"; DATA_ROOT='{tmp}'
img_ensure probe PROBE_IMAGE '{REF}'; echo "RC=$?"; echo "PROBE_IMAGE=${{PROBE_IMAGE:-}}"
"""
    return sh(["bash", "-c", script])


def main():
    tmp = tempfile.mkdtemp(prefix="pghub-")
    try:
        ms = mirrors()
        check("список зеркал по умолчанию прочитан", len(ms) >= 2, ms)
        for m in ms:
            want = f"{m}/library/hello-world@{DIGEST}"
            drop(want)
            r = ensure(tmp, f"hub.invalid={m}")
            out = r.stdout + r.stderr
            ok = "RC=0" in out and f"PROBE_IMAGE={want}" in out and sh(f"docker image inspect {want}").returncode == 0
            check(f"{m}: Docker Hub недоступен → образ загружен через зеркало и проверен по дайджесту", ok, out)
            drop(want)
        first, second = "mirror-down.invalid", ms[1]
        r = ensure(tmp, f"hub.invalid={first} hub.invalid={second}")
        out = r.stdout + r.stderr
        check(f"первое зеркало недоступно → взято следующее ({second})", "RC=0" in out and f"PROBE_IMAGE={second}/library/hello-world@{DIGEST}" in out, out)
        drop(f"{second}/library/hello-world@{DIGEST}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
    print(f"\nПроверок: {len(res)}, не прошли: {res.count(False)}")
    sys.exit(1 if False in res else 0)
