"""Один нагрузочный прогон: зрители (loadgen) на узле CDN или прямо на источнике + показатели контейнеров.
  python load_run.py ТОКЕН_ЭФИРА ЗРИТЕЛЕЙ МЕТКА [--direct] [--seconds 60] [--ramp 20] [--node КОНТЕЙНЕР]
Результат — строка JSON в ~/pg-work/cdnlab/logs/load.jsonl и на экран. Запускается с хоста, стенд — docs/BROADCAST_CDN.md «Испытания»."""
import argparse
import json
import os
import re
import subprocess
import threading
import time

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("token")
ap.add_argument("viewers", type=int)
ap.add_argument("label")
ap.add_argument("--direct", action="store_true")
ap.add_argument("--seconds", type=int, default=60)
ap.add_argument("--ramp", type=int, default=20)
ap.add_argument("--node", default="pgcdnload")
a = ap.parse_args()
LAB = os.path.expanduser("~/pg-work/cdnlab")
BASE = "http://127.0.0.1:28480"
v = httpx.Client(base_url=BASE, headers={"Origin": "http://localhost:28480"}, timeout=30)
vt = v.post(f"/api/v1/live/{a.token}/viewer", json={"display_name": "Нагрузка"}).json()["viewer"]
acc = v.post(f"/api/v1/live/{a.token}/access", json={"viewer": vt}).json()
if a.direct:
    path = acc.get("origin_path") or acc["path"]
    url, extra = BASE + path, []
else:
    assert acc.get("node"), acc
    ip = subprocess.check_output(["docker", "inspect", "-f", '{{(index .NetworkSettings.Networks "pgdoc_default").IPAddress}}', a.node], text=True).strip()
    url, extra = acc["base"] + acc["path"], ["-insecure", "-resolve", f"{acc['node']}:443:{ip}"]


def tx(c):
    return int(subprocess.check_output(["docker", "exec", c, "cat", "/sys/class/net/eth0/statistics/tx_bytes"], text=True))


NAMES = ["pgdoc-web-1", "pgdoc-livekit-egress-1"] + ([] if a.direct else [a.node])
samples: dict = {n: [] for n in NAMES}
stop = threading.Event()


def sample():
    while not stop.wait(4):
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.CPUPerc}} {{.MemUsage}}", *NAMES], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            m = re.match(r"(\S+) ([\d.]+)% ([\d.]+)(\w+)", ln)
            if m:
                mb = float(m[3]) * {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}.get(m[4], 1)
                samples[m[1]].append((float(m[2]), mb))


threading.Thread(target=sample, daemon=True).start()
w0, n0, t0 = tx("pgdoc-web-1"), (0 if a.direct else tx(a.node)), time.time()
r = subprocess.run([f"{LAB}/loadgen", "-url", url, "-viewers", str(a.viewers), "-duration", f"{a.seconds}s", "-ramp", f"{a.ramp}s", *extra], capture_output=True, text=True)
dt = time.time() - t0
stop.set()
w1, n1 = tx("pgdoc-web-1"), (0 if a.direct else tx(a.node))
res = json.loads(r.stdout.strip().splitlines()[-1])
tail = lambda s: s[len(s) // 3:] or s  # без разгона
out = {"label": a.label, "viewers": a.viewers, "direct": a.direct, "mbit": round(res["mbit_per_s"]), "seg_ms": res["seg_ms"], "playlist_ms": res["playlist_ms"], "stalls": res["stalls"], "segments": res["segments"], "codes": res["codes"],
       "origin_tx_mbit": round((w1 - w0) * 8 / dt / 1e6, 1), "node_tx_mbit": round((n1 - n0) * 8 / dt / 1e6, 1) if not a.direct else None,
       "cpu_pct": {n: round(sum(x[0] for x in tail(s)) / max(len(tail(s)), 1)) for n, s in samples.items()}, "mem_mb": {n: round(max((x[1] for x in s), default=0)) for n, s in samples.items()}, "at": time.strftime("%H:%M:%S")}
line = json.dumps(out, ensure_ascii=False)
open(f"{LAB}/logs/load.jsonl", "a").write(line + "\n")
print(line)
