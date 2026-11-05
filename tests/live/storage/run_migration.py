#!/usr/bin/env python3
"""Перенос между хранилищами на настоящем стенде: локальный том ↔ настоящий Samba, через HTTP API, с большими файлами, обрывом связи и перезапуском backend.
Запуск на тестовом экземпляре (pgdoc): рядом контейнер Samba `pgsmb` с ресурсами a и b. Данные только синтетические. Параметры: BIG_MB (размер большого файла)."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import uuid

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
BACK, DB, SMB = "pgdoc-backend-1", "pgdoc-postgres-1", "pgsmb"
LOCAL = "/data/exports/migr-c"
BIG_MB = int(os.environ.get("BIG_MB", "1200"))
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


def sh(*a, check=True, inp=None):
    return subprocess.run(a, check=check, capture_output=True, text=inp is not None or None, input=inp)


def in_back(code: str) -> str:
    return subprocess.run(["docker", "exec", "-i", BACK, "python", "-"], input=code, capture_output=True, text=True, check=True).stdout


def sql(q: str) -> str:
    return subprocess.run(["docker", "exec", DB, "sh", "-c", f'psql -U $POSTGRES_USER -d $POSTGRES_DB -tAc "{q}"'], capture_output=True, text=True, check=True).stdout.strip()


def admin() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": BASE.replace("127.0.0.1", "localhost")}, timeout=120)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.path.expanduser("~/pg-work/cdnlab/admin.pw")).read().strip()})
    j = r.json()
    if r.status_code == 409:
        d = j["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][0]["id"]})
        j = r.json()
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = j["csrf_token"]
    return c


API = "/api/v1/admin/storage/migrations"
c = admin()


def profile(name, kind, config, secret=""):
    for p in c.get("/api/v1/admin/storages").json()["items"]:
        if p["name"] == name:
            return p
    r = c.post("/api/v1/admin/storages", json={"name": name, "kind": kind, "config": config, "secret": secret})
    assert r.status_code == 201, r.text
    p = r.json()
    r = c.post(f"/api/v1/admin/storages/{p['id']}/test")
    assert r.status_code == 200 and r.json().get("ok", True), r.text
    return p


def wait(job, states, limit=900):
    end = time.time() + limit
    while time.time() < end:
        try:
            j = c.get(f"{API}/{job}").json()
        except httpx.HTTPError:
            time.sleep(2)
            continue
        if j.get("state") in states:
            return j
        time.sleep(1)
    raise TimeoutError(f"{job} не дошёл до {states}: {c.get(f'{API}/{job}').text[:600]}")


def settings(group, **patch):
    r = c.put(f"/api/v1/admin/settings/{group}", json=patch)
    assert r.status_code == 200, r.text


def rss() -> int:
    out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", BACK], capture_output=True, text=True).stdout.split("/")[0].strip()
    return int(float(out[:-3]) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}[out[-3:]])


def seed_local() -> dict:
    """Файлы и строки базы как после обычной работы: записи разных областей, документы, вложения, журнал."""
    in_back(f"""
import os,hashlib
root={LOCAL!r}
for i in range(120):
    p=f"{{root}}/Audio/Планёрка/2026-11-06, пятница/10-00 (ab{{i:06d}})/дорожка {{i}}.wav"
    os.makedirs(os.path.dirname(p),exist_ok=True); open(p,'wb').write(os.urandom(4096+i))
for kind,sub in (("Transcripts","protocol.txt"),("Protocols","protocol.docx"),("Boards","whiteboard.drawio"),("Chat","chat.txt")):
    p=f"{{root}}/{{kind}}/Планёрка/2026-11-06, пятница/10-00/{{sub}}"
    os.makedirs(os.path.dirname(p),exist_ok=True); open(p,'wb').write(os.urandom(2000))
p=f"{{root}}/Chat/files/m1/{{'0'*8}}.png"; os.makedirs(os.path.dirname(p),exist_ok=True); open(p,'wb').write(os.urandom(900))
p=f"{{root}}/Logs/journal/2026-11-05.jsonl.gz"; os.makedirs(os.path.dirname(p),exist_ok=True); open(p,'wb').write(os.urandom(900))
p=f"{{root}}/Video/Планёрка/video-1.mp4"; os.makedirs(os.path.dirname(p),exist_ok=True)
with open(p,'wb') as f:
    for _ in range({BIG_MB}): f.write(os.urandom(1<<20))
""")
    mid, rid = sql("select id||'|'||room_id from meetings order by started_at limit 1").split("|")
    aid = str(uuid.uuid4())
    sql(f"insert into recordings (id, meeting_id, room_id, participant_identity, path, size_bytes, kind, mime, status, export_status, export_area, file_state, has_video, created_at, delete_attempts) "
        f"values ('{aid}','{mid}','{rid}','u-mig','Планёрка/video-1.mp4',{BIG_MB << 20},'mix_video','video/mp4','ready','exported','video','ok',true,now(),0)")
    return {"meeting": mid, "rec": aid}


def size_of(where, rel):
    return in_back(f"import os;print(os.path.getsize({LOCAL + '/' + rel!r}))").strip() if where == "local" else None


def sha_api(meeting, rec, limit=None):
    h = hashlib.sha256()
    with c.stream("GET", f"/api/v1/meetings/{meeting}/media/{rec}/stream", headers={"Range": f"bytes=0-{limit}"} if limit else {}) as r:
        assert r.status_code in (200, 206), (r.status_code, r.read()[:200])
        for ch in r.iter_bytes(1 << 20):
            h.update(ch)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ подготовка
in_back(f"import os;os.makedirs({LOCAL!r},exist_ok=True)")
loc = profile("Перенос: локальный том", "local", {"local_path": LOCAL})
sa = profile("Перенос: SMB A", "smb", {"smb_server": "pgsmb", "smb_share": "a", "smb_username": "pgu"}, "pgp")
sb = profile("Перенос: SMB B", "smb", {"smb_server": "pgsmb", "smb_share": "b", "smb_username": "pgu"}, "pgp")
ids = seed_local()
sql(f"update recordings set export_profile_id='{loc['id']}' where id='{ids['rec']}'")
source_sha = in_back(f"""
import hashlib
h=hashlib.sha256()
with open({LOCAL + '/Video/Планёрка/video-1.mp4'!r},'rb') as f:
    while c:=f.read(1<<20): h.update(c)
