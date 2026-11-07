#!/usr/bin/env python3
"""Резервные копии при переносе: копия настроек создаётся на хранилище A, переносится на B, скачивается с нового места и применяется на изолированном экземпляре.
Заодно проверяется экземпляр, восстановленный из полного набора: вход, расшифровка паролей хранилищ (нужен APP_MASTER_KEY из набора), файлы."""
import os
import sys
import time
import httpx

MAIN, CLEAN = os.environ.get("BASE", "http://127.0.0.1:28480"), os.environ.get("CLEAN", "http://127.0.0.1:28580")
PW = open(os.path.expanduser("~/pg-work/cdnlab/admin.pw")).read().strip()
PASS, FAIL = [], []


def t(name, ok, note=""):
    (PASS if ok else FAIL).append(name)
    print(("ok   " if ok else "FAIL ") + name + (f"  [{note}]" if note else ""), flush=True)


def admin(base):
    c = httpx.Client(base_url=base, headers={"Origin": MAIN.replace("127.0.0.1", "localhost")}, timeout=120)     # у восстановленного экземпляра .env из набора: допустимый адрес прежний
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": PW})
    j = r.json()
    if r.status_code == 409:
        d = j["detail"]
        j = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][0]["id"]}).json()
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = j["csrf_token"]
    return c


a = admin(MAIN)
b = admin(CLEAN)
t("чистый экземпляр: вход прежним паролем", True)
items = {p["name"]: p for p in b.get("/api/v1/admin/storages").json()["items"]}
t("чистый экземпляр: хранилища на месте", "Перенос: SMB A" in items)
r = b.post(f"/api/v1/admin/storages/{items['Перенос: SMB A']['id']}/test")
t("чистый экземпляр: пароль SMB расшифрован ключом из набора", r.status_code == 200 and r.json().get("ok", True), r.text[:120])
t("чистый экземпляр: комнаты совпадают", a.get("/api/v1/admin/rooms").text.count('"slug"') == b.get("/api/v1/admin/rooms").text.count('"slug"'))

ps = {p["name"]: p for p in a.get("/api/v1/admin/storages").json()["items"]}
A, B = ps["Перенос: SMB A"], ps["Перенос: SMB B"]
assert a.put("/api/v1/admin/auto-backup/settings", json={"profile_id": A["id"]}).status_code == 200
key = a.post("/api/v1/admin/auto-backup/key", json={"password": PW}).json()["key"]
run = a.post("/api/v1/admin/auto-backup/run").json()
for _ in range(60):
    files = a.get("/api/v1/admin/auto-backup/files").json()
    names = [f["name"] for f in (files["items"] if isinstance(files, dict) else files)]
    if names:
        break
    time.sleep(1)
t("копия настроек лежит на A", bool(names), str(run)[:100])
J = "/api/v1/admin/storage/migrations"
job = a.post(J, json={"source_id": A["id"], "target_id": B["id"], "groups": ["backups"]}).json()["id"]


def wait(states):
    for _ in range(300):
        j = a.get(f"{J}/{job}").json()
        if j["state"] in states:
            return j
        time.sleep(1)
    raise TimeoutError(j)


wait(("ready",))
a.post(f"{J}/{job}/start")
j = wait(("switched", "attention", "failed"))
t("перенос копий: проверка копии на новом месте пройдена", j["state"] == "switched", str(j.get("error")))
v = a.post("/api/v1/admin/auto-backup/verify", json={}).json()
t("копия проверяется с нового хранилища", v.get("status") == "ok", str(v)[:160])
name = names[0]
blob = a.post("/api/v1/admin/auto-backup/download", json={"name": name, "password": PW}).content
t("копия скачивается с нового хранилища", len(blob) > 1000, f"{len(blob)} байт")
r = b.post("/api/v1/admin/config/import/preview", content=blob, headers={"X-Archive-Password": key})
t("изолированный экземпляр: копия открывается ключом и проходит проверку", r.status_code == 200, r.text[:200])
print(f"\nПРОВЕРОК: {len(PASS) + len(FAIL)}, не пройдено: {len(FAIL)}")
sys.exit(1 if FAIL else 0)
