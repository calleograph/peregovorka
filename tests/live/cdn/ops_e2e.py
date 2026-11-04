"""Обслуживание установленного узла через API администрирования: обновление пакета, смена ключа, повторная установка, вывод из работы, удаление.
  ADMIN_PW_FILE=… LAB_PW_FILE=… python ops_e2e.py имя_узла  (узел — настоящая ВМ в состоянии ready, самоподписанный сертификат)"""
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
public = sys.argv[1]
res = {}


def login():
    c = httpx.Client(base_url=BASE, headers={"Origin": os.environ.get("ORIGIN", "http://localhost:28480")}, timeout=300)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][-1]["id"]})
    r.raise_for_status()
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


c = login()
ov = c.get("/api/v1/admin/cdn").json()
n = next(x for x in ov["nodes"] if x["public_name"] == public)
nid = n["id"]


def node():
    return c.get(f"/api/v1/admin/cdn/nodes/{nid}").json()


def wait():
    for _ in range(240):
        d = node()
        if not d["busy"] and d["state"] != "installing":
            return d
        time.sleep(5)
    raise TimeoutError


# обновление пакета
res["before"] = n["version"]
r = c.post(f"/api/v1/admin/cdn/nodes/{nid}/upgrade")
d = wait()
res["upgrade"] = {"http": r.status_code, "version": d["version"], "outdated": d["outdated"], "error": d["last_error"]}
res["test_after_upgrade"] = c.post(f"/api/v1/admin/cdn/nodes/{nid}/test").json().get("ok")
# смена ключа: ссылка на старом ключе работает после одной смены и отвергается после двух
acc = "0" * 24
k0 = c.get("/api/v1/admin/cdn").json()["keys"]
r1 = c.post("/api/v1/admin/cdn/keys/rotate").json()
res["rotate1"] = {"ok": r1["ok"], "kid": r1["kid"], "applied": r1["applied"]}
res["test_after_rotate"] = c.post(f"/api/v1/admin/cdn/nodes/{nid}/test").json().get("ok")
res["stats_keys"] = (c.post(f"/api/v1/admin/cdn/nodes/{nid}/poll").json().get("stats") or {}).get("keys")
res["keys_before"] = k0
# повторная установка поверх работающего узла
pw = open(os.environ["LAB_PW_FILE"]).read().strip()
r = c.post(f"/api/v1/admin/cdn/nodes/{nid}/install", json={"password": pw})
res["reinstall_http"] = r.status_code
d = wait()
res["reinstall"] = {"state": d["state"], "error": d["last_error"], "stages": [s["name"] for s in d["stages"] if s["status"] != "ok"]}
# вывод из работы
res["disable"] = c.patch(f"/api/v1/admin/cdn/nodes/{nid}", json={"enabled": False}).json()["enabled"]
res["enable"] = c.patch(f"/api/v1/admin/cdn/nodes/{nid}", json={"enabled": True}).json()["enabled"]
if len(sys.argv) > 2 and sys.argv[2] == "delete":
    r = c.delete(f"/api/v1/admin/cdn/nodes/{nid}")
    res["delete"] = {"http": r.status_code, "body": r.json()}
print(json.dumps(res, ensure_ascii=False, indent=1))
