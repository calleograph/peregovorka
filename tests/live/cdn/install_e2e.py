"""Подключение узла CDN через API администрирования — тот же путь, что у кнопок в интерфейсе: отпечаток → проверка сервера → установка → состояние.
  BASE=http://127.0.0.1:28480 ADMIN_PW_FILE=… LAB_PW_FILE=… python install_e2e.py имя_ssh публичное_имя [название]
Сервер — настоящая ВМ (tests/live/cdn/vm). Печатает JSON: система, этапы с временем, итог сквозной проверки, управление."""
import json
import os
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:28480")
ssh_host, public = sys.argv[1], sys.argv[2]


def login() -> httpx.Client:
    c = httpx.Client(base_url=BASE, headers={"Origin": os.environ.get("ORIGIN", "http://localhost:28480")}, timeout=120)
    r = c.post("/api/v1/auth/login", json={"login": "admin", "password": open(os.environ["ADMIN_PW_FILE"]).read().strip()})
    if r.status_code == 409:
        d = r.json()["detail"]
        r = c.post("/api/v1/auth/login/free-slot", json={"free_token": d["free_token"], "session_id": d["sessions"][-1]["id"]})
    r.raise_for_status()
    c.headers["Cookie"] = "vm_session=" + c.cookies["vm_session"]    # cookie с Secure: на локальный http-порт клиент сам его не отправит
    c.cookies.clear()
    c.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    return c


c = login()
out = {"server": ssh_host}
t0 = time.time()
hk = c.post("/api/v1/admin/cdn/hostkey", json={"ssh_host": ssh_host, "ssh_port": 22})
hk.raise_for_status()
hk = hk.json()
out["fingerprint"] = hk["fingerprint"]
r = c.post("/api/v1/admin/cdn/nodes", json={"name": sys.argv[3] if len(sys.argv) > 3 else public, "public_name": public, "ssh_host": ssh_host, "ssh_port": 22, "ssh_user": "cdnadmin",
                                            "password": open(os.environ["LAB_PW_FILE"]).read().strip(), "host_key": hk["host_key"], "fingerprint": hk["fingerprint"], "quality": "1080p",
                                            **({"tls_mode": "files", "tls_cert_pem": open(os.environ["TLS_CERT"]).read(), "tls_key_pem": open(os.environ["TLS_KEY"]).read()} if os.environ.get("TLS_CERT") else {"tls_mode": "selfsigned"})})
if r.status_code != 201:
    print(json.dumps({**out, "add": r.status_code, "detail": r.json()}, ensure_ascii=False))
    sys.exit(1)
n = r.json()
out["facts"] = {k: n["facts"].get(k) for k in ("os_name", "arch", "cpus", "sudo", "systemd", "container")}
out["assessment"] = {k: n["assessment"].get(k) for k in ("ok", "viewers", "blockers")}
c.post(f"/api/v1/admin/cdn/nodes/{n['id']}/install", json={}).raise_for_status()
while time.time() - t0 < 2400:
    time.sleep(10)
    try:
        r = c.get(f"/api/v1/admin/cdn/nodes/{n['id']}")
        if r.status_code == 401:                     # лимит сеансов: другой сценарий занял слот
            c = login()
            continue
        n = r.json()
    except httpx.HTTPError:
        continue
    if n["state"] in ("ready", "error") and not n["busy"]:
        break
out.update(state=n["state"], error=n["last_error"], seconds=int(time.time() - t0), version=n["version"], stages=[f"{s['name']}:{s['status']}" + (f" ({s['msg'][:160]})" if s["msg"] else "") for s in n["stages"]])
if n.get("diag") and n["state"] != "ready":
    out["diag_tail"] = n["diag"][-1500:]
out["id"] = n["id"]
c.post("/api/v1/auth/logout")
print(json.dumps(out, ensure_ascii=False, indent=1))