print(h.hexdigest())""").strip()


def move(a, b, label, *, chaos=None):
    r = c.post(API, json={"source_id": a["id"], "target_id": b["id"]})
    assert r.status_code == 201, r.text
    job = r.json()["id"]
    j = wait(job, ("ready", "failed"))
    t(f"{label}: оценка", j["state"] == "ready" and j["total"] >= 120, f"{j['total']} файлов, {j['bytes_total'] >> 20} МБ, скорость {(j['estimate']['speed_bps'] or 0) >> 20} МБ/с, ждём {j['estimate']['seconds']} с")
    base = rss()
    assert c.post(f"{API}/{job}/start").status_code == 200
    peak = base
    t0 = time.time()
    if chaos:
        chaos(job)
    while True:
        j = c.get(f"{API}/{job}").json()
        peak = max(peak, rss())
        if j["state"] in ("switched", "failed", "attention", "cancelled"):
            break
        time.sleep(1.5)
    dt = time.time() - t0
    t(f"{label}: перенесено и переключено", j["state"] == "switched", f"{j['state']} {j.get('error') or ''} за {dt:.0f} с, память backend {base}→{peak} МБ")
    t(f"{label}: память не растёт с размером файла", peak - base < 250, f"+{peak - base} МБ при файле {BIG_MB} МБ")
    return job, j


def clean(job, name, label):
    j = c.get(f"{API}/{job}").json()
    wait_s = max(0, 305 - (time.time() - time.mktime(time.strptime(j["switched_at"][:19], "%Y-%m-%dT%H:%M:%S")) + time.timezone))
    if os.environ.get("SKIP_GRACE"):
        sql(f"update storage_migrations set switched_at = switched_at - interval '10 minutes' where id='{job}'")
    else:
        time.sleep(wait_s)
    r = c.post(f"{API}/{job}/clean", json={"confirm": name})
    t(f"{label}: очистка принята", r.status_code == 200, r.text[:160])
    j = wait(job, ("cleaned", "failed"))
    t(f"{label}: источник очищен", j["state"] == "cleaned" and j["report"]["kept"] == [], json.dumps(j.get("report"), ensure_ascii=False)[:200])


# 1. локальный том → SMB, с обрывом связи посреди копирования
def outage(job):
    while c.get(f"{API}/{job}").json()["bytes_done"] < 1 << 20:
        time.sleep(0.05)
    sh("docker", "stop", "-t", "0", SMB)                # сервер хранения недоступен: соединение оборвано
    end, state = time.time() + 120, ""
    while time.time() < end and state != "waiting":
        state = c.get(f"{API}/{job}").json()["state"]
        time.sleep(1)
    t("обрыв SMB: задание ждёт, а не падает", state == "waiting", state)
    sh("docker", "start", SMB)


job, j = move(loc, sa, "локальный том → SMB", chaos=outage)
t("локальный том → SMB: запись читается через приложение с новым адресом", sha_api(ids["meeting"], ids["rec"]) == source_sha)
t("локальный том → SMB: строка базы на новом хранилище", sql(f"select export_profile_id from recordings where id='{ids['rec']}'") == sa["id"])
clean(job, loc["name"], "локальный том → SMB")
t("локальный том → SMB: на источнике не осталось данных", in_back(f"import os\nprint(sum(len(f) for _,_,f in os.walk({LOCAL!r})))").strip() in ("0", "1"))
t("локальный том → SMB: запись читается и после очистки", sha_api(ids["meeting"], ids["rec"]) == source_sha)


# 2. SMB → SMB, с перезапуском backend посреди копирования
def restart(job):
    global c
    while c.get(f"{API}/{job}").json()["bytes_done"] < 80 << 20:
        time.sleep(0.2)
    sh("docker", "restart", BACK)
    for _ in range(60):
        try:
            c = admin()
            break
        except Exception:  # noqa: BLE001
            time.sleep(2)
    t("перезапуск backend: задание продолжается", c.get(f"{API}/{job}").json()["state"] in ("copying", "verifying", "switching", "switched"))


job, j = move(sa, sb, "SMB → SMB", chaos=restart)
t("SMB → SMB: запись цела", sha_api(ids["meeting"], ids["rec"]) == source_sha)
clean(job, sa["name"], "SMB → SMB")

# 3. SMB → локальный том
job, j = move(sb, loc, "SMB → локальный том")
t("SMB → локальный том: запись цела", sha_api(ids["meeting"], ids["rec"]) == source_sha)
t("SMB → локальный том: файл на месте, время сохранено", in_back(f"import os\nprint(os.path.getsize({LOCAL + '/Video/Планёрка/video-1.mp4'!r}))").strip() == str(BIG_MB << 20))
clean(job, sb["name"], "SMB → локальный том")

print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
for f in FAIL:
    print("  ", f)
sys.exit(1 if FAIL else 0)
